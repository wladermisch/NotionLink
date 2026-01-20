# Main UI Components - Dashboard Window and System Tray Application
import sys
import os
import json
import time
import fnmatch
import threading
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, QMenu, 
                                QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                                QTextEdit, QCheckBox, QWidget, QDialog, QMessageBox)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QIcon, QAction, QFont, QPainter, QColor, QPixmap
import webbrowser
from notion_client import Client
from watchdog.observers import Observer

from .core import (APP_VERSION, config, config_file_path, logger, 
                   observer, httpd, notification_batch, notified_errors, 
                   is_user_error, sentry_sdk)
from .notion import (get_existing_links, link_cache, sync_file_to_notion, 
                     check_notion_status_once, run_startup_sync, process_pending_uploads)
from .server import (NotionFileHandler, start_server_blocking, 
                     manage_autostart, TRAY_ICON_ICO)
from .ui_styles import DARK_STYLESHEET
from .ui_dialogs import (InitialSetupDialog, ManageTokenWindow, 
                         EditMappingDialog, ManageMappingsListDialog, 
                         ManualUploadWindow, ConvertPathWindow, FeedbackDialog, 
                         LogWatcher)


class MainDashboardWindow(QMainWindow):
    # Main application dashboard window
    
    def __init__(self, tray_app):
        super().__init__()
        self.tray_app = tray_app
        self.setWindowTitle(f"NotionLink {APP_VERSION} - Dashboard")
        self.setWindowIcon(QIcon(TRAY_ICON_ICO))
        self.setMinimumSize(900, 600)
        self.setStyleSheet(DARK_STYLESHEET)
        
        # Initialize UI
        self.init_ui()
        
        # Start log watcher (auto-starts when initialized)
        self.log_watcher = LogWatcher(logger.handlers[0].baseFilename)
        self.log_watcher.new_log_line.connect(self.append_log_line)
        
        # Connect to tray app signals for status updates
        if self.tray_app:
            self.tray_app.status_updated.connect(self.update_token_status)
            self.tray_app.server_error_signal.connect(self.update_status_panel_error)
            self.tray_app.user_error_signal.connect(self.update_status_panel_warning)
            self.tray_app.op_success_signal.connect(self.reset_status_panel)

    def init_ui(self):
        # Main container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        
        # Left column - Actions & Settings (narrower)
        left_column = self._init_left_column()
        
        # Right column - Status & Logs (wider)
        right_column = self._init_right_column()
        
        # Add columns to main layout (30% left, 70% right)
        main_layout.addLayout(left_column, stretch=30)
        main_layout.addLayout(right_column, stretch=70)

    def _init_left_column(self):
        left_column = QVBoxLayout()
        left_column.setSpacing(10)
        
        # Actions Section
        actions_label = QLabel("Quick Actions")
        actions_label.setStyleSheet("font-size: 14pt; font-weight: bold; margin-bottom: 5px;")
        left_column.addWidget(actions_label)
        
        convert_btn = QPushButton("Convert Path to Link")
        convert_btn.clicked.connect(self.tray_app.show_convert_path)
        left_column.addWidget(convert_btn)
        
        upload_btn = QPushButton("Manual Upload")
        upload_btn.clicked.connect(self.tray_app.show_manual_upload)
        left_column.addWidget(upload_btn)
        
        left_column.addSpacing(20)
        
        # Management Section
        mgmt_label = QLabel("Management")
        mgmt_label.setStyleSheet("font-size: 14pt; font-weight: bold; margin-bottom: 5px;")
        left_column.addWidget(mgmt_label)
        
        page_btn = QPushButton("Page Mappings")
        page_btn.clicked.connect(self.tray_app.show_page_mappings)
        left_column.addWidget(page_btn)
        
        db_btn = QPushButton("Database Mappings")
        db_btn.clicked.connect(self.tray_app.show_database_mappings)
        left_column.addWidget(db_btn)
        
        token_btn = QPushButton("Notion Token")
        token_btn.clicked.connect(self.tray_app.show_token)
        left_column.addWidget(token_btn)
        
        left_column.addSpacing(20)
        
        # Settings Section
        settings_label = QLabel("Settings")
        settings_label.setStyleSheet("font-size: 14pt; font-weight: bold; margin-bottom: 5px;")
        left_column.addWidget(settings_label)
        
        self.autostart_checkbox = QCheckBox("Start with Windows")
        self.autostart_checkbox.setChecked(config.get("autostart_with_windows", False))
        self.autostart_checkbox.toggled.connect(self.tray_app.toggle_autostart)
        left_column.addWidget(self.autostart_checkbox)
        
        self.sentry_checkbox = QCheckBox("Enable Error Reports")
        self.sentry_checkbox.setChecked(config.get("sentry_enabled", False))
        self.sentry_checkbox.toggled.connect(self.tray_app.toggle_sentry)
        left_column.addWidget(self.sentry_checkbox)
        
        feedback_btn = QPushButton("Send Feedback")
        feedback_btn.clicked.connect(self.tray_app.show_feedback_dialog)
        left_column.addWidget(feedback_btn)

        # Small Help button for quick access to runtime instructions and wiki
        help_btn = QPushButton("Help")
        help_btn.setToolTip("Help & documentation")
        help_btn.clicked.connect(self.show_help)
        left_column.addWidget(help_btn)
        
        left_column.addStretch()
        
        quit_btn = QPushButton("Quit NotionLink")
        quit_btn.setStyleSheet("background-color: #8B0000; font-weight: bold;")
        quit_btn.clicked.connect(self.tray_app.quit_app)
        left_column.addWidget(quit_btn)
        
        return left_column

    def _init_right_column(self):
        right_column = QVBoxLayout()
        right_column.setSpacing(10)
        
        # Status Panel
        status_header = QLabel("System Status")
        status_header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        right_column.addWidget(status_header)
        
        self.status_panel = QLabel("NotionLink is running...")
        self.status_panel.setStyleSheet(self._get_status_style("#1e3a1e", "#66ff66", "#2e5a2e"))
        self.status_panel.setWordWrap(True)
        self.status_panel.setMinimumHeight(80)
        right_column.addWidget(self.status_panel)
        
        # Token Status Indicator
        token_status_layout = QHBoxLayout()
        self.token_status_icon = QLabel()
        self.token_status_icon.setFixedSize(16, 16)
        self.update_token_status_icon(self.tray_app.current_token_status if self.tray_app else "Notion: No Token")
        token_status_layout.addWidget(self.token_status_icon)
        
        self.token_status_label = QLabel(self.tray_app.current_token_status if self.tray_app else "Notion: No Token")
        self.token_status_label.setStyleSheet("font-weight: bold;")
        token_status_layout.addWidget(self.token_status_label)
        
        # Reconnect button (hidden by default)
        self.reconnect_btn = QPushButton("Retry Connection")
        self.reconnect_btn.setCursor(Qt.PointingHandCursor)
        self.reconnect_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a4a6a;
                color: white;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #3a5a7a;
            }
        """)
        self.reconnect_btn.setVisible(False)
        self.reconnect_btn.clicked.connect(self.tray_app.start_auto_retry_loop)
        token_status_layout.addWidget(self.reconnect_btn)
        
        # Go Offline button (hidden by default)
        self.offline_btn = QPushButton("Go Offline")
        self.offline_btn.setCursor(Qt.PointingHandCursor)
        self.offline_btn.setStyleSheet("""
            QPushButton {
                background-color: #444444;
                color: #cccccc;
                border: none;
                padding: 4px 8px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 9pt;
            }
            QPushButton:hover {
                background-color: #555555;
            }
        """)
        self.offline_btn.setVisible(False)
        self.offline_btn.clicked.connect(self.tray_app.activate_offline_mode_manually)
        token_status_layout.addWidget(self.offline_btn)
        
        token_status_layout.addStretch()
        right_column.addLayout(token_status_layout)
        
        # Log Viewer
        log_label = QLabel("Application Log")
        log_label.setStyleSheet("font-size: 12pt; font-weight: bold; margin-top: 10px;")
        right_column.addWidget(log_label)
        
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setStyleSheet("""
            QTextEdit {
                background-color: #1a1a1a;
                color: #cccccc;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 9pt;
                border: 1px solid #444444;
                border-radius: 3px;
            }
        """)
        right_column.addWidget(self.log_display, stretch=1)
        # App version label (small, unobtrusive) shown under the log
        self.version_label = QLabel(f"NotionLink - wladermisch | Version {APP_VERSION}")
        self.version_label.setStyleSheet("font-size: 8pt; color: #888888; margin-top: 6px;")
        self.version_label.setAlignment(Qt.AlignRight)
        right_column.addWidget(self.version_label)
        
        return right_column

    def _get_status_style(self, bg_color, text_color, border_color):
        return f"""
            QLabel {{
                background-color: {bg_color};
                color: {text_color};
                padding: 15px;
                border-radius: 5px;
                font-size: 11pt;
                font-weight: bold;
                border: 2px solid {border_color};
            }}
        """

    def update_token_status(self, status):
        # Update token status display and dashboard status panel
        self.token_status_label.setText(status)
        self.update_token_status_icon(status)
        
        # Show/hide buttons
        self.reconnect_btn.setVisible(False)
        self.offline_btn.setVisible(False)
        
        if status == "Notion: Connection Error":
            self.reconnect_btn.setVisible(True)
            self.reconnect_btn.setText("Retry Connection")
            self.offline_btn.setVisible(True)
        elif status == "Notion: Offline Mode":
            self.reconnect_btn.setVisible(True)
            self.reconnect_btn.setText("Reconnect Now")
        elif status == "Notion: Disconnected":
            self.reconnect_btn.setVisible(True)
            self.reconnect_btn.setText("Retry Connection")
        
        # Update dashboard status panel based on token status
        if status == "Notion: Connected":
            self.status_panel.setText("NotionLink is running...")
            self.status_panel.setStyleSheet(self._get_status_style("#1e3a1e", "#66ff66", "#2e5a2e"))
        elif status == "Notion: Connection Error":
            self.status_panel.setText("Connection Failed. Please check your internet connection or Notion token.")
            self.status_panel.setStyleSheet(self._get_status_style("#4a3a1a", "#ffcc66", "#6a5a2a"))
        elif status in ["Notion: Disconnected", "Notion: Invalid Token", "Notion: Access Denied"]:
            self.status_panel.setText(f"{status}")
            self.status_panel.setStyleSheet(self._get_status_style("#4a1a1a", "#ff6666", "#6a2a2a"))
        elif status == "Notion: Offline Mode":
            self.status_panel.setText("Offline Mode Active. Sync is paused.")
            self.status_panel.setStyleSheet(self._get_status_style("#333333", "#aaaaaa", "#555555"))
        elif status == "Notion: No Token":
            self.status_panel.setText(f"{status} - Please configure your Notion token")
            self.status_panel.setStyleSheet(self._get_status_style("#4a3a1a", "#ffcc66", "#6a5a2a"))
        
    def update_token_status_icon(self, status):
        # Update the colored circle indicator
        if status == "Notion: Connected":
            color = "#00ff00"  # Green
        elif status == "Notion: Disconnected" or status == "Notion: Connection Error":
            color = "#ff0000"  # Red
        elif status == "Notion: Offline Mode":
            color = "#808080"  # Gray
        elif status == "Notion: No Token":
            color = "#808080"  # Gray
        else:
            color = "#ffff00"  # Yellow
        
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        self.token_status_icon.setPixmap(pixmap)
        
    def update_status_panel_error(self, message):
        # Update status panel with server error (red)
        self.status_panel.setText(f"❌ {message}")
        self.status_panel.setStyleSheet(self._get_status_style("#4a1a1a", "#ff6666", "#6a2a2a"))
    
    def update_status_panel_warning(self, message):
        # Update status panel with user error warning (yellow)
        self.status_panel.setText(f"{message}")
        self.status_panel.setStyleSheet(self._get_status_style("#4a4a1a", "#ffff66", "#6a6a2a"))

    def reset_status_panel(self):
        # Reset status panel to normal state
        self.status_panel.setText("NotionLink is running...")
        self.status_panel.setStyleSheet(self._get_status_style("#1e3a1e", "#66ff66", "#2e5a2e"))
        
    def append_log_line(self, line):
        # Append new log line to display
        self.log_display.append(line)
        scrollbar = self.log_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def show_help(self):
        # Show a compact help dialog with runtime notes and wiki link
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("NotionLink - Help & Docs")
        msg.setWindowIcon(QIcon(TRAY_ICON_ICO))
        msg.setStyleSheet(DARK_STYLESHEET.replace("QMenu", "QMessageBox"))
        msg.setText("Important Notes")
        informative = (
            "<ul>"
            "<li><b>Keep the app running:</b> NotionLink must be running to open files from the local links. "
            "If the app is not running those links will not resolve.</li>"
            "<li><b>Add the integration:</b> After creating a Notion integration, add it to the page via the three-dots menu (Share → Add connections) so the integration can access the page.</li>"
            "<li><b>Offline Support:</b> If you have no internet connection, you can still link files manually. "
            "Use 'Convert Path to Link' to generate a link, then paste it into your Notion page yourself. "
            "The link will work locally as long as NotionLink is running.</li>"
            f"<li><a href=\"https://github.com/wladermisch/NotionLink/wiki\">Full documentation & FAQ (Wiki)</a></li>"
            "</ul>"
        )
        msg.setInformativeText(informative)
        open_btn = msg.addButton("Open Wiki", QMessageBox.AcceptRole)
        msg.addButton(QMessageBox.Close)
        msg.exec()

        if msg.clickedButton() == open_btn:
            try:
                webbrowser.open_new("https://github.com/wladermisch/NotionLink/wiki")
            except Exception:
                pass
        
    def closeEvent(self, event):
        # Handle window close event
        # Stop log watcher timer
        if self.log_watcher and self.log_watcher.timer:
            self.log_watcher.timer.stop()
        if self.tray_app:
            self.tray_app.on_dashboard_closed()
        event.accept()


class NotionLinkTrayApp(QObject):
    # System tray application controller
    
    status_updated = Signal(str)
    server_error_signal = Signal(str)
    user_error_signal = Signal(str)
    op_success_signal = Signal()
    offline_mode_signal = Signal()
    
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.dashboard_window = None
        self.current_token_status = "Notion: No Token"
        self.status_check_timer = None
        self.notification_timer = None
        self.auto_retry_timer = None
        self.is_auto_retrying = False
        
        # Cached errors for dashboard display
        self.last_server_error = None
        self.last_user_error = None
        
        # Create colored icons for status
        self.green_icon = self.create_color_icon("#00ff00")
        self.yellow_icon = self.create_color_icon("#ffff00")
        self.red_icon = self.create_color_icon("#ff0000")
        self.gray_icon = self.create_color_icon("#808080")
        
        # Create system tray icon
        self.tray_icon = QSystemTrayIcon(QIcon(TRAY_ICON_ICO), parent=app)
        
        # Create context menu
        self.menu = QMenu()
        self.menu.setStyleSheet(DARK_STYLESHEET)
        
        # Status indicator (clickable) - shows current Notion token status
        self.status_action = QAction("Notion: No Token", self)
        self.status_action.setIcon(self.gray_icon)
        # make it clickable so user can force a manual status check
        self.status_action.setEnabled(True)
        self.status_action.setToolTip("Click to check Notion token status")
        self.status_action.triggered.connect(self.manual_status_check)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        
        # Main actions
        self.add_menu_action("Dashboard", self.show_dashboard, bold=True)
        self.menu.addSeparator()
        self.add_menu_action("Convert Path to Link", self.show_convert_path)
        self.add_menu_action("Manual Upload", self.show_manual_upload)
        self.menu.addSeparator()
        self.add_menu_action("Page Mappings", self.show_page_mappings)
        self.add_menu_action("Database Mappings", self.show_database_mappings)
        self.add_menu_action("Notion Token", self.show_token)
        self.menu.addSeparator()
        
        # Settings
        self.autostart_action = QAction("Start with Windows", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(config.get("autostart_with_windows", False))
        self.autostart_action.toggled.connect(self.toggle_autostart)
        self.menu.addAction(self.autostart_action)
        
        self.add_menu_action("Send Feedback", self.show_feedback_dialog)
        self.menu.addSeparator()
        self.add_menu_action("Quit", self.quit_app)
        
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()
        
        # Start notification batch timer
        self.notification_timer = QTimer(self)
        self.notification_timer.timeout.connect(self.process_notification_batch)
        self.notification_timer.start(5000)
        
        # Connect status signal
        self.status_updated.connect(self.update_status_ui)
        self.offline_mode_signal.connect(self.on_offline_mode_activated)
        
    def process_notification_batch(self):
        # Process batched sync notifications
        global notification_batch
        if not notification_batch:
            return
        
        # Create a snapshot to avoid "dictionary changed size during iteration"
        batch_snapshot = dict(notification_batch)
        notification_batch.clear()
        
        print(f"Processing notification batch with {len(batch_snapshot)} entries...")
        for notion_title, filenames in batch_snapshot.items():
            count = len(filenames)
            if count == 1:
                message = f"'{filenames[0]}' was added to {notion_title}."
            else:
                message = f"Synced {count} new files to {notion_title}."
            self.tray_icon.showMessage("NotionLink: Sync Success", message, QSystemTrayIcon.Information, 3000)

    def on_tray_icon_activated(self, reason):
        # Handle tray icon click
        print(f"Tray icon activated, reason={reason}")
        try:
            trigger_value = QSystemTrayIcon.ActivationReason.Trigger
        except Exception:
            trigger_value = getattr(QSystemTrayIcon, 'Trigger', None)

        if reason == trigger_value or reason == QSystemTrayIcon.Trigger:
            print("Tray click detected: opening dashboard")
            self.show_dashboard()
            # Trigger a status check when opening dashboard to refresh status
            self.manual_status_check()
        
    def create_color_icon(self, color_hex):
        # Create a small colored circle icon
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(color_hex))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 12, 12)
        painter.end()
        return QIcon(pixmap)

    def add_menu_action(self, text, callback, bold=False):
        # add action to tray menu
        action = QAction(text, self)
        if bold:
            font = action.font()
            font.setBold(True)
            action.setFont(font)
        action.triggered.connect(callback)
        self.menu.addAction(action)

    def run_status_check_thread(self):
        # Check Notion token status periodically
        def check_and_update():
            check_notion_status_once(self.update_status_ui_from_thread)
        
        threading.Thread(target=check_and_update, daemon=True).start()
        
        if not self.status_check_timer:
            self.status_check_timer = QTimer(self)
            self.status_check_timer.timeout.connect(self.run_status_check_thread)
            self.status_check_timer.start(300000)  # 5 minutes

    def update_status_ui_from_thread(self, status):
        # Emit signal from background thread
        self.status_updated.emit(status)

    def manual_status_check(self):
        # User-triggered manual check of Notion token status (runs in background)
        def _check():
            try:
                # Show an intermediate checking state
                self.status_updated.emit("Notion: Checking...")
                # Force check even if in offline mode
                check_notion_status_once(self.update_status_ui_from_thread, force=True)
            except Exception as e:
                print(f"Manual status check failed: {e}")
                # Fallback to disconnected
                self.status_updated.emit("Notion: Disconnected")

        threading.Thread(target=_check, daemon=True).start()

    def start_auto_retry_loop(self):
        # Start auto-retry loop for connection
        if self.is_auto_retrying:
            return
            
        print("Starting auto-retry loop...")
        self.is_auto_retrying = True
        self.status_updated.emit("Notion: Retrying...")
        
        # Disable offline mode temporarily to allow checks
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
                    self.tray_icon.showMessage("NotionLink", "Internet connection restored.", QSystemTrayIcon.Information, 3000)
                    
                    # Process any pending uploads that were queued during outage
                    process_pending_uploads()
                    
                    if self.auto_retry_timer:
                        self.auto_retry_timer.stop()
                        self.auto_retry_timer = None
                else:
                    print(f"Auto-retry failed ({status}). Retrying in 10s...")
                    # Don't update UI to "Disconnected" constantly to avoid flickering/spam
                    # Just keep "Retrying..." or update if it changes to something specific like "Invalid Token"
                    if status != "Notion: Connection Error" and status != "Notion: Disconnected":
                         self.status_updated.emit(status)
            
            check_notion_status_once(_callback, force=True)

        # Create timer for periodic checks
        self.auto_retry_timer = QTimer(self)
        self.auto_retry_timer.timeout.connect(lambda: threading.Thread(target=_retry_check, daemon=True).start())
        self.auto_retry_timer.start(10000) # Check every 10 seconds
        
        # Run first check immediately
        threading.Thread(target=_retry_check, daemon=True).start()

    def update_status_ui(self, status):
        # Update status UI in main thread
        self.current_token_status = status
        self.status_action.setText(status)
        if status == "Notion: Connected":
            self.status_action.setIcon(self.green_icon)
        elif status == "Notion: Disconnected" or status == "Notion: Connection Error":
            self.status_action.setIcon(self.red_icon)
        elif status == "Notion: Offline Mode":
            self.status_action.setIcon(self.gray_icon)
        elif status == "Notion: No Token":
            self.status_action.setIcon(self.gray_icon)
        else:
            self.status_action.setIcon(self.yellow_icon)
            
        if self.dashboard_window:
            self.dashboard_window.update_token_status(status)

    def sync_autostart_ui(self, is_checked):
        # Sync autostart checkbox across UI components
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(is_checked)
        self.autostart_action.blockSignals(False)
        
        if self.dashboard_window:
            self.dashboard_window.autostart_checkbox.blockSignals(True)
            self.dashboard_window.autostart_checkbox.setChecked(is_checked)
            self.dashboard_window.autostart_checkbox.blockSignals(False)

    def toggle_autostart(self, checked):
        # Toggle Windows autostart
        global config
        print(f"Setting autostart to: {checked}")
        try:
            manage_autostart(checked)
            config["autostart_with_windows"] = checked
            with open(config_file_path, "w") as f:
                json.dump(config, f, indent=4)
            self.sync_autostart_ui(checked)
                
        except Exception as e:
            print(f"Error toggling autostart: {e}")
            self.sync_autostart_ui(not checked) 
            
            error_dialog = QMessageBox()
            error_dialog.setWindowIcon(QIcon(TRAY_ICON_ICO))
            error_dialog.setStyleSheet(DARK_STYLESHEET.replace("QMenu", "QMessageBox"))
            error_dialog.setIcon(QMessageBox.Warning)
            error_dialog.setText("Autostart Error")
            error_dialog.setInformativeText(f"Could not update autostart setting.\nError: {e}")
            error_dialog.setStandardButtons(QMessageBox.Ok)
            error_dialog.exec()

    def sync_sentry_ui(self, is_checked):
        # Sync Sentry checkbox across UI components
        if self.dashboard_window:
            self.dashboard_window.sentry_checkbox.blockSignals(True)
            self.dashboard_window.sentry_checkbox.setChecked(is_checked)
            self.dashboard_window.sentry_checkbox.blockSignals(False)

    def toggle_sentry(self, checked):
        # Toggle Sentry error reporting
        global config
        print(f"Setting Sentry to: {checked}")
        try:
            config["sentry_enabled"] = checked
            with open(config_file_path, "w") as f:
                json.dump(config, f, indent=4)
            self.sync_sentry_ui(checked)
            
            if checked:
                print("Sentry enabled. Will take effect on next restart.")
            else:
                print("Sentry disabled. Will take effect on next restart.")
                
        except Exception as e:
            print(f"Error toggling Sentry: {e}")
            self.sync_sentry_ui(not checked)

    def stop_file_observer(self):
        # Stop file system observer
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
        # Start file system observer for all mapped folders
        global observer, config
        self.stop_file_observer()
        
        observer = Observer()
        all_mappings = [("page", pm) for pm in config.get("page_mappings", [])] + \
                       [("database", dbm) for dbm in config.get("database_mappings", [])]
        
        if all_mappings:
            print("--- (Re)starting Watcher Setup ---")
            for mapping_type, mapping in all_mappings:
                notion_id = mapping.get("notion_id")
                for folder_path in mapping.get("folders", []):
                    if not folder_path or not notion_id:
                        continue
                    path = os.path.expandvars(folder_path)
                    if os.path.isdir(path):
                        event_handler = NotionFileHandler(config, mapping, mapping_type, self)
                        # Watch only the top-level folder (non-recursive) so files dropped as
                        # nested folders are not automatically synced. This prevents syncing
                        # files that are placed inside a subfolder of the watched folder.
                        observer.schedule(event_handler, path, recursive=False)
                        print(f"--> Watching (non-recursive): {path} -> {mapping_type} ID: ...{notion_id[-6:]}")
            if observer.emitters:
                observer.start()
                print("File watcher(s) started.")
        else:
            print("No folder mappings configured to watch.")

    def restart_file_observer(self):
        # Restart file observer in background thread
        threading.Thread(target=self.start_file_observer, daemon=True).start()

    def upload_folder_to_notion(self, folder_path, mapping_config, mapping_type):
        # Manual upload all files in folder to Notion
        print(f"Starting manual upload for folder: {folder_path}")
        global config, notified_errors
        
        target_page_id = mapping_config.get("notion_id")
        notion_title = mapping_config.get("notion_title", "Unknown")
        if not target_page_id:
            print(f"Error: No Notion ID found for folder '{folder_path}'.")
            return
            
        print(f"Found mapping. Uploading files to {mapping_type} ...{target_page_id[-6:]}")
        try:
            notion = Client(auth=config.get("notion_token"))
            if mapping_type == "page":
                get_existing_links(target_page_id, notion, force_refresh=True)
                
                # Note: Don't warn about empty cache here - pages might legitimately be empty on first sync
                # If there's a real access issue, get_existing_links() will catch it and set cache to empty on API error
            
            files_uploaded_count = 0
            handler = NotionFileHandler(config, mapping_config, mapping_type, self)
            
            for filename in os.listdir(folder_path):
                full_file_path = os.path.join(folder_path, filename)
                if os.path.isfile(full_file_path):
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
                    
                    if '404' in error_str or 'could not find' in error_str:
                        msg = f"Cannot access Notion page '{notion_title}'. Please ensure the page is shared with your integration."
                    elif '401' in error_str or 'unauthorized' in error_str or 'invalid token' in error_str:
                        msg = f"Invalid Notion token. Please update your token in settings."
                    elif '403' in error_str or 'forbidden' in error_str or 'not shared' in error_str:
                        msg = f"Access denied to '{notion_title}'. Check Notion page sharing permissions."
                    else:
                        msg = f"Configuration issue accessing '{notion_title}'. Please check your settings."
                    
                    print(f"ERROR: {msg}")
                    if self:
                        self.tray_icon.showMessage("NotionLink: Configuration Error", msg, QSystemTrayIcon.Warning, 5000)
                        self.user_error_signal.emit(msg)
                return
            else:
                if error_key not in notified_errors:
                    notified_errors.add(error_key)
                    
                    sentry_active = 'sentry_sdk' in globals() and sentry_sdk is not None
                    if sentry_active:
                        bug_msg = f"An unexpected error occurred during upload to '{notion_title}'. The problem has been logged and sent to the developer for fixing in the next version."
                    else:
                        bug_msg = f"An unexpected error occurred during upload to '{notion_title}'. The problem has been logged for review."
                    
                    print(f"An unexpected error occurred during upload: {e}")
                    if self:
                        self.tray_icon.showMessage("NotionLink: Application Error", bug_msg, QSystemTrayIcon.Critical, 5000)
                        self.user_error_signal.emit(bug_msg)
                raise e

    def show_window(self, window_name, window_class, **kwargs):
        # Generic window/dialog display handler
        print(f"Opening dialog: {window_name}")
        dialog = window_class(self, **kwargs) 
        print(f"Dialog created: {window_name}")
        result = dialog.exec()
        print(f"Closed dialog: {window_name} with result: {result}")
        
        if result == QDialog.Accepted:
            if window_name == "upload" and hasattr(dialog, 'selected_task') and dialog.selected_task:
                folder, mapping, m_type = dialog.selected_task
                print(f"Starting manual upload for: {folder}")
                threading.Thread(target=self.upload_folder_to_notion, args=(folder, mapping, m_type), daemon=True).start()
        
        print(f"Dialog {window_name} cleanup complete, continuing...")
        return result
    
    def show_dashboard(self):
        # Show main dashboard window
        print("show_dashboard called")
        if self.dashboard_window is None:
            print("Creating new dashboard window...")
            self.dashboard_window = MainDashboardWindow(self)
        
        self.dashboard_window.show()
        self.dashboard_window.activateWindow()
        self.dashboard_window.raise_()

    def on_dashboard_closed(self):
        # Handle dashboard window closure
        print("Dashboard window closed.")
        self.dashboard_window = None
    
    def show_feedback_dialog(self):
        # Show feedback dialog
        print("show_feedback_dialog called")
        self.show_window("feedback", FeedbackDialog)
        print("show_feedback_dialog finished")
    
    def show_convert_path(self):
        # Show path conversion dialog
        print("show_convert_path called")
        self.show_window("convert", ConvertPathWindow)
        print("show_convert_path finished")

    def show_token(self):
        # Show token management dialog
        print("show_token called")
        self.show_window("token", ManageTokenWindow)
        print("show_token finished")

    def show_page_mappings(self):
        # Show page mappings dialog
        print("show_page_mappings called")
        self.show_window("mappings_page", ManageMappingsListDialog, mapping_type="page")
        print("show_page_mappings finished")

    def show_database_mappings(self):
        # Show database mappings dialog
        print("show_database_mappings called")
        self.show_window("mappings_db", ManageMappingsListDialog, mapping_type="database")
        print("show_database_mappings finished")

    def show_manual_upload(self):
        # Show manual upload dialog
        print("show_manual_upload called")
        self.show_window("upload", ManualUploadWindow)
        print("show_manual_upload finished")

    def activate_offline_mode_manually(self):
        # Manually activate offline mode
        import src.core as core_module
        core_module.offline_mode = True
        self.on_offline_mode_activated()

    def trigger_offline_mode_ui(self):
        # Trigger offline mode UI update from background thread
        self.offline_mode_signal.emit()

    def on_offline_mode_activated(self):
        # Handle offline mode activation (runs in main thread)
        print("Offline mode activated - showing popup")
        
        # Update status UI
        self.update_status_ui("Notion: Offline Mode")
        if self.dashboard_window:
            self.dashboard_window.update_status_panel_warning("Offline Mode Active. Restart NotionLink to reconnect.")
            
        # Show popup
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("NotionLink - Offline Mode")
        msg.setWindowIcon(QIcon(TRAY_ICON_ICO))
        msg.setText("Offline Mode Activated")
        msg.setInformativeText(
            "NotionLink could not connect to Notion after retrying.\n\n"
            "• Existing links will continue to work locally as long as NotionLink is running.\n"
            "• New files will NOT be synced automatically.\n"
            "• To add new links, use 'Convert Path to Link' and paste them manually into Notion.\n"
            "• These links are hosted locally and are only available on this computer."
        )
        
        # Add buttons
        retry_btn = msg.addButton("Keep Retrying", QMessageBox.ActionRole)
        stay_offline_btn = msg.addButton("Stay Offline", QMessageBox.ActionRole)
        msg.setDefaultButton(stay_offline_btn)
        
        msg.exec()
        
        if msg.clickedButton() == retry_btn:
            self.start_auto_retry_loop()

    def quit_app(self):
        # Graceful application shutdown
        global observer, httpd
        print("=== QUIT_APP CALLED - Shutting down... ===")
        
        if self.dashboard_window:
            self.dashboard_window.close()
            
        if hasattr(self, 'status_check_timer') and self.status_check_timer:
            self.status_check_timer.stop()
            print("Status check timer stopped.")
        
        if hasattr(self, 'notification_timer') and self.notification_timer:
            self.notification_timer.stop()
            print("Notification batch timer stopped.")
            
        self.stop_file_observer()
        
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
                print("HTTP server stopped and socket closed.")
            except Exception as e:
                print(f"Error stopping server: {e}")
        
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
            print("Tray icon hidden.")
        
        try:
            import logging
            logging.shutdown()
            print("Log handlers shut down.")
        except Exception:
            pass
            
        print("Calling app.quit()...")
        self.app.quit()
        print("=== PySide6 App quit complete ===")
