"""
DownloadManager - Handle platform installers and Minecraft server downloads.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import requests

from hosty.shared.backend.platforms import Platform, normalize_platform
from hosty.shared.utils.constants import (
    CACHE_DIR,
    FABRIC_GAME_VERSIONS_URL,
    FABRIC_INSTALLER_VERSIONS_URL,
    FABRIC_LOADER_VERSIONS_URL,
    MOJANG_VERSION_MANIFEST,
    NEOFORGE_INSTALLER_URL,
    NEOFORGE_MAVEN_METADATA,
    PAPER_FILL_API,
    PAPER_USER_AGENT,
    PURPUR_API_BASE,
    SPIGOT_BUILDTOOLS_URL,
)
from hosty.shared.utils.subprocess_utils import hidden_subprocess_kwargs


class DownloadManager:
    """Manages downloads and installation for all supported server platforms."""

    def __init__(self):
        self._game_versions: list[dict] = []
        self._loader_versions: list[dict] = []
        self._installer_url: str | None = None
        self._installer_version: str | None = None
        self._mojang_manifest: dict | None = None
        self._neoforge_versions: list[str] | None = None

    # ----- Shared Mojang manifest -----

    def _fetch_mojang_manifest(self) -> dict | None:
        if self._mojang_manifest:
            return self._mojang_manifest
        try:
            resp = requests.get(MOJANG_VERSION_MANIFEST, timeout=15)
            resp.raise_for_status()
            self._mojang_manifest = resp.json()
            return self._mojang_manifest
        except Exception as e:
            print(f"Failed to fetch Mojang manifest: {e}")
            return None

    def fetch_mojang_game_versions(self, include_snapshots: bool = False) -> list[str]:
        manifest = self._fetch_mojang_manifest()
        if not manifest:
            return []
        versions: list[str] = []
        for entry in manifest.get("versions", []):
            version_type = str(entry.get("type", "")).lower()
            if include_snapshots or version_type == "release":
                vid = str(entry.get("id", "")).strip()
                if vid:
                    versions.append(vid)
        return versions

    def _get_version_json_url(self, mc_version: str) -> str | None:
        manifest = self._fetch_mojang_manifest()
        if not manifest:
            return None
        for entry in manifest.get("versions", []):
            if entry.get("id") == mc_version:
                return entry.get("url")
        return None

    # ----- Fabric Meta API -----

    def fetch_game_versions(self, include_snapshots: bool = False) -> list[str]:
        try:
            resp = requests.get(FABRIC_GAME_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            self._game_versions = resp.json()
            versions = []
            for v in self._game_versions:
                if include_snapshots or v.get("stable", False):
                    versions.append(v["version"])
            return versions
        except Exception as e:
            print(f"Failed to fetch Fabric game versions: {e}")
            return []

    def fetch_loader_versions(self) -> list[str]:
        try:
            resp = requests.get(FABRIC_LOADER_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            self._loader_versions = resp.json()
            return [v["version"] for v in self._loader_versions]
        except Exception as e:
            print(f"Failed to fetch loader versions: {e}")
            return []

    def fetch_installer_info(self) -> tuple[str | None, str | None]:
        try:
            resp = requests.get(FABRIC_INSTALLER_VERSIONS_URL, timeout=15)
            resp.raise_for_status()
            installers = resp.json()
            if installers:
                latest = installers[0]
                self._installer_url = latest.get("url")
                self._installer_version = latest.get("version")
                return self._installer_url, self._installer_version
        except Exception as e:
            print(f"Failed to fetch installer info: {e}")
        return None, None

    def download_installer(self, progress_callback: Callable[[float, str], None] | None = None) -> str | None:
        url, version = self.fetch_installer_info()
        if not url:
            return None

        cached_jar = CACHE_DIR / f"fabric-installer-{version}.jar"
        if cached_jar.exists():
            if progress_callback:
                progress_callback(1.0, _("Using cached installer"))
            return str(cached_jar)

        try:
            if progress_callback:
                progress_callback(0.0, _("Downloading Fabric installer..."))
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(cached_jar, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        progress_callback(downloaded / total, _("Downloading installer... {:.0f} KB").format(downloaded / 1024))
            if progress_callback:
                progress_callback(1.0, _("Installer downloaded"))
            return str(cached_jar)
        except Exception as e:
            print(f"Failed to download installer: {e}")
            cached_jar.unlink(missing_ok=True)
            return None

    # ----- Platform version APIs -----

    def fetch_platform_game_versions(self, platform: str | Platform, include_snapshots: bool = False) -> list[str]:
        plat = normalize_platform(platform)
        if plat == Platform.FABRIC:
            return self.fetch_game_versions(include_snapshots=include_snapshots)
        if plat == Platform.PAPER:
            return self._fetch_paper_game_versions()
        if plat == Platform.PURPUR:
            return self._fetch_purpur_game_versions()
        if plat == Platform.NEOFORGE:
            return self._fetch_neoforge_game_versions()
        return self.fetch_mojang_game_versions(include_snapshots=include_snapshots)

    def fetch_platform_build_versions(self, platform: str | Platform, mc_version: str) -> list[str]:
        plat = normalize_platform(platform)
        mc_version = str(mc_version or "").strip()
        if not mc_version:
            return []

        if plat == Platform.FABRIC:
            return self.fetch_loader_versions()
        if plat == Platform.NEOFORGE:
            return self._fetch_neoforge_builds_for_mc(mc_version)
        if plat == Platform.PAPER:
            build = self._fetch_paper_stable_build(mc_version)
            return [str(build)] if build else []
        if plat == Platform.PURPUR:
            build = self._fetch_purpur_latest_build(mc_version)
            return [str(build)] if build else []
        if plat == Platform.SPIGOT:
            return [mc_version]
        return []

    def _fetch_paper_game_versions(self) -> list[str]:
        try:
            resp = requests.get(PAPER_FILL_API, headers={"User-Agent": PAPER_USER_AGENT}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            versions_obj = data.get("versions") or {}
            versions: list[str] = []
            for group in versions_obj.values():
                if isinstance(group, list):
                    versions.extend(str(v) for v in group if str(v).strip())
            return versions
        except Exception as e:
            print(f"Failed to fetch Paper versions: {e}")
            return []

    def _fetch_paper_stable_build(self, mc_version: str) -> str | None:
        try:
            url = f"{PAPER_FILL_API}/versions/{mc_version}/builds"
            resp = requests.get(url, headers={"User-Agent": PAPER_USER_AGENT}, timeout=20)
            resp.raise_for_status()
            builds = resp.json()
            if not isinstance(builds, list):
                return None
            stable = [b for b in builds if str(b.get("channel", "")).upper() == "STABLE"]
            chosen = stable[-1] if stable else (builds[-1] if builds else None)
            if not chosen:
                return None
            return str(chosen.get("id", ""))
        except Exception as e:
            print(f"Failed to fetch Paper build for {mc_version}: {e}")
            return None

    def _fetch_purpur_game_versions(self) -> list[str]:
        try:
            resp = requests.get(f"{PURPUR_API_BASE}/", timeout=20)
            resp.raise_for_status()
            data = resp.json()
            versions = data.get("versions") or []
            return [str(v) for v in versions if str(v).strip()]
        except Exception as e:
            print(f"Failed to fetch Purpur versions: {e}")
            return []

    def _fetch_purpur_latest_build(self, mc_version: str) -> str | None:
        try:
            resp = requests.get(f"{PURPUR_API_BASE}/{mc_version}", timeout=20)
            resp.raise_for_status()
            data = resp.json()
            builds = data.get("builds") or {}
            if isinstance(builds, dict):
                keys = [int(k) for k in builds.keys() if str(k).isdigit()]
                return str(max(keys)) if keys else None
            if isinstance(builds, list) and builds:
                return str(builds[-1])
            return None
        except Exception as e:
            print(f"Failed to fetch Purpur build for {mc_version}: {e}")
            return None

    def _fetch_neoforge_versions(self) -> list[str]:
        if self._neoforge_versions is not None:
            return self._neoforge_versions
        try:
            resp = requests.get(NEOFORGE_MAVEN_METADATA, timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            versions = [elem.text.strip() for elem in root.findall(".//version") if elem.text]
            self._neoforge_versions = versions
            return versions
        except Exception as e:
            print(f"Failed to fetch NeoForge versions: {e}")
            self._neoforge_versions = []
            return []

    @staticmethod
    def _mc_to_neoforge_prefix(mc_version: str) -> str:
        mc = str(mc_version or "").strip()
        if mc.startswith("1."):
            return mc[2:]
        return mc

    def _fetch_neoforge_game_versions(self) -> list[str]:
        versions = self._fetch_neoforge_versions()
        prefixes: set[str] = set()
        for ver in versions:
            match = re.match(r"^(\d+(?:\.\d+){0,2})", ver)
            if not match:
                continue
            prefix = match.group(1)
            if prefix.startswith("20.") or prefix.startswith("21.") or prefix.startswith("22."):
                prefixes.add(f"1.{prefix}")
            else:
                prefixes.add(prefix)
        ordered = sorted(prefixes, key=lambda v: [int(x) for x in re.findall(r"\d+", v)], reverse=True)
        return ordered

    def _fetch_neoforge_builds_for_mc(self, mc_version: str) -> list[str]:
        prefix = self._mc_to_neoforge_prefix(mc_version)
        versions = self._fetch_neoforge_versions()
        matched = [v for v in versions if v.startswith(prefix + ".") or v == prefix]
        return list(reversed(matched))

    def fetch_all_versions_async(
        self,
        callback: Callable[[list[str], list[str]], None],
        platform: str | Platform = Platform.FABRIC,
        mc_version: str | None = None,
    ):
        """Fetch game and build/loader versions in a background thread."""

        def _fetch():
            plat = normalize_platform(platform)
            games = self.fetch_platform_game_versions(plat)
            if mc_version:
                loaders = self.fetch_platform_build_versions(plat, mc_version)
            elif games:
                loaders = self.fetch_platform_build_versions(plat, games[0])
            else:
                loaders = []
            callback(games, loaders)

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()
        return thread

    # ----- Vanilla server.jar -----

    def download_server_jar(
        self, mc_version: str, server_dir: str, progress_callback: Callable[[float, str], None] | None = None
    ) -> tuple[bool, str]:
        dest = Path(server_dir) / "server.jar"
        if dest.exists() and dest.stat().st_size > 1000:
            if progress_callback:
                progress_callback(1.0, _("server.jar already present"))
            return True, _("server.jar already present")

        try:
            if progress_callback:
                progress_callback(0.05, _("Fetching MC {} metadata...").format(mc_version))
            version_url = self._get_version_json_url(mc_version)
            if not version_url:
                return False, _("Minecraft version {} not found in Mojang manifest").format(mc_version)

            if progress_callback:
                progress_callback(0.1, _("Reading version details..."))
            resp = requests.get(version_url, timeout=15)
            resp.raise_for_status()
            version_data = resp.json()

            downloads = version_data.get("downloads", {})
            server_info = downloads.get("server")
            if not server_info:
                return False, _("No server download available for MC {}").format(mc_version)

            jar_url = server_info.get("url")
            jar_size = server_info.get("size", 0)
            if not jar_url:
                return False, _("server.jar URL not found in version metadata")

            if progress_callback:
                progress_callback(0.15, _("Downloading server.jar..."))
            resp = requests.get(jar_url, stream=True, timeout=120)
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", jar_size))
            downloaded = 0
            Path(server_dir).mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and progress_callback:
                        frac = 0.15 + (downloaded / total) * 0.85
                        progress_callback(
                            frac,
                            _("Downloading server.jar... {:.1f}/{:.1f} MB").format(
                                downloaded / (1024 * 1024), total / (1024 * 1024)
                            ),
                        )

            if progress_callback:
                progress_callback(1.0, _("server.jar downloaded"))
            return True, _("server.jar downloaded successfully")
        except Exception as e:
            dest.unlink(missing_ok=True)
            return False, _("Failed to download server.jar: {}").format(e)

    # ----- Platform installation -----

    def install_fabric_server(
        self,
        java_path: str,
        installer_jar: str,
        mc_version: str,
        server_dir: str,
        loader_version: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        Path(server_dir).mkdir(parents=True, exist_ok=True)
        cmd = [
            java_path,
            "-jar",
            installer_jar,
            "server",
            "-mcversion",
            mc_version,
            "-dir",
            server_dir,
        ]
        if loader_version:
            cmd.extend(["-loader", loader_version])

        if progress_callback:
            progress_callback(0.5, _("Installing Fabric server for MC {}...").format(mc_version))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=server_dir,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode == 0:
                launch_jar = Path(server_dir) / "fabric-server-launch.jar"
                if launch_jar.exists():
                    if progress_callback:
                        progress_callback(1.0, _("Fabric server installed successfully"))
                    return True, _("Installation successful")
                return False, _("Installation completed but fabric-server-launch.jar not found")
            error_msg = result.stderr or result.stdout or _("Unknown error")
            return False, _("Installation failed: {}").format(error_msg)
        except subprocess.TimeoutExpired:
            return False, _("Installation timed out (5 minutes)")
        except Exception as e:
            return False, _("Installation error: {}").format(e)

    def install_neoforge_server(
        self,
        java_path: str,
        neoforge_version: str,
        server_dir: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        Path(server_dir).mkdir(parents=True, exist_ok=True)
        installer_url = NEOFORGE_INSTALLER_URL.format(version=neoforge_version)
        cached = CACHE_DIR / f"neoforge-installer-{neoforge_version}.jar"

        try:
            if not cached.exists():
                if progress_callback:
                    progress_callback(0.1, _("Downloading NeoForge installer..."))
                resp = requests.get(installer_url, stream=True, timeout=120)
                resp.raise_for_status()
                with open(cached, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)

            if progress_callback:
                progress_callback(0.5, _("Installing NeoForge {}...").format(neoforge_version))

            result = subprocess.run(
                [java_path, "-jar", str(cached), "--installServer"],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=server_dir,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or _("Unknown error")
                return False, _("NeoForge installation failed: {}").format(error_msg)

            run_sh = Path(server_dir) / "run.sh"
            unix_args = next(Path(server_dir).glob("*unix_args.txt"), None)
            if run_sh.exists() or unix_args:
                if progress_callback:
                    progress_callback(1.0, _("NeoForge server installed successfully"))
                return True, _("Installation successful")
            return False, _("NeoForge installation completed but launch files were not found")
        except subprocess.TimeoutExpired:
            return False, _("NeoForge installation timed out")
        except Exception as e:
            return False, _("NeoForge installation error: {}").format(e)

    def install_paper_server(
        self,
        mc_version: str,
        server_dir: str,
        build: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        build_id = build or self._fetch_paper_stable_build(mc_version)
        if not build_id:
            return False, _("No Paper build found for Minecraft {}").format(mc_version)

        try:
            url = f"{PAPER_FILL_API}/versions/{mc_version}/builds/{build_id}"
            if progress_callback:
                progress_callback(0.1, _("Fetching Paper build {}...").format(build_id))
            resp = requests.get(url, headers={"User-Agent": PAPER_USER_AGENT}, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            downloads = data.get("downloads") or {}
            server_dl = downloads.get("server:default") or {}
            dl_url = str(server_dl.get("url", "")).strip()
            if not dl_url:
                return False, _("Paper build has no download URL")

            dest = Path(server_dir) / "server.jar"
            Path(server_dir).mkdir(parents=True, exist_ok=True)
            if progress_callback:
                progress_callback(0.3, _("Downloading Paper server.jar..."))
            resp = requests.get(dl_url, stream=True, timeout=180, headers={"User-Agent": PAPER_USER_AGENT})
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            if progress_callback:
                progress_callback(1.0, _("Paper server installed successfully"))
            return True, _("Installation successful")
        except Exception as e:
            return False, _("Paper installation error: {}").format(e)

    def install_purpur_server(
        self,
        mc_version: str,
        server_dir: str,
        build: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        build_id = build or self._fetch_purpur_latest_build(mc_version)
        if not build_id:
            return False, _("No Purpur build found for Minecraft {}").format(mc_version)

        try:
            dl_url = f"{PURPUR_API_BASE}/{mc_version}/{build_id}/download"
            dest = Path(server_dir) / "server.jar"
            Path(server_dir).mkdir(parents=True, exist_ok=True)
            if progress_callback:
                progress_callback(0.3, _("Downloading Purpur build {}...").format(build_id))
            resp = requests.get(dl_url, stream=True, timeout=180)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            if progress_callback:
                progress_callback(1.0, _("Purpur server installed successfully"))
            return True, _("Installation successful")
        except Exception as e:
            return False, _("Purpur installation error: {}").format(e)

    def _download_buildtools(self, progress_callback: Callable[[float, str], None] | None = None) -> Path | None:
        cached = CACHE_DIR / "BuildTools.jar"
        if cached.exists():
            return cached
        try:
            if progress_callback:
                progress_callback(0.05, _("Downloading Spigot BuildTools..."))
            resp = requests.get(SPIGOT_BUILDTOOLS_URL, stream=True, timeout=120)
            resp.raise_for_status()
            with open(cached, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return cached
        except Exception as e:
            print(f"Failed to download BuildTools: {e}")
            cached.unlink(missing_ok=True)
            return None

    def install_spigot_server(
        self,
        java_path: str,
        mc_version: str,
        server_dir: str,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        buildtools = self._download_buildtools(progress_callback)
        if not buildtools:
            return False, _("Failed to download Spigot BuildTools")

        workspace = CACHE_DIR / "buildtools" / mc_version
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            if progress_callback:
                progress_callback(0.2, _("Building Spigot for MC {} (this may take several minutes)...").format(mc_version))

            result = subprocess.run(
                [java_path, "-jar", str(buildtools), "--rev", mc_version],
                capture_output=True,
                text=True,
                timeout=1800,
                cwd=str(workspace),
                **hidden_subprocess_kwargs(),
            )
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or _("Unknown error")
                return False, _("Spigot BuildTools failed: {}").format(error_msg)

            jars = sorted(workspace.glob("spigot-*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not jars:
                jars = sorted(workspace.glob("craftbukkit-*.jar"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not jars:
                return False, _("BuildTools completed but no Spigot jar was produced")

            Path(server_dir).mkdir(parents=True, exist_ok=True)
            dest = Path(server_dir) / "server.jar"
            shutil.copy2(jars[0], dest)

            if progress_callback:
                progress_callback(1.0, _("Spigot server built successfully"))
            return True, _("Installation successful")
        except subprocess.TimeoutExpired:
            return False, _("Spigot BuildTools timed out (30 minutes)")
        except Exception as e:
            return False, _("Spigot installation error: {}").format(e)

    def install_platform_server(
        self,
        platform: str | Platform,
        java_path: str,
        mc_version: str,
        server_dir: str,
        loader_version: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> tuple[bool, str]:
        plat = normalize_platform(platform)
        if plat == Platform.FABRIC:
            installer_path = self.download_installer(progress_callback=progress_callback)
            if not installer_path:
                return False, _("Failed to download Fabric installer")
            return self.install_fabric_server(
                java_path=java_path,
                installer_jar=installer_path,
                mc_version=mc_version,
                server_dir=server_dir,
                loader_version=loader_version,
                progress_callback=progress_callback,
            )
        if plat == Platform.NEOFORGE:
            neoforge_version = loader_version or (self._fetch_neoforge_builds_for_mc(mc_version)[-1:] or [""])[0]
            if not neoforge_version:
                return False, _("No NeoForge build found for Minecraft {}").format(mc_version)
            return self.install_neoforge_server(
                java_path=java_path,
                neoforge_version=neoforge_version,
                server_dir=server_dir,
                progress_callback=progress_callback,
            )
        if plat == Platform.PAPER:
            return self.install_paper_server(
                mc_version=mc_version,
                server_dir=server_dir,
                build=loader_version,
                progress_callback=progress_callback,
            )
        if plat == Platform.PURPUR:
            return self.install_purpur_server(
                mc_version=mc_version,
                server_dir=server_dir,
                build=loader_version,
                progress_callback=progress_callback,
            )
        if plat == Platform.SPIGOT:
            return self.install_spigot_server(
                java_path=java_path,
                mc_version=mc_version,
                server_dir=server_dir,
                progress_callback=progress_callback,
            )
        return False, _("Unsupported platform")

    def platform_needs_vanilla_jar(self, platform: str | Platform) -> bool:
        return normalize_platform(platform) == Platform.FABRIC

    def cleanup_platform_artifacts(self, server_dir: str, platform: str | Platform) -> None:
        root = Path(server_dir)
        plat = normalize_platform(platform)
        if plat == Platform.FABRIC:
            for filename in ("server.jar", "fabric-server-launch.jar"):
                try:
                    (root / filename).unlink(missing_ok=True)
                except Exception:
                    pass
        elif plat == Platform.NEOFORGE:
            for path in list(root.glob("libraries")) + list(root.glob("*unix_args.txt")) + [root / "run.sh", root / "run.bat"]:
                try:
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            try:
                (root / "server.jar").unlink(missing_ok=True)
            except Exception:
                pass
