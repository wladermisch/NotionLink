# Main app runtime wiring: pystray tray + CustomTkinter dashboard
import fnmatch
import json
import os
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox
from tkinter import filedialog

from notion_client import Client
from watchdog.observers import Observer

from ..core import (
    APP_VERSION,
    config,
    config_file_path,
    httpd,
    is_user_error,
    logger,
    notification_batch,
    notified_errors,
    observer,
    sentry_sdk,
)
from ..events import EventSignal
from ..notion import (
    check_notion_status_once,
    get_existing_links,
    process_pending_uploads,
    sync_file_to_notion,
)
from ..server import NotionFileHandler, manage_autostart, TRAY_ICON_ICO
from .dashboard import CtkDashboardBridge
from .dialogs import (
    DialogResult,
    LogWatcher,
    UpdateAvailableDialog,
    UpdateCheckThread,
)
from .tray import PystrayTrayBackend, TrayIconCompat


class NotionLinkUIController:
    def __init__(self, config_obj, config_path, manage_autostart_fn):
        self.config = config_obj
        self.config_path = config_path
        self.manage_autostart = manage_autostart_fn

    def _save_config(self):
        with open(self.config_path, "w") as config_file:
            json.dump(self.config, config_file, indent=4)

    def set_autostart(self, checked):
        self.manage_autostart(checked)
        self.config["autostart_with_windows"] = checked
        self._save_config()

    def set_sentry(self, checked):
        self.config["sentry_enabled"] = checked
        self._save_config()

    def get_status_descriptor(self, status):
        descriptor = {
            "icon": "yellow",
            "reconnect_visible": False,
            "reconnect_text": "Retry Connection",
            "offline_visible": False,
            "panel_text": None,
            "panel_colors": None,
        }

        if status == "Notion: Connected":
            descriptor["icon"] = "green"
            descriptor["panel_text"] = "NotionLink is running..."
            descriptor["panel_colors"] = ("#1e3a1e", "#66ff66", "#2e5a2e")
        elif status == "Notion: Connection Error":
            descriptor["icon"] = "red"
            descriptor["reconnect_visible"] = True
            descriptor["offline_visible"] = True
            descriptor["panel_text"] = "Connection Failed. Please check your internet connection or Notion token."
            descriptor["panel_colors"] = ("#4a3a1a", "#ffcc66", "#6a5a2a")
        elif status in ["Notion: Disconnected", "Notion: Invalid Token", "Notion: Access Denied"]:
            descriptor["reconnect_visible"] = status == "Notion: Disconnected"
            descriptor["icon"] = "red" if status == "Notion: Disconnected" else "yellow"
            descriptor["panel_text"] = status
            descriptor["panel_colors"] = ("#4a1a1a", "#ff6666", "#6a2a2a")
        elif status == "Notion: Offline Mode":
            descriptor["icon"] = "gray"
            descriptor["reconnect_visible"] = True
            descriptor["reconnect_text"] = "Reconnect Now"
            descriptor["panel_text"] = "Offline Mode Active. Sync is paused."
            descriptor["panel_colors"] = ("#333333", "#aaaaaa", "#555555")
        elif status == "Notion: No Token":
            descriptor["icon"] = "gray"
            descriptor["panel_text"] = f"{status} - Please configure your Notion token"
            descriptor["panel_colors"] = ("#4a3a1a", "#ffcc66", "#6a5a2a")

        return descriptor


class RepeatingTimer:
    def __init__(self, interval_seconds, callback):
        self.interval = interval_seconds
        self.callback = callback
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.callback()
            except Exception as e:
                print(f"RepeatingTimer callback failed: {e}")

    def stop(self):
        self._stop.set()


class NotionLinkTrayApp:
    def __init__(self, app):
        self.app = app

        self.status_updated = EventSignal()
        self.server_error_signal = EventSignal()
        self.user_error_signal = EventSignal()
        self.op_success_signal = EventSignal()
        self.offline_mode_signal = EventSignal()

        self.ui_controller = NotionLinkUIController(config, config_file_path, manage_autostart)

        self.dashboard_window = None
        self.dashboard_log_watcher = None
        self._dashboard_intro_pending = True
        self._dashboard_transient_notice = None

        self.current_token_status = "Notion: No Token"
        self.status_check_timer = None
        self.auto_retry_timer = None
        self.notification_timer = None
        self.notification_timer_lock = threading.Lock()
        self.is_auto_retrying = False

        self.tray_backend = None
        self.tray_icon = None

        self.status_updated.connect(self.update_status_ui)
        self.offline_mode_signal.connect(self.on_offline_mode_activated)
        self.user_error_signal.connect(self.on_user_error)

        self._init_pystray_backend()

        self.update_thread = UpdateCheckThread()
        self.update_thread.update_available.connect(self.show_update_dialog)
        threading.Timer(3.0, self.update_thread.start).start()

    def invoke_on_main_thread(self, fn):
        # Keep compatibility name used by bridge; now routed to app main dispatcher.
        self.app.invoke(fn)

    def _init_pystray_backend(self):
        callbacks = {
            "manual_status_check": lambda: self.invoke_on_main_thread(self.manual_status_check),
            "show_dashboard": lambda: self.invoke_on_main_thread(self.show_dashboard),
            "show_convert_path": lambda: self.invoke_on_main_thread(self.show_convert_path),
            "show_manual_upload": lambda: self.invoke_on_main_thread(self.show_manual_upload),
            "quit_app": lambda: self.invoke_on_main_thread(self.quit_app),
        }
        self.tray_backend = PystrayTrayBackend(TRAY_ICON_ICO, callbacks)
        self.tray_backend.start()
        self.tray_icon = TrayIconCompat(self.tray_backend)

    def show_update_dialog(self, latest_version, url):
        dialog = UpdateAvailableDialog(APP_VERSION, latest_version, url)
        dialog.exec()

    def reset_notification_timer(self):
        with self.notification_timer_lock:
            if self.notification_timer:
                self.notification_timer.cancel()
            self.notification_timer = threading.Timer(5.0, self.process_notification_batch)
            self.notification_timer.daemon = True
            self.notification_timer.start()

    def process_notification_batch(self):
        global notification_batch
        if not notification_batch:
            return

        batch_snapshot = dict(notification_batch)
        notification_batch.clear()

        print(f"Processing notification batch with {len(batch_snapshot)} entries...")
        for notion_title, filenames in batch_snapshot.items():
            count = len(filenames)
            if count == 1:
                message = f"'{filenames[0]}' was added to {notion_title}."
            else:
                message = f"Synced {count} new files to {notion_title}."
            self.tray_icon.showMessage("NotionLink: Sync Success", message, None, 3000)

    def run_status_check_thread(self):
        def check_and_update():
            check_notion_status_once(self.update_status_ui_from_thread)

        threading.Thread(target=check_and_update, daemon=True).start()

        if not self.status_check_timer:
            self.status_check_timer = RepeatingTimer(300, self.run_status_check_thread)
            self.status_check_timer.start()

    def update_status_ui_from_thread(self, status):
        self.status_updated.emit(status)

    def manual_status_check(self):
        def _check():
            try:
                self.status_updated.emit("Notion: Checking...")
                check_notion_status_once(self.update_status_ui_from_thread, force=True)
            except Exception as e:
                print(f"Manual status check failed: {e}")
                self.status_updated.emit("Notion: Disconnected")

        threading.Thread(target=_check, daemon=True).start()

    def start_auto_retry_loop(self):
        if self.is_auto_retrying:
            return

        print("Starting auto-retry loop...")
        self.is_auto_retrying = True
        self.status_updated.emit("Notion: Retrying...")

        import src.core as core_module
        core_module.offline_mode = False

        def _retry_check():
            if not self.is_auto_retrying:
                return

            def _callback(status):
                if status == "Notion: Connected":
                    print("Auto-retry successful! Connection restored.")
                    self.is_auto_retrying = False
                    self.status_updated.emit(status)
                    self.tray_icon.showMessage("NotionLink", "Internet connection restored.", None, 3000)
                    process_pending_uploads()
                    if self.auto_retry_timer:
                        self.auto_retry_timer.stop()
                        self.auto_retry_timer = None
                else:
                    print(f"Auto-retry failed ({status}). Retrying in 10s...")
                    if status not in ["Notion: Connection Error", "Notion: Disconnected"]:
                        self.status_updated.emit(status)

            check_notion_status_once(_callback, force=True)

        self.auto_retry_timer = RepeatingTimer(10, lambda: threading.Thread(target=_retry_check, daemon=True).start())
        self.auto_retry_timer.start()
        threading.Thread(target=_retry_check, daemon=True).start()

    def update_status_ui(self, status):
        self.current_token_status = status
        if self.tray_backend:
            self.tray_backend.update_status(status)
        if self.dashboard_window:
            self.dashboard_window.update_token_status(status)

    def on_user_error(self, message):
        self._dashboard_transient_notice = message
        if self.dashboard_window:
            self.dashboard_window.show_transient_notice(message, "warning")

    def acknowledge_dashboard_notice(self):
        self._dashboard_transient_notice = None

    def sync_autostart_ui(self, is_checked):
        if self.dashboard_window and hasattr(self.dashboard_window, "set_autostart_checked"):
            self.dashboard_window.set_autostart_checked(is_checked)

    def toggle_autostart(self, checked):
        print(f"Setting autostart to: {checked}")
        try:
            self.ui_controller.set_autostart(checked)
            self.sync_autostart_ui(checked)
        except Exception as e:
            print(f"Error toggling autostart: {e}")
            self.sync_autostart_ui(not checked)
            root = tk.Tk()
            root.withdraw()
            try:
                messagebox.showerror("Autostart Error", f"Could not update autostart setting.\nError: {e}")
            finally:
                root.destroy()

    def sync_sentry_ui(self, is_checked):
        if self.dashboard_window and hasattr(self.dashboard_window, "set_sentry_checked"):
            self.dashboard_window.set_sentry_checked(is_checked)

    def toggle_sentry(self, checked):
        print(f"Setting Sentry to: {checked}")
        try:
            self.ui_controller.set_sentry(checked)
            self.sync_sentry_ui(checked)
            if checked:
                print("Sentry enabled. Will take effect on next restart.")
            else:
                print("Sentry disabled. Will take effect on next restart.")
        except Exception as e:
            print(f"Error toggling Sentry: {e}")
            self.sync_sentry_ui(not checked)

    def show_help(self):
        root = tk.Tk()
        root.withdraw()
        try:
            open_wiki = messagebox.askyesno(
                "NotionLink - Help & Docs",
                "Important Notes:\n\n"
                "- Keep the app running so localhost links resolve.\n"
                "- Share Notion pages with your integration.\n"
                "- In offline mode, use Convert Path to Link and paste manually.\n\n"
                "Open full docs wiki now?",
            )
            if open_wiki:
                webbrowser.open_new("https://github.com/wladermisch/NotionLink/wiki")
        finally:
            root.destroy()

    def _ensure_dashboard_log_watcher(self):
        if self.dashboard_log_watcher:
            return
        try:
            self.dashboard_log_watcher = LogWatcher(logger.handlers[0].baseFilename)
            self.dashboard_log_watcher.new_log_line.connect(self._forward_dashboard_log_line)
        except Exception as e:
            print(f"Failed to start dashboard log watcher: {e}")

    def _forward_dashboard_log_line(self, line):
        if self.dashboard_window and hasattr(self.dashboard_window, "append_log_line"):
            self.dashboard_window.append_log_line(line)

    def _stop_dashboard_log_watcher(self):
        if self.dashboard_log_watcher:
            self.dashboard_log_watcher.stop()
        self.dashboard_log_watcher = None

    def stop_file_observer(self):
        global observer
        if observer:
            try:
                observer.stop()
                observer.join(timeout=2)
                print("Observer stopped.")
                observer = None
            except Exception as e:
                print(f"Error stopping observer: {e}")

    def start_file_observer(self):
        global observer, config
        self.stop_file_observer()

        observer = Observer()
        all_mappings = [("page", pm) for pm in config.get("page_mappings", [])] + [("database", dbm) for dbm in config.get("database_mappings", [])]

        if all_mappings:
            print("--- (Re)starting Watcher Setup ---")
            for mapping_type, mapping in all_mappings:
                if not mapping.get("enabled", True):
                    continue
                notion_id = mapping.get("notion_id")
                for folder_path in mapping.get("folders", []):
                    if not folder_path or not notion_id:
                        continue
                    path = os.path.expandvars(folder_path)
                    if os.path.isdir(path):
                        event_handler = NotionFileHandler(config, mapping, mapping_type, self)
                        recursive_watch = bool(mapping.get("folder_discovery", False))
                        observer.schedule(event_handler, path, recursive=recursive_watch)
                        mode = "recursive" if recursive_watch else "non-recursive"
                        print(f"--> Watching ({mode}): {path} -> {mapping_type} ID: ...{notion_id[-6:]}")
            if observer.emitters:
                observer.start()
                print("File watcher(s) started.")
        else:
            print("No folder mappings configured to watch.")

    def restart_file_observer(self):
        threading.Thread(target=self.start_file_observer, daemon=True).start()

    def upload_folder_to_notion(self, folder_path, mapping_config, mapping_type, suppress_notifications=False):
        print(f"Starting manual upload for folder: {folder_path}")
        global config, notified_errors

        target_page_id = mapping_config.get("notion_id")
        notion_title = mapping_config.get("notion_title", "Unknown")
        if not target_page_id:
            print(f"Error: No Notion ID found for folder '{folder_path}'.")
            return

        normalized_folder = os.path.expandvars(folder_path or "")
        if not normalized_folder or not os.path.isdir(normalized_folder):
            msg = f"Folder '{folder_path}' was not found. Please update this mapping path."
            error_key = f"{notion_title}:missing-folder:{normalized_folder}"
            if error_key not in notified_errors:
                notified_errors.add(error_key)
                if not suppress_notifications:
                    self.tray_icon.showMessage("NotionLink: Missing Folder", msg, None, 5000)
                self.user_error_signal.emit(msg)
            return

        print(f"Found mapping. Uploading files to {mapping_type} ...{target_page_id[-6:]}")
        try:
            notion = Client(auth=config.get("notion_token"))
            if mapping_type == "page":
                get_existing_links(target_page_id, notion, force_refresh=True)

            files_uploaded_count = 0
            handler = NotionFileHandler(config, mapping_config, mapping_type, self)
            discover_subfolder_files = bool(mapping_config.get("folder_discovery", False))
            add_subfolder_links = bool(mapping_config.get("folder_links", False))

            if add_subfolder_links:
                for name in os.listdir(normalized_folder):
                    full_path = os.path.join(normalized_folder, name)
                    if os.path.isdir(full_path):
                        sync_file_to_notion(full_path, config, mapping_config, mapping_type, self, is_batch=True)
                        files_uploaded_count += 1
                        time.sleep(0.05)

            if discover_subfolder_files:
                file_paths = []
                for root, _, files in os.walk(normalized_folder):
                    for filename in files:
                        file_paths.append(os.path.join(root, filename))
            else:
                file_paths = []
                for filename in os.listdir(normalized_folder):
                    full_file_path = os.path.join(normalized_folder, filename)
                    if os.path.isfile(full_file_path):
                        file_paths.append(full_file_path)

            for full_file_path in file_paths:
                filename = os.path.basename(full_file_path)
                try:
                    ignore_exts = handler.mapping_config.get("ignore_extensions", [])
                    if any(fnmatch.fnmatch(filename, p) for p in ignore_exts):
                        print(f"Skipping (ext filter): {filename}")
                        continue

                    ignore_files = handler.mapping_config.get("ignore_files", [])
                    if any(fnmatch.fnmatch(filename, p) for p in ignore_files):
                        print(f"Skipping (file/wildcard filter): {filename}")
                        continue
                except Exception as e:
                    print(f"Error applying filters: {e}")

                sync_file_to_notion(full_file_path, config, mapping_config, mapping_type, self, is_batch=True)
                files_uploaded_count += 1
                time.sleep(0.05)

            print(f"Upload complete. {files_uploaded_count} files processed for {folder_path}.")
        except Exception as e:
            error_str = str(e).lower()
            error_key = f"{notion_title}:upload:{type(e).__name__}:{error_str[:50]}"

            if is_user_error(e):
                if error_key not in notified_errors:
                    notified_errors.add(error_key)
                    if "404" in error_str or "could not find" in error_str:
                        msg = f"Cannot access Notion page '{notion_title}'. Please ensure the page is shared with your integration."
                    elif "401" in error_str or "unauthorized" in error_str or "invalid token" in error_str:
                        msg = "Invalid Notion token. Please update your token in settings."
                    elif "403" in error_str or "forbidden" in error_str or "not shared" in error_str:
                        msg = f"Access denied to '{notion_title}'. Check Notion page sharing permissions."
                    else:
                        msg = f"Configuration issue accessing '{notion_title}'. Please check your settings."

                    if not suppress_notifications:
                        self.tray_icon.showMessage("NotionLink: Configuration Error", msg, None, 5000)
                    self.user_error_signal.emit(msg)
                return
            else:
                if error_key not in notified_errors:
                    notified_errors.add(error_key)
                    sentry_active = sentry_sdk is not None
                    if sentry_active:
                        bug_msg = f"Unexpected upload error for '{notion_title}'. Logged and sent to developer."
                    else:
                        bug_msg = f"Unexpected upload error for '{notion_title}'. Logged locally."
                    if not suppress_notifications:
                        self.tray_icon.showMessage("NotionLink: Application Error", bug_msg, None, 5000)
                    self.user_error_signal.emit(bug_msg)
                raise e

    def show_window(self, window_name, window_class, **kwargs):
        print(f"Opening dialog: {window_name}")
        dialog = window_class(self, **kwargs)
        result = dialog.exec()
        print(f"Closed dialog: {window_name} with result: {result}")

        if result == DialogResult.Accepted:
            if window_name == "upload" and hasattr(dialog, "selected_task") and dialog.selected_task:
                folder, mapping, m_type = dialog.selected_task
                threading.Thread(target=self.upload_folder_to_notion, args=(folder, mapping, m_type), daemon=True).start()

        return result

    def show_dashboard(self):
        print("show_dashboard called")
        if self.dashboard_window is None:
            print("Creating CustomTkinter dashboard window...")
            self.dashboard_window = CtkDashboardBridge(self, show_intro=self._dashboard_intro_pending)
            self._dashboard_intro_pending = False
            self._ensure_dashboard_log_watcher()
        self.dashboard_window.show()
        if self._dashboard_transient_notice:
            self.dashboard_window.show_transient_notice(self._dashboard_transient_notice, "warning")

    def on_dashboard_closed(self):
        print("Dashboard window closed.")
        self.dashboard_window = None
        self._stop_dashboard_log_watcher()

    def show_feedback_dialog(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("feedback")

    def show_convert_path(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("convert")

    def show_token(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("token")

    def show_page_mappings(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("mappings", "page")

    def show_database_mappings(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("mappings", "database")

    def show_manual_upload(self):
        self.show_dashboard()
        if self.dashboard_window:
            self.dashboard_window.navigate_to("manual_upload")

    def get_notion_token(self):
        return config.get("notion_token", "")

    def set_notion_token(self, token):
        token = (token or "").strip()
        if not token or "PLEASE_ENTER" in token or len(token) < 50:
            raise ValueError("That does not look like a valid token. Please paste the full secret token.")
        config["notion_token"] = token
        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)
        self.run_status_check_thread()

    def get_mappings(self, mapping_type):
        key = "page_mappings" if mapping_type == "page" else "database_mappings"
        mappings = config.get(key, [])
        return [dict(m) for m in mappings]

    def save_mapping(self, mapping_type, mapping_data, index=None):
        key = "page_mappings" if mapping_type == "page" else "database_mappings"
        if key not in config:
            config[key] = []

        if index is None:
            config[key].append(mapping_data)
        else:
            config[key][index] = mapping_data

        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)

        self.restart_file_observer()
        for folder_path in mapping_data.get("folders", []):
            threading.Thread(
                target=self.upload_folder_to_notion,
                args=(folder_path, mapping_data, mapping_type),
                daemon=True,
            ).start()

    def delete_mapping(self, mapping_type, index):
        key = "page_mappings" if mapping_type == "page" else "database_mappings"
        mappings = config.get(key, [])
        if index < 0 or index >= len(mappings):
            raise IndexError("Invalid mapping index")
        del mappings[index]
        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)
        self.restart_file_observer()

    def list_manual_upload_options(self):
        all_mappings = [
            ("page", pm) for pm in config.get("page_mappings", [])
        ] + [
            ("database", dbm) for dbm in config.get("database_mappings", [])
        ]

        options = []
        for mapping_type, mapping in all_mappings:
            type_label = "Page" if mapping_type == "page" else "Database"
            for folder_path in mapping.get("folders", []):
                options.append(
                    {
                        "label": f"[{type_label}] {mapping.get('notion_title', 'Untitled')} - {os.path.basename(folder_path)}",
                        "folder": folder_path,
                        "mapping": mapping,
                        "mapping_type": mapping_type,
                    }
                )
        return options

    def trigger_manual_upload(self, option):
        folder = option.get("folder")
        mapping = option.get("mapping")
        mapping_type = option.get("mapping_type")
        if not folder or not mapping or not mapping_type:
            raise ValueError("Invalid manual upload target")
        threading.Thread(
            target=self.upload_folder_to_notion,
            args=(folder, mapping, mapping_type),
            daemon=True,
        ).start()

    def convert_path_to_link(self, path_to_convert):
        path_to_convert = (path_to_convert or "").strip().replace('"', "")
        if not path_to_convert:
            raise ValueError("Please provide a file path.")
        port = config.get("server_port")
        server_host = config.get("server_host")
        url_path = path_to_convert.replace("\\", "/")
        if url_path.startswith("/"):
            url_path = url_path[1:]
        return f"{server_host}:{port}/{url_path}"

    def browse_file_for_convert(self):
        return filedialog.askopenfilename(title="Select file to convert")

    def browse_folder_for_mapping(self):
        return filedialog.askdirectory(title="Select folder to sync")

    def send_feedback(self, feedback, discord_name=""):
        if not feedback:
            raise ValueError("Please enter feedback text.")

        if sentry_sdk is not None:
            with sentry_sdk.isolation_scope() as scope:
                if discord_name:
                    scope.set_user({"username": discord_name})
                sentry_sdk.capture_message(feedback, level="info")
            return True
        return False

    def activate_offline_mode_manually(self):
        import src.core as core_module
        core_module.offline_mode = True
        self.on_offline_mode_activated()

    def trigger_offline_mode_ui(self):
        self.offline_mode_signal.emit()

    def on_offline_mode_activated(self):
        print("Offline mode activated - showing popup")
        self.update_status_ui("Notion: Offline Mode")
        if self.dashboard_window:
            self.dashboard_window.update_status_panel_warning("Offline Mode Active. Restart NotionLink to reconnect.")

        root = tk.Tk()
        root.withdraw()
        try:
            retry = messagebox.askyesno(
                "NotionLink - Offline Mode",
                "Offline Mode activated.\n\n"
                "- Existing links still work while app is running.\n"
                "- New files are not synced automatically.\n"
                "- Use Convert Path to Link for manual links.\n\n"
                "Keep retrying connection?",
            )
        finally:
            root.destroy()

        if retry:
            self.start_auto_retry_loop()

    def quit_app(self):
        global observer, httpd
        print("=== QUIT_APP CALLED - Shutting down... ===")

        if self.dashboard_window:
            self.dashboard_window.close(wait=True, timeout=3.0)

        if self.status_check_timer:
            self.status_check_timer.stop()
            self.status_check_timer = None

        if self.auto_retry_timer:
            self.auto_retry_timer.stop()
            self.auto_retry_timer = None

        with self.notification_timer_lock:
            if self.notification_timer:
                self.notification_timer.cancel()
                self.notification_timer = None

        self.stop_file_observer()

        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
                print("HTTP server stopped and socket closed.")
            except Exception as e:
                print(f"Error stopping server: {e}")

        if self.tray_icon:
            self.tray_icon.hide()
            print("Tray icon hidden.")

        try:
            import logging
            logging.shutdown()
            print("Log handlers shut down.")
        except Exception:
            pass

        self.app.quit()
        print("=== App quit complete ===")

