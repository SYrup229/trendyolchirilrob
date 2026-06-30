from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableView, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.storage.repo import Repo
from src.ui.models import DictRowsModel


class TrendyolProductsView(QWidget):
    """
    Catalog of unique Trendyol listings discovered from order history.
    Uses QTableView + a virtualised model so it stays responsive with thousands of rows.
    """

    pair_requested = Signal(int)  # trendyol_listing_id → main window opens Mappings

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel(t("Trendyol products"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "Every listing the app has seen in your Trendyol order history. "
            "Click a product → opens Mappings."
        ))
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Filter by title, barcode, stock code or product code…"))
        self.search.textChanged.connect(self._on_filter_changed)

        self.unmapped_only_btn = QPushButton(t("Show only unmapped"))
        self.unmapped_only_btn.setCheckable(True)
        self.unmapped_only_btn.toggled.connect(self._reapply_filters)

        refresh_btn = QPushButton(t("Refresh"))
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(self.search, 1)
        header_row.addWidget(self.unmapped_only_btn)
        header_row.addWidget(refresh_btn)

        # Model
        self._model = DictRowsModel(columns=[
            (t("Trendyol title"), lambda r, role: r["title"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Barcode"),        lambda r, role: r["barcode"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Stock code"),     lambda r, role: r["stock_code"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Product code"),   lambda r, role: r["product_main_id"] if role == Qt.ItemDataRole.DisplayRole else None),
            ("Oblio →",           lambda r, role: r["mapped_text"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Status"),         self._status_data),
        ])
        # Proxy provides free filtering across all columns.
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # search all columns

        self.table = QTableView()
        self.table.setModel(self._proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self._open_mapping)
        self.table.verticalHeader().setDefaultSectionSize(22)

        self.summary = QLabel("")
        self.summary.setObjectName("subtitle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(header_row)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)

        self._listings: list[dict] = []
        self._mapped_lookup: dict[int, dict] = {}

    @staticmethod
    def _status_data(row: dict, role: int):
        if role == Qt.ItemDataRole.DisplayRole:
            return t("Mapped") if row.get("mapped") else t("Unmapped")
        if role == Qt.ItemDataRole.ForegroundRole:
            return Qt.GlobalColor.green if row.get("mapped") else Qt.GlobalColor.yellow
        return None

    def refresh(self) -> None:
        self._listings = self.repo.list_trendyol_listings()
        # A listing can be mapped to MULTIPLE Oblio products (composite bundle case),
        # so the lookup must hold a LIST per listing, not a single dict.
        self._mapped_lookup: dict[int, list[dict]] = {}
        for r in self.repo.list_all_mappings_with_oblio():
            self._mapped_lookup.setdefault(r["trendyol_listing_id"], []).append(r)
        self._reapply_filters()

    def _on_filter_changed(self, text: str) -> None:
        self._proxy.setFilterFixedString(text or "")
        self._update_summary()

    def _reapply_filters(self) -> None:
        rows: list[dict] = []
        for l in self._listings:
            ops = self._mapped_lookup.get(l["id"]) or []
            is_mapped = bool(ops)
            if self.unmapped_only_btn.isChecked() and is_mapped:
                continue
            if not is_mapped:
                mapped_text = "—"
            else:
                parts = []
                for op in ops:
                    nm = op.get("oblio_name") or "(unnamed)"
                    if op.get("oblio_code"):
                        nm += f" [{op['oblio_code']}]"
                    buc = op.get("units_per_listing") or 1
                    if buc != 1:
                        nm += f" · {buc} buc"
                    parts.append(nm)
                # Join multi-Oblio with " + " so the bundle is visually clear.
                mapped_text = "  +  ".join(parts)
                if len(ops) > 1:
                    mapped_text = f"({len(ops)} componente)  " + mapped_text
            rows.append({
                "id": l["id"],
                "title": l.get("title") or "",
                "barcode": l.get("barcode") or "",
                "stock_code": l.get("stock_code") or "",
                "product_main_id": l.get("product_main_id") or "",
                "mapped": is_mapped,
                "mapped_text": mapped_text,
            })
        self._model.set_rows(rows)
        self._proxy.setFilterFixedString(self.search.text().strip())
        self._update_summary()

    def _update_summary(self) -> None:
        total = len(self._listings)
        unmapped = sum(1 for l in self._listings if not self._mapped_lookup.get(l["id"]))
        shown = self._proxy.rowCount()
        self.summary.setText(
            f"{shown} shown · {total} total · {unmapped} unmapped · {total - unmapped} mapped"
        )

    def _open_mapping(self, _proxy_idx: QModelIndex) -> None:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return
        src_idx = self._proxy.mapToSource(sel[0])
        row = self._model.row_at(src_idx.row())
        if row and row.get("id"):
            self.pair_requested.emit(int(row["id"]))
