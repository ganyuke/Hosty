"""
Manage Playit Tunnel dialog.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, GObject

class ManagePlayitTunnelDialog(Adw.Dialog):
    """Dialog to manage a playit tunnel (show details, regenerate, delete)."""

    __gsignals__ = {
        "regenerate": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, tunnel_name: str, connection_type: str, port: int, domain: str):
        super().__init__()

        self.set_title(f"Manage {tunnel_name} Tunnel")
        self.set_content_width(400)
        
        # Parse domain if it contains a remote port (format: "domain:port")
        remote_port = port
        display_domain = domain
        if ":" in domain:
            parts = domain.rsplit(":", 1)
            display_domain = parts[0]
            try:
                remote_port = int(parts[1])
            except (ValueError, IndexError):
                pass
        
        self._toolbar_view = Adw.ToolbarView()
        
        header = Adw.HeaderBar()
        
        self._toolbar_view.add_top_bar(header)
        
        page = Adw.PreferencesPage()
        
        group = Adw.PreferencesGroup(title="Tunnel Details")
        
        # Connection Type
        type_row = Adw.ActionRow(title="Connection type", subtitle=connection_type)
        group.add(type_row)
        
        # Port (show remote port for tunnel endpoint)
        port_row = Adw.ActionRow(title="Port", subtitle=str(remote_port))
        group.add(port_row)
        
        # Domain
        domain_row = Adw.ActionRow(title="Domain", subtitle=display_domain)
        group.add(domain_row)
        
        page.add(group)
        
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        action_box.set_margin_top(32)
        action_box.set_margin_bottom(18)
        action_box.set_halign(Gtk.Align.CENTER)
        
        # Regenerate button
        regen_btn = Gtk.Button(label="Regenerate Domain")
        regen_btn.add_css_class("pill")
        regen_btn.set_size_request(220, 36)
        regen_btn.connect("clicked", self._on_regenerate)
        action_box.append(regen_btn)
        
        # Delete button
        delete_btn = Gtk.Button(label="Delete Tunnel")
        delete_btn.add_css_class("pill")
        delete_btn.add_css_class("destructive-action")
        delete_btn.set_size_request(220, 36)
        delete_btn.connect("clicked", self._on_delete)
        action_box.append(delete_btn)
        
        page.set_vexpand(True)
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(page)
        content_box.append(action_box)
        
        self._toolbar_view.set_content(content_box)
        self.set_child(self._toolbar_view)

    def _on_regenerate(self, *_args):
        self.emit("regenerate")
        self.close()

    def _on_delete(self, *_args):
        self.emit("delete")
        self.close()
