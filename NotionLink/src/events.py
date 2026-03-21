import threading


class EventSignal:
    """Small thread-safe signal helper with connect/emit API parity."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers = []

    def connect(self, callback):
        with self._lock:
            self._subscribers.append(callback)

    def emit(self, *args, **kwargs):
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(*args, **kwargs)
            except Exception as error:
                print(f"Signal callback failed: {error}")