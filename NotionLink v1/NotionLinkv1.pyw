import os
import sys
import time
import json
import re
import subprocess
import socketserver
from http.server import BaseHTTPRequestHandler
from http import HTTPStatus
from urllib.parse import unquote, urlparse
import threading
import webbrowser

import win32com.client
import pyautogui
import pyperclip as clip

import pystray
from PIL import Image

import customtkinter as ctk
from customtkinter import CTkInputDialog

from notion_client import Client
from tkinter import filedialog

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

ctk.set_default_color_theme('green')

config_file_path = "config.json"

default_config = {
    "server_port": 3030,
    "server_host": "http://localhost",
    "notion_token": "PLEASE_ENTER_YOUR_NEW_TOKEN_HERE",
    "folder_mappings": [],
    "tutorial_completed": False
}

root = None
observer = None
httpd = None
link_cache = {}
notion_status = "Notion: Checking..."


if getattr(sys, 'frozen', False):
    path = os.path.dirname(sys.executable)
elif __file__:
    path = os.path.dirname(__file__)

log_file_path = os.path.join(path, "output.log")
log_file = open(log_file_path, "a", buffering=1)
sys.stdout = log_file
sys.stderr = log_file

config_file_path = os.path.join(path, config_file_path)

try:
    if not os.path.isfile(config_file_path):
        with open(config_file_path, "w") as config_file:
            json.dump(default_config, config_file, indent=4)
        print("Config file created with default settings.")
        config = default_config
    else:
        with open(config_file_path, "r") as config_file:
            config = json.load(config_file)

        config_updated = False
        for key, value in default_config.items():
            if key not in config:
                config[key] = value
                config_updated = True

        old_keys = ["base_dir", "notion_page_id", "watched_folders"]
        for key in old_keys:
            if key in config:
                del config[key]
                config_updated = True

        if config_updated:
            with open(config_file_path, "w") as config_file:
                json.dump(config, config_file, indent=4)
            print("Config file migrated to new mapping structure.")

    print("Configuration loaded.")
except Exception as e:
    print(f"Error loading config, using defaults. Error: {e}")
    config = default_config


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

ASSETS_DIR = 'assets'
TRAY_ICON_PNG = resource_path(os.path.join(ASSETS_DIR, 'logo.png'))
TRAY_ICON_ICO = resource_path(os.path.join(ASSETS_DIR, 'logo.ico'))

shell = win32com.client.Dispatch("WScript.Shell")


def run_initial_setup_wizard():
    global config

    window = ctk.CTk()
    window.title("Welcome to NotionLink!")
    window.geometry("600x450")
    window.iconbitmap(resource_path(os.path.join('assets', 'logo.ico')))

    def _open_link():
        webbrowser.open_new("https://www.notion.so/my-integrations")

    def _save_and_exit():
        global config
        token = token_entry.get()

        if not token or "PLEASE_ENTER" in token or len(token) < 50:
            error_label.configure(text="That doesn't look like a valid token. Please paste the full secret token.")
            return

        config["notion_token"] = token
        config["tutorial_completed"] = True

        try:
            with open(config_file_path, "w") as f:
                json.dump(config, f, indent=4)
            print("Initial setup complete. Token saved.")
            window.destroy()
        except Exception as e:
            print(f"Error saving config during setup: {e}")
            error_label.configure(text=f"Error saving config: {e}")

    title_label = ctk.CTkLabel(window, text="Welcome to NotionLink!", font=ctk.CTkFont(size=20, weight="bold"))
    title_label.pack(padx=20, pady=(20, 10))

    intro_label = ctk.CTkLabel(window, text="This one-time setup will connect the app to your Notion account.\nIt only takes 3 steps:", justify="left")
    intro_label.pack(padx=20, pady=10, anchor="w")

    step1_label = ctk.CTkLabel(window, text="1. Go to the Notion Integrations page by clicking the button below.")
    step1_label.pack(padx=20, pady=(10, 5), anchor="w")

    link_button = ctk.CTkButton(window, text="Open Notion Integrations Page", command=_open_link)
    link_button.pack(padx=20, pady=5)

    step2_label = ctk.CTkLabel(window, text="2. Click 'New integration', give it a name (e.g., 'NotionLink'),\nand copy the 'Internal Integration Token' (Secret).", justify="left")
    step2_label.pack(padx=20, pady=(10, 5), anchor="w")

    step3_label = ctk.CTkLabel(window, text="3. Paste your 'Internal Integration Token' (Secret) here:")
    step3_label.pack(padx=20, pady=(10, 5), anchor="w")

    token_entry = ctk.CTkEntry(window, width=500, show="*")
    token_entry.pack(padx=20, pady=5)

    error_label = ctk.CTkLabel(window, text="", text_color="red")
    error_label.pack(padx=20, pady=(5,0))

    save_button = ctk.CTkButton(window, text="Save and Start Application", command=_save_and_exit, height=40)
    save_button.pack(padx=20, pady=(20, 20))

    window.mainloop()


def open_explorer(Path):
    pyautogui.hotkey('ctrl', 'w')
    Path = unquote(Path)
    full_path = Path[1:].replace("/", "\\")
    print(f"Opening full path: {full_path}")
    shell.SendKeys('%')
    subprocess.Popen(['explorer', full_path])
    time.sleep(1)
    window_title = os.path.basename(os.path.normpath(full_path)) + " - File Explorer"
    print(window_title)


class MyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        print('Getting path : --------')
        print(self.path)
        if not ('GET' in self.path) and not ('favicon' in self.path):
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b'OpenExplorer')
            open_explorer(self.path)
        else:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b'File Connection Server is running.')


def create_image():
    return Image.open(TRAY_ICON_PNG)


def extract_id_from_link_or_id(text_input):
    if "notion.so" in text_input:
        try:
            match = re.search(r'([a-fA-F0-9]{32})$', urlparse(text_input).path.split('-')[-1])
            if match:
                extracted_id = match.group(1)
                print(f"Extracted Page ID: {extracted_id}")
                return extracted_id
            else:
                print(f"Could not extract ID from link: {text_input}")
                return None
        except Exception as e:
            print(f"Error extracting ID: {e}")
            return None
    elif len(text_input) == 32 and all(c in '0123456789abcdefABCDEF' for c in text_input):
        return text_input
    else:
        print(f"Input is not a valid link or 32-char ID: {text_input}")
        return None


def get_existing_links(page_id, notion_client):
    global link_cache
    if page_id in link_cache:
        print(f"Cache hit for page ...{page_id[-6:]}.")
        return

    print(f"Fetching existing links for page ...{page_id[-6:]} to prevent duplicates...")
    links_set = set()
    try:
        next_cursor = None
        while True:
            response = notion_client.blocks.children.list(
                block_id=page_id,
                start_cursor=next_cursor
            )
            results = response.get("results", [])
            for block in results:
                block_type = block.get("type")
                if block_type in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"):
                    rich_text = block.get(block_type, {}).get("rich_text", [])
                    for item in rich_text:
                        link = item.get("text", {}).get("link")
                        if link and link.get("url"):
                            links_set.add(link["url"])

            if response.get("has_more"):
                next_cursor = response.get("next_cursor")
            else:
                break

        link_cache[page_id] = links_set
        print(f"Cached {len(links_set)} existing links for page ...{page_id[-6:]}.")
    except Exception as e:
        print(f"Error fetching existing links: {e}")


def send_file_to_notion(full_file_path, config_data, notion_page_id_to_use):
    global link_cache
    try:
        notion_token = config_data.get("notion_token")
        server_host = config_data.get("server_host")
        port = config_data.get("server_port")

        if not notion_token or "EINFUEGEN" in notion_token or "PLEASE_ENTER" in notion_token:
            print("Notion Token not configured. Skipping upload.")
            return

        server_address = f"{server_host}:{port}/"
        url_path = full_file_path.replace("\\", "/")
        if url_path.startswith('/'):
            url_path = url_path[1:]

        server_link = server_address + url_path
        filename = os.path.basename(full_file_path)

        if notion_page_id_to_use in link_cache and server_link in link_cache[notion_page_id_to_use]:
            print(f"Skipping (already exists): {filename}")
            return

        print(f"Sending file to Notion page ...{notion_page_id_to_use[-6:]}: {filename}")
        notion = Client(auth=notion_token)

        blocks_to_append = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {
                            "content": filename,
                            "link": {"url": server_link}
                        }
                    }]
                }
            }
        ]

        notion.blocks.children.append(block_id=notion_page_id_to_use, children=blocks_to_append)

        link_cache.setdefault(notion_page_id_to_use, set()).add(server_link)

        print(f"Successfully uploaded {filename} to Notion.")
    except Exception as e:
        print(f"Error sending file {full_file_path} to Notion: {e}")


class NotionFileHandler(FileSystemEventHandler):
    def __init__(self, config_data, notion_page_id_for_this_folder):
        self.config_data = config_data
        self.notion_page_id = notion_page_id_for_this_folder
        print(f"File Watcher Handler initialized for page ...{self.notion_page_id[-6:]}")

    def on_created(self, event):
        if event.is_directory:
            return
        print(f"New file detected by watcher: {event.src_path}")
        upload_thread = threading.Thread(
            target=send_file_to_notion,
            args=(event.src_path, self.config_data, self.notion_page_id)
        )
        upload_thread.start()


def convert_clipboard_path():
    global config
    port = config.get("server_port")
    server_host = config.get("server_host")
    server_address = f"{server_host}:{port}/"
    path_to_convert = clip.paste().replace("\"", "")
    resulting_path = server_address + path_to_convert.replace("\\", "/")
    clip.copy(resulting_path)


def _spawn_convert_path_window():
    def convert_path(event=None):
        global config
        port = config.get("server_port")
        server_host = config.get("server_host")
        server_address = f"{server_host}:{port}/"
        path_to_convert = entry.get().replace("\"", "")
        resulting_path = server_address + path_to_convert.replace("\\", "/")
        output_label.configure(state="normal")
        output_label.delete("0.0", "end")
        output_label.insert("0.0", resulting_path)
        output_label.configure(state="disabled")
        clip.copy(resulting_path)

    window = ctk.CTkToplevel()
    window.title("Convert Path")
    window.iconbitmap(TRAY_ICON_ICO)
    window.attributes("-topmost", True)

    label = ctk.CTkLabel(window, text="Path to convert:")
    label.pack(padx=20, pady=10)
    entry = ctk.CTkEntry(window, width=300)
    entry.insert(0, "")
    entry.bind("<Return>", convert_path)
    entry.pack(padx=20, pady=10)
    save_button = ctk.CTkButton(window, text="Convert", command=convert_path)
    save_button.pack(padx=20, pady=10)
    output_label = ctk.CTkTextbox(window, width=300, height =80)
    output_label.insert("0.0", "")
    output_label.configure(state="disabled")
    output_label.pack(padx=20, pady=10)


def show_customtkinter_window_convert_path():
    global root
    if root:
        root.after(0, _spawn_convert_path_window)


def _spawn_token_config_window():
    global config
    notion_token = config.get("notion_token", "")

    window = ctk.CTkToplevel()
    window.title("Manage Notion Token")
    window.iconbitmap(TRAY_ICON_ICO)
    window.attributes("-topmost", True)

    def save_notion_config():
        global config
        config["notion_token"] = token_entry.get()

        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)
        window.destroy()

    token_label = ctk.CTkLabel(window, text="Notion Token:")
    token_label.pack(padx=20, pady=(10, 0))
    token_entry = ctk.CTkEntry(window, width=400, show="*")
    token_entry.insert(0, notion_token)
    token_entry.pack(padx=20, pady=5)
    save_button = ctk.CTkButton(window, text="Save", command=save_notion_config)
    save_button.pack(padx=20, pady=20)


def show_customtkinter_window_token_config():
    global root
    if root:
        root.after(0, _spawn_token_config_window)


def upload_folder_to_notion(folder_path):
    print(f"Starting upload for folder: {folder_path}")
    global config, link_cache

    target_page_id = None
    mappings = config.get("folder_mappings", [])
    for mapping in mappings:
        if os.path.normpath(mapping["folder_path"]) == os.path.normpath(folder_path):
            target_page_id = extract_id_from_link_or_id(mapping.get("notion_page_link_or_id"))
            break

    if not target_page_id:
        print(f"Error: No mapping found for folder '{folder_path}'.")
        return

    print(f"Found mapping. Uploading files to page ...{target_page_id[-6:]}")
    try:
        notion = Client(auth=config.get("notion_token"))

        if target_page_id not in link_cache:
            get_existing_links(target_page_id, notion)

        files_uploaded_count = 0
        for filename in os.listdir(folder_path):
            full_file_path = os.path.join(folder_path, filename)
            if os.path.isfile(full_file_path):
                send_file_to_notion(full_file_path, config, target_page_id)
                files_uploaded_count += 1
                time.sleep(0.05)
        print(f"Upload complete. {files_uploaded_count} files processed for {folder_path}.")
    except Exception as e:
        print(f"An error occurred during upload: {e}")


def _spawn_mappings_window():
    global config
    old_folder_paths = set(m["folder_path"] for m in config.get("folder_mappings", []))
    current_mappings = list(config.get("folder_mappings", []))

    window = ctk.CTkToplevel()
    window.title("Manage Folder-Page Mappings")
    window.iconbitmap(TRAY_ICON_ICO)
    window.geometry("700x400")
    window.attributes("-topmost", True)

    label = ctk.CTkLabel(window, text="Automatically watched folders and their Notion pages:")
    label.pack(padx=20, pady=(10, 0))

    scrollable_frame = ctk.CTkScrollableFrame(window, width=650, height=200)
    scrollable_frame.pack(padx=20, pady=10, fill="x")

    def update_display():
        for widget in scrollable_frame.winfo_children():
            widget.destroy()

        for mapping in current_mappings:
            folder = mapping.get("folder_path", "N/A")
            page = mapping.get("notion_page_link_or_id", "N/A")
            frame = ctk.CTkFrame(scrollable_frame)
            frame.pack(fill="x", pady=2)
            text = f"FOLDER: {folder}\nPAGE: {page}"
            label = ctk.CTkLabel(frame, text=text, wraplength=550, justify="left")
            label.pack(side="left", fill="x", expand=True, padx=5, pady=5)
            remove_button = ctk.CTkButton(frame, text="X", width=30, command=lambda m=mapping: remove_mapping(m))
            remove_button.pack(side="right", padx=5)

    def remove_mapping(mapping_to_remove):
        if mapping_to_remove in current_mappings:
            current_mappings.remove(mapping_to_remove)
            update_display()

    def add_mapping():
        folder_path = filedialog.askdirectory(title="Select a folder to watch")
        if not folder_path: return

        dialog = CTkInputDialog(text="Enter the Notion page link:", title="Notion Page")
        notion_link = dialog.get_input()
        if not notion_link: return

        if not extract_id_from_link_or_id(notion_link):
            restart_label.configure(text="Invalid Notion link. Mapping not created.", text_color="red")
            return

        new_mapping = {"folder_path": folder_path, "notion_page_link_or_id": notion_link}
        current_mappings.append(new_mapping)
        update_display()
        restart_label.configure(text="")

    def save_and_close():
        global config

        new_folder_paths = set(m["folder_path"] for m in current_mappings)
        folders_to_backfill = new_folder_paths - old_folder_paths

        config["folder_mappings"] = current_mappings
        with open(config_file_path, "w") as config_file:
            json.dump(config, config_file, indent=4)

        print("Folder mappings updated.")

        if folders_to_backfill:
            print(f"Found {len(folders_to_backfill)} newly added folders to backfill.")
            for folder in folders_to_backfill:
                print(f"Starting initial backfill for: {folder}")
                upload_thread = threading.Thread(target=upload_folder_to_notion, args=(folder,))
                upload_thread.start()
            restart_label.configure(text="Saved. Starting initial upload. Please restart.", text_color="green")
        else:
            restart_label.configure(text="Saved. Please restart the program.", text_color="green")

        print("PLEASE RESTART THE APPLICATION for watcher changes to take effect.")
        window.after(3000, window.destroy)

    add_button = ctk.CTkButton(window, text="Add New Mapping (Folder + Page)", command=add_mapping)
    add_button.pack(padx=20, pady=5)
    save_button = ctk.CTkButton(window, text="Save & Close", command=save_and_close)
    save_button.pack(padx=20, pady=(5, 0))
    restart_label = ctk.CTkLabel(window, text="", text_color="green")
    restart_label.pack(padx=20, pady=(5, 10))
    update_display()


def show_customtkinter_window_mappings():
    global root
    if root:
        root.after(0, _spawn_mappings_window)


def _spawn_manual_upload_window():
    global config
    mappings = config.get("folder_mappings", [])

    window = ctk.CTkToplevel()
    window.title("Start Manual Upload")
    window.iconbitmap(TRAY_ICON_ICO)
    window.geometry("600x300")
    window.attributes("-topmost", True)

    label = ctk.CTkLabel(window, text="Select a mapped folder to upload:")
    label.pack(padx=20, pady=(10, 0))

    if not mappings:
        ctk.CTkLabel(window, text="No folders have been mapped.", text_color="gray").pack(padx=20, pady=20)
        return

    scrollable_frame = ctk.CTkScrollableFrame(window, width=550, height=200)
    scrollable_frame.pack(padx=20, pady=10, fill="x")

    def start_upload(folder_path):
        print(f"Starting manual upload for: {folder_path}")
        upload_thread = threading.Thread(target=upload_folder_to_notion, args=(folder_path,))
        upload_thread.start()
        window.destroy()

    for mapping in mappings:
        folder_path = mapping["folder_path"]
        folder_name = os.path.basename(folder_path)
        parent_folder = os.path.basename(os.path.dirname(folder_path))

        button_text = f"{folder_name}  (in ...\\{parent_folder})"

        button = ctk.CTkButton(
            scrollable_frame,
            text=button_text,
            command=lambda p=folder_path: start_upload(p)
        )
        button.pack(fill="x", padx=10, pady=5)


def show_manual_upload_window():
    global root
    if root:
        root.after(0, _spawn_manual_upload_window)


def check_notion_connection():
    global notion_status, config
    while True:
        try:
            token = config.get("notion_token")
            if not token or "PLEASE_ENTER" in token:
                notion_status = "Notion: No Token"
                time.sleep(60)
                continue

            notion = Client(auth=token)
            notion.users.me()
            notion_status = "Notion: Connected"
        except Exception as e:
            print(f"Notion connection check failed: {e}")
            notion_status = "Notion: Disconnected"

        time.sleep(300)


def on_clicked(icon, item):
    print("Tray icon clicked")


def quit_program(icon):
    global observer, root, httpd

    print("Quit command received. Shutting down...")
    try:
        if observer:
            is_running = getattr(observer, "is_alive", lambda: False)()
            if is_running:
                observer.stop()
                observer.join(timeout=2)
                print("Observer stopped.")
            else:
                try:
                    observer.stop()
                except Exception:
                    pass
                print("Observer was not running; skip join.")
    except Exception as e:
        print(f"Error stopping observer: {e}")

    try:
        if httpd:
            httpd.shutdown()
            try:
                httpd.server_close()
            except Exception:
                pass
            print("HTTP server stopped.")
    except Exception as e:
        print(f"Error shutting down HTTP server: {e}")

    try:
        if icon:
            icon.stop()
            print("Tray icon stopped.")
    except Exception as e:
        print(f"Error stopping tray icon: {e}")

    try:
        if root:
            root.after(0, root.destroy)
            print("GUI main loop signaled to stop.")
    except Exception as e:
        print(f"Error signaling GUI to stop: {e}")

    try:
        log_file.close()
    except Exception:
        pass

    time.sleep(0.5)
    live = [t for t in threading.enumerate() if t is not threading.current_thread()]
    if any(not t.daemon for t in live):
        print("Threads still alive after shutdown, forcing process exit.")
        os._exit(0)


def setup_tray_icon():
    icon = pystray.Icon("NotionLink")
    icon.icon = create_image()
    icon.title = "NotionLink"

    icon.menu = pystray.Menu(
        pystray.MenuItem(
            lambda icon: notion_status,
            None,
            enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Convert Path", lambda: show_customtkinter_window_convert_path()),
        pystray.MenuItem("Convert Clipboard Path", lambda: convert_clipboard_path()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Notion: Start Manual Upload", lambda: show_manual_upload_window()),
        pystray.MenuItem("Manage Folder-Page Mappings", lambda: show_customtkinter_window_mappings()),
        pystray.MenuItem("Manage Notion Token", lambda: show_customtkinter_window_token_config()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda: quit_program(icon))
    )

    print("Setting up tray icon")
    icon.run()
    print("Tray icon setup complete")


def start_server():
    global httpd
    httpd = socketserver.TCPServer(("", config.get('server_port')), MyHandler)
    print("Starting server")
    httpd.serve_forever()
    print("Server loop exited.")


if __name__ == '__main__':

    print(f"Application starting. PID={os.getpid()}, base_path={path}")
    ctk.set_appearance_mode("dark")

    if not config.get("tutorial_completed", False):
        print("First run detected. Starting setup wizard...")
        run_initial_setup_wizard()

        try:
            with open(config_file_path, "r") as config_file:
                config = json.load(config_file)
        except Exception as e:
            print(f"Failed to reload config after wizard: {e}")
            sys.exit(1)

    try:
        root = ctk.CTk()
        root.withdraw()
    except Exception as e:
        print(f"Could not initialize ctk (possibly no display): {e}")
        sys.exit(1)

    observer = Observer()
    print("Observer created.")
    mappings = config.get("folder_mappings", [])

    if mappings:
        print("--- Starting Initial Sync and Watcher Setup ---")

        try:
            startup_notion_client = Client(auth=config.get("notion_token"))
            print("Priming link cache on startup...")
            cache_threads = []
            page_ids_to_prime = set()

            for mapping in mappings:
                target_page_id = extract_id_from_link_or_id(mapping.get("notion_page_link_or_id"))
                if target_page_id and target_page_id not in page_ids_to_prime:
                    page_ids_to_prime.add(target_page_id)
                    t = threading.Thread(target=get_existing_links, args=(target_page_id, startup_notion_client))
                    cache_threads.append(t)
                    t.start()

            print(f"Waiting for {len(cache_threads)} cache(s) to populate...")
            for t in cache_threads:
                t.join()
            print("All caches populated.")

        except Exception as e:
            print(f"Could not create Notion client to prime cache (invalid token?): {e}")

        print(f"Starting initial sync for {len(mappings)} mapping(s)...")
        for mapping in mappings:
            folder_path = mapping.get("folder_path")
            if folder_path and os.path.isdir(folder_path):
                print(f"--> Queuing startup sync for: {folder_path}")
                sync_thread = threading.Thread(target=upload_folder_to_notion, args=(folder_path,))
                sync_thread.start()
            else:
                print(f"--> Skipping startup sync for invalid path: {folder_path}")

        print(f"Starting file watcher for {len(mappings)} mapping(s)...")
        for mapping in mappings:
            folder_path = mapping.get("folder_path")
            notion_link_or_id = mapping.get("notion_page_link_or_id")

            if not folder_path or not notion_link_or_id:
                print(f"--> Invalid mapping, skipping: {mapping}")
                continue

            path = os.path.expandvars(folder_path)
            target_page_id = extract_id_from_link_or_id(notion_link_or_id)

            if os.path.isdir(path) and target_page_id:
                event_handler = NotionFileHandler(config, target_page_id)
                observer.schedule(event_handler, path, recursive=True)
                print(f"--> Watching: {path} -> PageID: ...{target_page_id[-6:]}")
            else:
                print(f"--> Invalid path or Notion ID for: {path}")

        if observer.emitters:
            observer.start()
            print("File watcher(s) started.")
    else:
        print("No folder mappings configured to watch.")

    tray_thread = threading.Thread(target=setup_tray_icon)
    server_thread = threading.Thread(target=start_server)
    notion_check_thread = threading.Thread(target=check_notion_connection, daemon=True)

    tray_thread.start()
    print(f"Tray thread started: {tray_thread.name}")
    server_thread.start()
    print(f"Server thread started: {server_thread.name}")
    notion_check_thread.start()
    print(f"Notion check thread started (daemon={notion_check_thread.daemon}): {notion_check_thread.name}")

    print("Starting main GUI loop (root.mainloop())...")
    root.mainloop()

    print("Main GUI loop exited. Waiting for threads to join...")
    server_thread.join()
    tray_thread.join()
    print("All threads joined. Exiting.")