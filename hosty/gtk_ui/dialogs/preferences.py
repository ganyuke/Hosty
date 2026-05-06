"""
Application preferences window
"""
from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib, Gio

from hosty.shared.utils.constants import (
    DATA_DIR,
)
from hosty.shared.backend.preferences_manager import PreferencesManager
from hosty.shared.backend.server_manager import ServerManager


def show_preferences_window(parent: Gtk.Window, preferences: PreferencesManager, server_manager: ServerManager | None = None):
    win = Adw.PreferencesDialog()
    # Properties like default_size or modal are usually handled slightly differently in Adw.Dialog if at all, but we can set them if supported or skip them.

    page = Adw.PreferencesPage(title="General")
    group = Adw.PreferencesGroup(
        title="Application",
    )
    data_row = Adw.ActionRow(title="Data folder", subtitle=str(DATA_DIR))
    data_button = Gtk.Button(valign=Gtk.Align.CENTER)
    data_image = Gtk.Image.new_from_icon_name("folder-open-symbolic")
    data_button.set_child(data_image)
    data_button.connect("clicked", lambda _: Gio.AppInfo.launch_default_for_uri(f"file://{DATA_DIR}"))
    data_row.add_suffix(data_button)
    group.add(data_row)

    bg_row = Adw.SwitchRow(
        title="Run in background",
    )
    bg_row.set_active(preferences.run_in_background_on_close)
    
    startup_row = Adw.SwitchRow(
        title="Open Hosty on startup",
    )
    startup_row.set_active(preferences.open_on_startup)

    def on_bg_toggled(row, _pspec):
        active = row.get_active()
        preferences.run_in_background_on_close = active
        
        if active:
            # If turning on background
            from hosty.shared.utils.portal import request_background
            def on_bg_response(success, bg, auto, err):
                if not success or not bg:
                    GLib.idle_add(row.set_active, False)
                    GLib.idle_add(preferences.__setattr__, "run_in_background_on_close", False)
            request_background(False, on_bg_response)

    bg_row.connect("notify::active", on_bg_toggled)

    def on_startup_toggled(row, _pspec):
        active = row.get_active()
        preferences.open_on_startup = active
        
        if active:
            from hosty.shared.utils.portal import request_background
            def on_start_response(success, bg, auto, err):
                if not success or not auto:
                    GLib.idle_add(row.set_active, False)
                    GLib.idle_add(preferences.__setattr__, "open_on_startup", False)
            request_background(True, on_start_response)

    startup_row.connect("notify::active", on_startup_toggled)
    
    group.add(bg_row)
    group.add(startup_row)

    autobackup_row = Adw.SwitchRow(
        title="Auto backup world on stop",
    )
    autobackup_row.set_active(preferences.auto_backup_on_stop)

    def on_autobackup_toggled(row, _pspec):
        preferences.auto_backup_on_stop = row.get_active()

    autobackup_row.connect("notify::active", on_autobackup_toggled)
    group.add(autobackup_row)

    dep_row = Adw.SwitchRow(
        title="Auto resolve mod dependencies",
    )
    dep_row.set_active(preferences.auto_resolve_mod_dependencies)

    def on_dep_toggled(row, _pspec):
        preferences.auto_resolve_mod_dependencies = row.get_active()

    dep_row.connect("notify::active", on_dep_toggled)
    group.add(dep_row)

    page.add(group)

    win.add(page)

    win.present(parent)
