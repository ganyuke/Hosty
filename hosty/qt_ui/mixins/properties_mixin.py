"""
Properties mixin — GUI editor for server.properties with auto-save.
"""

from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QSpinBox,
    QProgressBar,
    QStackedWidget,
)

from ..components import SmoothScrollArea
from hosty.shared.backend.config_manager import ConfigManager
from hosty.shared.backend.server_manager import ServerInfo, ServerManager
from hosty.shared.core.events import dispatch_on_main_thread
from hosty.shared.utils.constants import (
    DEFAULT_RAM_MB,
    DIFFICULTIES,
    GAMEMODES,
    LEVEL_TYPE_NAMES,
    LEVEL_TYPES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    ServerStatus,
)


class PropertiesMixin:
    """Mixin providing a grouped GUI editor for server.properties."""

    def _build_properties_tab(self) -> None:
        self._prop_config: Optional[ConfigManager] = None
        self._prop_server_info: Optional[ServerInfo] = None
        
        self._suppress_prop_changes = False

        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Restart banner
        self._props_banner = QWidget(tab)
        self._props_banner.setVisible(False)
        self._props_banner.setStyleSheet(
            "background: rgba(229, 165, 10, 0.15); padding: 8px 16px;"
        )
        banner_layout = QHBoxLayout(self._props_banner)
        banner_layout.setContentsMargins(16, 6, 16, 6)
        banner_label = QLabel("⚠️ Restart the server to apply changes")
        banner_label.setStyleSheet("color: #e5a50a; font-weight: 600; font-size: 12px;")
        banner_layout.addWidget(banner_label)
        banner_layout.addStretch()
        dismiss_btn = QLabel("✕")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet("color: #e5a50a; font-weight: 700; padding: 0 4px;")
        dismiss_btn.mousePressEvent = lambda _: self._props_banner.setVisible(False)
        banner_layout.addWidget(dismiss_btn)
        outer.addWidget(self._props_banner)

        scroll = SmoothScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        self._prop_widgets = {}
        self._prop_config: Optional[ConfigManager] = None
        self._prop_server_info: Optional[ServerInfo] = None
        self._suppress_prop_changes = False

        # ===== General =====
        general = QGroupBox("General")
        gen_lay = QVBoxLayout(general)
        gen_lay.setSpacing(10)

        # Version Row
        version_row = QWidget()
        version_lay = QHBoxLayout(version_row)
        version_lay.setContentsMargins(0, 0, 0, 0)
        
        self._version_label = QLabel("Minecraft Version:")
        version_lay.addWidget(self._version_label)
        
        self._version_val = QLabel("Unknown")
        self._version_val.setProperty("class", "dim")
        version_lay.addWidget(self._version_val)
        
        version_lay.addStretch()
        
        self._change_version_btn = QPushButton()
        try:
            from ..theme import get_material_icon, get_colors, is_system_dark
            icon_color = get_colors(is_system_dark()).get("text_secondary", "#C4B5A3")
            self._change_version_btn.setIcon(get_material_icon("upgrade", icon_color, 20))
        except Exception:
            self._change_version_btn.setText("↑")
        self._change_version_btn.setToolTip("Upgrade server version")
        self._change_version_btn.setFixedSize(36, 36)
        self._change_version_btn.setProperty("class", "flat")
        self._change_version_btn.setEnabled(False)
        self._change_version_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_version_btn.clicked.connect(self._on_change_version_clicked)
        version_lay.addWidget(self._change_version_btn)
        
        gen_lay.addWidget(version_row)

        self._prop_widgets["motd"] = self._add_prop_entry(gen_lay, "Message of the Day", "motd", "a hosty server")
        self._prop_widgets["max-players"] = self._add_prop_spin(gen_lay, "Max Players", "max-players", 1, 1000, 20)
        self._prop_widgets["difficulty"] = self._add_prop_combo(gen_lay, "Difficulty", "difficulty", DIFFICULTIES, "easy")
        self._prop_widgets["gamemode"] = self._add_prop_combo(gen_lay, "Default Gamemode", "gamemode", GAMEMODES, "survival")

        layout.addWidget(general)

        # ===== Resources =====
        resources = QGroupBox("Resources")
        res_lay = QVBoxLayout(resources)
        res_lay.setSpacing(10)

        self._ram_prop_spin = self._add_prop_spin(res_lay, "Allocated RAM (MB)", "_ram", MIN_RAM_MB, MAX_RAM_MB, DEFAULT_RAM_MB, step=256)
        layout.addWidget(resources)

        # ===== World =====
        world = QGroupBox("World")
        world_lay = QVBoxLayout(world)
        world_lay.setSpacing(10)

        display_types = [LEVEL_TYPE_NAMES.get(t, t) for t in LEVEL_TYPES]
        self._prop_widgets["level-type"] = self._add_prop_combo(world_lay, "World Type", "level-type", display_types, "Default")
        self._prop_widgets["view-distance"] = self._add_prop_spin(world_lay, "View Distance", "view-distance", 2, 32, 10)
        self._prop_widgets["simulation-distance"] = self._add_prop_spin(world_lay, "Simulation Distance", "simulation-distance", 2, 32, 10)
        self._prop_widgets["spawn-protection"] = self._add_prop_spin(world_lay, "Spawn Protection Radius", "spawn-protection", 0, 256, 16)
        self._prop_widgets["max-world-size"] = self._add_prop_spin(world_lay, "Max World Size", "max-world-size", 1000, 29999984, 29999984, step=1000)

        layout.addWidget(world)

        # ===== Network =====
        network = QGroupBox("Network")
        net_lay = QVBoxLayout(network)
        net_lay.setSpacing(10)

        self._prop_widgets["server-port"] = self._add_prop_spin(net_lay, "Server Port", "server-port", 1024, 65535, 25565)
        self._prop_widgets["online-mode"] = self._add_prop_check(net_lay, "Online Mode", "online-mode", True)
        self._prop_widgets["enable-query"] = self._add_prop_check(net_lay, "Enable Query", "enable-query", False)

        layout.addWidget(network)

        # ===== Players =====
        players = QGroupBox("Players")
        play_lay = QVBoxLayout(players)
        play_lay.setSpacing(10)

        self._prop_widgets["pvp"] = self._add_prop_check(play_lay, "PvP", "pvp", True)
        self._prop_widgets["allow-flight"] = self._add_prop_check(play_lay, "Allow Flight", "allow-flight", False)

        layout.addWidget(players)

        # ===== Advanced =====
        advanced = QGroupBox("Advanced")
        adv_lay = QVBoxLayout(advanced)
        adv_lay.setSpacing(10)

        self._prop_widgets["enable-command-block"] = self._add_prop_check(adv_lay, "Command Blocks", "enable-command-block", False)
        self._prop_widgets["allow-nether"] = self._add_prop_check(adv_lay, "Allow Nether", "allow-nether", True)
        self._prop_widgets["hardcore"] = self._add_prop_check(adv_lay, "Hardcore Mode", "hardcore", False)
        self._prop_widgets["enable-rcon"] = self._add_prop_check(adv_lay, "Enable RCON", "enable-rcon", False)

        layout.addWidget(advanced)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        self._content_stack.addWidget(tab)

    def _add_prop_entry(self, layout, label: str, key: str, default: str) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        entry = QLineEdit(default)
        entry.setProperty("_prop_key", key)
        entry.textChanged.connect(self._schedule_save)
        row.addWidget(entry, 1)
        layout.addLayout(row)
        return entry

    def _add_prop_spin(self, layout, label: str, key: str, min_v: int, max_v: int, default: int, step: int = 1) -> QSpinBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        spin = QSpinBox()
        spin.setRange(min_v, max_v)
        spin.setSingleStep(step)
        spin.setValue(default)
        spin.setCursor(Qt.CursorShape.PointingHandCursor)
        spin.setProperty("_prop_key", key)
        spin.wheelEvent = lambda e: e.ignore()
        spin.valueChanged.connect(self._schedule_save)
        row.addWidget(spin, 1)
        layout.addLayout(row)
        return spin

    def _add_prop_combo(self, layout, label: str, key: str, options: list, default: str) -> QComboBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(options)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setProperty("_prop_key", key)
        combo.setProperty("_options", options)
        try:
            idx = options.index(default)
            combo.setCurrentIndex(idx)
        except ValueError:
            combo.setCurrentIndex(0)
        combo.wheelEvent = lambda e: e.ignore()
        combo.currentIndexChanged.connect(self._schedule_save)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _add_prop_check(self, layout, label: str, key: str, default: bool) -> QCheckBox:
        check = QCheckBox(label)
        check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.setChecked(default)
        check.setProperty("_prop_key", key)
        check.stateChanged.connect(self._schedule_save)
        layout.addWidget(check)
        return check

    def _load_properties(self, info: ServerInfo) -> None:
        config = self._server_manager.get_config(info.id)
        if not config:
            return

        self._prop_config = config
        self._prop_server_info = info
        
        if hasattr(self, '_version_val'):
            v_text = info.mc_version or "Unknown"
            if info.loader_version:
                v_text += f" ({info.loader_version})"
            self._version_val.setText(v_text)
            
        config.load()
        self._populate_properties()
        self._refresh_upgrade_button()
        self._props_banner.setVisible(False)

    def _refresh_upgrade_button(self) -> None:
        if not hasattr(self, "_change_version_btn") or not self._server_manager or not self._prop_server_info:
            return
        self._change_version_btn.setEnabled(False)
        self._change_version_btn.setToolTip("Checking for newer Minecraft versions...")

        def worker():
            versions = self._server_manager.download_manager.fetch_game_versions()
            current = self._prop_server_info.mc_version if self._prop_server_info else ""
            has_upgrade = any(ServerManager.is_version_after(v, current) for v in versions)

            def done():
                self._change_version_btn.setEnabled(has_upgrade)
                self._change_version_btn.setToolTip(
                    "Upgrade server version" if has_upgrade else "No newer Minecraft versions available"
                )

            dispatch_on_main_thread(done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_change_version_clicked(self) -> None:
        if not self._server_manager or not self._prop_server_info:
            self._show_toast("Select a server first.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Update Version")
        dialog.setMinimumSize(560, 520)
        root = QVBoxLayout(dialog)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 20)

        header = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        primary_btn = QPushButton("Next")
        primary_btn.setProperty("class", "accent")
        primary_btn.setEnabled(False)
        header.addWidget(cancel_btn)
        header.addStretch()
        header.addWidget(primary_btn)
        root.addLayout(header)

        stack = QStackedWidget(dialog)
        root.addWidget(stack, 1)

        runtime_page = QWidget(dialog)
        runtime_layout = QVBoxLayout(runtime_page)
        runtime_layout.setSpacing(12)
        runtime_layout.setContentsMargins(0, 0, 0, 0)

        form = QGroupBox("Runtime")
        form_layout = QVBoxLayout(form)
        mc_combo = QComboBox()
        mc_combo.addItem("Loading...")
        mc_combo.setEnabled(False)

        mc_row = QHBoxLayout()
        mc_row.addWidget(QLabel("Minecraft version"))
        mc_row.addWidget(mc_combo, 1)
        form_layout.addLayout(mc_row)
        runtime_layout.addWidget(form)
        runtime_layout.addStretch()
        stack.addWidget(runtime_page)

        mods_page = QWidget(dialog)
        mods_layout = QVBoxLayout(mods_page)
        mods_layout.setSpacing(12)
        mods_layout.setContentsMargins(0, 0, 0, 0)
        info = QLabel("Compatible items will be updated. Incompatible items will be moved to mods_incompatible/ or datapacks_incompatible/ so the server can start.")
        info.setWordWrap(True)
        info.setProperty("class", "dim")
        mods_layout.addWidget(info)
        review_group = QGroupBox("Mod Compatibility")
        review_layout = QVBoxLayout(review_group)
        review_layout.setSpacing(8)
        mods_layout.addWidget(review_group, 1)
        stack.addWidget(mods_page)

        progress_page = QWidget(dialog)
        progress_layout = QVBoxLayout(progress_page)
        progress_layout.setSpacing(16)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_title = QLabel("Updating Server")
        progress_title.setProperty("class", "header")
        progress_layout.addWidget(progress_title)
        progress_detail = QLabel("Preparing update...")
        progress_detail.setProperty("class", "dim")
        progress_detail.setWordWrap(True)
        progress_layout.addWidget(progress_detail)
        apply_progress = QProgressBar()
        apply_progress.setRange(0, 100)
        apply_progress.setValue(0)
        progress_layout.addWidget(apply_progress)
        progress_layout.addStretch()
        stack.addWidget(progress_page)

        game_versions: list[str] = []
        loader_versions: list[str] = []
        selected_mc = {"value": ""}
        selected_loader = {"value": ""}
        compatibility_plan: dict = {}

        def validate():
            primary_btn.setEnabled(bool(game_versions))

        def on_cancel():
            if stack.currentIndex() == 1:
                stack.setCurrentIndex(0)
                cancel_btn.setText("Cancel")
                primary_btn.setText("Next")
                primary_btn.setEnabled(bool(game_versions) and bool(loader_versions))
                return
            if stack.currentIndex() == 2:
                return
            dialog.reject()

        cancel_btn.clicked.connect(on_cancel)

        def clear_review() -> None:
            while review_layout.count():
                item = review_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        def add_group(title: str, items: list[dict], fallback: str) -> None:
            if not items:
                lbl = QLabel(fallback)
                lbl.setProperty("class", "dim")
                review_layout.addWidget(lbl)
                return
            header_lbl = QLabel(f"{title} ({len(items)})")
            header_lbl.setProperty("class", "title")
            review_layout.addWidget(header_lbl)
            for item in items:
                name = str(item.get("title") or item.get("filename") or "Unknown")
                version = str(item.get("version_number") or item.get("version_id") or "").strip()
                filename = str(item.get("filename") or item.get("current_filename") or "").strip()
                row = QLabel(" · ".join([x for x in (name, version, filename) if x]))
                row.setProperty("class", "dim")
                row.setWordWrap(True)
                review_layout.addWidget(row)

        def load_versions():
            games = self._server_manager.download_manager.fetch_game_versions()
            loaders = self._server_manager.download_manager.fetch_loader_versions()

            def done():
                current_mc = self._prop_server_info.mc_version if self._prop_server_info else ""
                current_loader = self._prop_server_info.loader_version if self._prop_server_info else ""
                next_games = [
                    v for v in games
                    if ServerManager.is_version_after(v, current_mc)
                ]
                next_loaders = [
                    v for v in loaders
                    if not current_loader or ServerManager.is_version_at_least(v, current_loader)
                ]
                game_versions.clear()
                game_versions.extend(next_games)
                loader_versions.clear()
                loader_versions.extend(next_loaders)
                mc_combo.clear()
                mc_combo.addItems(game_versions or ["No versions found"])
                mc_combo.setEnabled(bool(game_versions))
                # Automatically use the newest loader (first in list)
                if loader_versions:
                    selected_loader["value"] = loader_versions[0]
                validate()

            dispatch_on_main_thread(done)

        def show_review():
            if not game_versions or not loader_versions or not self._prop_server_info:
                return
            selected_mc["value"] = game_versions[mc_combo.currentIndex()]
            # Use the automatically selected newest loader
            selected_loader["value"] = loader_versions[0] if loader_versions else ""
            primary_btn.setEnabled(False)
            primary_btn.setText("Update")
            cancel_btn.setText("Back")
            clear_review()
            stack.setCurrentIndex(1)
            loading = QLabel("Checking installed mods and datapacks...")
            loading.setProperty("class", "dim")
            review_layout.addWidget(loading)
            scan_progress = QProgressBar()
            scan_progress.setRange(0, 0)
            review_layout.addWidget(scan_progress)

            def worker():
                plan = self._server_manager.scan_update_compatibility(
                    self._prop_server_info.id,
                    selected_mc["value"],
                )

                def done():
                    compatibility_plan.clear()
                    compatibility_plan.update(plan)
                    clear_review()
                    compatible = plan.get("compatible", {})
                    incompatible = plan.get("incompatible", {})
                    unknown = plan.get("unknown", {})
                    add_group(
                        "Compatible and will be updated",
                        [*compatible.get("modpacks", []), *compatible.get("mods", []), *compatible.get("datapacks", [])],
                        "No tracked compatible items found",
                    )
                    add_group(
                        "Incompatible and will be disabled",
                        [*incompatible.get("modpacks", []), *incompatible.get("mods", []), *incompatible.get("datapacks", [])],
                        "No incompatible items found",
                    )
                    unknown_items = [*unknown.get("modpacks", []), *unknown.get("mods", []), *unknown.get("datapacks", [])]
                    if unknown_items:
                        add_group("Could not check", unknown_items, "")
                    stack.setCurrentIndex(1)
                    primary_btn.setText("Update")
                    primary_btn.setEnabled(True)

                dispatch_on_main_thread(done)

            import threading
            threading.Thread(target=worker, daemon=True).start()

        def run_update():
            mc_version = selected_mc["value"]
            loader_version = selected_loader["value"]
            if not mc_version or not loader_version:
                show_review()
                return
            primary_btn.setEnabled(False)
            cancel_btn.setEnabled(False)
            primary_btn.setText("Update")
            stack.setCurrentIndex(2)
            apply_progress.setValue(0)
            progress_detail.setText("Preparing update...")

            def progress_cb(frac, message):
                def update_progress_ui():
                    apply_progress.setValue(int(max(0.0, min(1.0, float(frac))) * 100))
                    progress_detail.setText(str(message))
                    primary_btn.setToolTip(str(message))
                dispatch_on_main_thread(update_progress_ui)

            def worker():
                ok, msg = self._server_manager.update_server_runtime(
                    self._prop_server_info.id,
                    mc_version,
                    loader_version,
                    progress_callback=progress_cb,
                    compatibility_plan=compatibility_plan,
                )

                def done():
                    if ok:
                        self._prop_server_info.mc_version = mc_version
                        self._prop_server_info.loader_version = loader_version
                        self._version_val.setText(f"{mc_version} ({loader_version})")
                        self._refresh_upgrade_button()
                        self._show_toast(msg)
                        dialog.accept()
                    else:
                        QMessageBox.warning(dialog, "Update Failed", msg)
                        cancel_btn.setEnabled(True)
                        cancel_btn.setText("Back")
                        stack.setCurrentIndex(1)
                        primary_btn.setText("Update")
                        primary_btn.setEnabled(True)

                dispatch_on_main_thread(done)

            import threading
            threading.Thread(target=worker, daemon=True).start()

        def on_primary():
            if stack.currentIndex() == 0:
                show_review()
            else:
                run_update()

        primary_btn.clicked.connect(on_primary)

        import threading
        threading.Thread(target=load_versions, daemon=True).start()
        dialog.exec()

    def _populate_properties(self) -> None:
        if not self._prop_config:
            return

        self._suppress_prop_changes = True

        # RAM
        if hasattr(self, '_ram_prop_spin') and self._prop_server_info:
            self._ram_prop_spin.setValue(int(self._prop_server_info.ram_mb))

        for key, widget in self._prop_widgets.items():
            if isinstance(widget, QLineEdit):
                val = self._prop_config.get(key, "")
                widget.setText(val)
            elif isinstance(widget, QSpinBox):
                val = self._prop_config.get_int(key, widget.value())
                widget.setValue(val)
            elif isinstance(widget, QCheckBox):
                val = self._prop_config.get_bool(key, widget.isChecked())
                widget.setChecked(val)
            elif isinstance(widget, QComboBox):
                val = self._prop_config.get(key, "")
                options = widget.property("_options") or []
                if key == "level-type":
                    display_val = LEVEL_TYPE_NAMES.get(val, val)
                    try:
                        idx = options.index(display_val)
                        widget.setCurrentIndex(idx)
                    except (ValueError, IndexError):
                        widget.setCurrentIndex(0)
                else:
                    try:
                        idx = options.index(val)
                        widget.setCurrentIndex(idx)
                    except (ValueError, IndexError):
                        widget.setCurrentIndex(0)

        self._suppress_prop_changes = False

    def _schedule_save(self, *_args) -> None:
        if self._suppress_prop_changes:
            return
        # If the RAM spin box changed, save it directly
        if hasattr(self, '_ram_prop_spin') and self._prop_server_info:
            new_ram = self._ram_prop_spin.value()
            if self._prop_server_info.ram_mb != new_ram:
                self._server_manager.update_server_ram(self._prop_server_info.id, new_ram)
        
        self._do_auto_save()

    def _do_auto_save(self) -> None:
        if not self._prop_config:
            self._props_banner.setVisible(False)
            return

        for key, widget in self._prop_widgets.items():
            if isinstance(widget, QLineEdit):
                self._prop_config.set_value(key, widget.text())
            elif isinstance(widget, QSpinBox):
                self._prop_config.set_value(key, int(widget.value()))
            elif isinstance(widget, QCheckBox):
                self._prop_config.set_value(key, widget.isChecked())
            elif isinstance(widget, QComboBox):
                idx = widget.currentIndex()
                options = widget.property("_options") or []
                if key == "level-type":
                    display_name = options[idx] if idx < len(options) else options[0] if options else ""
                    raw_val = next(
                        (k for k, v in LEVEL_TYPE_NAMES.items() if v == display_name),
                        LEVEL_TYPES[0] if LEVEL_TYPES else "",
                    )
                    self._prop_config.set_value(key, raw_val)
                else:
                    val = options[idx] if idx < len(options) else (options[0] if options else "")
                    self._prop_config.set_value(key, val)

        self._prop_config.save()

        # Update RAM if changed
        running = False
        if self._server_manager and self._prop_server_info and hasattr(self, '_ram_prop_spin'):
            ram_mb = int(self._ram_prop_spin.value())
            if ram_mb != int(self._prop_server_info.ram_mb):
                self._server_manager.update_server_ram(self._prop_server_info.id, ram_mb)

            process = self._server_manager.get_process(self._prop_server_info.id)
            if process:
                process.set_max_players(self._prop_config.get_int("max-players", 20))
                running = bool(process.is_running)

        if self._server_manager and self._prop_server_info:
            self._server_manager.emit_on_main_thread("server-changed", self._prop_server_info.id)

        self._props_banner.setVisible(running)
