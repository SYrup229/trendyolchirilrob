import traceback

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from src.logging_setup import get_app_logger


class BusyDialog(QDialog):
    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        bar = QProgressBar()
        bar.setRange(0, 0)
        layout.addWidget(bar)

    def set_message(self, msg: str) -> None:
        self.label.setText(msg)


class TaskRunner(QObject):
    """
    Runs a blocking function in a background QThread.
    Usage:
      t = TaskRunner(lambda progress: do_thing(progress=progress))
      t.progress.connect(...)
      t.done.connect(...)
      t.failed.connect(...)
      t.start()
    """
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._thread: QThread | None = None
        self._worker: _Worker | None = None

    def start(self) -> None:
        self._thread = QThread()
        self._worker = _Worker(self._fn)
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(self.progress)
        self._worker.done.connect(self.done)
        self._worker.failed.connect(self.failed)
        self._worker.finished.connect(self._cleanup)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def _cleanup(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None
        self.finished.emit()


class _Worker(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
            self.done.emit(result)
        except Exception as e:
            get_app_logger().error("Background task failed: %s\n%s", e, traceback.format_exc())
            self.failed.emit(str(e))
        finally:
            self.finished.emit()
