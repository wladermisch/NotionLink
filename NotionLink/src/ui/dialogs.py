import json
import os
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk
import pyperclip as clip
from PIL import Image
from notion_client import Client

from ..events import EventSignal
from ..core import config, config_file_path, sentry_sdk, APP_VERSION, resource_path
from ..notion import extract_id_and_title_from_link, get_notion_title
from ..server import manage_autostart, TRAY_ICON_ICO


class DialogResult:
    Accepted = 1
    Rejected = 0


class BaseDialog:
    def __init__(self, title, parent=None):
        self.title = title
        self.parent = parent


class InitialSetupDialog(BaseDialog):
    def __init__(self, tray_app_instance):
        super().__init__("Welcome to NotionLink!")
        self.tray_app = tray_app_instance
        self._result = DialogResult.Rejected

    def exec(self):
        return self.exec_with_intro(show_intro=False)

    def exec_with_intro(self, show_intro=False, intro_duration_ms=2200):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        window = ctk.CTk()
        window.title("Welcome to NotionLink")
        window.configure(fg_color="#0B1220")

        try:
            window.iconbitmap(default=TRAY_ICON_ICO)
        except Exception:
            pass

        content = ctk.CTkFrame(window, fg_color="#111827", corner_radius=14, border_color="#1F2937", border_width=1)
        content.pack(fill="both", expand=True, padx=16, pady=16)

        def center_window(min_w=720, min_h=540, max_h=640):
            window.update_idletasks()

            req_w = max(window.winfo_reqwidth(), min_w)
            req_h = max(window.winfo_reqheight(), min_h)

            screen_w = window.winfo_screenwidth()
            screen_h = window.winfo_screenheight()

            target_w = min(req_w + 10, max(540, screen_w - 80))
            target_h = min(req_h + 8, max(500, screen_h - 160), max_h)

            x = max(int((screen_w - target_w) / 2), 0)
            y = max(int((screen_h - target_h) / 2), 0)
            window.geometry(f"{target_w}x{target_h}+{x}+{y}")
            window.minsize(target_w, target_h)

        def build_setup_view():
            for child in content.winfo_children():
                child.destroy()

            logo_image = None
            try:
                logo_path = resource_path("assets/logo.png")
                pil_logo = Image.open(logo_path)
                logo_image = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(92, 92))
            except Exception:
                logo_image = None

            if logo_image is not None:
                logo_label = ctk.CTkLabel(content, text="", image=logo_image)
                logo_label.image = logo_image
            else:
                logo_label = ctk.CTkLabel(content, text="NL", font=ctk.CTkFont(size=44, weight="bold"), text_color="#6EE7B7")
            logo_label.pack(pady=(18, 8))

            ctk.CTkLabel(content, text="Welcome to NotionLink", font=ctk.CTkFont(size=30, weight="bold"), text_color="#E5E7EB").pack()
            ctk.CTkLabel(content, text="One-time setup to connect your Notion workspace", font=ctk.CTkFont(size=14), text_color="#94A3B8").pack(pady=(4, 14))

            steps = ctk.CTkFrame(content, fg_color="#0F172A", corner_radius=10)
            steps.pack(fill="x", padx=20, pady=(0, 14))

            ctk.CTkLabel(steps, text="1. Create an integration in Notion.", anchor="w", justify="left").pack(fill="x", padx=14, pady=(10, 2))
            ctk.CTkLabel(steps, text="2. Copy the Internal Integration Token (secret).", anchor="w", justify="left").pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(steps, text="3. Paste the token below and start the app.", anchor="w", justify="left").pack(fill="x", padx=14, pady=(2, 10))

            links_row = ctk.CTkFrame(content, fg_color="transparent")
            links_row.pack(fill="x", padx=20, pady=(0, 12))
            links_row.grid_columnconfigure(0, weight=1)
            links_row.grid_columnconfigure(1, weight=1)

            ctk.CTkButton(
                links_row,
                text="Open Notion Integrations",
                command=lambda: webbrowser.open_new("https://www.notion.so/my-integrations"),
                fg_color="#2563EB",
                hover_color="#1D4ED8",
            ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

            ctk.CTkButton(
                links_row,
                text="Setup Help (Wiki)",
                command=lambda: webbrowser.open_new("https://github.com/wladermisch/NotionLink/wiki/First-Time-Setup-(Wizard)"),
                fg_color="#374151",
                hover_color="#4B5563",
            ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

            token_wrap = ctk.CTkFrame(content, fg_color="transparent")
            token_wrap.pack(fill="x", padx=20)
            ctk.CTkLabel(token_wrap, text="Notion Token", anchor="w").pack(fill="x")
            token_entry = ctk.CTkEntry(token_wrap, show="*", placeholder_text="secret_xxx...", height=40)
            token_entry.pack(fill="x", pady=(6, 10))

            autostart_var = ctk.BooleanVar(value=True)
            sentry_var = ctk.BooleanVar(value=True)

            ctk.CTkCheckBox(content, text="Start NotionLink automatically with Windows", variable=autostart_var).pack(anchor="w", padx=24, pady=(4, 4))
            ctk.CTkCheckBox(content, text="Enable anonymous error reporting", variable=sentry_var).pack(anchor="w", padx=24, pady=(0, 10))

            error_label = ctk.CTkLabel(content, text="", text_color="#FCA5A5")
            error_label.pack(fill="x", padx=20, pady=(0, 8))

            actions = ctk.CTkFrame(content, fg_color="transparent")
            actions.pack(fill="x", padx=20, pady=(2, 16))
            actions.grid_columnconfigure(0, weight=1)
            actions.grid_columnconfigure(1, weight=1)

            def save_and_start():
                token = token_entry.get().strip()
                if not token or "PLEASE_ENTER" in token or len(token) < 50:
                    error_label.configure(text="That does not look like a valid token. Please paste the full secret token.")
                    return

                autostart_enabled = bool(autostart_var.get())
                sentry_enabled = bool(sentry_var.get())

                config["notion_token"] = token
                config["tutorial_completed"] = True
                config["autostart_with_windows"] = autostart_enabled
                config["sentry_enabled"] = sentry_enabled

                try:
                    with open(config_file_path, "w") as f:
                        json.dump(config, f, indent=4)

                    if self.tray_app:
                        self.tray_app.sync_autostart_ui(autostart_enabled)
                        self.tray_app.sync_sentry_ui(sentry_enabled)

                    manage_autostart(autostart_enabled)
                    self._result = DialogResult.Accepted
                    window.destroy()
                except Exception as save_error:
                    error_label.configure(text=f"Could not save setup: {save_error}")

            ctk.CTkButton(actions, text="Cancel", fg_color="#374151", hover_color="#4B5563", command=cancel_setup).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            ctk.CTkButton(actions, text="Save and Start", fg_color="#059669", hover_color="#047857", command=save_and_start).grid(row=0, column=1, sticky="ew", padx=(6, 0))

            center_window(min_w=720, min_h=540, max_h=640)
            window.after(30, token_entry.focus_set)

        def build_intro_view():
            for child in content.winfo_children():
                child.destroy()

            center = ctk.CTkFrame(content, fg_color="transparent")
            center.place(relx=0.5, rely=0.5, anchor="center")

            logo_image = None
            try:
                logo_path = resource_path("assets/logo.png")
                pil_logo = Image.open(logo_path)
                logo_image = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(120, 120))
            except Exception:
                logo_image = None

            if logo_image is not None:
                logo_label = ctk.CTkLabel(center, text="", image=logo_image)
                logo_label.image = logo_image
            else:
                logo_label = ctk.CTkLabel(center, text="NL", font=ctk.CTkFont(size=56, weight="bold"), text_color="#6EE7B7")
            logo_label.pack(pady=(0, 10))

            title_label = ctk.CTkLabel(center, text="NotionLink", font=ctk.CTkFont(size=34, weight="bold"), text_color="#E5E7EB")
            title_label.pack()
            ctk.CTkLabel(center, text=f"v{APP_VERSION}", font=ctk.CTkFont(size=14), text_color="#94A3B8").pack(pady=(6, 0))

            center_window(min_w=420, min_h=320, max_h=420)

            def animate_breath(step=0):
                if not window.winfo_exists() or not title_label.winfo_exists():
                    return
                brightness = 225 + int(20 * abs((step % 24) - 12) / 12)
                title_label.configure(text_color=f"#{brightness:02X}{brightness:02X}{brightness:02X}")
                window.after(60, lambda: animate_breath(step + 1))

            animate_breath()
            window.after(max(0, int(intro_duration_ms)), build_setup_view)

        def cancel_setup():
            self._result = DialogResult.Rejected
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", cancel_setup)
        if show_intro:
            window.after(10, build_intro_view)
        else:
            window.after(10, build_setup_view)

        try:
            window.mainloop()
            return self._result
        finally:
            try:
                window.destroy()
            except Exception:
                pass


class ManageTokenWindow(BaseDialog):
    def __init__(self, tray_app_instance):
        super().__init__("Manage Notion Token")
        self.tray_app = tray_app_instance

    def exec(self):
        root = tk.Tk()
        root.withdraw()
        try:
            token = simpledialog.askstring("Notion Token", "Update Notion Token:", initialvalue=config.get("notion_token", ""), show="*")
            if token is None:
                return DialogResult.Rejected
            config["notion_token"] = token
            with open(config_file_path, "w") as config_file:
                json.dump(config, config_file, indent=4)
            if self.tray_app:
                self.tray_app.run_status_check_thread()
            return DialogResult.Accepted
        finally:
            root.destroy()


class LogWatcher:
    def __init__(self, log_path):
        self.log_path = log_path
        self.new_log_line = EventSignal()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        file_handle = None
        while not self._stop_event.is_set():
            try:
                if file_handle is None:
                    file_handle = open(self.log_path, "r", encoding="utf-8")
                    file_handle.seek(0, os.SEEK_END)

                line = file_handle.readline()
                if line:
                    self.new_log_line.emit(line.strip())
                else:
                    time.sleep(0.3)
            except Exception:
                time.sleep(1.0)
                file_handle = None

    def stop(self):
        self._stop_event.set()


class EditMappingDialog(BaseDialog):
    def __init__(self, tray_app_instance, existing_mapping=None, mapping_type="page"):
        self.mapping_type = mapping_type
        self.mapping_type_name = "Page" if mapping_type == "page" else "Database"
        title = f"Edit {self.mapping_type_name} Mapping" if existing_mapping else f"Add New {self.mapping_type_name} Mapping"
        super().__init__(title)
        self.tray_app = tray_app_instance
        self.mapping = existing_mapping.copy() if existing_mapping else {}

    def _collect_folders(self, initial):
        folders = list(initial)
        root = tk.Tk()
        root.withdraw()
        try:
            while True:
                folder = filedialog.askdirectory(title="Select a folder to sync")
                if folder:
                    folders.append(folder)
                if not messagebox.askyesno("Folders", "Add another folder?"):
                    break
            return [f for f in folders if f]
        finally:
            root.destroy()

    def get_mapping_data(self):
        root = tk.Tk()
        root.withdraw()
        try:
            notion_in = simpledialog.askstring(
                self.title,
                f"Enter Notion {self.mapping_type_name} link or ID:",
                initialvalue=self.mapping.get("notion_id", ""),
            )
            if not notion_in:
                return None

            id_tuple = extract_id_and_title_from_link(notion_in)
            if not id_tuple:
                messagebox.showerror("Invalid Input", f"Invalid Notion {self.mapping_type_name} Link or ID.")
                return None

            notion_id, title_from_url = id_tuple
            default_title = self.mapping.get("notion_title") or title_from_url or f"Untitled (...{notion_id[-6:]})"
            notion_title = simpledialog.askstring(self.title, "Mapping title:", initialvalue=default_title)
            notion_title = notion_title or default_title

            folders = self._collect_folders(self.mapping.get("folders", []))
            if not folders:
                messagebox.showerror("Missing Folders", "You must add at least one folder to sync.")
                return None

            ignore_ext = simpledialog.askstring(
                self.title,
                "Ignore extensions (comma-separated):",
                initialvalue=", ".join(self.mapping.get("ignore_extensions", ["*.tmp", ".*", "desktop.ini"])),
            ) or "*.tmp, .*, desktop.ini"

            ignore_files = simpledialog.askstring(
                self.title,
                "Ignore filenames/patterns (comma-separated):",
                initialvalue=", ".join(self.mapping.get("ignore_files", [])),
            ) or ""

            folder_discovery = messagebox.askyesno("Folder Discovery", "Include files in subfolders?")
            folder_links = messagebox.askyesno("Folder Links", "Add subfolder links too?")
            full_lifecycle = messagebox.askyesno("Lifecycle Sync", "Enable deletion/rename lifecycle sync?")

            data = {
                "notion_title": notion_title.strip(),
                "notion_id": notion_id,
                "folders": folders,
                "ignore_extensions": [p.strip() for p in ignore_ext.split(",") if p.strip()],
                "ignore_files": [p.strip() for p in ignore_files.split(",") if p.strip()],
                "full_lifecycle_sync": full_lifecycle,
                "folder_discovery": folder_discovery,
                "folder_links": folder_links,
            }

            if self.mapping_type == "database":
                self.ensure_database_properties(data["notion_id"])
            return data
        finally:
            root.destroy()

    def ensure_database_properties(self, database_id):
        try:
            notion = Client(auth=config.get("notion_token"))
            db_response = notion.databases.retrieve(database_id=database_id)
            properties = db_response.get("properties", {})

            required_props = {
                "Name": "title",
                "Link": "url",
                "Created": "date",
                "Modified": "date",
                "Size (Bytes)": "number",
            }

            missing_props = {}
            for prop_name, prop_type in required_props.items():
                if prop_name not in properties:
                    missing_props[prop_name] = prop_type
                elif prop_name == "Name" and properties["Name"].get("type") != "title":
                    missing_props[prop_name] = prop_type

            if not missing_props:
                return True

            root = tk.Tk()
            root.withdraw()
            try:
                if not messagebox.askyesno(
                    "Auto-Configure Database",
                    "Database is missing required properties. Create them automatically?",
                ):
                    return False
            finally:
                root.destroy()

            new_properties = {}
            for prop_name, prop_type in missing_props.items():
                if prop_type == "title":
                    new_properties[prop_name] = {"title": {}}
                elif prop_type == "url":
                    new_properties[prop_name] = {"url": {}}
                elif prop_type == "date":
                    new_properties[prop_name] = {"date": {}}
                elif prop_type == "number":
                    new_properties[prop_name] = {"number": {"format": "number"}}

            notion.databases.update(database_id=database_id, properties=new_properties)
            return True
        except Exception as e:
            root = tk.Tk()
            root.withdraw()
            try:
                messagebox.showerror("Database Error", f"Failed to configure database properties:\n{e}")
            finally:
                root.destroy()
            return False

    def exec(self):
        data = self.get_mapping_data()
        if data is None:
            return DialogResult.Rejected
        self.mapping = data
        return DialogResult.Accepted


class ManageMappingsListDialog(BaseDialog):
    def __init__(self, tray_app_instance, mapping_type="page"):
        self.tray_app = tray_app_instance
        self.mapping_type = mapping_type
        self.mapping_key = f"{mapping_type}_mappings"
        self.mapping_type_name = "Page" if mapping_type == "page" else "Database"
        super().__init__(f"Manage {self.mapping_type_name} Mappings")

    def _select_index(self, mappings, prompt):
        listing = "\n".join([f"{i + 1}. {m.get('notion_title', 'Untitled')}" for i, m in enumerate(mappings)])
        root = tk.Tk()
        root.withdraw()
        try:
            idx = simpledialog.askinteger(self.title, f"{prompt}\n\n{listing}\n\nEnter number:")
            if not idx or idx < 1 or idx > len(mappings):
                return None
            return idx - 1
        finally:
            root.destroy()

    def save_and_restart_watchers(self, new_mapping_data=None):
        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)

        self.tray_app.restart_file_observer()

        if new_mapping_data:
            for folder_path in new_mapping_data.get("folders", []):
                threading.Thread(
                    target=self.tray_app.upload_folder_to_notion,
                    args=(folder_path, new_mapping_data, self.mapping_type),
                    daemon=True,
                ).start()

    def exec(self):
        root = tk.Tk()
        root.withdraw()
        try:
            while True:
                action = simpledialog.askstring(
                    self.title,
                    "Action: add / edit / remove / close",
                )
                if not action:
                    return DialogResult.Rejected

                action = action.strip().lower()
                mappings = config.get(self.mapping_key, [])

                if action == "add":
                    dialog = EditMappingDialog(self.tray_app, mapping_type=self.mapping_type)
                    if dialog.exec() == DialogResult.Accepted:
                        new_mapping_data = dialog.mapping
                        config[self.mapping_key].append(new_mapping_data)
                        self.save_and_restart_watchers(new_mapping_data)
                        return DialogResult.Accepted

                elif action == "edit":
                    if not mappings:
                        messagebox.showinfo(self.title, "No mappings available.")
                        continue
                    index = self._select_index(mappings, "Select mapping to edit")
                    if index is None:
                        continue
                    dialog = EditMappingDialog(self.tray_app, existing_mapping=mappings[index], mapping_type=self.mapping_type)
                    if dialog.exec() == DialogResult.Accepted:
                        config[self.mapping_key][index] = dialog.mapping
                        self.save_and_restart_watchers(dialog.mapping)
                        return DialogResult.Accepted

                elif action == "remove":
                    if not mappings:
                        messagebox.showinfo(self.title, "No mappings available.")
                        continue
                    index = self._select_index(mappings, "Select mapping to remove")
                    if index is None:
                        continue
                    if messagebox.askyesno(self.title, "Are you sure you want to remove this mapping?"):
                        del config[self.mapping_key][index]
                        self.save_and_restart_watchers()
                        return DialogResult.Accepted

                elif action == "close":
                    return DialogResult.Rejected
        finally:
            root.destroy()


class ManualUploadWindow(BaseDialog):
    def __init__(self, tray_app_instance):
        super().__init__("Start Manual Upload")
        self.tray_app = tray_app_instance
        self.selected_task = None

    def exec(self):
        all_mappings = [("page", pm) for pm in config.get("page_mappings", [])] + [("database", dbm) for dbm in config.get("database_mappings", [])]
        if not all_mappings:
            root = tk.Tk()
            root.withdraw()
            try:
                messagebox.showinfo(self.title, "No mappings have been configured.")
            finally:
                root.destroy()
            return DialogResult.Rejected

        options = []
        for mapping_type, mapping in all_mappings:
            type_label = "Page" if mapping_type == "page" else "Database"
            for folder_path in mapping.get("folders", []):
                options.append((f"[{type_label}] {mapping.get('notion_title', 'Untitled')} - {os.path.basename(folder_path)}", folder_path, mapping, mapping_type))

        listing = "\n".join([f"{i + 1}. {opt[0]}" for i, opt in enumerate(options)])
        root = tk.Tk()
        root.withdraw()
        try:
            idx = simpledialog.askinteger(self.title, f"Select upload target:\n\n{listing}\n\nEnter number:")
            if not idx or idx < 1 or idx > len(options):
                return DialogResult.Rejected
            _, folder, mapping, m_type = options[idx - 1]
            self.selected_task = (folder, mapping, m_type)
            return DialogResult.Accepted
        finally:
            root.destroy()


class ConvertPathWindow(BaseDialog):
    def __init__(self, tray_app_instance):
        super().__init__("Convert Path")
        self.tray_app = tray_app_instance

    def exec(self):
        root = tk.Tk()
        root.withdraw()
        try:
            path_to_convert = simpledialog.askstring(
                self.title,
                "Paste file path (leave empty to browse):",
                initialvalue=clip.paste().replace('"', "") if clip.paste() else "",
            )
            if not path_to_convert:
                path_to_convert = filedialog.askopenfilename(title="Select file to convert")
            if not path_to_convert:
                return DialogResult.Rejected

            port = config.get("server_port")
            server_host = config.get("server_host")
            url_path = path_to_convert.replace("\\", "/")
            if url_path.startswith("/"):
                url_path = url_path[1:]
            result = f"{server_host}:{port}/{url_path}"
            clip.copy(result)
            messagebox.showinfo(self.title, f"Copied link to clipboard:\n\n{result}")
            return DialogResult.Accepted
        finally:
            root.destroy()


class FeedbackDialog(BaseDialog):
    def __init__(self, tray_app_instance):
        super().__init__("Send Feedback")
        self.tray_app = tray_app_instance

    def exec(self):
        root = tk.Tk()
        root.withdraw()
        try:
            feedback = simpledialog.askstring(self.title, "Feedback:")
            if not feedback:
                return DialogResult.Rejected
            discord_name = simpledialog.askstring(self.title, "Discord name (optional):") or ""

            if sentry_sdk is not None:
                with sentry_sdk.isolation_scope() as scope:
                    if discord_name:
                        scope.set_user({"username": discord_name})
                    sentry_sdk.capture_message(feedback, level="info")
                messagebox.showinfo(self.title, "Feedback sent. Thank you!")
            else:
                messagebox.showwarning(self.title, "Feedback service unavailable (Sentry disabled).")
            return DialogResult.Accepted
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror(self.title, f"Could not send feedback:\n{e}")
            return DialogResult.Rejected
        finally:
            root.destroy()


class UpdateCheckThread:
    def __init__(self):
        self.update_available = EventSignal()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            url = "https://api.github.com/repos/wladermisch/NotionLink/releases/latest"
            headers = {"User-Agent": f"NotionLink/{APP_VERSION}"}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    tag_name = data.get("tag_name", "").strip()
                    latest_version = tag_name.lstrip("v")
                    if self._is_newer(latest_version, APP_VERSION):
                        self.update_available.emit(latest_version, data.get("html_url", "https://github.com/wladermisch/NotionLink/releases/latest"))
        except Exception as e:
            print(f"Update check failed: {e}")

    def _is_newer(self, latest, current):
        try:
            return [int(p) for p in latest.split(".")] > [int(p) for p in current.split(".")]
        except Exception:
            return False


class UpdateAvailableDialog(BaseDialog):
    def __init__(self, current_v, latest_v, url, parent=None):
        super().__init__("Update Available", parent)
        self.current_v = current_v
        self.latest_v = latest_v
        self.url = url

    def exec(self):
        root = tk.Tk()
        root.withdraw()
        try:
            if messagebox.askyesno(
                self.title,
                f"New version available.\n\nCurrent: {self.current_v}\nLatest: {self.latest_v}\n\nOpen release page now?",
            ):
                webbrowser.open(self.url)
                return DialogResult.Accepted
            return DialogResult.Rejected
        finally:
            root.destroy()
