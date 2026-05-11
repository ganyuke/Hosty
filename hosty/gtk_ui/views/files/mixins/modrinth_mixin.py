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



from ..utils import *

class ModrinthMixin:
    def _push_modrinth_page(self, *_args) -> None:
        page = Adw.NavigationPage(title="Modrinth", child=self._build_modrinth_page())
        self._modrinth_page = page
        if self._push_fullscreen_page_cb:
            # Push onto the outer nav to overlay the tab bar
            if self._modrinth_header:
                self._modrinth_header.set_show_end_title_buttons(True)
            self._push_fullscreen_page_cb(page)
        else:
            self._nav.push(page)

    def _build_modrinth_page(self) -> Gtk.Widget:
        from hosty.shared.backend import modrinth_client

        tv = Adw.ToolbarView()
        tv.set_hexpand(True)
        header = Adw.HeaderBar()
        self._modrinth_header = header
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(False)

        search_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_header.set_hexpand(True)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        entry.set_placeholder_text("Search Modrinth…")
        btn = Gtk.Button(label="Search")
        btn.add_css_class("suggested-action")
        search_header.append(entry)
        search_header.append(btn)
        header.set_title_widget(search_header)
        tv.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_hexpand(True)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        outer.set_margin_top(12)
        outer.set_margin_bottom(18)

        mc_ver = self._server_info.mc_version if self._server_info else ""

        controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        project_type_items = [
            ("Mods", "mod"),
            ("Modpacks", "modpack"),
            ("Datapacks", "datapack"),
        ]
        type_dd = Gtk.DropDown.new_from_strings([x[0] for x in project_type_items])
        type_dd.set_valign(Gtk.Align.CENTER)

        category_items = [
            ("Any category", ""),
            ("Optimization", "optimization"),
            ("Utility", "utility"),
            ("Technology", "technology"),
            ("Adventure", "adventure"),
            ("Decoration", "decoration"),
            ("Magic", "magic"),
            ("Storage", "storage"),
            ("Worldgen", "worldgen"),
            ("Library", "library"),
        ]
        cat_dd = Gtk.DropDown.new_from_strings([x[0] for x in category_items])
        cat_dd.set_valign(Gtk.Align.CENTER)

        sort_items = [
            ("Relevance", "relevance"),
            ("Downloads", "downloads"),
            ("Follows", "follows"),
            ("Newest", "newest"),
            ("Recently updated", "updated"),
        ]
        sort_dd = Gtk.DropDown.new_from_strings([x[0] for x in sort_items])
        sort_dd.set_valign(Gtk.Align.CENTER)
        sort_dd.set_selected(1)

        results = Gtk.ListBox()
        results.set_selection_mode(Gtk.SelectionMode.NONE)
        results.add_css_class("mod-results-list")
        results.set_vexpand(True)

        prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        prev_btn.add_css_class("flat")
        next_btn = Gtk.Button(icon_name="go-next-symbolic")
        next_btn.add_css_class("flat")
        page_label = Gtk.Label(label="Page 1/1", xalign=0.0)
        page_label.add_css_class("dim-label")
        results_label = Gtk.Label(label="", xalign=1.0)
        results_label.add_css_class("dim-label")
        results_label.set_ellipsize(Pango.EllipsizeMode.END)
        results_label.set_max_width_chars(16)
        controls_row.append(prev_btn)
        controls_row.append(next_btn)
        controls_row.append(page_label)
        controls_row.append(results_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls_row.append(spacer)

        controls_row.append(type_dd)
        controls_row.append(cat_dd)
        controls_row.append(sort_dd)
        outer.append(controls_row)

        page_size = 10
        state = {"offset": 0, "total": 0, "busy": False}

        def selected_category() -> str:
            idx = int(cat_dd.get_selected())
            if idx < 0 or idx >= len(category_items):
                return ""
            return category_items[idx][1]

        def selected_project_type() -> str:
            idx = int(type_dd.get_selected())
            if idx < 0 or idx >= len(project_type_items):
                return "mod"
            return project_type_items[idx][1]

        def selected_sort() -> str:
            idx = int(sort_dd.get_selected())
            if idx < 0 or idx >= len(sort_items):
                return "downloads"
            return sort_items[idx][1]

        def update_pager():
            total = max(0, int(state["total"]))
            page = (state["offset"] // page_size) + 1
            max_page = max(1, (total + page_size - 1) // page_size)
            page_label.set_label(f"Page {page}/{max_page}")
            prev_btn.set_sensitive((not state["busy"]) and state["offset"] > 0)
            next_btn.set_sensitive(
                (not state["busy"]) and (state["offset"] + page_size < total)
            )

        def set_busy(busy: bool):
            state["busy"] = busy
            entry.set_sensitive(not busy)
            btn.set_sensitive(not busy)
            type_dd.set_sensitive(not busy)
            cat_dd.set_sensitive(not busy)
            sort_dd.set_sensitive(not busy)
            update_pager()

        def update_search_hint() -> None:
            ptype = selected_project_type()
            if ptype == "modpack":
                entry.set_placeholder_text("Search Fabric modpacks…")
            elif ptype == "datapack":
                entry.set_placeholder_text("Search datapacks…")
            else:
                entry.set_placeholder_text("Search Fabric mods…")

        def clear_results():
            while True:
                r = results.get_row_at_index(0)
                if r is None:
                    break
                results.remove(r)

        def installed_mod_names() -> set[str]:
            root = self._server_dir()
            if not root:
                return set()
            mods_dir = root / "mods"
            if not mods_dir.is_dir():
                return set()
            return {p.name.lower() for p in mods_dir.glob("*.jar")}

        def finish_search(hits, total, err, version, qtxt):
            set_busy(False)
            if err:
                results_label.set_label("Search failed")
                results.append(self._empty_listbox_row("Could not fetch Modrinth results."))
                return
            state["total"] = int(total)
            update_pager()
            results_label.set_label(f"{state['total']:,} results")
            if not hits:
                results.append(self._empty_listbox_row("No results"))
                return

            installed = installed_mod_names()
            for h in hits:
                results.append(self._make_modrinth_row(h, version, installed))

        def do_search(reset: bool = False):
            if reset:
                state["offset"] = 0
            clear_results()
            q = entry.get_text().strip()
            mc_version = self._server_info.mc_version if self._server_info else ""
            qtxt = q
            results_label.set_label("Searching…")
            set_busy(True)
            offset = int(state["offset"])
            category = selected_category() or None
            sort_key = selected_sort()
            project_type = selected_project_type()

            def thread_fn():
                try:
                    hits, total = modrinth_client.search_mods(
                        qtxt,
                        limit=page_size,
                        offset=offset,
                        sort=sort_key,
                        game_version=(mc_version if mc_version else None),
                        category=category,
                        loader="fabric",
                        server_side_only=(project_type != "datapack"),
                        project_type=project_type,
                    )
                    GLib.idle_add(
                        lambda h=hits, t=total, v=mc_version, qq=qtxt: finish_search(
                            h, t, None, v, qq
                        )
                    )
                except Exception as ex:
                    GLib.idle_add(
                        lambda e=str(ex), v=mc_version, qq=qtxt: finish_search(
                            [], 0, e, v, qq
                        )
                    )

            threading.Thread(target=thread_fn, daemon=True).start()

        def on_prev(*_):
            if state["offset"] >= page_size:
                state["offset"] -= page_size
                do_search(reset=False)

        def on_next(*_):
            if state["offset"] + page_size < state["total"]:
                state["offset"] += page_size
                do_search(reset=False)

        # Explicitly propagate events after triggering search to avoid
        # accidentally consuming default focus handling on text widgets.
        def trigger_search(*_):
            update_search_hint()
            do_search(reset=True)
            return False

        btn.connect("clicked", trigger_search)
        entry.connect("activate", trigger_search)
        prev_btn.connect("clicked", on_prev)
        next_btn.connect("clicked", on_next)
        type_dd.connect("notify::selected", trigger_search)
        cat_dd.connect("notify::selected", trigger_search)
        sort_dd.connect("notify::selected", trigger_search)

        sw = Gtk.ScrolledWindow()
        sw.set_vexpand(True)
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(results)
        outer.append(sw)

        # Run initial discovery search when opening the page.
        update_search_hint()
        update_pager()
        GLib.idle_add(lambda: do_search(reset=True) or False)
        tv.set_content(outer)
        return tv

    def _empty_listbox_row(self, title: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        label = Gtk.Label(label=title, xalign=0.0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_lines(3)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        row.set_child(label)
        return row

    def _looks_installed(self, hit, installed_names: set[str]) -> bool:
        slug = (hit.slug or "").strip().lower()
        if slug and any(slug in n for n in installed_names):
            return True
        needle = (hit.title or "").strip().lower().replace(" ", "-")
        if needle and any(needle in n for n in installed_names):
            return True
        return False

    def _configure_known_mod_after_download(self, hit) -> None:
        if not self._server_manager or not self._server_info:
            return
        slug = str(getattr(hit, "slug", "") or "").strip().lower()
        title = str(getattr(hit, "title", "") or "").strip().lower()
        project_id = str(getattr(hit, "project_id", "") or "").strip().lower()
        identifiers = {slug, title.replace(" ", "-"), project_id}
        playit = self._server_manager.playit_manager
        server_dir = str(self._server_info.server_dir)

        if "geyser" in identifiers:
            playit.configure_geyser_mod(server_dir)
            return

        if "floodgate" in identifiers:
            playit.configure_floodgate_mod(server_dir)
            return

        if "simple-voice-chat" in identifiers or "voice-chat" in identifiers:
            try:
                from hosty.shared.backend.playit_config import load_playit_config
                cfg = load_playit_config(self._server_info.server_dir)
                endpoint = str(cfg.get("voicechat_endpoint", "")).strip()
            except Exception:
                endpoint = ""
            playit.configure_voicechat_mod(server_dir, self._server_info.id, endpoint=endpoint)

    def _load_icon_async(self, image: Gtk.Image, url: str) -> None:
        def worker():
            try:
                from hosty.shared.backend import modrinth_client
                path = modrinth_client.get_icon_path(url)
                if not path:
                    return

                with open(path, "rb") as f:
                    data = f.read()

                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if not pixbuf:
                    return
                scaled = pixbuf.scale_simple(44, 44, GdkPixbuf.InterpType.BILINEAR) or pixbuf
                texture = Gdk.Texture.new_for_pixbuf(scaled)

                def ui_set():
                    image.set_from_paintable(texture)

                GLib.idle_add(ui_set)
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def _make_modrinth_row(self, hit, mc_version: str, installed_names: set[str]) -> Gtk.ListBoxRow:
        from hosty.shared.backend import modrinth_client

        ptype = str(getattr(hit, "project_type", "mod")).lower()
        is_modpack = ptype == "modpack"
        is_datapack = ptype == "datapack"

        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.add_css_class("mod-card-row")
        row.add_css_class("card")
        row.set_margin_bottom(10)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)

        icon = Gtk.Image.new_from_icon_name("application-x-addon-symbolic")
        icon.set_pixel_size(44)
        icon.set_valign(Gtk.Align.START)
        outer.append(icon)
        if hit.icon_url:
            self._load_icon_async(icon, hit.icon_url)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.set_hexpand(True)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_author = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_author.set_hexpand(True)

        title = Gtk.Label(label=hit.title, xalign=0.0)
        title.add_css_class("title-4")
        title.set_wrap(False)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        title_author.append(title)

        author_label = Gtk.Label(label=f"by {hit.author or 'Unknown'}", xalign=0.0)
        author_label.add_css_class("caption")
        author_label.add_css_class("dim-label")
        title_author.append(author_label)
        top_row.append(title_author)

        downloads_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        downloads_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        downloads_icon.set_pixel_size(12)
        downloads_box.append(downloads_icon)
        downloads_label = Gtk.Label(
            label=_format_compact_count(int(hit.downloads or 0)),
            xalign=1.0,
        )
        downloads_label.add_css_class("caption")
        downloads_label.add_css_class("dim-label")
        downloads_box.append(downloads_label)
        top_row.append(downloads_box)
        content.append(top_row)

        desc_text = (hit.description or "No description available.").strip()
        desc = Gtk.Label(label=desc_text, xalign=0.0)
        desc.set_wrap(True)
        desc.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        desc.set_lines(2)
        desc.set_ellipsize(Pango.EllipsizeMode.END)
        desc.add_css_class("dim-label")
        content.append(desc)

        version_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        version_dd = Gtk.DropDown.new_from_strings(["Checking versions…"])
        version_dd.set_hexpand(True)
        version_row.append(version_dd)

        if is_modpack:
            btn_label = "Install pack"
        elif is_datapack:
            btn_label = "Install datapack"
        else:
            btn_label = "Install"
        install_btn = Gtk.Button(label=btn_label)
        install_btn.add_css_class("suggested-action")
        install_btn.set_halign(Gtk.Align.START)
        if is_modpack and self._is_modpack_installed(hit.project_id):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        elif is_datapack and self._is_datapack_installed(hit.project_id):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        elif (not is_modpack) and (not is_datapack) and self._looks_installed(hit, installed_names):
            install_btn.set_label("Installed")
            install_btn.set_sensitive(False)
        version_row.append(install_btn)

        open_btn = Gtk.Button(label="Open page")
        open_btn.add_css_class("flat")
        version_row.append(open_btn)
        content.append(version_row)

        outer.append(content)
        row.set_child(outer)

        version_objs = []

        def on_open_page(*_):
            slug = hit.slug or hit.project_id
            if is_modpack:
                route = "modpack"
            elif is_datapack:
                route = "datapack"
            else:
                route = "mod"
            if not _open_uri(f"https://modrinth.com/{route}/{slug}"):
                self._alert("Could not open browser", "Unable to open the Modrinth page.")

        def selected_version():
            if not version_objs:
                return None
            idx = int(version_dd.get_selected())
            if idx < 0 or idx >= len(version_objs):
                return None
            return version_objs[idx]

        def on_install(*_b):
            if self._is_running():
                self._alert("Server is running", "Stop the server before installing mods.")
                return
            if not mc_version and not is_datapack:
                self._alert("Unknown version", "Could not read Minecraft version for this server.")
                return

            chosen = selected_version()
            if not chosen:
                self._alert("No compatible version", "No compatible server version is available.")
                return

            op_token = self._begin_mod_operation()
            if not op_token:
                self._alert("No server selected", "Select a server before installing mods.")
                return

            install_btn.set_label("Installing…")
            install_btn.set_sensitive(False)

            if is_datapack:
                def ui_ok_dp(fname: str, dep_count: int):
                    install_btn.set_label("Installed")
                    install_btn.set_sensitive(False)
                    self._record_datapack_install(
                        hit.project_id,
                        hit.title,
                        chosen.version_id,
                        chosen.filename,
                        version_number=chosen.version_number,
                    )
                    if dep_count > 0:
                        self._toast(f"Installed {dep_count} required dependencies")
                    self._toast(f"Installed datapack {fname}")
                    self._end_mod_operation(op_token)
                    self._rebuild_lists()

                def ui_err_dp(msg: str):
                    install_btn.set_label("Install datapack")
                    install_btn.set_sensitive(True)
                    self._end_mod_operation(op_token)
                    self._alert("Install failed", msg)

                def install_dp_thread(deps_to_install: list):
                    try:
                        root = self._server_dir()
                        if not root:
                            raise RuntimeError("No server selected.")
                        dp_dir = self._datapacks_dir()
                        if not dp_dir:
                            raise RuntimeError("Could not determine datapacks folder.")
                        dp_dir.mkdir(parents=True, exist_ok=True)

                        installed_dep_count = 0
                        for dep in deps_to_install:
                            dep_dest = dp_dir / dep.filename
                            modrinth_client.download_to(dep.download_url, dep_dest)
                            # Record dependency as an individual datapack install so it shows in UI and gets updates
                            self._record_datapack_install(
                                dep.project_id,
                                dep.name or dep.filename,
                                dep.version_id,
                                dep.filename,
                                version_number=dep.version_number,
                            )
                            installed_dep_count += 1

                        dest = dp_dir / chosen.filename
                        modrinth_client.download_to(chosen.download_url, dest)
                        GLib.idle_add(lambda f=chosen.filename, c=installed_dep_count: ui_ok_dp(f, c))
                    except Exception as e:
                        GLib.idle_add(lambda m=str(e): ui_err_dp(m))

                def prompt_dp_dependencies(deps_to_install: list):
                    if not deps_to_install:
                        threading.Thread(
                            target=install_dp_thread,
                            args=([],),
                            daemon=True,
                        ).start()
                        return

                    dep_names = [d.filename for d in deps_to_install]
                    preview = "\n".join([f"- {n}" for n in dep_names[:6]])
                    more = ""
                    if len(dep_names) > 6:
                        more = f"\n- and {len(dep_names) - 6} more"

                    dialog = Adw.AlertDialog()
                    dialog.set_heading("Install required dependencies?")
                    dialog.set_body(
                        "This datapack requires additional dependencies:\n\n"
                        f"{preview}{more}\n\n"
                        "Install them as well?"
                    )
                    dialog.add_response("cancel", "Cancel")
                    dialog.add_response("install", "Install")
                    dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
                    dialog.set_default_response("install")
                    dialog.set_close_response("cancel")

                    def on_response(_d, response):
                        if response == "install":
                            threading.Thread(
                                target=install_dp_thread,
                                args=(deps_to_install,),
                                daemon=True,
                            ).start()
                        else:
                            install_btn.set_label("Install datapack")
                            install_btn.set_sensitive(True)
                            self._end_mod_operation(op_token)

                    dialog.connect("response", on_response)
                    dialog.present(self.get_root())

                def resolve_and_prompt_dp():
                    try:
                        # For datapacks, we use the "datapack" loader for dependency resolution
                        deps = modrinth_client.resolve_required_dependencies(
                            chosen.version_id,
                            mc_version,
                            loader="datapack",
                        )
                        # Filter out already installed datapacks
                        dp_state = self._read_datapack_state().get("datapacks", {})
                        installed_ids = set(dp_state.keys())
                        
                        deps_to_install = []
                        for dep in deps:
                            if dep.project_id in installed_ids:
                                continue
                            if dep.project_id == hit.project_id:
                                continue
                            deps_to_install.append(dep)

                        GLib.idle_add(lambda d=deps_to_install: prompt_dp_dependencies(d))
                    except Exception as e:
                        GLib.idle_add(lambda m=str(e): ui_err_dp(m))

                threading.Thread(target=resolve_and_prompt_dp, daemon=True).start()
                return

            if is_modpack:
                install_btn.set_label("Installing...")

                def ui_ok_pack(downloaded_count: int, override_count: int, managed_mods: list[str]):
                    install_btn.set_label("Installed")
                    install_btn.set_sensitive(False)
                    self._record_modpack_install(
                        hit.project_id,
                        chosen.version_id,
                        version_number=chosen.version_number,
                        title=hit.title,
                        mod_files=sorted(
                            {
                                str(name).strip().lower()
                                for name in managed_mods
                                if str(name).strip().lower().endswith(".jar")
                            }
                        ),
                    )
                    self._toast(
                        f"Installed modpack ({downloaded_count} files)"
                    )
                    self._end_mod_operation(op_token)
                    self._rebuild_lists()

                def ui_err_pack(msg: str):
                    if self._is_modpack_installed(hit.project_id):
                        install_btn.set_label("Installed")
                        install_btn.set_sensitive(False)
                        self._end_mod_operation(op_token)
                        self._alert("Install failed", msg)
                        return
                    install_btn.set_label("Install pack")
                    install_btn.set_sensitive(True)
                    self._end_mod_operation(op_token)
                    self._alert("Install failed", msg)

                def ui_progress_pack(done: int, total: int):
                    if int(total) <= 0:
                        install_btn.set_label("Installing...")
                    else:
                        install_btn.set_label(f"{done}/{total}")

                def install_pack_thread():
                    try:
                        root = self._server_dir()
                        if not root:
                            raise RuntimeError("No server selected.")

                        def on_pack_progress(d: int, t: int, rel_path: str):
                            GLib.idle_add(lambda dd=d, tt=t: ui_progress_pack(dd, tt))

                        result = modrinth_client.install_modpack(
                            chosen.version_id,
                            root,
                            progress_callback=on_pack_progress,
                        )
                        GLib.idle_add(
                            lambda d=result.downloaded_files, o=result.extracted_override_files, m=result.managed_mod_files: ui_ok_pack(d, o, m)
                        )
                    except Exception as e:
                        GLib.idle_add(lambda m=str(e): ui_err_pack(m))

                threading.Thread(target=install_pack_thread, daemon=True).start()
                return

            def ui_ok(fname: str, dep_count: int):
                install_btn.set_label("Installed")
                install_btn.set_sensitive(False)
                self._record_individual_mod_install(
                    hit.project_id,
                    hit.title,
                    chosen.version_id,
                    chosen.filename,
                    version_number=chosen.version_number,
                )
                if dep_count > 0:
                    self._toast(f"Installed {dep_count} required dependencies")
                self._toast(f"Installed {fname}")
                if self._is_running():
                    self._toast("Restart the server for mod changes to apply")
                self._end_mod_operation(op_token)
                self._rebuild_lists()

            def ui_err(msg: str):
                install_btn.set_label("Install")
                install_btn.set_sensitive(True)
                self._end_mod_operation(op_token)
                self._alert("Install failed", msg)

            def thread_fn(deps_to_install: list, all_required_deps: list):
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")

                    mods_dir = root / "mods"
                    mods_dir.mkdir(parents=True, exist_ok=True)
                    installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}

                    installed_dep_count = 0
                    for dep in deps_to_install:
                        dep_name = dep.filename.lower()
                        if dep_name in installed_names_local:
                            continue
                        if dep_name == chosen.filename.lower():
                            continue
                        dep_dest = mods_dir / dep.filename
                        modrinth_client.download_to(dep.download_url, dep_dest)
                        installed_names_local.add(dep_name)
                        installed_dep_count += 1

                    dest = mods_dir / chosen.filename
                    modrinth_client.download_to(chosen.download_url, dest)
                    self._record_dependency_installs(chosen.filename, all_required_deps)
                    self._configure_known_mod_after_download(hit)
                    GLib.idle_add(lambda f=chosen.filename, c=installed_dep_count: ui_ok(f, c))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err(m))

            def prompt_dependencies(deps_to_install: list, all_required_deps: list):
                if not deps_to_install:
                    threading.Thread(
                        target=thread_fn,
                        args=([], all_required_deps),
                        daemon=True,
                    ).start()
                    return

                dep_names = [d.filename for d in deps_to_install]
                preview = "\n".join([f"- {n}" for n in dep_names[:6]])
                more = ""
                if len(dep_names) > 6:
                    more = f"\n- and {len(dep_names) - 6} more"

                dialog = Adw.AlertDialog()
                dialog.set_heading("Install required dependencies?")
                dialog.set_body(
                    "This mod requires additional dependencies:\n\n"
                    f"{preview}{more}\n\n"
                    "Install them as well?"
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("install", "Install")
                dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
                dialog.set_default_response("install")
                dialog.set_close_response("cancel")

                def on_response(_d, response):
                    if response == "install":
                        threading.Thread(
                            target=thread_fn,
                            args=(deps_to_install, all_required_deps),
                            daemon=True,
                        ).start()
                    else:
                        install_btn.set_label("Install")
                        install_btn.set_sensitive(True)
                        self._end_mod_operation(op_token)

                dialog.connect("response", on_response)
                dialog.present(self.get_root())

            def resolve_and_prompt():
                try:
                    root = self._server_dir()
                    if not root:
                        raise RuntimeError("No server selected.")

                    mods_dir = root / "mods"
                    mods_dir.mkdir(parents=True, exist_ok=True)
                    installed_names_local = {p.name.lower() for p in mods_dir.glob("*.jar")}
                    deps = modrinth_client.resolve_required_dependencies(
                        chosen.version_id,
                        mc_version,
                        loader="fabric",
                    )
                    deps_to_install = []
                    for dep in deps:
                        dep_name = dep.filename.lower()
                        if dep_name in installed_names_local:
                            continue
                        if dep_name == chosen.filename.lower():
                            continue
                        deps_to_install.append(dep)

                    GLib.idle_add(lambda d=deps_to_install, a=deps: prompt_dependencies(d, a))
                except Exception as e:
                    GLib.idle_add(lambda m=str(e): ui_err(m))

            threading.Thread(target=resolve_and_prompt, daemon=True).start()

        def load_versions():
            if not mc_version and not is_datapack:
                GLib.idle_add(lambda: version_dd.set_model(Gtk.StringList.new(["No server version"])))
                return
            try:
                loader_for_query = "datapack" if is_datapack else "fabric"
                versions = modrinth_client.find_compatible_versions(
                    hit.project_id,
                    mc_version,
                    loader=loader_for_query,
                    limit=5,
                )
                if not versions:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
                    return

                names = []
                seen = set()
                chosen_for_labels = []
                for v in versions:
                    vn = (v.version_number or v.name or "").strip()
                    if not vn or vn in seen:
                        continue
                    seen.add(vn)
                    names.append(vn)
                    chosen_for_labels.append(v)

                if not names:
                    GLib.idle_add(
                        lambda: version_dd.set_model(Gtk.StringList.new(["No compatible versions"]))
                    )
                    return

                version_objs.clear()
                version_objs.extend(chosen_for_labels)

                def ui_set_versions():
                    version_dd.set_model(Gtk.StringList.new(names))
                    version_dd.set_selected(0)

                GLib.idle_add(ui_set_versions)

                first = version_objs[0]
                if (not is_modpack) and (not is_datapack) and first.filename.lower() in installed_names:
                    dependents = self._dependency_dependents(first.filename)
                    if dependents:
                        GLib.idle_add(lambda: install_btn.set_label("Dependency"))
                    else:
                        GLib.idle_add(lambda: install_btn.set_label("Installed"))
                    GLib.idle_add(lambda: install_btn.set_sensitive(False))
            except Exception as e:
                GLib.idle_add(
                    lambda m=str(e): version_dd.set_model(Gtk.StringList.new(["Version lookup failed"]))
                )
                GLib.idle_add(lambda m=str(e): version_dd.set_tooltip_text(m))

        open_btn.connect("clicked", on_open_page)
        install_btn.connect("clicked", on_install)
        threading.Thread(target=load_versions, daemon=True).start()
        return row
