import traceback

from PySide6.QtCore import QModelIndex, QObject, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QProgressBar, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView, QVBoxLayout,
)

from src.logging_setup import get_app_logger


# (bg, fg) pairs tuned for the dark palette in style.py.
PILL_GREEN = ("#14532d", "#86efac")
PILL_BLUE = ("#0c4a6e", "#7dd3fc")
PILL_INDIGO = ("#1e3a8a", "#93c5fd")
PILL_AMBER = ("#78350f", "#fcd34d")
PILL_RED = ("#7f1d1d", "#fca5a5")
PILL_GREY = ("#1e293b", "#94a3b8")


class PillDelegate(QStyledItemDelegate):
    """Paints the cell's value as a rounded badge. `resolver(row) -> (text, bg, fg)`
    or None to fall back to plain text rendering. Works with models exposing
    `row_at(row) -> dict` (DictRowsModel). Tooltips still come from the model's
    ToolTipRole (delegates don't intercept them)."""

    def __init__(self, resolver, parent=None):
        super().__init__(parent)
        self._resolver = resolver

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        model = index.model()
        # Support proxy models (Variants/Bundles filter through one).
        if hasattr(model, "mapToSource"):
            src_index = model.mapToSource(index)
            model = model.sourceModel()
        else:
            src_index = index
        row = model.row_at(src_index.row()) if hasattr(model, "row_at") else None
        spec = self._resolver(row) if row else None
        if not spec:
            super().paint(painter, option, index)
            return
        text, bg, fg = spec
        # Draw the normal cell background (selection / alternate stripe) first.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        fm = option.fontMetrics
        pill_h = fm.height() + 4
        pill_w = min(fm.horizontalAdvance(text) + 16, option.rect.width() - 8)
        if pill_w <= 16:
            return
        r = option.rect
        rect = QRect(r.x() + 6, r.y() + (r.height() - pill_h) // 2, pill_w, pill_h)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, pill_h / 2, pill_h / 2)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                         fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width() - 10))
        painter.restore()


def attach_search_debounce(line_edit, callback, ms: int = 200) -> QTimer:
    """Fire `callback` once, `ms` after the user STOPS typing, instead of on every
    keystroke. Table-rebuilding search boxes go from N rebuilds per word to one."""
    timer = QTimer(line_edit)
    timer.setSingleShot(True)
    timer.setInterval(ms)
    timer.timeout.connect(callback)
    line_edit.textChanged.connect(lambda _text: timer.start())
    return timer


def polish_table(table: QTableView, row_height: int = 28) -> None:
    """The shared table look: striped rows, no grid, no row-number gutter,
    single-line cells."""
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)


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
