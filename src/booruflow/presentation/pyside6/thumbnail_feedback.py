"""Throttle existing thumbnail progress for a compact footer or dialog label."""
from PySide6.QtCore import QObject, QTimer


class ThumbnailFeedback(QObject):
    def __init__(self, loader, display, parent=None):
        super().__init__(parent)
        self.display = display
        self.latest = None
        self.wave = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.flush)
        loader.progress.connect(self.update)

    def update(self, state):
        self.latest = state
        wave = state.get("wave")
        if wave != self.wave or state["done"] >= state["total"]:
            self.wave = wave
            self.timer.stop()
            self.flush()
        elif not self.timer.isActive():
            self.timer.start()

    def flush(self):
        if self.latest is not None:
            failed = self.latest.get("failed", 0)
            self.display(max(0, self.latest["done"] - failed), self.latest["total"], failed)

    def stop(self):
        self.timer.stop()
