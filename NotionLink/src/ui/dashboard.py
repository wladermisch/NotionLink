import queue
import sys
import threading
import ctypes
from ctypes import wintypes
import json
import os
import webbrowser

import customtkinter as ctk
import pyperclip as clip
from PIL import Image

from ..core import APP_VERSION, config, config_file_path, resource_path
from ..server import TRAY_ICON_ICO


_ICON_COLOR = {
    "green": "#00C853",
    "yellow": "#FFD600",
    "red": "#FF1744",
    "gray": "#9E9E9E",
}


class CtkTooltip:
    def __init__(self, widget, text, delay_ms=450, hide_after_ms=2200):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.hide_after_ms = hide_after_ms
        self.tip = None
        self._show_after_id = None
        self._hide_after_id = None
        self.widget.bind("<Enter>", self._schedule_show)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<ButtonPress>", self._hide)

    def _schedule_show(self, _event=None):
        self._cancel_show()
        self._show_after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_show(self):
        if self._show_after_id is not None:
            try:
                self.widget.after_cancel(self._show_after_id)
            except Exception:
                pass
            self._show_after_id = None

    def _cancel_hide(self):
        if self._hide_after_id is not None:
            try:
                self.widget.after_cancel(self._hide_after_id)
            except Exception:
                pass
            self._hide_after_id = None

    def _show(self, _event=None):
        self._show_after_id = None
        if self.tip or not self.text:
            return
        self.tip = ctk.CTkToplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip.geometry(f"+{x}+{y}")
        label = ctk.CTkLabel(
            self.tip,
            text=self.text,
            fg_color="#0F172A",
            text_color="#E2E8F0",
            corner_radius=6,
            padx=10,
            pady=6,
        )
        label.pack()
        self._cancel_hide()
        self._hide_after_id = self.widget.after(self.hide_after_ms, self._hide)

    def _hide(self, _event=None):
        self._cancel_show()
        self._cancel_hide()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class CtkDashboardWindow(ctk.CTk):
    def __init__(self, callbacks, app_version, initial_status="Notion: No Token"):
        super().__init__()
        self.callbacks = callbacks
        self._apply_window_icon()
        self.title(f"NotionLink {app_version} - Dashboard")
        self.minsize(900, 600)
        self._nav_stack = [
            {
                "route": "dashboard",
                "label": "Dashboard",
                "args": (),
            }
        ]
        self._log_textbox = None
        self._status_widgets = {}
        self._transitioning = False
        self._transient_notice_active = False
        self._transient_notice_message = ""
        self._transient_notice_level = "warning"
        self._last_status = initial_status
        self._last_descriptor = {
            "icon": "gray",
            "reconnect_visible": False,
            "reconnect_text": "Retry Connection",
            "offline_visible": False,
            "panel_text": "NotionLink is running...",
            "panel_colors": ("#1e3a1e", "#66ff66", "#2e5a2e"),
        }
        initial_descriptor = self._invoke_callback("get_status_descriptor", initial_status, default=None)
        if isinstance(initial_descriptor, dict):
            self._last_descriptor = initial_descriptor
        self._build_layout(initial_status, app_version)

    def _compact_status_text(self, status):
        if status.startswith("Notion: "):
            return status.replace("Notion: ", "", 1)
        return status

    def _load_logo(self, size=(120, 120)):
        try:
            logo_path = resource_path("assets/logo.png")
            logo_image = Image.open(logo_path)
            return ctk.CTkImage(light_image=logo_image, dark_image=logo_image, size=size)
        except Exception as logo_error:
            print(f"Failed to load splash logo: {logo_error}")
            return None

    def _apply_window_icon(self):
        if not sys.platform.startswith("win"):
            return
        try:
            self.iconbitmap(default=TRAY_ICON_ICO)
        except Exception:
            try:
                self.wm_iconbitmap(TRAY_ICON_ICO)
            except Exception as icon_error:
                print(f"Failed to set dashboard window icon: {icon_error}")

    def _build_layout(self, initial_status, app_version):
        self.configure(fg_color="#0B1220")
        self.grid_columnconfigure(0, weight=27)
        self.grid_columnconfigure(1, weight=73)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, fg_color="#111827", corner_radius=14, border_width=1, border_color="#1F2937")
        left.grid(row=0, column=0, sticky="nsew", padx=(15, 7), pady=15)
        left.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(self, fg_color="#111827", corner_radius=14, border_width=1, border_color="#1F2937")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 15), pady=15)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Quick Actions", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )

        self.convert_btn = ctk.CTkButton(left, text="Convert Path to Link", command=lambda: self.open_primary_page("convert", "Convert Path"))
        self.convert_btn.grid(row=1, column=0, sticky="ew", padx=12, pady=4)

        ctk.CTkLabel(left, text="Management", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(16, 6)
        )

        self.page_btn = ctk.CTkButton(left, text="Page Mappings", command=lambda: self.open_primary_page("mappings", "Page Mappings", "page"))
        self.page_btn.grid(row=3, column=0, sticky="ew", padx=12, pady=4)

        self.db_btn = ctk.CTkButton(left, text="Database Mappings", command=lambda: self.open_primary_page("mappings", "Database Mappings", "database"))
        self.db_btn.grid(row=4, column=0, sticky="ew", padx=12, pady=4)

        self.token_btn = ctk.CTkButton(left, text="Notion Token", command=lambda: self.open_primary_page("token", "Notion Token"))
        self.token_btn.grid(row=5, column=0, sticky="ew", padx=12, pady=4)

        ctk.CTkLabel(left, text="Settings", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=6, column=0, sticky="w", padx=12, pady=(16, 6)
        )

        self.autostart_var = ctk.BooleanVar(value=False)
        self.autostart_checkbox = ctk.CTkCheckBox(
            left,
            text="Start with Windows",
            variable=self.autostart_var,
            command=lambda: self.callbacks.get("toggle_autostart", lambda _v: None)(self.autostart_var.get()),
        )
        self.autostart_checkbox.grid(row=7, column=0, sticky="w", padx=12, pady=4)

        self.sentry_var = ctk.BooleanVar(value=False)
        self.sentry_checkbox = ctk.CTkCheckBox(
            left,
            text="Enable Error Reports",
            variable=self.sentry_var,
            command=lambda: self.callbacks.get("toggle_sentry", lambda _v: None)(self.sentry_var.get()),
        )
        self.sentry_checkbox.grid(row=8, column=0, sticky="w", padx=12, pady=4)

        self.feedback_btn = ctk.CTkButton(left, text="Send Feedback", command=lambda: self.open_primary_page("feedback", "Feedback"))
        self.feedback_btn.grid(row=9, column=0, sticky="ew", padx=12, pady=4)

        self.help_btn = ctk.CTkButton(left, text="Help", command=lambda: self.open_primary_page("help", "Help"))
        self.help_btn.grid(row=10, column=0, sticky="ew", padx=12, pady=4)

        left.grid_rowconfigure(11, weight=1)
        self.minimize_btn = ctk.CTkButton(
            left,
            text="Minimize to Tray",
            fg_color="#334155",
            hover_color="#475569",
            command=self.withdraw,
        )
        self.minimize_btn.grid(row=12, column=0, sticky="ew", padx=12, pady=(8, 6))

        self.quit_btn = ctk.CTkButton(
            left,
            text="Close NotionLink",
            fg_color="#8B0000",
            hover_color="#A30000",
            command=self._confirm_and_close_app,
        )
        self.quit_btn.grid(row=13, column=0, sticky="ew", padx=12, pady=(6, 12))

        self.content_area = ctk.CTkFrame(right, fg_color="#111827", corner_radius=12, border_width=1, border_color="#1F2937")
        self.content_area.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.content_area.grid_columnconfigure(0, weight=1)
        self.content_area.grid_rowconfigure(1, weight=1)

        nav_bar = ctk.CTkFrame(self.content_area, fg_color="transparent")
        nav_bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        nav_bar.grid_columnconfigure(1, weight=1)

        self.back_btn = ctk.CTkButton(
            nav_bar,
            text="←",
            width=36,
            height=30,
            corner_radius=8,
            fg_color="#1F2937",
            hover_color="#334155",
            text_color="#CBD5E1",
            command=self.go_back,
        )
        self.back_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.breadcrumb_frame = ctk.CTkFrame(nav_bar, fg_color="transparent")
        self.breadcrumb_frame.grid(row=0, column=1, sticky="ew")

        self.page_container = ctk.CTkFrame(self.content_area, fg_color="transparent")
        self.page_container.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        self.page_container.grid_columnconfigure(0, weight=1)
        self.page_container.grid_rowconfigure(0, weight=1)

        footer = ctk.CTkFrame(self.content_area, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)

        self.version_label = ctk.CTkLabel(
            footer,
            text=f"NotionLink - wladermisch | Version {app_version}",
            text_color="#999999",
            anchor="w",
        )
        self.version_label.grid(row=0, column=0, sticky="w")

        self.footer_status_icon = ctk.CTkLabel(footer, text="●", text_color=_ICON_COLOR["gray"])
        self.footer_status_icon.grid(row=0, column=1, sticky="e", padx=(0, 6))
        self.footer_status_label = ctk.CTkLabel(footer, text=self._compact_status_text(initial_status), text_color="#CBD5E1")
        self.footer_status_label.grid(row=0, column=2, sticky="e")

        self._render_current_page(initial_status)

    def _confirm_and_close_app(self):
        if config.get("skip_close_confirmation", False):
            close_cb = self.callbacks.get("quit_app")
            if close_cb:
                close_cb()
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Close NotionLink")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        try:
            dialog.iconbitmap(default=TRAY_ICON_ICO)
        except Exception:
            pass

        container = ctk.CTkFrame(dialog, fg_color="#111827", corner_radius=12, border_width=1, border_color="#1F2937")
        container.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(container, text="Really close NotionLink?", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            container,
            text="Localhost links will stop working while NotionLink is fully closed.",
            anchor="w",
            justify="left",
            text_color="#CBD5E1",
        ).pack(anchor="w", padx=12, pady=(0, 10))

        dont_remind_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(container, text="Don't remind me again", variable=dont_remind_var).pack(anchor="w", padx=12, pady=(0, 10))

        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 12))

        def _cancel():
            dialog.destroy()

        def _close_now():
            if dont_remind_var.get():
                config["skip_close_confirmation"] = True
                self._save_config()
            dialog.destroy()
            close_cb = self.callbacks.get("quit_app")
            if close_cb:
                close_cb()

        ctk.CTkButton(buttons, text="Cancel", fg_color="#374151", hover_color="#4B5563", command=_cancel).pack(side="left")
        ctk.CTkButton(buttons, text="Close NotionLink", fg_color="#8B0000", hover_color="#A30000", command=_close_now).pack(side="right")

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 520)
        height = max(dialog.winfo_reqheight(), 220)
        x = self.winfo_rootx() + int((self.winfo_width() - width) / 2)
        y = self.winfo_rooty() + int((self.winfo_height() - height) / 2)
        dialog.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _invoke_callback(self, name, *args, default=None):
        fn = self.callbacks.get(name)
        if not fn:
            return default
        return fn(*args)

    def _save_config(self):
        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)

    def _clear_page(self):
        for child in self.page_container.winfo_children():
            child.destroy()

    def _render_breadcrumbs(self):
        for child in self.breadcrumb_frame.winfo_children():
            child.destroy()

        for index, item in enumerate(self._nav_stack):
            is_current = index == len(self._nav_stack) - 1
            if is_current:
                widget = ctk.CTkLabel(self.breadcrumb_frame, text=item["label"], text_color="#E2E8F0")
            else:
                widget = ctk.CTkButton(
                    self.breadcrumb_frame,
                    text=item["label"],
                    width=0,
                    fg_color="transparent",
                    hover_color="#1E293B",
                    text_color="#93C5FD",
                    command=lambda i=index: self._jump_to_breadcrumb(i),
                )
            widget.pack(side="left")
            if not is_current:
                ctk.CTkLabel(self.breadcrumb_frame, text="/", text_color="#64748B").pack(side="left", padx=4)

    def _jump_to_breadcrumb(self, index):
        if 0 <= index < len(self._nav_stack):
            self._nav_stack = self._nav_stack[: index + 1]
            self._animate_page_change(self._render_current_page)

    def _update_nav(self):
        self._render_breadcrumbs()
        if len(self._nav_stack) > 1:
            self.back_btn.grid()
        else:
            self.back_btn.grid_remove()

    def open_primary_page(self, route, label=None, *args):
        self._nav_stack = [{"route": "dashboard", "label": "Dashboard", "args": ()}]
        if route != "dashboard":
            self._nav_stack.append({"route": route, "label": label or route.title(), "args": args})
        self._animate_page_change(self._render_current_page)

    def navigate_to(self, route, label=None, *args):
        if route == "dashboard":
            self._nav_stack = [{"route": "dashboard", "label": "Dashboard", "args": ()}]
        else:
            self._nav_stack.append({"route": route, "label": label or route.title(), "args": args})
        self._animate_page_change(self._render_current_page)

    def go_back(self):
        if len(self._nav_stack) > 1:
            self._nav_stack.pop()
            self._animate_page_change(self._render_current_page)

    def _animate_page_change(self, render_action):
        if self._transitioning:
            render_action()
            return

        self._transitioning = True
        overlay = ctk.CTkFrame(self.content_area, fg_color="#0B1220", corner_radius=10)
        overlay.place(in_=self.page_container, relx=0, rely=0, relwidth=1, relheight=1)
        self.after(20, lambda: self._run_transition_mid(render_action, overlay))

    def _run_transition_mid(self, render_action, overlay):
        render_action()

        def _fade(step=0):
            if not overlay.winfo_exists():
                self._transitioning = False
                return
            colors = ["#0B1220", "#0E1726", "#111827"]
            if step < len(colors):
                overlay.configure(fg_color=colors[step])
                self.after(20, lambda: _fade(step + 1))
                return
            overlay.destroy()
            self._finish_transition()

        _fade()

    def _finish_transition(self):
        self._transitioning = False

    def _render_current_page(self, initial_status=None):
        self._clear_page()
        self._update_nav()
        current = self._nav_stack[-1]
        route = current["route"]
        args = current["args"]

        if route == "dashboard":
            status_text = initial_status
            if not status_text:
                status_text = self._last_status
            self._build_dashboard_page(status_text)
        elif route == "mappings":
            self._build_mappings_page(args[0])
        elif route == "mapping_editor":
            self._build_mapping_editor_page(*args)
        elif route == "token":
            self._build_token_page()
        elif route == "manual_upload":
            self._build_manual_upload_page()
        elif route == "convert":
            self._build_convert_page()
        elif route == "feedback":
            self._build_feedback_page()
        elif route == "help":
            self._build_help_page()
        else:
            self._build_dashboard_page(initial_status or "Notion: No Token")

    def _build_dashboard_page(self, initial_status):
        wrapper = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrapper.grid(row=0, column=0, sticky="nsew")
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_rowconfigure(3, weight=1)
        self._status_wrapper = wrapper

        ctk.CTkLabel(wrapper, text="System Status", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )

        status_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        status_row.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        status_row.grid_columnconfigure(0, weight=1)
        status_row.grid_columnconfigure(1, minsize=36)
        self._status_row = status_row

        self.status_panel = ctk.CTkLabel(
            status_row,
            text="NotionLink is running...",
            anchor="w",
            justify="left",
            corner_radius=8,
            height=58,
            wraplength=640,
            fg_color="#1e3a1e",
            text_color="#66ff66",
        )
        self.status_panel.grid(row=0, column=0, sticky="ew")

        self.ack_notice_btn = ctk.CTkButton(
            status_row,
            text="✔",
            width=34,
            height=34,
            corner_radius=8,
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#E2E8F0",
            fg_color="#374151",
            hover_color="#4B5563",
            command=self._acknowledge_transient_notice,
        )
        self.ack_notice_btn.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self.ack_notice_btn.grid_remove()
        CtkTooltip(self.ack_notice_btn, "Acknowledge this error message")

        self.after(0, self._refresh_status_wraplength)
        wrapper.bind("<Configure>", lambda _e: self._refresh_status_wraplength())

        action_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        action_row.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        action_row.grid_columnconfigure(0, weight=1)
        self.status_action_row = action_row

        button_strip = ctk.CTkFrame(action_row, fg_color="transparent")
        button_strip.grid(row=0, column=0, sticky="w")
        self.status_action_strip = button_strip

        self.reconnect_btn = ctk.CTkButton(button_strip, text="Retry Connection", width=140, command=self.callbacks.get("start_auto_retry_loop"))
        self.reconnect_btn.pack(in_=button_strip, side="left", padx=(0, 8))
        self.reconnect_btn.pack_forget()

        self.offline_btn = ctk.CTkButton(button_strip, text="Go Offline", width=120, command=self.callbacks.get("activate_offline_mode_manually"))
        self.offline_btn.pack(in_=button_strip, side="left", padx=(0, 8))
        self.offline_btn.pack_forget()

        action_row.grid_remove()

        mappings_box = ctk.CTkScrollableFrame(wrapper, fg_color="#0F172A", height=1, width=1)
        mappings_box.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        mappings_box.grid_columnconfigure(0, weight=1)

        all_items = []
        for mapping_type, key in (("page", "page_mappings"), ("database", "database_mappings")):
            kind = "Page" if mapping_type == "page" else "Database"
            for idx, mapping in enumerate(config.get(key, [])):
                all_items.append((mapping_type, idx, kind, mapping))

        if not all_items:
            ctk.CTkLabel(mappings_box, text="No mappings configured yet.", text_color="#94A3B8").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        else:
            for row_idx, (mapping_type, index, kind, mapping) in enumerate(all_items):
                row = ctk.CTkFrame(mappings_box, fg_color="#111827", corner_radius=8)
                row.pack(fill="x", padx=4, pady=4, anchor="n")
                row.grid_columnconfigure(0, weight=1)

                title = mapping.get("notion_title", "Untitled")
                enabled = bool(mapping.get("enabled", True))
                subtitle = f"{kind} mapping"
                ctk.CTkLabel(row, text=f"{title}\n{subtitle}", anchor="w", justify="left").grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)

                toggle_var = ctk.BooleanVar(value=enabled)

                def _toggle_mapping(mt=mapping_type, i=index, var=toggle_var):
                    key_name = "page_mappings" if mt == "page" else "database_mappings"
                    mappings = config.get(key_name, [])
                    if 0 <= i < len(mappings):
                        mappings[i]["enabled"] = bool(var.get())
                        self._save_config()
                        self._invoke_callback("restart_file_observer")

                toggle = ctk.CTkSwitch(row, text="", width=48, variable=toggle_var, command=_toggle_mapping)
                toggle.grid(row=0, column=1, padx=(2, 8), pady=8)
                CtkTooltip(toggle, "Enable or disable this mapping")

                spinner_state = {"running": False, "frame": 0}
                spinner_frames = ["⟳", "↻", "⟲", "↺"]

                def _tick_spinner():
                    if not spinner_state["running"] or not refresh.winfo_exists():
                        return
                    refresh.configure(text=spinner_frames[spinner_state["frame"] % len(spinner_frames)])
                    spinner_state["frame"] += 1
                    self.after(120, _tick_spinner)

                def _refresh_mapping(mt=mapping_type, i=index):
                    if spinner_state["running"]:
                        return
                    key_name = "page_mappings" if mt == "page" else "database_mappings"
                    mappings = config.get(key_name, [])
                    if not (0 <= i < len(mappings)):
                        return
                    current_mapping = mappings[i]
                    folders = list(current_mapping.get("folders", []))
                    if not folders:
                        return

                    spinner_state["running"] = True
                    spinner_state["frame"] = 0
                    refresh.configure(state="disabled")
                    _tick_spinner()

                    def _run_batch_upload():
                        try:
                            for folder_path in folders:
                                self._invoke_callback("upload_folder_blocking", folder_path, current_mapping, mt)
                        finally:
                            def _stop_spinner():
                                spinner_state["running"] = False
                                if refresh.winfo_exists():
                                    refresh.configure(text="⟳", state="normal")

                            self.after(0, _stop_spinner)

                    threading.Thread(target=_run_batch_upload, daemon=True).start()

                refresh = ctk.CTkButton(row, text="⟳", width=34, command=_refresh_mapping)
                refresh.grid(row=0, column=2, padx=(0, 8), pady=8)
                CtkTooltip(refresh, "Manual upload for all folders in this mapping")

            self.after(10, lambda: mappings_box._parent_canvas.yview_moveto(0.0))

        self.log_display = None
        self.apply_status_descriptor(self._last_status, self._last_descriptor)

    def _refresh_status_wraplength(self):
        if not hasattr(self, "status_panel") or not self.status_panel or not self.status_panel.winfo_exists():
            return

        available_width = 0
        if hasattr(self, "_status_wrapper") and self._status_wrapper and self._status_wrapper.winfo_exists():
            available_width = self._status_wrapper.winfo_width() - 64
        if available_width <= 1:
            available_width = self.status_panel.winfo_width() - 20

        # Keep wrapping bound to the current content width to avoid horizontal window growth.
        self.status_panel.configure(wraplength=max(260, available_width))

    def _set_ack_visible(self, visible):
        if not hasattr(self, "ack_notice_btn") or not self.ack_notice_btn or not self.ack_notice_btn.winfo_exists():
            return
        if visible:
            self.ack_notice_btn.grid()
        else:
            self.ack_notice_btn.grid_remove()

    def _update_status_action_row_visibility(self):
        if not hasattr(self, "status_action_row") or not self.status_action_row or not self.status_action_row.winfo_exists():
            return

        has_reconnect = hasattr(self, "reconnect_btn") and self.reconnect_btn and self.reconnect_btn.winfo_ismapped()
        has_offline = hasattr(self, "offline_btn") and self.offline_btn and self.offline_btn.winfo_ismapped()

        if has_reconnect or has_offline:
            self.status_action_row.grid()
        else:
            self.status_action_row.grid_remove()

    def _mapping_type_meta(self, mapping_type):
        return ("Page", "page_mappings") if mapping_type == "page" else ("Database", "database_mappings")

    def _build_mappings_page(self, mapping_type):
        title, key = self._mapping_type_meta(mapping_type)

        wrap = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(wrap, text=f"{title} Mappings", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))

        actions = ctk.CTkFrame(wrap, fg_color="transparent")
        actions.grid(row=1, column=0, sticky="e", pady=(0, 4))
        ctk.CTkButton(actions, text=f"Add {title} Mapping", command=lambda: self.navigate_to("mapping_editor", "New", mapping_type, None)).pack(side="left")

        list_box = ctk.CTkScrollableFrame(wrap, fg_color="#0F172A", height=1, width=1)
        list_box.grid(row=2, column=0, sticky="nsew")
        list_box.grid_columnconfigure(0, weight=1)
        list_box.grid_rowconfigure(999, weight=1)

        mappings = config.get(key, [])
        if not mappings:
            ctk.CTkLabel(list_box, text="No mappings configured yet.", text_color="#94A3B8").grid(row=0, column=0, sticky="w", padx=10, pady=10)
            return

        for index, mapping in enumerate(mappings):
            row = ctk.CTkFrame(list_box, fg_color="#111827", corner_radius=8)
            row.grid(row=index, column=0, sticky="ew", pady=5, padx=4)
            row.grid_columnconfigure(0, weight=1)
            label = mapping.get("notion_title", "Untitled")
            folder_count = len(mapping.get("folders", []))
            ctk.CTkLabel(row, text=f"{label}  ({folder_count} folder{'s' if folder_count != 1 else ''})", anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=10)
            ctk.CTkButton(row, text="Edit", width=80, command=lambda i=index, mt=mapping_type, l=label: self.navigate_to("mapping_editor", l, mt, i)).grid(row=0, column=1, padx=(6, 4), pady=8)
            ctk.CTkButton(row, text="Remove", width=90, fg_color="#7f1d1d", hover_color="#991b1b", command=lambda i=index: self._delete_mapping(mapping_type, i)).grid(row=0, column=2, padx=(4, 8), pady=8)

    def _delete_mapping(self, mapping_type, index):
        title, key = self._mapping_type_meta(mapping_type)
        try:
            mappings = config.get(key, [])
            if 0 <= index < len(mappings):
                del mappings[index]
                self._save_config()
                self._invoke_callback("restart_file_observer")
                self._render_current_page()
        except Exception as error:
            self._show_inline_error(f"Could not delete {title.lower()} mapping: {error}")

    def _build_mapping_editor_page(self, mapping_type, index):
        title, key = self._mapping_type_meta(mapping_type)
        existing = None
        if index is not None:
            mappings = config.get(key, [])
            if 0 <= index < len(mappings):
                existing = dict(mappings[index])

        wrap = ctk.CTkScrollableFrame(self.page_container, fg_color="#0F172A")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text=f"{title} Mapping Details", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 10))

        ctk.CTkLabel(wrap, text=f"Notion {title} Link or ID").grid(row=1, column=0, sticky="w", padx=10)
        notion_entry = ctk.CTkEntry(wrap, placeholder_text="https://www.notion.so/... or ID")
        notion_entry.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))

        ctk.CTkLabel(wrap, text="Display Title").grid(row=3, column=0, sticky="w", padx=10)
        name_entry = ctk.CTkEntry(wrap, placeholder_text="Friendly name")
        name_entry.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 8))

        ctk.CTkLabel(wrap, text="Folders to sync").grid(row=5, column=0, sticky="w", padx=10)
        folder_controls = ctk.CTkFrame(wrap, fg_color="transparent")
        folder_controls.grid(row=6, column=0, sticky="ew", padx=10, pady=(4, 8))
        folder_controls.grid_columnconfigure(0, weight=1)

        folder_list = ctk.CTkScrollableFrame(folder_controls, fg_color="#111827", height=170)
        folder_list.grid(row=0, column=0, sticky="nsew")
        folder_controls.grid_rowconfigure(0, weight=1)

        folder_values = list(existing.get("folders", [])) if existing else []

        def _folder_state(path):
            if not path or not os.path.isdir(path):
                return "red", "Folder does not exist"

            try:
                has_files = any(entry.is_file() for entry in os.scandir(path))
            except Exception:
                return "red", "Folder cannot be accessed"

            if has_files:
                return "green", "Folder exists and contains files"
            return "yellow", "Folder exists but has no files"

        def _dot_color(state):
            return {
                "green": _ICON_COLOR["green"],
                "red": _ICON_COLOR["red"],
                "yellow": _ICON_COLOR["yellow"],
            }.get(state, _ICON_COLOR["gray"])

        def _render_folders():
            for child in folder_list.winfo_children():
                child.destroy()

            if not folder_values:
                ctk.CTkLabel(folder_list, text="No folders added yet.", text_color="#94A3B8").pack(anchor="w", padx=10, pady=10)
                return

            for idx, path in enumerate(folder_values):
                row = ctk.CTkFrame(folder_list, fg_color="#0F172A", corner_radius=8)
                row.pack(fill="x", padx=4, pady=4)
                row.grid_columnconfigure(0, weight=1)

                ctk.CTkLabel(row, text=path, anchor="w").grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)
                state, tip = _folder_state(path)
                dot = ctk.CTkLabel(row, text="●", text_color=_dot_color(state))
                dot.grid(row=0, column=1, padx=(0, 8), pady=8)
                CtkTooltip(dot, tip)

                ctk.CTkButton(
                    row,
                    text="Remove",
                    width=78,
                    fg_color="#374151",
                    hover_color="#4B5563",
                    command=lambda i=idx: _remove_folder(i),
                ).grid(row=0, column=2, padx=(0, 8), pady=8)

        def _add_folder():
            folder_path = self._invoke_callback("browse_folder_for_mapping", default="")
            if folder_path and folder_path not in folder_values:
                folder_values.append(folder_path)
                _render_folders()

        def _remove_folder(index_to_remove):
            if 0 <= index_to_remove < len(folder_values):
                del folder_values[index_to_remove]
                _render_folders()

        ctk.CTkButton(folder_controls, text="Add Folder", command=_add_folder, fg_color="#059669", hover_color="#047857").grid(row=1, column=0, sticky="w", pady=(8, 0))
        _render_folders()

        ctk.CTkLabel(wrap, text="Ignore extensions (comma separated)").grid(row=7, column=0, sticky="w", padx=10)
        ignore_ext_entry = ctk.CTkEntry(wrap)
        ignore_ext_entry.grid(row=8, column=0, sticky="ew", padx=10, pady=(4, 8))

        ctk.CTkLabel(wrap, text="Ignore files/patterns (comma separated)").grid(row=9, column=0, sticky="w", padx=10)
        ignore_files_entry = ctk.CTkEntry(wrap)
        ignore_files_entry.grid(row=10, column=0, sticky="ew", padx=10, pady=(4, 8))

        discovery_var = ctk.BooleanVar(value=bool(existing.get("folder_discovery", False)) if existing else False)
        links_var = ctk.BooleanVar(value=bool(existing.get("folder_links", False)) if existing else False)
        lifecycle_var = ctk.BooleanVar(value=bool(existing.get("full_lifecycle_sync", True)) if existing else True)
        include_subfolders_cb = ctk.CTkCheckBox(wrap, text="Include subfolders", variable=discovery_var)
        include_subfolders_cb.grid(row=11, column=0, sticky="w", padx=10, pady=(2, 2))
        add_subfolder_links_cb = ctk.CTkCheckBox(wrap, text="Add subfolder links", variable=links_var)
        add_subfolder_links_cb.grid(row=12, column=0, sticky="w", padx=10, pady=2)
        lifecycle_cb = ctk.CTkCheckBox(wrap, text="Enable lifecycle sync (rename/delete)", variable=lifecycle_var)
        lifecycle_cb.grid(row=13, column=0, sticky="w", padx=10, pady=(2, 8))

        CtkTooltip(include_subfolders_cb, "Watches and syncs files from nested subfolders.")
        CtkTooltip(add_subfolder_links_cb, "Adds link entries for subfolders in Notion.")
        CtkTooltip(lifecycle_cb, "Syncs delete and rename events to Notion.")

        message_label = ctk.CTkLabel(wrap, text="", text_color="#FCA5A5", anchor="w")
        message_label.grid(row=14, column=0, sticky="ew", padx=10, pady=(0, 8))

        if existing:
            notion_entry.insert(0, existing.get("notion_id", ""))
            name_entry.insert(0, existing.get("notion_title", ""))
            ignore_ext_entry.insert(0, ", ".join(existing.get("ignore_extensions", ["*.tmp", ".*", "desktop.ini"])))
            ignore_files_entry.insert(0, ", ".join(existing.get("ignore_files", [])))
        else:
            ignore_ext_entry.insert(0, "*.tmp, .*, desktop.ini")

        def save_mapping():
            notion_id = notion_entry.get().strip()
            notion_title = name_entry.get().strip()
            folders = [f.strip() for f in folder_values if f.strip()]
            ignore_ext = [p.strip() for p in ignore_ext_entry.get().split(",") if p.strip()]
            ignore_files = [p.strip() for p in ignore_files_entry.get().split(",") if p.strip()]

            if not notion_id:
                message_label.configure(text="Please provide a Notion link or ID.")
                return
            if not notion_title:
                notion_title = f"Untitled (...{notion_id[-6:]})" if len(notion_id) >= 6 else "Untitled"
            if not folders:
                message_label.configure(text="Add at least one folder path.")
                return

            mapping_data = {
                "notion_title": notion_title,
                "notion_id": notion_id,
                "folders": folders,
                "ignore_extensions": ignore_ext,
                "ignore_files": ignore_files,
                "full_lifecycle_sync": bool(lifecycle_var.get()),
                "folder_discovery": bool(discovery_var.get()),
                "folder_links": bool(links_var.get()),
            }

            try:
                if key not in config:
                    config[key] = []
                if index is None:
                    config[key].append(mapping_data)
                else:
                    config[key][index] = mapping_data
                self._save_config()
                self._invoke_callback("restart_file_observer")
                for folder_path in mapping_data.get("folders", []):
                    self._invoke_callback("queue_upload", folder_path, mapping_data, mapping_type)

                self.go_back()
            except Exception as error:
                message_label.configure(text=f"Could not save mapping: {error}")

        buttons = ctk.CTkFrame(wrap, fg_color="transparent")
        buttons.grid(row=15, column=0, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkButton(buttons, text="Back", fg_color="#374151", hover_color="#4B5563", command=self.go_back).pack(side="left")
        ctk.CTkButton(buttons, text="Save Mapping", fg_color="#059669", hover_color="#047857", command=save_mapping).pack(side="right")

    def _build_token_page(self):
        wrap = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="Notion Token", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ctk.CTkLabel(wrap, text="Paste your Notion internal integration token.", text_color="#94A3B8").grid(row=1, column=0, sticky="w", pady=(0, 6))

        entry = ctk.CTkEntry(wrap, show="*", height=40)
        entry.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        entry.insert(0, config.get("notion_token", ""))

        message = ctk.CTkLabel(wrap, text="", anchor="w", text_color="#FCA5A5")
        message.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        def save_token():
            token = entry.get().strip()
            try:
                if not token or "PLEASE_ENTER" in token or len(token) < 50:
                    raise ValueError("That does not look like a valid token. Please paste the full secret token.")
                config["notion_token"] = token
                self._save_config()
                self._invoke_callback("run_status_check_thread")
                message.configure(text="Token updated.", text_color="#86EFAC")
            except Exception as error:
                message.configure(text=str(error), text_color="#FCA5A5")

        ctk.CTkButton(wrap, text="Save Token", command=save_token, fg_color="#059669", hover_color="#047857").grid(row=4, column=0, sticky="e")

    def _build_manual_upload_page(self):
        wrap = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(wrap, text="Manual Upload", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        list_box = ctk.CTkScrollableFrame(wrap, fg_color="#0F172A")
        list_box.grid(row=1, column=0, sticky="nsew")

        options = []
        for mapping_type, mapping in [
            ("page", pm) for pm in config.get("page_mappings", [])
        ] + [
            ("database", dbm) for dbm in config.get("database_mappings", [])
        ]:
            type_label = "Page" if mapping_type == "page" else "Database"
            for folder_path in mapping.get("folders", []):
                options.append((f"[{type_label}] {mapping.get('notion_title', 'Untitled')} - {os.path.basename(folder_path)}", folder_path, mapping, mapping_type))

        if not options:
            ctk.CTkLabel(list_box, text="No mappings configured.", text_color="#94A3B8").pack(anchor="w", padx=10, pady=10)
            return

        status = ctk.CTkLabel(wrap, text="", anchor="w", text_color="#86EFAC")
        status.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        for label, folder, mapping, mapping_type in options:
            row = ctk.CTkFrame(list_box, fg_color="#111827", corner_radius=8)
            row.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left", padx=10, pady=8, expand=True, fill="x")

            def start_upload(f=folder, m=mapping, mt=mapping_type):
                self._invoke_callback("queue_upload", f, m, mt)
                status.configure(text=f"Upload started for {os.path.basename(f)}")

            ctk.CTkButton(row, text="Start", width=80, command=start_upload).pack(side="right", padx=8, pady=8)

    def _build_convert_page(self):
        wrap = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="Convert Path to Link", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        entry = ctk.CTkEntry(wrap, height=40, placeholder_text="Paste absolute file path")
        entry.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        if clip.paste():
            entry.insert(0, clip.paste().replace('"', ""))

        out = ctk.CTkTextbox(wrap, height=90)
        out.grid(row=3, column=0, sticky="ew", pady=(8, 8))
        out.configure(state="disabled")

        def set_output(text):
            out.configure(state="normal")
            out.delete("1.0", "end")
            out.insert("1.0", text)
            out.configure(state="disabled")

        def browse_file():
            path = self._invoke_callback("browse_file_for_convert", default="")
            if path:
                entry.delete(0, "end")
                entry.insert(0, path)

        def convert_now():
            path_to_convert = entry.get().strip()
            if not path_to_convert:
                set_output("Please provide a file path.")
                return
            port = config.get("server_port")
            server_host = config.get("server_host")
            url_path = path_to_convert.replace("\\", "/")
            if url_path.startswith("/"):
                url_path = url_path[1:]
            result = f"{server_host}:{port}/{url_path}"
            clip.copy(result)
            set_output(f"Copied to clipboard:\n{result}")

        actions = ctk.CTkFrame(wrap, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew")
        ctk.CTkButton(actions, text="Browse", width=100, command=browse_file).pack(side="left")
        ctk.CTkButton(actions, text="Convert", width=100, command=convert_now).pack(side="left", padx=8)

    def _build_feedback_page(self):
        wrap = ctk.CTkFrame(self.page_container, fg_color="transparent")
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="Send Feedback", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        feedback = ctk.CTkTextbox(wrap, height=180)
        feedback.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        discord = ctk.CTkEntry(wrap, placeholder_text="Discord name (optional)")
        discord.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        msg = ctk.CTkLabel(wrap, text="", anchor="w")
        msg.grid(row=3, column=0, sticky="ew", pady=(0, 8))

        def submit():
            text = feedback.get("1.0", "end").strip()
            name = discord.get().strip()
            if not text:
                msg.configure(text="Please enter feedback.", text_color="#FCA5A5")
                return
            sent = self._invoke_callback("send_feedback", text, name, default=False)
            if sent:
                msg.configure(text="Feedback sent. Thank you!", text_color="#86EFAC")
            else:
                msg.configure(text="Feedback service unavailable (Sentry disabled).", text_color="#FDE68A")

        ctk.CTkButton(wrap, text="Send", fg_color="#059669", hover_color="#047857", command=submit).grid(row=4, column=0, sticky="e")

    def _build_help_page(self):
        wrap = ctk.CTkFrame(self.page_container, fg_color="#0F172A", corner_radius=10)
        wrap.grid(row=0, column=0, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="Help & Docs", font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        notes = (
            "- Keep the app running so localhost links resolve.\n"
            "- Share Notion pages with your integration.\n"
            "- In offline mode, use Convert Path to Link and paste manually."
        )
        ctk.CTkLabel(wrap, text=notes, anchor="w", justify="left", text_color="#CBD5E1").grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
        ctk.CTkButton(wrap, text="Open Full Wiki", command=lambda: webbrowser.open_new("https://github.com/wladermisch/NotionLink/wiki")).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

    def _show_inline_error(self, message):
        self._clear_page()
        err = ctk.CTkFrame(self.page_container, fg_color="#3F1D1D", corner_radius=10)
        err.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(err, text=message, text_color="#FCA5A5", justify="left", anchor="w").pack(fill="x", padx=12, pady=12)

    def _acknowledge_transient_notice(self):
        self._transient_notice_active = False
        self._transient_notice_message = ""
        self._transient_notice_level = "warning"
        ack_cb = self.callbacks.get("acknowledge_transient_notice")
        if ack_cb:
            ack_cb()
        self.apply_status_descriptor(self._last_status, self._last_descriptor)

    def show_transient_notice(self, message, level="warning"):
        self._transient_notice_active = True
        self._transient_notice_message = message
        self._transient_notice_level = level

        if not hasattr(self, "status_panel") or not self.status_panel or not self.status_panel.winfo_exists():
            return

        if level == "error":
            bg_color, text_color = "#4a1a1a", "#ffb4b4"
        else:
            bg_color, text_color = "#4a3a1a", "#ffdd8a"

        self.status_panel.configure(text=message, fg_color=bg_color, text_color=text_color)
        self._refresh_status_wraplength()

        if hasattr(self, "ack_notice_btn") and self.ack_notice_btn and self.ack_notice_btn.winfo_exists():
            self._set_ack_visible(True)
        self._update_status_action_row_visibility()

    def set_autostart_checked(self, checked):
        self.autostart_var.set(bool(checked))

    def set_sentry_checked(self, checked):
        self.sentry_var.set(bool(checked))

    def append_log_line(self, line):
        if not hasattr(self, "log_display") or self.log_display is None or not self.log_display.winfo_exists():
            return
        self.log_display.configure(state="normal")
        self.log_display.insert("end", f"{line}\n")
        self.log_display.see("end")
        self.log_display.configure(state="disabled")

    def apply_status_descriptor(self, status, descriptor):
        self._last_status = status
        self._last_descriptor = dict(descriptor or {})

        icon_key = descriptor.get("icon", "yellow")
        if hasattr(self, "footer_status_label") and self.footer_status_label and self.footer_status_label.winfo_exists():
            self.footer_status_label.configure(text=self._compact_status_text(status))
        if hasattr(self, "footer_status_icon") and self.footer_status_icon and self.footer_status_icon.winfo_exists():
            self.footer_status_icon.configure(text="●", text_color=_ICON_COLOR.get(icon_key, _ICON_COLOR["yellow"]))

        if not hasattr(self, "reconnect_btn") or not self.reconnect_btn or not self.reconnect_btn.winfo_exists():
            return

        persistent_issue = status in {
            "Notion: Connection Error",
            "Notion: Disconnected",
            "Notion: Invalid Token",
            "Notion: Access Denied",
            "Notion: Offline Mode",
            "Notion: No Token",
        }

        if self._transient_notice_active and not persistent_issue:
            self.show_transient_notice(self._transient_notice_message, self._transient_notice_level)
            return

        if persistent_issue and self._transient_notice_active:
            self._transient_notice_active = False
            self._transient_notice_message = ""
            self._transient_notice_level = "warning"

        if hasattr(self, "ack_notice_btn") and self.ack_notice_btn and self.ack_notice_btn.winfo_exists():
            self._set_ack_visible(False)

        reconnect_visible = bool(descriptor.get("reconnect_visible"))
        offline_visible = bool(descriptor.get("offline_visible"))

        if reconnect_visible:
            self.reconnect_btn.configure(text=descriptor.get("reconnect_text", "Retry Connection"))
            self.reconnect_btn.pack_forget()
            self.reconnect_btn.pack(in_=self.status_action_strip, side="left", padx=(0, 8))
        else:
            self.reconnect_btn.pack_forget()

        if offline_visible:
            self.offline_btn.pack_forget()
            self.offline_btn.pack(in_=self.status_action_strip, side="left", padx=(0, 8))
        else:
            self.offline_btn.pack_forget()

        self._update_status_action_row_visibility()

        panel_text = descriptor.get("panel_text")
        panel_colors = descriptor.get("panel_colors")
        if panel_text is not None and panel_colors is not None:
            bg_color, text_color, _ = panel_colors
            self.status_panel.configure(text=panel_text, fg_color=bg_color, text_color=text_color)
            self._refresh_status_wraplength()

    def show_intro_overlay(self, app_version, duration_ms=2200):
        self.update_idletasks()

        overlay = ctk.CTkFrame(self, corner_radius=0, fg_color="#0B1220")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        center = ctk.CTkFrame(overlay, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")

        logo = self._load_logo((128, 128))
        if logo is not None:
            logo_label = ctk.CTkLabel(center, text="", image=logo)
            logo_label.image = logo
        else:
            logo_label = ctk.CTkLabel(center, text="NL", font=ctk.CTkFont(size=56, weight="bold"), text_color="#6EE7B7")
        logo_label.pack(pady=(0, 14))

        title_label = ctk.CTkLabel(
            center,
            text="NotionLink",
            font=ctk.CTkFont(size=34, weight="bold"),
            text_color="#E5E7EB",
        )
        title_label.pack()

        subtitle_label = ctk.CTkLabel(
            center,
            text=f"v{app_version}",
            font=ctk.CTkFont(size=14),
            text_color="#94A3B8",
        )
        subtitle_label.pack(pady=(6, 0))

        def animate_breath(step=0):
            if not overlay.winfo_exists():
                return
            brightness = 225 + int(20 * abs((step % 24) - 12) / 12)
            title_label.configure(text_color=f"#{brightness:02X}{brightness:02X}{brightness:02X}")
            overlay.after(60, lambda: animate_breath(step + 1))

        def fade_out_intro(step=0):
            if not overlay.winfo_exists():
                return
            shades = ["#0B1220", "#0F172A", "#111827"]
            if step < len(shades):
                overlay.configure(fg_color=shades[step])
                overlay.after(35, lambda: fade_out_intro(step + 1))
                return

            overlay.destroy()

        animate_breath()
        overlay.after(duration_ms, fade_out_intro)


class CtkDashboardBridge:
    """Runs CustomTkinter dashboard in its own thread and exposes a main-thread-safe API."""

    def __init__(self, tray_app, show_intro=False):
        self.tray_app = tray_app
        self.show_intro = show_intro
        self._thread = None
        self._ready = threading.Event()
        self._queue = queue.Queue()
        self._window = None
        self._closed = False

    def show(self):
        if self._thread is None or not self._thread.is_alive():
            self._closed = False
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self._ready.wait(timeout=3)
        self._enqueue(self._show_window)

    def close(self, wait=False, timeout=2.5):
        if self._thread and self._thread.is_alive():
            self._enqueue(self._destroy_window)
            if wait:
                self._thread.join(timeout=max(0.1, float(timeout)))

    def navigate_to(self, route, *args):
        self._enqueue(self._navigate_to, route, *args)

    def update_token_status(self, status):
        descriptor = self.tray_app.ui_controller.get_status_descriptor(status)
        self._enqueue(self._apply_status, status, descriptor)

    def update_status_panel_error(self, message):
        descriptor = {
            "icon": "red",
            "reconnect_visible": False,
            "reconnect_text": "Retry Connection",
            "offline_visible": False,
            "panel_text": f"[ERROR] {message}",
            "panel_colors": ("#4a1a1a", "#ff6666", "#6a2a2a"),
        }
        self._enqueue(self._apply_status, self.tray_app.current_token_status, descriptor)

    def update_status_panel_warning(self, message):
        descriptor = {
            "icon": "yellow",
            "reconnect_visible": False,
            "reconnect_text": "Retry Connection",
            "offline_visible": False,
            "panel_text": message,
            "panel_colors": ("#4a4a1a", "#ffff66", "#6a6a2a"),
        }
        self._enqueue(self._apply_status, self.tray_app.current_token_status, descriptor)

    def reset_status_panel(self):
        self.update_token_status(self.tray_app.current_token_status)

    def append_log_line(self, line):
        self._enqueue(self._append_log_line, line)

    def show_transient_notice(self, message, level="warning"):
        self._enqueue(self._show_transient_notice, message, level)

    def set_autostart_checked(self, checked):
        self._enqueue(self._set_autostart_checked, checked)

    def set_sentry_checked(self, checked):
        self._enqueue(self._set_sentry_checked, checked)

    def _run(self):
        callbacks = {
            "toggle_autostart": lambda checked: self._invoke_main(self.tray_app.toggle_autostart, checked),
            "toggle_sentry": lambda checked: self._invoke_main(self.tray_app.toggle_sentry, checked),
            "quit_app": lambda: self._invoke_main(self.tray_app.quit_app),
            "acknowledge_transient_notice": lambda: self._invoke_main(self.tray_app.acknowledge_dashboard_notice),
            "get_status_descriptor": lambda status: self.tray_app.ui_controller.get_status_descriptor(status),
            "start_auto_retry_loop": lambda: self._invoke_main(self.tray_app.start_auto_retry_loop),
            "activate_offline_mode_manually": lambda: self._invoke_main(self.tray_app.activate_offline_mode_manually),
            "restart_file_observer": lambda: self._invoke_main(self.tray_app.restart_file_observer),
            "queue_upload": lambda folder_path, mapping_data, mapping_type: self._invoke_main(
                lambda: threading.Thread(
                    target=self.tray_app.upload_folder_to_notion,
                    args=(folder_path, mapping_data, mapping_type),
                    daemon=True,
                ).start()
            ),
            "run_status_check_thread": lambda: self._invoke_main(self.tray_app.run_status_check_thread),
            "send_feedback": lambda feedback, discord_name: self._invoke_main_sync(self.tray_app.send_feedback, feedback, discord_name),
            "browse_file_for_convert": lambda: self._invoke_main_sync(self.tray_app.browse_file_for_convert),
            "browse_folder_for_mapping": lambda: self._invoke_main_sync(self.tray_app.browse_folder_for_mapping),
            "upload_folder_blocking": lambda folder_path, mapping_data, mapping_type: self.tray_app.upload_folder_to_notion(
                folder_path,
                mapping_data,
                mapping_type,
                suppress_notifications=True,
            ),
        }

        self._window = CtkDashboardWindow(callbacks, APP_VERSION, initial_status=self.tray_app.current_token_status)
        self._window.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._window.set_autostart_checked(config.get("autostart_with_windows", False))
        self._window.set_sentry_checked(config.get("sentry_enabled", False))
        if self.show_intro:
            self._window.show_intro_overlay(APP_VERSION)
        self._start_queue_pump()
        self._ready.set()
        self._window.mainloop()
        self._window = None
        self._closed = True
        self._invoke_main(self.tray_app.on_dashboard_closed)

    def _start_queue_pump(self):
        def _pump():
            if not self._window:
                return

            while True:
                try:
                    action, args = self._queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    action(*args)
                except Exception as error:
                    print(f"CTk dashboard action failed: {error}")

            if self._window:
                self._window.after(50, _pump)

        self._window.after(50, _pump)

    def _enqueue(self, action, *args):
        if not self._closed:
            self._queue.put((action, args))

    def _invoke_main(self, fn, *args):
        self.tray_app.invoke_on_main_thread(lambda: fn(*args))

    def _invoke_main_sync(self, fn, *args):
        done = threading.Event()
        result = {"value": None, "error": None}

        def _runner():
            try:
                result["value"] = fn(*args)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        self.tray_app.invoke_on_main_thread(_runner)
        done.wait(timeout=5)
        if result["error"]:
            raise result["error"]
        return result["value"]

    def _show_window(self):
        if not self._window:
            return
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()

    def _apply_status(self, status, descriptor):
        if self._window:
            self._window.apply_status_descriptor(status, descriptor)

    def _append_log_line(self, line):
        if self._window:
            self._window.append_log_line(line)

    def _show_transient_notice(self, message, level="warning"):
        if self._window:
            self._window.show_transient_notice(message, level)

    def _set_autostart_checked(self, checked):
        if self._window:
            self._window.set_autostart_checked(checked)

    def _set_sentry_checked(self, checked):
        if self._window:
            self._window.set_sentry_checked(checked)

    def _on_window_close(self):
        self._destroy_window()

    def _navigate_to(self, route, *args):
        if self._window:
            if route == "mappings" and args:
                mapping_type = args[0]
                label = "Page Mappings" if mapping_type == "page" else "Database Mappings"
                self._window.open_primary_page("mappings", label, mapping_type)
                return
            label_map = {
                "token": "Notion Token",
                "manual_upload": "Manual Upload",
                "convert": "Convert Path",
                "feedback": "Feedback",
                "help": "Help",
            }
            self._window.open_primary_page(route, label_map.get(route, route.title()), *args)

    def _destroy_window(self):
        if self._window:
            try:
                self._window.quit()
            except Exception:
                pass
            self._window.destroy()


def show_startup_brand_splash(app_version, duration_ms=2200):
    """Blocking startup splash used before initial setup on first run."""
    splash = ctk.CTk()
    try:
        splash.withdraw()
        splash.overrideredirect(True)
        splash.configure(fg_color="#0B1220")
        try:
            splash.iconbitmap(default=TRAY_ICON_ICO)
        except Exception:
            pass

        container = ctk.CTkFrame(splash, corner_radius=14, fg_color="#111827", border_width=1, border_color="#1F2937")
        container.pack(fill="both", expand=True, padx=12, pady=12)

        center = ctk.CTkFrame(container, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")

        logo_image = None
        try:
            logo_path = resource_path("assets/logo.png")
            pil_logo = Image.open(logo_path)
            logo_image = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(120, 120))
        except Exception as logo_error:
            print(f"Failed to load startup splash logo: {logo_error}")

        if logo_image is not None:
            logo_label = ctk.CTkLabel(center, text="", image=logo_image)
            logo_label.image = logo_image
        else:
            logo_label = ctk.CTkLabel(center, text="NL", font=ctk.CTkFont(size=56, weight="bold"), text_color="#6EE7B7")
        logo_label.pack(pady=(0, 10))

        ctk.CTkLabel(center, text="NotionLink", font=ctk.CTkFont(size=34, weight="bold"), text_color="#E5E7EB").pack()
        ctk.CTkLabel(center, text=f"v{app_version}", font=ctk.CTkFont(size=14), text_color="#94A3B8").pack(pady=(6, 0))

        splash.update_idletasks()
        req_w = max(splash.winfo_reqwidth(), 420)
        req_h = max(splash.winfo_reqheight(), 320)
        width = req_w + 8
        height = req_h + 8

        x = 0
        y = 0
        centered = False
        if sys.platform.startswith("win"):
            try:
                monitor_from_point = ctypes.windll.user32.MonitorFromPoint
                get_monitor_info = ctypes.windll.user32.GetMonitorInfoW

                class _POINT(wintypes.POINT):
                    pass

                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                class _MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", _RECT),
                        ("rcWork", _RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                pointer = _POINT(splash.winfo_pointerx(), splash.winfo_pointery())
                monitor = monitor_from_point(pointer, 2)  # MONITOR_DEFAULTTONEAREST
                info = _MONITORINFO()
                info.cbSize = ctypes.sizeof(_MONITORINFO)

                if monitor and get_monitor_info(monitor, ctypes.byref(info)):
                    work_w = max(info.rcWork.right - info.rcWork.left, width)
                    work_h = max(info.rcWork.bottom - info.rcWork.top, height)
                    x = info.rcWork.left + int((work_w - width) / 2)
                    y = info.rcWork.top + int((work_h - height) / 2)
                    centered = True
            except Exception:
                centered = False

        if not centered:
            screen_w = splash.winfo_screenwidth()
            screen_h = splash.winfo_screenheight()
            width = min(width, max(320, screen_w - 80))
            height = min(height, max(240, screen_h - 80))
            x = max(int((screen_w - width) / 2), 0)
            y = max(int((screen_h - height) / 2), 0)

        splash.geometry(f"{width}x{height}+{x}+{y}")
        splash.deiconify()

        splash.after(duration_ms, splash.destroy)
        splash.mainloop()
    finally:
        try:
            splash.destroy()
        except Exception:
            pass
