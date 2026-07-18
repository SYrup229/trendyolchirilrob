from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.services.invoicing import InvoicePreview, auto_create_local_invoices, build_preview
from src.storage.repo import Repo
from src.ui.widgets import enable_cell_copy


class OrdersView(QWidget):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel("Orders waiting for mapping")
        title.setObjectName("title")
        subtitle = QLabel(
            "Orders listed here still have unmapped Trendyol listings. "
            "Map them in the Mappings tab — once every line is mapped, the invoice is created "
            "automatically and the order disappears from this list."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by order number or buyer…")
        self.search.textChanged.connect(self._apply_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        header = QHBoxLayout()
        header.addWidget(self.search, 1)
        header.addWidget(refresh_btn)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Order #", "Date", "Buyer", "Country", "Total", "Status", "Mapping"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enable_cell_copy(self.table)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._open_details)

        self.details_btn = QPushButton("View order details…")
        self.details_btn.clicked.connect(self._open_details)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.details_btn)

        self._rows: list[dict] = []

    def refresh(self) -> None:
        # Opportunistically auto-invoice in case mappings were updated elsewhere.
        try:
            auto_create_local_invoices(self.repo)
        except Exception:
            pass  # silently — main window surfaces errors via status bar too
        orders = self.repo.list_orders(state="pending")
        self._rows = orders
        self._render(orders)

    def _apply_filter(self) -> None:
        q = self.search.text().strip().lower()
        if not q:
            self._render(self._rows)
            return
        filtered = [
            o for o in self._rows
            if q in (o.get("order_number") or "").lower()
            or q in (o.get("buyer_name") or "").lower()
        ]
        self._render(filtered)

    def _render(self, orders: list[dict]) -> None:
        self.table.setRowCount(len(orders))
        for row, o in enumerate(orders):
            num = QTableWidgetItem(o["order_number"])
            num.setData(Qt.ItemDataRole.UserRole, o["id"])
            self.table.setItem(row, 0, num)
            self.table.setItem(row, 1, QTableWidgetItem(o.get("order_date") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(o.get("buyer_name") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(o.get("ship_country") or ""))
            total = f"{o.get('total_amount') or 0:.2f} {o.get('currency') or ''}".strip()
            self.table.setItem(row, 4, QTableWidgetItem(total))
            self.table.setItem(row, 5, QTableWidgetItem(o.get("status") or ""))

            try:
                preview = build_preview(self.repo, o["id"])
                label = "All mapped" if preview.all_mapped else "Needs mapping"
                item = QTableWidgetItem(label)
                item.setForeground(Qt.GlobalColor.green if preview.all_mapped else Qt.GlobalColor.yellow)
            except Exception:
                item = QTableWidgetItem("Unknown")
                item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(row, 6, item)

    def _selected_order_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _open_details(self) -> None:
        order_id = self._selected_order_id()
        if order_id is None:
            QMessageBox.information(self, "Pick an order", "Select an order first.")
            return
        try:
            preview = build_preview(self.repo, order_id)
        except Exception as e:
            QMessageBox.critical(self, "Could not load order", str(e))
            return
        OrderDetailsDialog(preview, self).exec()


class OrderDetailsDialog(QDialog):
    def __init__(self, preview: InvoicePreview, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Order details — {preview.order_number}")
        self.resize(820, 560)

        top = QLabel(
            f"<b>Buyer:</b> {preview.buyer_name}<br>"
            f"<b>Email:</b> {preview.buyer_email or '—'}<br>"
            f"<b>Address:</b> {preview.ship_address or '—'}, "
            f"{preview.ship_city or ''} {preview.ship_country or ''}<br>"
            f"<b>Currency:</b> {preview.currency}"
        )
        top.setTextFormat(Qt.TextFormat.RichText)
        top.setWordWrap(True)
        top.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        table = QTableWidget(len(preview.lines), 6)
        table.setHorizontalHeaderLabels(["Trendyol listing", "Oblio product", "Qty", "Unit price", "VAT%", "Line total"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enable_cell_copy(table)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, l in enumerate(preview.lines):
            table.setItem(row, 0, QTableWidgetItem(l.listing_title))
            mapped = l.mapped_oblio_name or "⚠ not mapped"
            item = QTableWidgetItem(mapped)
            if not l.is_mapped:
                item.setForeground(Qt.GlobalColor.yellow)
            table.setItem(row, 1, item)
            table.setItem(row, 2, QTableWidgetItem(str(l.quantity)))
            table.setItem(row, 3, QTableWidgetItem(f"{l.unit_price_gross:.2f}"))
            table.setItem(row, 4, QTableWidgetItem(f"{l.vat_percent:g}%"))
            table.setItem(row, 5, QTableWidgetItem(f"{l.line_total_gross:.2f}"))

        total_lbl = QLabel(f"<b>Total (VAT incl.):</b> {preview.total_gross:.2f} {preview.currency}")
        total_lbl.setTextFormat(Qt.TextFormat.RichText)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        status_lbl = QLabel()
        status_lbl.setWordWrap(True)
        if preview.all_mapped:
            status_lbl.setText("✅ All lines mapped — this order will be auto-invoiced on the next tick.")
            status_lbl.setObjectName("badge-ok")
        else:
            status_lbl.setText("⚠ Some lines are not mapped. Map them in the Mappings tab; the invoice will be created automatically afterwards.")
            status_lbl.setObjectName("badge-warn")

        layout = QVBoxLayout(self)
        layout.addWidget(top)
        layout.addWidget(status_lbl)
        layout.addWidget(table, 1)
        layout.addWidget(total_lbl)
        layout.addWidget(buttons)
