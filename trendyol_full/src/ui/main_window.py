from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget, QStatusBar, QWidget,
)

from src.config import APP_NAME
from src.i18n import t
from src.services.invoicing import auto_create_local_invoices
from src.services.poller import OrderPoller
from src.storage.repo import Repo
from src.ui.bundles_view import BundlesView
from src.ui.campaign_view import CampaignRebaseView
from src.ui.invoices_view import InvoicesView
from src.ui.mapping_view import MappingView
from src.ui.settings_view import SettingsView
from src.ui.style import APP_STYLESHEET
from src.ui.trendyol_products_view import TrendyolProductsView
from src.ui.widgets import TaskRunner


SIDEBAR_LABELS = (t("Trendyol products"), t("Variants"), t("Bundles"),
                  t("Invoices"), t("Campaign rebase"), t("Settings"))
TAB_TRENDYOL_PRODUCTS = 0
TAB_VARIANTS = 1
TAB_BUNDLES = 2
TAB_INVOICES = 3
TAB_CAMPAIGN = 4
TAB_SETTINGS = 5
TAB_MAPPINGS = TAB_VARIANTS  # back-compat alias for older nav code


class MainWindow(QMainWindow):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.setWindowTitle(APP_NAME)
        # Clamp the initial size to the usable desktop (minus taskbar). A fixed
        # 1280×820 is taller than a 1366×768 laptop screen (worse with Windows
        # DPI scaling), which pushed the bottom button rows below the screen edge.
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail is not None:
            self.resize(min(1280, avail.width() - 40), min(820, avail.height() - 60))
        else:
            self.resize(1280, 820)
        self.setMinimumSize(900, 540)
        self.setStyleSheet(APP_STYLESHEET)

        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(210)
        for label in SIDEBAR_LABELS:
            self.sidebar.addItem(QListWidgetItem(label))
        self.sidebar.setCurrentRow(TAB_TRENDYOL_PRODUCTS)
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.products_view = TrendyolProductsView(repo)
        self.mapping_view = MappingView(repo)        # Variants (Oblio → many Trendyol)
        self.bundles_view = BundlesView(repo)         # Bundles (Trendyol → many Oblio)
        self.invoices_view = InvoicesView(repo)
        self.campaign_view = CampaignRebaseView()
        self.settings_view = SettingsView(repo)
        self.stack.addWidget(self.products_view)
        self.stack.addWidget(self.mapping_view)
        self.stack.addWidget(self.bundles_view)
        self.stack.addWidget(self.invoices_view)
        self.stack.addWidget(self.campaign_view)
        self.stack.addWidget(self.settings_view)
        layout.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._on_view_changed)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.showMessage(t("Ready"))

        self.poller = OrderPoller(repo, self)
        self.poller.orders_fetched.connect(self._on_orders_fetched)
        self.poller.invoices_created.connect(self._on_invoices_created)
        self.poller.error.connect(self._on_poll_error)
        self.settings_view.poll_changed.connect(self.poller.restart)
        # Re-sync from Settings → mark dependent catalogs dirty so they reload on next view.
        self.settings_view.data_synced.connect(self._on_data_synced)
        # Reset-all-mappings from Settings → kill local invoices + refresh.
        self.settings_view.mappings_reset.connect(self._on_mappings_reset)
        self.poller.start()

        # ---- mapping-change cascade ----
        # Debounce all mapping changes through one timer; queue up affected listing
        # ids so a targeted-invalidation cascade can scope its work. A `0` listing id
        # means "do a full sweep" (added/removed mapping or unknown listing).
        self._mapping_debounce = QTimer(self)
        self._mapping_debounce.setSingleShot(True)
        self._mapping_debounce.setInterval(500)
        self._mapping_debounce.timeout.connect(self._run_mapping_cascade)
        self._pending_listing_ids: set[int] = set()
        self._cascade_runner: TaskRunner | None = None
        for view in (self.mapping_view, self.bundles_view):
            view.mappings_changed.connect(self._queue_full_cascade)
            view.listing_mapping_changed.connect(self._queue_listing_cascade)

        # ---- per-view dirty flags ----
        # When data changes off-screen, mark the tab dirty; refresh only when shown.
        self._dirty: set[QWidget] = {self.products_view, self.mapping_view,
                                     self.bundles_view, self.invoices_view}

        self.products_view.pair_requested.connect(self._jump_to_mappings)

    # --- view lifecycle -----------------------------------------------------

    def _on_view_changed(self, index: int) -> None:
        w = self.stack.widget(index)
        if w in self._dirty:
            self._dirty.discard(w)
            if hasattr(w, "refresh"):
                w.refresh()

    def _mark_dirty(self, *views: QWidget) -> None:
        for v in views:
            if self.stack.currentWidget() is v and hasattr(v, "refresh"):
                v.refresh()  # currently visible — refresh now
            else:
                self._dirty.add(v)

    # --- mapping cascade ----------------------------------------------------

    def _queue_full_cascade(self) -> None:
        self._pending_listing_ids.add(0)  # 0 = sentinel for "everything"
        self._mapping_debounce.start()

    def _queue_listing_cascade(self, listing_id: int) -> None:
        self._pending_listing_ids.add(int(listing_id))
        self._mapping_debounce.start()

    def _run_mapping_cascade(self) -> None:
        if self._cascade_runner is not None:
            # Another cascade is in flight — requeue and try again shortly.
            self._mapping_debounce.start()
            return
        ids = set(self._pending_listing_ids)
        self._pending_listing_ids.clear()
        full = 0 in ids
        targeted_ids = ids - {0}

        repo = self.repo

        def job(progress):  # runs on a background thread
            invalidated = 0
            if full:
                invalidated += repo.invalidate_local_invoices()
            else:
                for lid in targeted_ids:
                    invalidated += repo.invalidate_local_invoices_for_listing(lid)
            created, _ = auto_create_local_invoices(repo)
            return invalidated, created

        self._cascade_runner = TaskRunner(job, self)
        self._cascade_runner.done.connect(self._on_cascade_done)
        self._cascade_runner.failed.connect(self._on_cascade_failed)
        self._cascade_runner.finished.connect(self._cascade_cleanup)
        self._cascade_runner.start()

    def _on_cascade_done(self, payload) -> None:
        invalidated, created = payload
        if invalidated or created:
            self.status.showMessage(
                f"Mappings updated: regenerated {created} invoice(s) ({invalidated} replaced).",
                5000,
            )
        self._mark_dirty(self.invoices_view, self.products_view)

    def _on_cascade_failed(self, msg: str) -> None:
        self.status.showMessage(f"Mapping update error: {msg}", 8000)

    def _cascade_cleanup(self) -> None:
        self._cascade_runner = None

    # --- poller signals -----------------------------------------------------

    def _on_orders_fetched(self, count: int) -> None:
        self.status.showMessage(f"Order poll: upserted {count} orders", 5000)
        self._mark_dirty(self.products_view)

    def _on_data_synced(self) -> None:
        # Re-sync may have produced different Oblio/Trendyol catalogs; rebuild views
        # that depend on them. Currently visible view refreshes immediately;
        # off-screen views get marked dirty.
        self._mark_dirty(self.mapping_view, self.products_view)

    def _on_mappings_reset(self, removed: int) -> None:
        # Every mapping is gone → every local invoice is now wrong; clear them too.
        try:
            invalidated = self.repo.invalidate_local_invoices()
        except Exception as e:
            self.status.showMessage(f"Mapping reset error: {e}", 8000)
            return
        self.status.showMessage(
            f"Mappings reset: removed {removed}, dropped {invalidated} local invoice(s).",
            6000,
        )
        self._mark_dirty(self.mapping_view, self.bundles_view, self.products_view, self.invoices_view)

    def _on_invoices_created(self, count: int) -> None:
        if count:
            self.status.showMessage(f"Auto-created {count} invoice(s).", 5000)
            self._mark_dirty(self.invoices_view)

    def _on_poll_error(self, msg: str) -> None:
        self.status.showMessage(f"Poll error: {msg}", 8000)

    # --- nav helpers --------------------------------------------------------

    def _jump_to_mappings(self, _trendyol_listing_id: int) -> None:
        self.sidebar.setCurrentRow(TAB_MAPPINGS)

    # --- shutdown -----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self.poller.stop()
        super().closeEvent(event)
