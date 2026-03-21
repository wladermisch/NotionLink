import ctypes
import json
import msvcrt
import os
import queue
import sys
import tempfile
import threading
import time
import urllib.request


class AppRuntime:
    def __init__(self):
        self._queue = queue.Queue()
        self._stop = threading.Event()
        self.tray_app = None

    def invoke(self, fn):
        self._queue.put(fn)

    def process_pending(self):
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                print(f"Main-thread callback failed: {e}")

    def quit(self):
        self._stop.set()

    @property
    def is_running(self):
        return not self._stop.is_set()


_single_instance_mutex = None
_single_instance_lock_file = None
_open_dashboard_event = None


def _patch_tkinter_variable_cleanup():
    """Avoid noisy Tk finalizer race during shutdown when Tcl loop is already torn down."""
    try:
        import tkinter as tk
    except Exception:
        return

    original_del = getattr(tk.Variable, "__del__", None)
    if original_del is None:
        return

    # Idempotent patching guard.
    if getattr(original_del, "__name__", "") == "_safe_variable_del":
        return

    def _safe_variable_del(self):
        try:
            original_del(self)
        except RuntimeError as cleanup_error:
            if "main thread is not in main loop" in str(cleanup_error):
                return
            raise
        except Exception:
            return

    tk.Variable.__del__ = _safe_variable_del


def _signal_running_instance_open_dashboard_event():
    if not sys.platform.startswith("win"):
        return False
    try:
        EVENT_MODIFY_STATE = 0x0002
        handle = ctypes.windll.kernel32.OpenEventW(EVENT_MODIFY_STATE, False, "NotionLink.OpenDashboard")
        if not handle:
            return False
        ctypes.windll.kernel32.SetEvent(handle)
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _notify_running_instance_open_dashboard(max_attempts=4, delay_s=0.35):
    port = config.get("server_port", 3030)
    url = f"http://127.0.0.1:{port}/_notionlink/open-dashboard"
    for _ in range(max_attempts):
        try:
            with urllib.request.urlopen(url, timeout=1.5):
                return True
        except Exception:
            time.sleep(delay_s)
    return _signal_running_instance_open_dashboard_event()


def _acquire_single_instance_file_lock():
    """Cross-launch guard that works for both python script and packaged exe on Windows."""
    global _single_instance_lock_file

    lock_path = os.path.join(tempfile.gettempdir(), "NotionLink.single_instance.lock")
    lock_file = open(lock_path, "a+")
    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            lock_file.close()
        except Exception:
            pass
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _single_instance_lock_file = lock_file
    return True


try:
    from src.core import APP_VERSION, config, config_file_path, exception_handler
    from src.notion import run_startup_sync
    from src.server import start_server_blocking
    from src.ui.dialogs import InitialSetupDialog, DialogResult
    from src.ui.main import NotionLinkTrayApp
except ImportError as e:
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Critical startup error:\n\n{e}\n\nPlease install dependencies:\npip install -r requirements.txt",
            "NotionLink - Launch Error",
            0x10,
        )
    except Exception:
        pass
    sys.exit(1)


def main():
    print("Sentry initialization deferred (background).")
    sys.excepthook = exception_handler
    _patch_tkinter_variable_cleanup()

    if sys.platform.startswith("win"):
        try:
            appid = f"com.notionlink.app.{APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
            print(f"Set Windows AppUserModelID: {appid}")
        except Exception as e:
            print(f"Failed to set AppUserModelID: {e}")

        # Single-instance guard: second launch focuses first instance via local control endpoint.
        try:
            global _single_instance_mutex
            _single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "NotionLink.SingleInstance")
            global _open_dashboard_event
            _open_dashboard_event = ctypes.windll.kernel32.CreateEventW(None, False, False, "NotionLink.OpenDashboard")
            already_running = ctypes.windll.kernel32.GetLastError() == 183
            lock_ok = _acquire_single_instance_file_lock()
            if already_running:
                _notify_running_instance_open_dashboard()
                return
            if not lock_ok:
                # Fallback for stale lock edge-cases: only exit if we can reach a live instance.
                if _notify_running_instance_open_dashboard():
                    return
                print("Single-instance lock exists but no running app responded; continuing startup.")
        except Exception as e:
            print(f"Single-instance check failed: {e}")

    app = AppRuntime()

    if not config.get("tutorial_completed", False):
        print("First run detected. Starting setup wizard...")
        wizard = InitialSetupDialog(None)
        if wizard.exec_with_intro(show_intro=True, intro_duration_ms=2200) != DialogResult.Accepted:
            print("Setup not completed. Exiting.")
            sys.exit(0)

        try:
            with open(config_file_path, "r") as config_file:
                from src import core
                core.config.clear()
                core.config.update(json.load(config_file))
        except Exception as e:
            print(f"Failed to reload config after wizard: {e}")
            sys.exit(1)

    app.tray_app = NotionLinkTrayApp(app)

    def _start_background_services():
        try:
            print("Starting Notion status check...")
            app.tray_app.run_status_check_thread()

            print("Starting HTTP server thread...")
            threading.Thread(target=start_server_blocking, args=(app.tray_app,), daemon=True).start()

            print("Starting file observer (background)...")
            threading.Thread(target=app.tray_app.start_file_observer, daemon=True).start()

            print("Running startup sync...")
            threading.Thread(target=run_startup_sync, args=(app.tray_app,), daemon=True).start()
        except Exception as e:
            print(f"Error starting background services: {e}")

    app.invoke(_start_background_services)

    print("Starting main app loop...")
    while app.is_running:
        if sys.platform.startswith("win") and _open_dashboard_event:
            WAIT_OBJECT_0 = 0
            if ctypes.windll.kernel32.WaitForSingleObject(_open_dashboard_event, 0) == WAIT_OBJECT_0:
                if app.tray_app is not None:
                    app.tray_app.show_dashboard()
                    app.tray_app.on_user_error("NotionLink is already running and accessible via the tray menu.")
        app.process_pending()
        time.sleep(0.05)

    if sys.platform.startswith("win"):
        try:
            if _open_dashboard_event:
                ctypes.windll.kernel32.CloseHandle(_open_dashboard_event)
        except Exception:
            pass


if __name__ == "__main__":
    main()
