# --- Kern-Importe ---
import sys
import os
import socketserver
from http.server import BaseHTTPRequestHandler
import subprocess
from http import HTTPStatus
from urllib.parse import unquote, urlparse
import win32gui
import win32com.client
import time
import threading
import json
import re 
import webbrowser

# --- PySide6-Importe ---
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidget, QDialog,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QMessageBox, QDialogButtonBox,
    QFileDialog, QInputDialog
)
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
from PySide6.QtCore import QThread, Signal, QObject, QTimer, Qt, QSize

# --- Backend-Importe ---
from PIL import Image
import pyperclip as clip
import pyautogui
from notion_client import Client
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

config_file_path = "config.json"

default_config = {
    "server_port": 3030, 
    "server_host": "http://localhost",
    "notion_token": "PLEASE_ENTER_YOUR_NEW_TOKEN_HERE",
    "folder_mappings": [],
    "tutorial_completed": False
}

# Globale Variablen
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

# --- Konfiguration laden (unverändert) ---
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
# --------------------------------------------------

# --- CSS-Styling für das dunkle Kontextmenü ---
DARK_STYLESHEET = """
    QMenu {
        background-color: #2b2b2b; /* Dunkelgrauer Hintergrund */
        color: #ffffff; /* Weißer Text */
        border: 1px solid #555555; /* Hellerer Rand */
        padding: 5px;
    }
    QMenu::item {
        padding: 5px 25px 5px 20px; /* Platzierung (weniger links für Icon) */
    }
    QMenu::item:disabled {
        color: #888888; /* Grauer Text für deaktivierte Elemente */
    }
    QMenu::item:selected {
        background-color: #0078d7; /* Blaues Highlight (wie bei Windows Dark) */
        color: #ffffff;
    }
    QMenu::separator {
        height: 1px;
        background-color: #555555;
        margin-top: 5px;
        margin-bottom: 5px;
    }
    
    /* Styling für unsere Dialogfenster */
    QDialog {
        background-color: #2b2b2b;
        color: #ffffff;
    }
    QLabel {
        color: #ffffff;
    }
    QLineEdit {
        background-color: #3c3c3c;
        color: #ffffff;
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 5px;
    }
    QPushButton {
        background-color: #228B22; /* Standard-Grün */
        color: #ffffff;
        border: none;
        border-radius: 4px;
        padding: 8px 12px;
    }
    QPushButton:hover {
        background-color: #2E8B57; /* Dunkleres Grün */
    }
    QPushButton:pressed {
        background-color: #1E5631; /* Noch dunkleres Grün */
    }
    QScrollArea {
        background-color: #3c3c3c;
        border: 1px solid #555555;
    }
    QFrame {
        border: 1px solid #444444;
        border-radius: 4px;
    }
"""

# --- Backend-Code (unverändert) ---

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

TRAY_ICON_PNG = resource_path('logo.png')
TRAY_ICON_ICO = resource_path('logo.ico')

shell = win32com.client.Dispatch("WScript.Shell")

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
            response = notion_client.blocks.children.list(block_id=page_id, start_cursor=next_cursor)
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
        if url_path.startswith('/'): url_path = url_path[1:]
        server_link = server_address + url_path
        filename = os.path.basename(full_file_path)
        if notion_page_id_to_use in link_cache and server_link in link_cache[notion_page_id_to_use]:
            print(f"Skipping (already exists): {filename}")
            return
        print(f"Sending file to Notion page ...{notion_page_id_to_use[-6:]}: {filename}")
        notion = Client(auth=notion_token)
        blocks_to_append = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": filename, "link": {"url": server_link}}}]}}]
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
        upload_thread = threading.Thread(target=send_file_to_notion, args=(event.src_path, self.config_data, self.notion_page_id), daemon=True)
        upload_thread.start()

def convert_clipboard_path():
    global config, app
    port = config.get("server_port")
    server_host = config.get("server_host")
    server_address = f"{server_host}:{port}/" 
    path_to_convert = clip.paste().replace("\"", "")
    resulting_path = server_address + path_to_convert.replace("\\", "/")
    clip.copy(resulting_path)
    if app and hasattr(app, 'tray_app'): 
        app.tray_app.tray_icon.showMessage("Path Converted", "Path copied to clipboard.", QIcon(TRAY_ICON_ICO), 2000)

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

def start_server_blocking():
    global httpd
    try:
        httpd = socketserver.TCPServer(("", config.get('server_port')), MyHandler)
        print("Starting server...")
        httpd.serve_forever()
    except Exception as e:
        print(f"Error starting HTTP server: {e}")
    print("Server loop exited.")


# --- Notion Status Check (NORMALER Python-Thread) ---
def check_notion_status_once(status_callback):
    """Führt einen einzelnen Status-Check durch"""
    global config
    try:
        token = config.get("notion_token")
        if not token or "PLEASE_ENTER" in token:
            status_callback("Notion: No Token")
        else:
            notion = Client(auth=token)
            notion.users.me()
            status_callback("Notion: Connected")
    except Exception as e:
        print(f"Notion connection check failed: {e}")
        status_callback("Notion: Disconnected")

# --- Startup Sync (NORMALER Python-Thread, KEIN QThread) ---
def run_startup_sync():
    """Führt den initialen Sync beim Start durch"""
    global config
    mappings = config.get("folder_mappings", [])
    if not mappings:
        return
    
    print("--- Starting Initial Sync ---")
    try:
        startup_notion_client = Client(auth=config.get("notion_token"))
        print("Priming link cache on startup...")
        cache_threads = []
        page_ids_to_prime = set()
        for mapping in mappings:
            target_page_id = extract_id_from_link_or_id(mapping.get("notion_page_link_or_id"))
            if target_page_id and target_page_id not in page_ids_to_prime:
                page_ids_to_prime.add(target_page_id)
                t = threading.Thread(target=get_existing_links, args=(target_page_id, startup_notion_client), daemon=True)
                cache_threads.append(t)
                t.start()
        for t in cache_threads: 
            t.join()
        print("All caches populated.")
    except Exception as e:
        print(f"Could not create Notion client to prime cache (invalid token?): {e}")
    
    print(f"Starting initial file sync for {len(mappings)} mapping(s)...")
    for mapping in mappings:
        folder_path = mapping.get("folder_path")
        if folder_path and os.path.isdir(folder_path):
            print(f"--> Queuing startup sync for: {folder_path}")
            sync_thread = threading.Thread(target=upload_folder_to_notion, args=(folder_path,), daemon=True)
            sync_thread.start()
        else:
            print(f"--> Skipping startup sync for invalid path: {folder_path}")


# --- PySide6 GUI FENSTER (Vollständig implementiert) ---

class BaseDialog(QDialog):
    """Eine Basis-Dialogklasse für einheitliches Aussehen"""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(TRAY_ICON_ICO))
        self.setStyleSheet(DARK_STYLESHEET)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        # WICHTIG: Verhindere dass Dialog die ganze App schließt
        self.setAttribute(Qt.WA_QuitOnClose, False)

class InitialSetupDialog(BaseDialog):
    """Das Setup-Fenster, umgeschrieben in PySide6"""
    def __init__(self):
        super().__init__("Welcome to NotionLink!")
        self.setFixedSize(600, 450)
        layout = QVBoxLayout(self)
        title = QLabel("Welcome to NotionLink!"); title.setStyleSheet("font-size: 20px; font-weight: bold;"); layout.addWidget(title, alignment=Qt.AlignCenter)
        layout.addWidget(QLabel("This one-time setup will connect the app to your Notion account.\nIt only takes 3 steps:"), alignment=Qt.AlignLeft)
        layout.addWidget(QLabel("1. Go to the Notion Integrations page by clicking the button below."), alignment=Qt.AlignLeft)
        link_button = QPushButton("Open Notion Integrations Page"); link_button.clicked.connect(lambda: webbrowser.open_new("https://www.notion.so/my-integrations")); layout.addWidget(link_button)
        layout.addWidget(QLabel("2. Click 'New integration', give it a name (e.g., 'NotionLink'),\nand copy the 'Internal Integration Token' (Secret)."), alignment=Qt.AlignLeft)
        layout.addWidget(QLabel("3. Paste your 'Internal Integration Token' (Secret) here:"), alignment=Qt.AlignLeft)
        self.token_entry = QLineEdit(self); self.token_entry.setEchoMode(QLineEdit.Password); layout.addWidget(self.token_entry)
        self.error_label = QLabel(""); self.error_label.setStyleSheet("color: red;"); layout.addWidget(self.error_label)
        self.save_button = QPushButton("Save and Start Application"); self.save_button.setMinimumHeight(40); self.save_button.clicked.connect(self.save_and_exit); layout.addWidget(self.save_button)
        self.setLayout(layout)
        
    def save_and_exit(self):
        global config
        token = self.token_entry.text()
        if not token or "PLEASE_ENTER" in token or len(token) < 50:
            self.error_label.setText("That doesn't look like a valid token. Please paste the full secret token.")
            return
        config["notion_token"] = token; config["tutorial_completed"] = True
        try:
            with open(config_file_path, "w") as f: json.dump(config, f, indent=4)
            print("Initial setup complete. Token saved."); self.accept()
        except Exception as e:
            print(f"Error saving config during setup: {e}"); self.error_label.setText(f"Error saving config: {e}")

class ManageTokenWindow(BaseDialog):
    """Fenster zur Token-Verwaltung"""
    def __init__(self):
        super().__init__("Manage Notion Token")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Notion Token:"))
        self.token_entry = QLineEdit(self); self.token_entry.setEchoMode(QLineEdit.Password); self.token_entry.setText(config.get("notion_token", "")); layout.addWidget(self.token_entry)
        save_button = QPushButton("Save"); save_button.clicked.connect(self.save_and_close); layout.addWidget(save_button)
        self.setLayout(layout)
    def save_and_close(self):
        global config
        config["notion_token"] = self.token_entry.text()
        with open(config_file_path, "w") as config_file: json.dump(config, config_file, indent=4)
        print("Token updated."); self.accept()

class ManageMappingsWindow(BaseDialog):
    """Fenster zur Zuweisungs-Verwaltung"""
    
    # KEIN Signal mehr - gibt nur Daten zurück
    
    def __init__(self):
        super().__init__("Manage Folder-Page Mappings")
        self.setGeometry(300, 300, 700, 400)
        self.old_folder_paths = set(m["folder_path"] for m in config.get("folder_mappings", []))
        self.current_mappings = list(config.get("folder_mappings", []))
        self.folders_to_backfill = set()  # Speichert neue Ordner
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Automatically watched folders and their Notion pages:"))
        scroll_area = QScrollArea(self); scroll_area.setWidgetResizable(True); layout.addWidget(scroll_area)
        self.scroll_content = QWidget(); self.scroll_layout = QVBoxLayout(self.scroll_content); self.scroll_layout.setAlignment(Qt.AlignTop)
        scroll_area.setWidget(self.scroll_content)
        add_button = QPushButton("Add New Mapping (Folder + Page)"); add_button.clicked.connect(self.add_mapping); layout.addWidget(add_button)
        self.restart_label = QLabel(""); self.restart_label.setStyleSheet("color: green;"); layout.addWidget(self.restart_label)
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close); button_box.accepted.connect(self.save_and_close); button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        self.update_display()
    def update_display(self):
        while self.scroll_layout.count():
            child = self.scroll_layout.takeAt(0);
            if child.widget(): child.widget().deleteLater()
        for mapping in self.current_mappings:
            frame = QFrame(); frame.setLayout(QHBoxLayout());
            text = f"FOLDER: {mapping.get('folder_path', 'N/A')}\nPAGE: {mapping.get('notion_page_link_or_id', 'N/A')}"
            label = QLabel(text); label.setWordWrap(True); frame.layout().addWidget(label)
            remove_button = QPushButton("X"); remove_button.setFixedSize(30, 30)
            remove_button.clicked.connect(lambda checked=False, m=mapping: self.remove_mapping(m))
            frame.layout().addWidget(remove_button)
            self.scroll_layout.addWidget(frame)
    def remove_mapping(self, mapping_to_remove):
        if mapping_to_remove in self.current_mappings:
            self.current_mappings.remove(mapping_to_remove); self.update_display()
    def add_mapping(self):
        folder_path = QFileDialog.getExistingDirectory(self, "Select a folder to watch")
        if not folder_path: return
        notion_link, ok = QInputDialog.getText(self, "Notion Page", "Enter the Notion page link:")
        if not ok or not notion_link: return
        if not extract_id_from_link_or_id(notion_link):
            QMessageBox.warning(self, "Error", "Invalid Notion link. Mapping not created.")
            return
        self.current_mappings.append({"folder_path": folder_path, "notion_page_link_or_id": notion_link})
        self.update_display()
    def save_and_close(self):
        global config
        new_folder_paths = set(m["folder_path"] for m in self.current_mappings)
        self.folders_to_backfill = new_folder_paths - self.old_folder_paths  # Speichern statt emittieren
        config["folder_mappings"] = self.current_mappings
        with open(config_file_path, "w") as config_file: json.dump(config, config_file, indent=4)
        print("Folder mappings updated.")
        if self.folders_to_backfill:
            print(f"Found {len(self.folders_to_backfill)} newly added folders to backfill.")
            self.restart_label.setText("Saved. Starting initial upload. Please restart.")
        else: 
            self.restart_label.setText("Saved. Please restart the program.")
        print("PLEASE RESTART THE APPLICATION for watcher changes to take effect.")
        QTimer.singleShot(2000, self.accept)  # Einfacher Timer zum Schließen

class ManualUploadWindow(BaseDialog):
    """Fenster für den manuellen Upload"""
    
    # KEIN Signal mehr - gibt nur den Pfad zurück
    
    def __init__(self):
        super().__init__("Start Manual Upload")
        self.setGeometry(300, 300, 600, 300)
        self.selected_folder = None  # Speichert ausgewählten Ordner
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a mapped folder to upload:"))
        mappings = config.get("folder_mappings", [])
        if not mappings:
            layout.addWidget(QLabel("No folders have been mapped.", alignment=Qt.AlignCenter)); return
        scroll_area = QScrollArea(self); scroll_area.setWidgetResizable(True); layout.addWidget(scroll_area)
        scroll_content = QWidget(); scroll_layout = QVBoxLayout(scroll_content); scroll_layout.setAlignment(Qt.AlignTop)
        scroll_area.setWidget(scroll_content)
        for mapping in mappings:
            folder_path = mapping["folder_path"]; folder_name = os.path.basename(folder_path); parent_folder = os.path.basename(os.path.dirname(folder_path))
            button_text = f"{folder_name}  (in ...\\{parent_folder})"
            button = QPushButton(button_text); button.clicked.connect(lambda checked=False, p=folder_path: self.start_upload(p))
            scroll_layout.addWidget(button)
    def start_upload(self, folder_path):
        print(f"User selected folder for upload: {folder_path}")
        self.selected_folder = folder_path  # Speichern statt emittieren
        self.accept()  # Dialog sofort schließen

class ConvertPathWindow(BaseDialog):
    """Fenster für den Pfad-Konverter"""
    def __init__(self):
        super().__init__("Convert Path")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Path to convert:"))
        self.entry = QLineEdit(self)
        self.entry.returnPressed.connect(self.convert_path)
        layout.addWidget(self.entry)
        save_button = QPushButton("Convert"); save_button.clicked.connect(self.convert_path); layout.addWidget(save_button)
        self.output_label = QLineEdit(self); self.output_label.setReadOnly(True); layout.addWidget(self.output_label)
    def convert_path(self):
        global config
        port = config.get("server_port"); server_host = config.get("server_host"); server_address = f"{server_host}:{port}/" 
        path_to_convert = self.entry.text().replace("\"", ""); resulting_path = server_address + path_to_convert.replace("\\", "/")
        self.output_label.setText(resulting_path); clip.copy(resulting_path)


# --- Haupt-Applikationsklasse ---

class NotionLinkTrayApp(QObject):
    
    status_updated = Signal(str)
    
    def __init__(self, app):
        super().__init__()
        self.app = app
        
        # Timer für Status-Checks initialisieren
        self.status_check_timer = None
        
        self.green_icon = self.create_color_icon("#28a745") 
        self.red_icon = self.create_color_icon("#dc3545")   
        self.yellow_icon = self.create_color_icon("#ffc107") 
        self.gray_icon = self.create_color_icon("#6c757d")   
        
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(TRAY_ICON_ICO))
        self.tray_icon.setToolTip("NotionLink")
        
        self.menu = QMenu()
        self.menu.setStyleSheet(DARK_STYLESHEET)
        
        self.status_action = QAction(notion_status, self)
        self.status_action.setIcon(self.yellow_icon) 
        self.status_action.setEnabled(True) 
        self.status_action.triggered.connect(self.run_status_check_thread) 
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        
        self.add_menu_action("Convert Path", self.show_convert_path)
        self.add_menu_action("Convert Clipboard Path", convert_clipboard_path)
        self.menu.addSeparator()
        self.add_menu_action("Notion: Start Manual Upload", self.show_manual_upload)
        self.add_menu_action("Manage Folder-Page Mappings", self.show_mappings)
        self.add_menu_action("Manage Notion Token", self.show_token)
        self.menu.addSeparator()
        self.add_menu_action("Quit", self.quit_app)
        
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.show()
        
        self.status_updated.connect(self.update_status_ui)
        
        # Starte ersten Status-Check
        self.run_status_check_thread()
        
    def create_color_icon(self, color_hex):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(color_hex))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        return QIcon(pixmap)

    def add_menu_action(self, text, callback):
        action = QAction(text, self)
        action.triggered.connect(callback)
        self.menu.addAction(action)

    def run_status_check_thread(self):
        """Startet einen Status-Check in einem normalen Python-Thread"""
        def check_and_update():
            check_notion_status_once(self.update_status_ui_from_thread)
        
        # Starte Check in Python-Thread (kein QThread!)
        threading.Thread(target=check_and_update, daemon=True).start()
        
        # Starte Timer für regelmäßige Checks (alle 5 Minuten)
        if not self.status_check_timer:
            self.status_check_timer = QTimer(self)
            self.status_check_timer.timeout.connect(self.run_status_check_thread)
        self.status_check_timer.start(300000)  # 5 Minuten

    def update_status_ui_from_thread(self, status):
        """Thread-sichere Methode zum Updaten der UI"""
        # Verwende Signal um Thread-sicher zu sein
        self.status_updated.emit(status)

    def update_status_ui(self, status):
        self.status_action.setText(status)
        if status == "Notion: Connected":
            self.status_action.setIcon(self.green_icon)
        elif status == "Notion: Disconnected":
            self.status_action.setIcon(self.red_icon)
        elif status == "Notion: No Token":
            self.status_action.setIcon(self.gray_icon)
        else:
            self.status_action.setIcon(self.yellow_icon)

    # KORREKTUR: Kein Slot mehr nötig - Uploads werden direkt gestartet
    # def on_start_upload_requested... ENTFERNT

    def show_window(self, window_name, window_class):
        print(f"Opening dialog: {window_name}")
        dialog = window_class()
        print(f"Dialog created: {window_name}")
        result = dialog.exec()
        print(f"Closed dialog: {window_name} with result: {result}")
        
        # Nach dem Schließen: Prüfe ob Uploads gestartet werden sollen
        if result == QDialog.Accepted:
            if window_name == "mappings" and hasattr(dialog, 'folders_to_backfill'):
                for folder in dialog.folders_to_backfill:
                    print(f"Starting backfill upload for: {folder}")
                    threading.Thread(target=upload_folder_to_notion, args=(folder,), daemon=True).start()
            elif window_name == "upload" and hasattr(dialog, 'selected_folder') and dialog.selected_folder:
                print(f"Starting manual upload for: {dialog.selected_folder}")
                threading.Thread(target=upload_folder_to_notion, args=(dialog.selected_folder,), daemon=True).start()
        
        print(f"Dialog {window_name} cleanup complete, continuing...")
        # Dialog wird automatisch durch Qt gelöscht
        return result
    
    def _delayed_delete(self, dialog):
        """Hilfsmethode zum verzögerten Löschen von Dialogen (NICHT MEHR BENÖTIGT mit WA_DeleteOnClose)"""
        if dialog:
            try:
                dialog.deleteLater()
                print("Dialog deleted.")
            except:
                pass

    def show_convert_path(self):
        print("show_convert_path called")
        self.show_window("convert", ConvertPathWindow)
        print("show_convert_path finished")

    def show_token(self):
        print("show_token called")
        self.show_window("token", ManageTokenWindow)
        print("show_token finished")

    def show_mappings(self):
        print("show_mappings called")
        self.show_window("mappings", ManageMappingsWindow)
        print("show_mappings finished")

    def show_manual_upload(self):
        print("show_manual_upload called")
        self.show_window("upload", ManualUploadWindow)
        print("show_manual_upload finished")

    def quit_app(self):
        global observer, httpd
        print("=== QUIT_APP CALLED - Shutting down... ===")
        
        # Stoppe Timer falls vorhanden
        if hasattr(self, 'status_check_timer') and self.status_check_timer:
            self.status_check_timer.stop()
            print("Status check timer stopped.")
        
        # Stoppe Observer
        if observer:
            try:
                observer.stop()
                observer.join(timeout=2)
                print("Observer stopped.")
            except Exception as e:
                print(f"Error stopping observer: {e}")
        
        # Stoppe HTTP Server
        if httpd:
            try:
                httpd.shutdown()
                print("HTTP server stopped.")
            except Exception as e:
                print(f"Error stopping server: {e}")
        
        # Tray Icon verstecken
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
            print("Tray icon hidden.")
        
        # Log schließen
        try:
            log_file.close()
            print("Log file closed.")
        except:
            pass
            
        # App beenden
        print("Calling app.quit()...")
        self.app.quit()
        print("=== PySide6 App quit complete ===")

# --- Haupt-Startlogik ---
if __name__ == '__main__':
    
    QApplication.setStyle("Fusion")
    app = QApplication(sys.argv)
    
    if not config.get("tutorial_completed", False):
        print("First run detected. Starting setup wizard...")
        wizard = InitialSetupDialog()
        if wizard.exec() != QDialog.Accepted:
            print("Setup not completed. Exiting.")
            sys.exit(0)
        try:
             with open(config_file_path, "r") as config_file:
                config = json.load(config_file)
        except Exception as e:
            print(f"Failed to reload config after wizard: {e}")
            sys.exit(1)
    
    tray_app = NotionLinkTrayApp(app)
    
    # 1. Server-Thread (normaler Python-Thread, KEIN QThread)
    print("Starting server thread...")
    server_thread = threading.Thread(target=start_server_blocking, daemon=True)
    server_thread.start()
    
    # 2. Watchdog-Thread (normaler Python-Thread)
    observer = Observer()
    mappings = config.get("folder_mappings", [])
    if mappings:
        print("--- Starting Watcher Setup ---")
        for mapping in mappings:
            folder_path, notion_link_or_id = mapping.get("folder_path"), mapping.get("notion_page_link_or_id")
            if not folder_path or not notion_link_or_id: continue
            path = os.path.expandvars(folder_path)
            target_page_id = extract_id_from_link_or_id(notion_link_or_id)
            if os.path.isdir(path) and target_page_id:
                event_handler = NotionFileHandler(config, target_page_id)
                observer.schedule(event_handler, path, recursive=True)
                print(f"--> Watching: {path} -> PageID: ...{target_page_id[-6:]}")
        if observer.emitters:
            observer.start()
            print("File watcher(s) started.")
    else:
        print("No folder mappings configured to watch.")

    # 3. Startup-Sync (normaler Python-Thread, KEIN QThread)
    threading.Thread(target=run_startup_sync, daemon=True).start()
    
    print("Starting main GUI loop (app.exec())...")
    sys.exit(app.exec())