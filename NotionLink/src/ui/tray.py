from PIL import Image
import pystray


_STATUS_LABEL = {
    "Notion: Connected": "Connected",
    "Notion: Checking...": "Checking",
    "Notion: Retrying...": "Retrying",
    "Notion: Connection Error": "Connection Error",
    "Notion: Disconnected": "Disconnected",
    "Notion: Invalid Token": "Invalid Token",
    "Notion: Access Denied": "Access Denied",
    "Notion: Offline Mode": "Offline Mode",
    "Notion: No Token": "No Token",
}


class PystrayTrayBackend:
    """System tray backend based on pystray with dynamic status menu item."""

    def __init__(self, icon_path, callbacks):
        self.icon_path = icon_path
        self.callbacks = callbacks
        self.status_text = "Notion: No Token"
        self._icon = pystray.Icon("NotionLink")
        self._icon.icon = self._load_icon()
        self._icon.title = "NotionLink"
        self._icon.menu = self._build_menu()

    def start(self):
        self._icon.run_detached()

    def stop(self):
        try:
            self._icon.stop()
        except Exception:
            pass

    def update_status(self, status_text):
        self.status_text = status_text
        self._icon.menu = self._build_menu()
        self._icon.update_menu()

    def show_message(self, title, message):
        try:
            self._icon.notify(message, title=title)
        except Exception:
            print(f"{title}: {message}")

    def _status_with_indicator(self):
        return f"Status: {_STATUS_LABEL.get(self.status_text, self.status_text)}"

    def _load_icon(self):
        return Image.open(self.icon_path)

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Dashboard", self._on_dashboard, default=True),
            pystray.MenuItem(lambda _item: self._status_with_indicator(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check Connection", self._on_status_clicked),
            pystray.MenuItem("Convert Path to Link", self._on_convert),
            pystray.MenuItem("Manual Upload", self._on_upload),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    def _on_status_clicked(self, _icon, _item):
        self.callbacks["manual_status_check"]()

    def _on_dashboard(self, _icon, _item):
        self.callbacks["show_dashboard"]()

    def _on_convert(self, _icon, _item):
        self.callbacks["show_convert_path"]()

    def _on_upload(self, _icon, _item):
        self.callbacks["show_manual_upload"]()

    def _on_quit(self, _icon, _item):
        self.callbacks["quit_app"]()


class TrayIconCompat:
    """Compatibility wrapper so existing code can keep using tray_icon.showMessage/hide."""

    def __init__(self, backend):
        self.backend = backend

    def showMessage(self, title, message, _icon_kind=None, _duration_ms=3000):
        self.backend.show_message(title, message)

    def hide(self):
        self.backend.stop()
