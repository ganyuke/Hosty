"""
FilesView — folders, worlds, backups, and Modrinth integration (per selected server).
"""
from __future__ import annotations

import json
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import uuid

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio, GLib, Pango, Gdk, GdkPixbuf

from hosty.shared.backend.server_manager import ServerManager, ServerInfo



from .utils import *
from .mixins import ModsMixin, BackupsMixin, PlayersMixin, ModrinthMixin, WorldsMixin

class FilesView(Gtk.Box, BackupsMixin, ModsMixin, PlayersMixin, ModrinthMixin, WorldsMixin):
    """Browse files for the currently selected server only."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_hexpand(True)
        self._server_info: Optional[ServerInfo] = None
        self._server_manager: Optional[ServerManager] = None
        self._root_page: Optional[Adw.NavigationPage] = None
        self._push_fullscreen_page_cb = None
        self._modrinth_page = None
        self._modrinth_header = None

        self._worlds_group: Optional[Adw.PreferencesGroup] = None
        self._mods_group: Optional[Adw.PreferencesGroup] = None
        self._check_updates_row: Optional[Adw.ActionRow] = None
        self._mods_update_busy = False
        self._modpack_version_enrich_busy = False
        self._active_mod_operation_tokens: dict[str, str] = {}
        self._mod_operation_lock = threading.Lock()
        self._players_group: Optional[Adw.PreferencesGroup] = None
        self._world_rows: list[Gtk.Widget] = []
        self._mod_rows: list[Gtk.Widget] = []
        self._datapack_rows: list[Gtk.Widget] = []
        self._mods_expander: Optional[Adw.ExpanderRow] = None
        self._datapacks_expander: Optional[Adw.ExpanderRow] = None
        self._worlds_snapshot: tuple[tuple[str, tuple[str, ...]], ...] = tuple()

        self._backups_group: Optional[Adw.PreferencesGroup] = None
        self._backups_row: Optional[Adw.ActionRow] = None
        self._backup_rows: list[Gtk.Widget] = []
        self._backup_busy = False
        self._create_backup_row: Optional[Adw.ActionRow] = None
        self._backup_spinner: Optional[Gtk.Spinner] = None

        self._players_name_row: Optional[Adw.EntryRow] = None
        self._whitelist_group: Optional[Adw.PreferencesGroup] = None
        self._banned_group: Optional[Adw.PreferencesGroup] = None
        self._whitelist_rows: list[Gtk.Widget] = []
        self._banned_rows: list[Gtk.Widget] = []

        self._nav = Adw.NavigationView()
        self._nav.set_vexpand(True)
        self._nav.set_hexpand(True)
        self.append(self._nav)

        root_content = self._build_root_content()
        self._root_page = Adw.NavigationPage(title="Files", child=root_content)
        try:
            self._root_page.set_tag("hosty-files-root")
        except Exception:
            pass
        self._nav.push(self._root_page)

    def _build_root_content(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        self._worlds_group = Adw.PreferencesGroup(title="Worlds")
        open_server_row = Adw.ActionRow(title="Open server folder")
        open_server_row.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        open_server_row.set_activatable(True)
        open_server_row.connect("activated", self._on_open_server_folder)
        self._worlds_group.add(open_server_row)

        backups_row = Adw.ActionRow(
            title="Backups",
            subtitle="0 backups",
        )
        backups_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        backups_row.set_activatable(True)
        backups_row.connect("activated", self._push_backups_page)
        self._backups_row = backups_row
        self._worlds_group.add(backups_row)

        page.add(self._worlds_group)

        self._mods_group = Adw.PreferencesGroup(title="Mods")
        open_mods_row = Adw.ActionRow(title="Open mods folder")
        open_mods_row.add_prefix(Gtk.Image.new_from_icon_name("application-x-addon-symbolic"))
        open_mods_row.set_activatable(True)
        open_mods_row.connect("activated", self._on_open_mods_folder)
        self._mods_group.add(open_mods_row)

        modrinth_row = Adw.ActionRow(
            title="Modrinth",
        )
        modrinth_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        modrinth_row.set_activatable(True)
        modrinth_row.connect("activated", self._push_modrinth_page)
        self._mods_group.add(modrinth_row)

        check_updates_row = Adw.ActionRow(
            title="Check for updates",
        )
        check_updates_row.add_prefix(Gtk.Image.new_from_icon_name("software-update-available-symbolic"))
        check_updates_row.set_activatable(True)
        check_updates_row.connect("activated", self._on_check_mod_updates)
        self._mods_group.add(check_updates_row)
        self._check_updates_row = check_updates_row

        # "Installed Mods" collapsible section (modpacks + standalone mods)
        mods_expander = Adw.ExpanderRow(title="Installed Mods")
        mods_expander.set_expanded(False)
        self._mods_expander = mods_expander
        self._mods_group.add(mods_expander)

        # "Installed Datapacks" collapsible section
        datapacks_expander = Adw.ExpanderRow(title="Installed Datapacks")
        datapacks_expander.set_expanded(False)
        self._datapacks_expander = datapacks_expander
        self._mods_group.add(datapacks_expander)

        page.add(self._mods_group)

        self._players_group = Adw.PreferencesGroup(title="Players")

        scroll.set_child(page)
        return scroll

    def set_server(self, server_info: ServerInfo, server_manager: ServerManager):
        self._pop_to_root()
        self._server_info = server_info
        self._server_manager = server_manager
        self._worlds_snapshot = tuple()
        self._refresh_backups_row_subtitle()
        self._rebuild_lists()

    def refresh_worlds_if_changed(self, force: bool = False) -> None:
        """Refresh the Files lists when world or dimension folders changed on disk."""
        if not self._server_info:
            return

        snapshot = self._build_worlds_snapshot()
        if force or snapshot != self._worlds_snapshot:
            self._rebuild_lists()

    def _pop_to_root(self) -> None:
        if not self._root_page:
            return
        try:
            self._nav.pop_to_tag("hosty-files-root")
        except Exception:
            for _ in range(24):
                if self._nav.get_visible_page() == self._root_page:
                    break
                self._nav.pop()


    def _server_dir(self) -> Optional[Path]:
        if not self._server_info:
            return None
        return Path(self._server_info.server_dir)

    def _process(self):
        if not self._server_info or not self._server_manager:
            return None
        return self._server_manager.get_process(self._server_info.id)

    def _is_running(self) -> bool:
        p = self._process()
        return p is not None and p.is_running

    def _clear_group_rows(self, group: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for row in list(rows):
            try:
                if _is_descendant_of(row, group):
                    group.remove(row)
            except Exception:
                pass
        rows.clear()

    def _rebuild_lists(self) -> None:
        if not self._worlds_group or not self._mods_group:
            return

        self._refresh_backups_row_subtitle()

        self._ensure_modpack_version_numbers_async()

        self._clear_group_rows(self._worlds_group, self._world_rows)

        # Clear installed mods expander
        if self._mods_expander:
            for row in list(self._mod_rows):
                try:
                    self._mods_expander.remove(row)
                except Exception:
                    pass
        self._mod_rows.clear()

        # Clear installed datapacks expander
        if self._datapacks_expander:
            for row in list(self._datapack_rows):
                try:
                    self._datapacks_expander.remove(row)
                except Exception:
                    pass
        self._datapack_rows.clear()

        root = self._server_dir()
        if not root or not root.is_dir():
            self._world_rows.append(self._add_info_row(self._worlds_group, "No server folder"))
            if self._mods_expander:
                info = self._add_info_row_to_expander(self._mods_expander, "No server folder")
                self._mod_rows.append(info)
            if self._datapacks_expander:
                info = self._add_info_row_to_expander(self._datapacks_expander, "No server folder")
                self._datapack_rows.append(info)
            self._worlds_snapshot = tuple()
            return

        worlds = _world_dirs(root)
        if not worlds:
            self._world_rows.append(self._add_info_row(self._worlds_group, "No worlds yet"))
        else:
            for w in worlds:
                row = self._make_world_row(w)
                self._worlds_group.add(row)
                self._world_rows.append(row)

        # ---- Installed Mods expander ----
        mods_dir = root / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        jars = sorted(mods_dir.glob("*.jar"), key=lambda p: p.name.lower())
        entries = self._modpack_entries()
        managed_set = set(self._modpack_managed_mod_map().keys())

        if self._mods_expander:
            for project_id, entry in sorted(
                entries.items(),
                key=lambda item: (str(item[1].get("title", "")).strip() or item[0]).lower(),
            ):
                row = self._make_modpack_row(project_id, entry)
                self._mods_expander.add_row(row)
                self._mod_rows.append(row)

            standalone_jars = [jar for jar in jars if jar.name.lower() not in managed_set]

            for jar in standalone_jars:
                row = self._make_mod_row(jar)
                self._mods_expander.add_row(row)
                self._mod_rows.append(row)

            if not self._mod_rows:
                info = self._add_info_row_to_expander(self._mods_expander, "No mods installed")
                self._mod_rows.append(info)

            # Update expander subtitle with count
            total_mods = len(entries) + len([j for j in jars if j.name.lower() not in managed_set])
            self._mods_expander.set_subtitle(
                f"{total_mods} item{'s' if total_mods != 1 else ''}" if total_mods else "None installed"
            )

        # ---- Installed Datapacks expander ----
        if self._datapacks_expander:
            dp_state = self._read_datapack_state().get("datapacks", {})
            dp_dir = self._datapacks_dir()

            for project_id, meta in sorted(
                dp_state.items(),
                key=lambda item: (str(item[1].get("title", "")).strip() or item[0]).lower(),
            ):
                row = self._make_datapack_row(project_id, meta)
                self._datapacks_expander.add_row(row)
                self._datapack_rows.append(row)

            # Also scan datapacks dir for untracked zip files
            tracked_filenames = {
                str(m.get("filename", "")).strip().lower()
                for m in dp_state.values()
            }
            if dp_dir and dp_dir.is_dir():
                for dp_file in sorted(dp_dir.glob("*.zip"), key=lambda p: p.name.lower()):
                    if dp_file.name.lower() in tracked_filenames:
                        continue
                    row = Adw.ActionRow(title=dp_file.name)
                    row.set_subtitle(_format_size(dp_file.stat().st_size))
                    row.set_activatable(False)
                    del_btn = self._icon_button(
                        "user-trash-symbolic",
                        "Delete datapack file",
                        lambda *_p, p=dp_file: self._soft_delete_with_undo(
                            p, f"datapack \"{p.name}\"",
                            on_refresh=self._rebuild_lists,
                        ),
                        destructive=True,
                    )
                    row.add_suffix(del_btn)
                    self._datapacks_expander.add_row(row)
                    self._datapack_rows.append(row)

            if not self._datapack_rows:
                info = self._add_info_row_to_expander(self._datapacks_expander, "No datapacks installed")
                self._datapack_rows.append(info)

            # Count real items (tracked + untracked files)
            untracked_count = 0
            if dp_dir and dp_dir.is_dir():
                for dp_file in dp_dir.glob("*.zip"):
                    if dp_file.name.lower() not in tracked_filenames:
                        untracked_count += 1
            real_count = len(dp_state) + untracked_count
            self._datapacks_expander.set_subtitle(
                f"{real_count} item{'s' if real_count != 1 else ''}" if real_count else "None installed"
            )

        self._worlds_snapshot = self._build_worlds_snapshot()

    def _refresh_backups_row_subtitle(self) -> None:
        if not self._backups_row:
            return

        bdir = self._backups_dir()
        if not bdir:
            self._backups_row.set_subtitle("No server selected")
            return

        count = sum(1 for _ in bdir.glob("*.zip"))
        suffix = "backup" if count == 1 else "backups"
        self._backups_row.set_subtitle(f"{count} {suffix}")

    def _build_worlds_snapshot(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        root = self._server_dir()
        if not root or not root.is_dir():
            return tuple()

        snapshot: list[tuple[str, tuple[str, ...]]] = []
        for world in _world_dirs(root):
            world_root = world.resolve()
            dim_keys: list[str] = []
            for _label, dim_path in _world_dimension_dirs(world):
                try:
                    rel = dim_path.resolve().relative_to(world_root)
                    key = str(rel) if str(rel) else "."
                except Exception:
                    key = str(dim_path.resolve())
                dim_keys.append(key.lower())

            snapshot.append((world.name.lower(), tuple(sorted(dim_keys))))

        return tuple(snapshot)

    def _add_info_row(self, group: Adw.PreferencesGroup, title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        group.add(row)
        return row

    def _add_info_row_to_expander(self, expander: Adw.ExpanderRow, title: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title)
        row.set_activatable(False)
        expander.add_row(row)
        return row

    def _icon_button(
        self,
        icon_name: str,
        tooltip: str,
        handler,
        destructive: bool = False,
    ) -> Gtk.Button:
        b = Gtk.Button(icon_name=icon_name, valign=Gtk.Align.CENTER)
        b.add_css_class("flat")
        if destructive:
            b.add_css_class("destructive-action")
        b.set_tooltip_text(tooltip)
        b.connect("clicked", handler)
        return b

    def _on_open_server_folder(self, *_):
        root = self._server_dir()
        if root:
            self._open_target(root)

    def _on_open_mods_folder(self, *_):
        root = self._server_dir()
        if root:
            d = root / "mods"
            d.mkdir(parents=True, exist_ok=True)
            self._open_target(d)

    def _open_target(self, path: Path):
        if not _open_path(path):
            self._alert("Could not open path", str(path))

    def _trash_dir(self) -> Optional[Path]:
        root = self._server_dir()
        if not root:
            return None
        d = root / ".hosty-trash"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _soft_delete_with_undo(
        self,
        target: Path,
        label: str,
        on_refresh,
        on_finalize=None,
        toast_seconds: int = 6,
    ):
        trash_dir = self._trash_dir()
        if not trash_dir:
            self._alert("No server selected", "Select a server first.")
            return

        trash_name = f"{target.name}.{uuid.uuid4().hex}.trash"
        trashed = trash_dir / trash_name

        try:
            shutil.move(str(target), str(trashed))
        except OSError as e:
            self._alert("Could not delete", str(e))
            return

        state = {"undone": False}

        def undo_delete():
            if state["undone"]:
                return
            state["undone"] = True
            try:
                restore_target = target
                restore_target.parent.mkdir(parents=True, exist_ok=True)
                if restore_target.exists():
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    restore_target = restore_target.with_name(f"{restore_target.stem}-restored-{stamp}{restore_target.suffix}")
                shutil.move(str(trashed), str(restore_target))
                on_refresh()
                self._toast(f"Restored {label}")
            except OSError as e:
                self._alert("Could not undo", str(e))

        def finalize_delete():
            if state["undone"]:
                return False
            try:
                if trashed.is_dir():
                    shutil.rmtree(trashed, ignore_errors=True)
                else:
                    trashed.unlink(missing_ok=True)
            except Exception:
                pass

            if on_finalize:
                try:
                    on_finalize()
                except Exception:
                    pass

            return False

        on_refresh()
        self._toast(f"Deleted {label}", button_label="Undo", on_button=undo_delete, timeout=toast_seconds)
        GLib.timeout_add_seconds(toast_seconds, finalize_delete)

    def _alert(self, title: str, body: str):
        d = Adw.AlertDialog()
        d.set_heading(title)
        d.set_body(body)
        d.add_response("ok", "OK")
        d.present(self.get_root())

    def _toast(
        self,
        message: str,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 3,
    ):
        root = self.get_root()
        if root and hasattr(root, "show_toast"):
            root.show_toast(
                message,
                button_label=button_label,
                on_button=on_button,
                timeout=timeout,
            )
