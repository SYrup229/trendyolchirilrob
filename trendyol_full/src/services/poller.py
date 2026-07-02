from PySide6.QtCore import QObject, QThread, QTimer, Signal

from src.services.invoicing import (
    auto_create_local_invoices,
    auto_notify_trendyol_pending,
    refresh_local_drafts,
)
from src.services.sync import fetch_and_store_orders
from src.storage.repo import Repo


class OrderPoller(QObject):
    orders_fetched = Signal(int)
    invoices_created = Signal(int)
    error = Signal(str)

    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._tick)
        self._thread: QThread | None = None
        self._worker: _FetchWorker | None = None

    def start(self) -> None:
        interval_ms = max(60, self.repo.poll_seconds()) * 1000
        self._timer.start(interval_ms)
        self._tick()

    def stop(self) -> None:
        self._timer.stop()
        self._cleanup_worker()

    def restart(self) -> None:
        self.stop()
        self.start()

    def _tick(self) -> None:
        if self._worker is not None:
            return
        self._thread = QThread(self)
        self._worker = _FetchWorker(self.repo)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _on_done(self, payload: tuple[int, int]) -> None:
        orders_count, invoices_count = payload
        self.orders_fetched.emit(orders_count)
        self.invoices_created.emit(invoices_count)

    def _on_failed(self, msg: str) -> None:
        self.error.emit(msg)

    def _cleanup_worker(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None


class _FetchWorker(QObject):
    done = Signal(tuple)  # (orders_fetched, invoices_created)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

    def run(self) -> None:
        try:
            count = fetch_and_store_orders(self.repo)
            # If the poll picked up new orders where Trendyol already has an invoice
            # link attached (e.g. user issued manually in Oblio web UI), drop any
            # local DRAFTS we made for those orders before going further.
            self.repo.cleanup_externally_invoiced_drafts()
            # Refresh unsent drafts so changes to invoicing logic (e.g. country VAT
            # map) propagate to old drafts without a delete-and-recreate.
            refresh_local_drafts(self.repo)
            created, _ = auto_create_local_invoices(self.repo)
            # Retry any Oblio-sent invoices that didn't get notified to Trendyol yet.
            # Cheap when there are none pending; absorbs transient Trendyol errors.
            if not self.repo.oblio_dry_run():
                auto_notify_trendyol_pending(self.repo)
            self.done.emit((count, created))
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            self.finished.emit()
