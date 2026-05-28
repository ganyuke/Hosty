"""
HostyWindow - Main application window with NavigationSplitView.
"""
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, GObject, Gio, Gdk

from hosty.shared.backend.playit_config import load_playit_config
from hosty.shared.backend.server_manager import ServerManager
from hosty.shared.utils.constants import APP_ID
from hosty.gtk_ui.views.sidebar import Sidebar
from hosty.gtk_ui.views.server_detail import ServerDetailView
from hosty.gtk_ui.views.welcome_view import WelcomeView


class HostyWindow(Adw.ApplicationWindow):
    """Main Hosty application window."""
    
    def __init__(self, server_manager: ServerManager, **kwargs):
        super().__init__(**kwargs)
        self._server_manager = server_manager
        self._current_server_id = None
        self._status_poll_id = None
        self._last_running_server_id = self._server_manager.get_running_server_id()
        self._playit_starting_server_id = None
        self._playit_autostart_paused_server_id: str | None = None
        
        self.set_title("Hosty")
        try:
            self.set_icon_name(APP_ID)
        except Exception:
            pass
        self.set_default_size(1000, 700)
        self.set_size_request(400, 400)
        self.add_css_class("hosty-window")
        
        # Toast overlay wraps everything
        self._toast_overlay = Adw.ToastOverlay()
        
        # OverlaySplitView
        self._split_view = Adw.OverlaySplitView()
        self._split_view.set_pin_sidebar(True)
        self._split_view.set_show_sidebar(True)
        
        # ===== Sidebar =====
        self._sidebar = Sidebar(server_manager)
        self._sidebar.connect('server-selected', self._on_server_selected)
        self._split_view.set_sidebar(self._sidebar)
        
        # ===== Content =====
        self._content_stack = Gtk.Stack()
        self._content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._content_stack.set_transition_duration(200)
        
        # Welcome view
        self._welcome_view = WelcomeView()
        self._content_stack.add_named(self._welcome_view, "welcome")
        
        # Server detail view
        self._detail_view = ServerDetailView(server_manager, toast_overlay=self._toast_overlay)
        self._content_stack.add_named(self._detail_view, "detail")
        
        self._split_view.set_content(self._content_stack)
        
        # Responsive breakpoint
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 600sp")
        )
        breakpoint.add_setter(self._split_view, "collapsed", True)
        self.add_breakpoint(breakpoint)
        
        self._toast_overlay.set_child(self._split_view)
        self.set_content(self._toast_overlay)

        # Sidebar toggle buttons for desktop mode
        self._welcome_sidebar_toggle = Gtk.ToggleButton()
        self._welcome_sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self._welcome_sidebar_toggle.set_tooltip_text("Toggle Sidebar")
        self._welcome_view.header.pack_start(self._welcome_sidebar_toggle)
        
        self._detail_sidebar_toggle = Gtk.ToggleButton()
        self._detail_sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self._detail_sidebar_toggle.set_tooltip_text("Toggle Sidebar")
        self._detail_view.header.pack_start(self._detail_sidebar_toggle)
        
        # Bind toggles to show-sidebar property
        self._split_view.bind_property(
            "show-sidebar", self._welcome_sidebar_toggle, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )
        self._split_view.bind_property(
            "show-sidebar", self._detail_sidebar_toggle, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE
        )
        
        # Show welcome or auto-select first server immediately to avoid welcome flicker.
        if server_manager.servers:
            first_id = server_manager.servers[0].id
            self._sidebar.select_server(first_id)
        else:
            self._content_stack.set_visible_child_name("welcome")
        
        # Connect server add to switch content
        server_manager.connect('server-added', self._on_server_added)
        server_manager.connect('server-removed', self._on_server_removed)

        self._status_poll_id = GLib.timeout_add(1000, self._poll_runtime_state)
        
        self._quit_requested = False
        self.connect("close-request", self._on_close_request)

        action_close_window = Gio.SimpleAction.new("close-window", None)
        action_close_window.connect("activate", self._on_close_window)
        self.add_action(action_close_window)

        action_show_menu = Gio.SimpleAction.new("show-menu", None)
        action_show_menu.connect("activate", self._on_show_menu)
        self.add_action(action_show_menu)

        shortcut_controller = Gtk.ShortcutController()
        shortcut_controller.set_scope(Gtk.ShortcutScope.GLOBAL)
        shortcut_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        shortcut = Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("F10"),
            Gtk.NamedAction.new("win.show-menu"),
        )
        shortcut_controller.add_shortcut(shortcut)
        self.add_controller(shortcut_controller)

    def _on_close_request(self, window):
        prefs = self._server_manager.preferences
        if prefs.run_in_background_on_close and not self._quit_requested:
            self.set_visible(False)
            
            from hosty.shared.utils.portal import set_background_status
            if self._server_manager.get_running_server_id():
                set_background_status("Server running")
            else:
                set_background_status("Server not running")
            
            if hasattr(self.get_application(), "_is_held_for_background"):
                app = self.get_application()
                if not app._is_held_for_background:
                    app.hold()
                    app._is_held_for_background = True
                    
            return True # stop close
            
        app = self.get_application()
        if hasattr(app, "_is_held_for_background") and app._is_held_for_background:
            app.release()
            app._is_held_for_background = False
            
        return False # continue close

    def _on_close_window(self, action, param):
        """Close the current window."""
        self.close()

    def _on_show_menu(self, action, param):
        """Open the primary app menu."""
        self._sidebar.popup_main_menu()
    
    def _on_server_selected(self, sidebar, server_id):
        """Handle server selection from sidebar."""
        if not server_id:
            self._content_stack.set_visible_child_name("welcome")
            return
        
        self._current_server_id = server_id
        server_info = self._server_manager.get_server(server_id)
        
        if server_info:
            self._detail_view.load_server(server_info)
            self._content_stack.set_visible_child_name("detail")
            
            # Hide sidebar in collapsed mode
            if self._split_view.get_collapsed():
                self._split_view.set_show_sidebar(False)
    
    def _on_server_added(self, manager, server_id):
        """Handle new server added - switch to it."""
        # The sidebar handles adding the row and selecting it
        pass

    def _on_server_removed(self, manager, server_id):
        """Return to welcome when current selection is removed or list is empty."""
        if self._current_server_id == server_id or not self._server_manager.servers:
            self._current_server_id = None
            self._content_stack.set_visible_child_name("welcome")

    def restore_from_background(self):
        """Compatibility no-op after removing background mode."""
        return

    def shutdown_background(self):
        """Compatibility no-op after removing background mode."""
        if self._status_poll_id:
            GLib.source_remove(self._status_poll_id)
            self._status_poll_id = None

    def _poll_runtime_state(self):
        self._detail_view.poll_runtime_state()

        running_id = self._server_manager.get_running_server_id()
        prefs = self._server_manager.preferences

        if running_id != self._last_running_server_id:
            previous_id = self._last_running_server_id
            self._last_running_server_id = running_id

            from hosty.shared.utils.portal import set_background_status
            if running_id:
                set_background_status("Server running")
            else:
                set_background_status("Server not running")

            if running_id:
                self._apply_playit_runtime(previous_id, running_id)
            else:
                self._apply_playit_runtime(previous_id, None)

                if previous_id:
                    if prefs.auto_backup_on_stop:
                        self._start_auto_backup(previous_id)
        else:
            # Keep playit in sync even when server runtime does not change.
            self._apply_playit_runtime(None, running_id)

        return True

    def _load_playit_config(self, server_id: str) -> dict:
        info = self._server_manager.get_server(server_id)
        if not info:
            return {}
        return load_playit_config(info.server_dir)

    def _apply_playit_runtime(self, previous_id: str | None, running_id: str | None):
        playit = self._server_manager.playit_manager

        if running_id != self._playit_autostart_paused_server_id and previous_id == self._playit_autostart_paused_server_id:
            self._playit_autostart_paused_server_id = None

        # Stop the current tunnel if the associated server stopped.
        if previous_id and previous_id != running_id and playit.is_running_for(previous_id):
            playit.stop()

        if not running_id:
            self._playit_autostart_paused_server_id = None
            return

        if running_id == self._playit_autostart_paused_server_id:
            return

        cfg = self._load_playit_config(running_id)
        if not cfg.get("enabled", False):
            return
        if not cfg.get("auto_start", True):
            return

        if playit.is_running_for(running_id):
            return

        if self._playit_starting_server_id == running_id:
            return

        info = self._server_manager.get_server(running_id)
        if not info:
            return

        self._playit_starting_server_id = running_id

        def worker():
            ok, _msg = playit.start(
                running_id,
                str(info.server_dir),
                secret=str(cfg.get("secret", "")).strip(),
                auto_install=bool(cfg.get("auto_install", True)),
            )
            if ok:
                playit.verify_playit_mod_configs(
                    str(info.server_dir),
                    running_id,
                    bedrock_endpoint=str(cfg.get("bedrock_endpoint", "")).strip(),
                    voicechat_endpoint=str(cfg.get("voicechat_endpoint", "")).strip(),
                )

            def clear_starting_flag():
                if self._playit_starting_server_id == running_id:
                    self._playit_starting_server_id = None

            GLib.idle_add(clear_starting_flag)

        threading.Thread(target=worker, daemon=True).start()

    def _start_auto_backup(self, server_id: str):
        def worker():
            ok, msg = self._server_manager.create_world_backup(server_id, auto=True)

            def ui_done():
                if ok:
                    self.show_toast(f"Auto backup created: {msg}")
                else:
                    self.show_toast(f"Auto backup skipped: {msg}")

            GLib.idle_add(ui_done)

        threading.Thread(target=worker, daemon=True).start()
    
    def show_toast(
        self,
        message: str,
        button_label: str | None = None,
        on_button=None,
        timeout: int = 3,
    ):
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        toast.set_timeout(max(1, int(timeout)))
        if button_label:
            toast.set_button_label(button_label)
            if on_button:
                toast.connect("button-clicked", lambda *_args: on_button())
        self._toast_overlay.add_toast(toast)

    def pause_playit_auto_start_for_running_server(self, server_id: str):
        self._playit_autostart_paused_server_id = server_id

    def clear_playit_auto_start_pause(self, server_id: str):
        if self._playit_autostart_paused_server_id == server_id:
            self._playit_autostart_paused_server_id = None
    
    @property
    def sidebar(self):
        return self._sidebar
    
    @property
    def detail_view(self):
        return self._detail_view
    
    @property
    def current_server_id(self):
        return self._current_server_id
