import sys
import json
import threading
import logging
import time
import subprocess
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.backend.server_process import ServerProcess
from hosty.shared.core.events import set_main_thread_dispatcher
from hosty.shared.utils.constants import COMMON_COMMANDS

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

logging.basicConfig(filename='win_ipc.log', level=logging.DEBUG, 
                    format='%(asctime)s %(levelname)s %(message)s')

class WinIPCBackend:
    def __init__(self):
        self.server_manager = ServerManager()
        self.stdout_lock = threading.Lock()
        self._output_handlers: dict[str, int] = {}  # server_id -> handler_id
        self._status_handlers: dict[str, int] = {}  # server_id -> handler_id
        self._psutil_processes: dict[str, Any] = {}  # server_id -> psutil.Process
        
        # Dispatch events from ServerManager back to stdout as JSON events
        set_main_thread_dispatcher(self.dispatch_event)
        
        # Subscribe to server manager events
        self.server_manager.connect('server-added', lambda m, sid: self.send_event('server-added', sid))
        self.server_manager.connect('server-removed', lambda m, sid: self.send_event('server-removed', sid))
        self.server_manager.connect('server-changed', lambda m, sid: self.send_event('server-changed', sid))
        
    def dispatch_event(self, callback, *args, **kwargs):
        # In stdin/stdout IPC, we don't have a UI event loop, so we just run it directly.
        # But we must be careful about thread safety.
        try:
            callback(*args, **kwargs)
        except Exception as e:
            logging.error(f"Event dispatch error: {e}")

    def send_response(self, req_id: Any, result: Any = None, error: str = None):
        msg = {"id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self._write_msg(msg)

    def send_event(self, event_name: str, data: Any):
        msg = {"event": event_name, "data": data}
        self._write_msg(msg)

    def _write_msg(self, msg: Dict):
        try:
            line = json.dumps(msg)
            with self.stdout_lock:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except Exception as e:
            logging.error(f"Error writing to stdout: {e}")

    def _attach_console_output(self, server_id: str, proc: ServerProcess):
        """Attach to a server process's output-received signal and forward lines as events."""
        # Detach any existing handler for this server
        self._detach_console_output(server_id)
        
        def on_output(process, text):
            self.send_event("console-output", {"server_id": server_id, "text": text})
        
        handler_id = proc.connect('output-received', on_output)
        self._output_handlers[server_id] = handler_id
    
    def _detach_console_output(self, server_id: str):
        """Detach console output handler for a server."""
        if server_id in self._output_handlers:
            proc = self.server_manager.get_existing_process(server_id)
            if proc:
                try:
                    proc.disconnect(self._output_handlers[server_id])
                except Exception:
                    pass
            del self._output_handlers[server_id]

    def _attach_status_output(self, server_id: str, proc: ServerProcess):
        """Attach to status changes once and mirror GTK's stop-time background work."""
        if server_id in self._status_handlers:
            return

        def on_status(process, status):
            self.send_event("server-status", {"server_id": server_id, "status": status})
            if status == "stopped" and self.server_manager.preferences.auto_backup_on_stop:
                def backup_task():
                    ok, msg = self.server_manager.create_world_backup(server_id, auto=True)
                    event = "backup-complete" if ok else "backup-skipped"
                    self.send_event(event, {"server_id": server_id, "message": msg})
                threading.Thread(target=backup_task, daemon=True).start()

        handler_id = proc.connect('status-changed', on_status)
        self._status_handlers[server_id] = handler_id

    def _detach_status_output(self, server_id: str):
        if server_id in self._status_handlers:
            proc = self.server_manager.get_existing_process(server_id)
            if proc:
                try:
                    proc.disconnect(self._status_handlers[server_id])
                except Exception:
                    pass
            del self._status_handlers[server_id]

    def _preferences_to_dict(self) -> dict:
        prefs = self.server_manager.preferences
        return {
            "default_ram_mb": prefs.default_ram_mb,
            "run_in_background_on_close": prefs.run_in_background_on_close,
            "open_on_startup": prefs.open_on_startup,
            "prevent_sleep_while_running": prefs.prevent_sleep_while_running,
            "auto_backup_on_stop": prefs.auto_backup_on_stop,
            "auto_resolve_mod_dependencies": prefs.auto_resolve_mod_dependencies,
            "theme": prefs.theme,
        }

    def _set_preference(self, key: str, value: Any):
        allowed = set(self._preferences_to_dict().keys())
        if key not in allowed:
            raise ValueError(f"Unknown preference: {key}")
        setattr(self.server_manager.preferences, key, value)

    def _get_config_payload(self, server_id: str) -> dict:
        info = self.server_manager.get_server(server_id)
        if not info:
            raise ValueError("Server not found")
        config = self.server_manager.get_config(server_id)
        if not config:
            raise ValueError("Server config not found")
        props = config.load()
        return {
            "properties": props,
            "ram_mb": info.ram_mb,
            "autostart": info.autostart,
            "mc_version": info.mc_version,
            "loader_version": info.loader_version,
        }

    def _get_local_ip(self) -> str:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ip:
                return ip
        except Exception:
            pass

        return "Not available"

    def _get_connection_info(self, server_id: str) -> dict:
        info = self.server_manager.get_server(server_id)
        if not info:
            raise ValueError("Server not found")

        config = self.server_manager.get_config(server_id)
        port = "25565"
        whitelist = False
        if config:
            config.load()
            port = config.get("server-port", "25565") or "25565"
            whitelist = config.get_bool("white-list", False)

        local_ip = self._get_local_ip()
        local_address = f"{local_ip}:{port}" if local_ip != "Not available" else local_ip
        return {
            "local_ip": local_ip,
            "server_port": port,
            "local_address": local_address,
            "whitelist": whitelist,
        }

    def _list_backups(self, server_id: str) -> list[dict]:
        info = self.server_manager.get_server(server_id)
        if not info:
            raise ValueError("Server not found")
        backups_dir = info.server_dir / "hosty-backups"
        if not backups_dir.is_dir():
            return []
        items = []
        for path in backups_dir.glob("*.zip"):
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
                "is_full": path.name.startswith("hosty-full-backup-"),
            })
        return sorted(items, key=lambda item: item["modified"], reverse=True)

    def handle_request(self, req: Dict):
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})
        
        logging.debug(f"Received request: {method} {params}")
        
        try:
            if method == "get_servers":
                servers = [s.to_dict() for s in self.server_manager.servers]
                self.send_response(req_id, result=servers)
            
            elif method == "get_versions":
                # Each request already runs in its own thread, so we can fetch synchronously.
                try:
                    game_vers = self.server_manager.download_manager.fetch_game_versions()
                    loader_vers = self.server_manager.download_manager.fetch_loader_versions()
                    self.send_response(req_id, result={"game_versions": game_vers, "loader_versions": loader_vers})
                except Exception as e:
                    self.send_response(req_id, error=f"Failed to fetch versions: {e}")
            
            elif method == "get_server_info":
                sid = params.get("server_id")
                info = self.server_manager.get_server(sid)
                self.send_response(req_id, result=info.to_dict() if info else None)

            elif method == "get_preferences":
                self.send_response(req_id, result=self._preferences_to_dict())

            elif method == "update_preference":
                self._set_preference(params.get("key"), params.get("value"))
                self.send_response(req_id, result=self._preferences_to_dict())

            elif method == "get_server_properties":
                self.send_response(req_id, result=self._get_config_payload(params.get("server_id")))

            elif method == "get_connection_info":
                self.send_response(req_id, result=self._get_connection_info(params.get("server_id")))

            elif method == "get_common_commands":
                self.send_response(req_id, result=COMMON_COMMANDS)

            elif method == "update_server_properties":
                sid = params.get("server_id")
                updates = params.get("properties", {})
                if not isinstance(updates, dict):
                    raise ValueError("properties must be an object")
                config = self.server_manager.get_config(sid)
                if not config:
                    raise ValueError("Server config not found")
                config.load()
                for key, value in updates.items():
                    config.set_value(str(key), value)
                config.save()

                proc = self.server_manager.get_existing_process(sid)
                if proc and "max-players" in updates:
                    try:
                        proc.set_max_players(config.get_int("max-players", 20))
                    except Exception:
                        pass

                self.server_manager.emit_on_main_thread("server-changed", sid)
                self.send_response(req_id, result=self._get_config_payload(sid))

            elif method == "set_autostart":
                ok, msg = self.server_manager.set_server_autostart(
                    params.get("server_id"),
                    bool(params.get("autostart", False)),
                )
                if ok:
                    self.send_response(req_id, result=True)
                else:
                    self.send_response(req_id, error=msg or "Could not update autostart")

            elif method == "create_world_backup":
                ok, msg = self.server_manager.create_world_backup(params.get("server_id"), auto=False)
                if ok:
                    self.send_response(req_id, result={"message": msg})
                else:
                    self.send_response(req_id, error=msg)

            elif method == "list_backups":
                self.send_response(req_id, result=self._list_backups(params.get("server_id")))

            elif method == "restore_backup":
                sid = params.get("server_id")
                backup_path = Path(str(params.get("path", ""))).expanduser()
                ok, msg = self.server_manager.restore_world_backup(sid, backup_path)
                if ok:
                    self.send_response(req_id, result={"message": msg})
                else:
                    self.send_response(req_id, error=msg)

            elif method == "open_server_folder":
                info = self.server_manager.get_server(params.get("server_id"))
                if not info:
                    raise ValueError("Server not found")
                path = str(info.server_dir)
                if sys.platform == "win32":
                    import os
                    os.startfile(path)
                else:
                    subprocess.Popen(["xdg-open", path])
                self.send_response(req_id, result=True)
                
            elif method == "install_server":
                name = params.get("name")
                mc_version = params.get("mc_version")
                loader_version = params.get("loader_version", "")
                ram_mb = params.get("ram_mb", 4096)
                
                # We need to run the heavy installation in a thread
                def _install_task():
                    try:
                        self.send_event("install-progress", {"progress": 0.1, "message": "Creating server profile..."})
                        info = self.server_manager.add_server(name, mc_version, loader_version, ram_mb)
                        
                        java_ver = info.java_version
                        java_mgr = self.server_manager.java_manager
                        dl_mgr = self.server_manager.download_manager
                        
                        if not java_mgr.is_java_available(java_ver):
                            self.send_event("install-progress", {"progress": 0.2, "message": f"Downloading Java {java_ver}..."})
                            success, msg = java_mgr.download_jre_sync(java_ver)
                            if not success:
                                raise Exception(f"Failed to download JRE: {msg}")
                                
                        self.send_event("install-progress", {"progress": 0.4, "message": "Downloading Fabric installer..."})
                        installer_path = dl_mgr.download_installer()
                        if not installer_path:
                            raise Exception("Failed to download Fabric installer")
                            
                        self.send_event("install-progress", {"progress": 0.6, "message": "Downloading Minecraft server..."})
                        success, msg = dl_mgr.download_server_jar(mc_version, str(info.server_dir))
                        if not success:
                            raise Exception(f"Failed to download server.jar: {msg}")
                            
                        self.send_event("install-progress", {"progress": 0.8, "message": "Installing Fabric server..."})
                        java_path = java_mgr.get_java_path(java_ver) or java_mgr.get_java_for_mc(mc_version) or "java"
                        success, msg = dl_mgr.install_fabric_server(
                            java_path=java_path,
                            installer_jar=installer_path,
                            mc_version=mc_version,
                            server_dir=str(info.server_dir),
                            loader_version=loader_version if loader_version else None
                        )
                        if not success:
                            raise Exception(f"Fabric installation failed: {msg}")
                            
                        from hosty.shared.backend.config_manager import ConfigManager
                        config = ConfigManager(str(info.server_dir))
                        config.load()
                        config.set_eula(True)
                        config.save()
                        
                        self.send_event("install-progress", {"progress": 1.0, "message": "Done!"})
                        self.send_event("install-complete", {"server_id": info.id})
                    except Exception as e:
                        self.send_event("install-error", {"error": str(e)})
                        
                threading.Thread(target=_install_task, daemon=True).start()
                self.send_response(req_id, result=True)
                
            elif method == "rename_server":
                self.server_manager.rename_server(params.get("server_id"), params.get("new_name"))
                self.send_response(req_id, result=True)
                
            elif method == "delete_server":
                sid = params.get("server_id")
                self._detach_console_output(sid)
                self._detach_status_output(sid)
                self.server_manager.delete_server(sid, delete_files=params.get("delete_files", False))
                self.send_response(req_id, result=True)
                
            elif method == "start_server":
                sid = params.get("server_id")
                running_id = self.server_manager.get_running_server_id()
                if running_id and running_id != sid:
                    running = self.server_manager.get_server(running_id)
                    name = running.name if running else "another server"
                    self.send_response(req_id, error=f"{name} is already running. Stop it before starting another server.")
                    return

                if self.server_manager.is_mod_operation_active(sid):
                    self.send_response(req_id, error="Mods are currently installing or updating for this server.")
                    return

                proc = self.server_manager.get_process(sid)
                if proc:
                    # Attach console output streaming before starting
                    self._attach_console_output(sid, proc)
                    self._attach_status_output(sid, proc)
                    
                    proc.start()
                    self.send_response(req_id, result=True)
                else:
                    self.send_response(req_id, error="Process not found")
                    
            elif method == "stop_server":
                sid = params.get("server_id")
                proc = self.server_manager.get_process(sid)
                if proc:
                    proc.stop()
                    self.send_response(req_id, result=True)
                else:
                    self.send_response(req_id, error="Process not found")
            
            elif method == "send_command":
                sid = params.get("server_id")
                command = params.get("command", "")
                proc = self.server_manager.get_existing_process(sid)
                if proc and proc.is_running:
                    proc.send_command(command)
                    self.send_response(req_id, result=True)
                else:
                    self.send_response(req_id, error="Server is not running")
            
            elif method == "get_console_log":
                sid = params.get("server_id")
                proc = self.server_manager.get_existing_process(sid)
                if proc:
                    # Also attach output if not already
                    if sid not in self._output_handlers:
                        self._attach_console_output(sid, proc)
                    self.send_response(req_id, result={"log": proc.log_history})
                else:
                    self.send_response(req_id, result={"log": []})
            
            elif method == "update_ram":
                sid = params.get("server_id")
                ram_mb = params.get("ram_mb", 2048)
                self.server_manager.update_server_ram(sid, ram_mb)
                self.send_response(req_id, result=True)
            
            elif method == "get_runtime_state":
                sid = params.get("server_id")
                proc = self.server_manager.get_existing_process(sid)
                if proc and proc.is_running:
                    state = {
                        "is_running": True,
                        "status": proc.status,
                        "pid": proc.pid,
                        "cpu_percent": 0.0,
                        "ram_mb": 0.0,
                        "player_count": proc.player_count,
                        "max_players": proc.max_players,
                    }
                    
                    # Get real CPU/RAM via psutil if available
                    if HAS_PSUTIL and proc.pid:
                        try:
                            ps = self._psutil_processes.get(sid)
                            if ps is None or ps.pid != proc.pid:
                                ps = psutil.Process(proc.pid)
                                self._psutil_processes[sid] = ps
                            
                            cpu_count = psutil.cpu_count() or 1
                            raw_cpu = ps.cpu_percent(interval=None)
                            state["cpu_percent"] = round(raw_cpu / cpu_count, 1)
                            
                            mem = ps.memory_info()
                            state["ram_mb"] = round(mem.rss / (1024 * 1024), 1)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            self._psutil_processes.pop(sid, None)
                    
                    self.send_response(req_id, result=state)
                else:
                    # Cleanup psutil cache
                    self._psutil_processes.pop(sid, None)
                    self.send_response(req_id, result={
                        "is_running": False,
                        "status": "stopped",
                        "player_count": 0,
                        "max_players": 0,
                    })
                    
            elif method == "ping":
                self.send_response(req_id, result="pong")
                
            else:
                self.send_response(req_id, error=f"Unknown method: {method}")
                
        except Exception as e:
            logging.exception("Error handling request")
            self.send_response(req_id, error=str(e))

    def run(self):
        logging.info("WinIPCBackend started")
        # Send a ready event
        self.send_event("ready", None)
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                # Handle in a thread so we don't block the stdin reader
                threading.Thread(target=self.handle_request, args=(req,), daemon=True).start()
            except json.JSONDecodeError:
                logging.error(f"Invalid JSON: {line}")
            except Exception as e:
                logging.error(f"Error reading line: {e}")

if __name__ == "__main__":
    backend = WinIPCBackend()
    backend.run()
