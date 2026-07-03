"""
Trendyol products tab — the mapping launchpad.

Every listing discovered from order history, with its unified mapping status
(variant OR bundle — the only view that shows both). New products appear here
after their first sale; map them on the spot:
  - "Asociază ca variantă…"  → pick ONE Oblio product (+ Buc)
  - "Transformă în pachet…"  → pick multiple Oblio components (+ Buc)
Double-click: unmapped rows open the variant picker (the common case);
mapped rows navigate to the Variants tab for review.
"""
from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableView, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.storage.repo import Repo
from src.ui.bundles_view import _OblioPickerDialog
from src.ui.models import DictRowsModel
from src.ui.widgets import (
    PILL_AMBER, PILL_GREEN, PillDelegate, attach_search_debounce, polish_table,
)


class TrendyolProductsView(QWidget):
    pair_requested = Signal(int)          # listing id → main window opens Variants tab
    mappings_changed = Signal()           # full cascade
    listing_mapping_changed = Signal(int) # targeted cascade for one listing

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel(t("Trendyol products"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "Every listing from your order history, with its mapping status. "
            "New products appear after their first sale — map them here directly."
        ))
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Filter by title, barcode or product code…"))
        self.search.textChanged.connect(self._on_filter_changed)

        self.unmapped_only_chk = QCheckBox(t("Show only unmapped"))
        self.unmapped_only_chk.toggled.connect(self._reapply_filters)

        refresh_btn = QPushButton("⟳  " + t("Refresh"))
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(self.search, 1)
        header_row.addWidget(self.unmapped_only_chk)
        header_row.addWidget(refresh_btn)

        def _status_pill(row: dict):
            if row.get("mapped"):
                return (t("Mapped"), *PILL_GREEN)
            return (t("Unmapped"), *PILL_AMBER)

        # No Stock code column: Trendyol International sends the literal
        # placeholder "merchantSku" there for every product — pure noise.
        self._model = DictRowsModel(columns=[
            (t("Trendyol title"), lambda r, role: r["title"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Barcode"),        lambda r, role: r["barcode"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Product code"),   lambda r, role: r["product_main_id"] if role == Qt.ItemDataRole.DisplayRole else None),
            ("Oblio →",           lambda r, role: r["mapped_text"] if role == Qt.ItemDataRole.DisplayRole else None),
            (t("Status"),         lambda r, role: (t("Mapped") if r.get("mapped") else t("Unmapped"))
                                                  if role == Qt.ItemDataRole.DisplayRole else None),
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
        # Title gets the lion's share of freed-up width; Oblio → stretches too.
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        polish_table(self.table)
        self.table.setItemDelegateForColumn(4, PillDelegate(_status_pill, self.table))
        self.table.doubleClicked.connect(self._on_double_clicked)

        # Summary chips (same look as the Invoices tab).
        def _chip(role: str) -> QLabel:
            lbl = QLabel("")
            lbl.setObjectName("chip")
            lbl.setProperty("chipRole", role)
            return lbl

        self.chip_shown = _chip("neutral")
        self.chip_unmapped = _chip("warn")
        self.chip_mapped = _chip("ok")
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        for c in (self.chip_shown, self.chip_unmapped, self.chip_mapped):
            chips_row.addWidget(c)
        chips_row.addStretch(1)

        # Mapping actions on the selected listing.
        self.variant_btn = QPushButton(t("Map as variant…"))
        self.variant_btn.setObjectName("primary")
        self.variant_btn.clicked.connect(self._map_as_variant)

        self.bundle_btn = QPushButton(t("Convert to bundle…"))
        self.bundle_btn.clicked.connect(self._convert_to_bundle)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.bundle_btn)
        buttons_row.addWidget(self.variant_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(header_row)
        layout.addLayout(chips_row)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons_row)

        self._listings: list[dict] = []
        self._mapped_lookup: dict[int, list[dict]] = {}

    def refresh(self) -> None:
        self._listings = self.repo.list_trendyol_listings()
        # A listing can be mapped to MULTIPLE Oblio products (composite bundle case),
        # so the lookup must hold a LIST per listing, not a single dict.
        self._mapped_lookup = {}
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
            if self.unmapped_only_chk.isChecked() and is_mapped:
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
        self.chip_shown.setText(f"{shown} afișate")
        self.chip_unmapped.setText(f"{unmapped} neasociate")
        self.chip_unmapped.setVisible(unmapped > 0)
        self.chip_mapped.setText(f"{total - unmapped} asociate")
        self.chip_mapped.setVisible(total - unmapped > 0)

    # ---- selection / actions ------------------------------------------------

    def _selected_listing_row(self) -> dict | None:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return None
        src_idx = self._proxy.mapToSource(sel[0])
        return self._model.row_at(src_idx.row())

    def _on_double_clicked(self, _idx: QModelIndex) -> None:
        row = self._selected_listing_row()
        if not row:
            return
        if row.get("mapped"):
            # Mapped → review in the Variants tab (previous behavior).
            self.pair_requested.emit(int(row["id"]))
        else:
            # Unmapped → fast path: map it right now.
            self._map_as_variant()

    def _map_as_variant(self) -> None:
        row = self._selected_listing_row()
        if not row:
            QMessageBox.information(self, t("Pick a listing"),
                                    t("Select a Trendyol listing first."))
            return
        dlg = _VariantOblioPicker(self.repo, row, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        oblio_id = dlg.chosen_oblio_id()
        if oblio_id is None:
            return
        self.repo.add_variant_mappings(oblio_id, [row["id"]], units_per_listing=dlg.buc_value())
        self.refresh()
        self.listing_mapping_changed.emit(int(row["id"]))

    def _convert_to_bundle(self) -> None:
        row = self._selected_listing_row()
        if not row:
            QMessageBox.information(self, t("Pick a listing"),
                                    t("Select a Trendyol listing first."))
            return
        dlg = _OblioPickerDialog(self.repo, row, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dlg.chosen_oblio_ids()
        if not chosen:
            return
        self.repo.add_bundle_components(row["id"], chosen, units_per_listing=dlg.buc_value())
        self.refresh()
        self.listing_mapping_changed.emit(int(row["id"]))


class _VariantOblioPicker(QDialog):
    """Single-select Oblio product picker: 'this Trendyol listing IS this Oblio
    product' (+ Buc). Row selection = the choice; Save validates one is picked."""

    def __init__(self, repo: Repo, listing_row: dict, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle(t("Map as variant…"))
        self.resize(820, 600)

        title = QLabel(t("Which Oblio product is this listing?")
                       + f"<br><b>{listing_row.get('title') or ''}</b>")
        title.setObjectName("title")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.RichText)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search Oblio products…"))
        attach_search_debounce(self.search, self._reload)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([t("Code"), t("Name"), t("Unit")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(lambda _i: self._try_accept())

        buc_row = QHBoxLayout()
        buc_row.addWidget(QLabel(t("Buc:")))
        self.buc = QSpinBox()
        self.buc.setRange(1, 9999)
        self.buc.setValue(1)
        self.buc.setToolTip(
            "Câte unități Oblio reprezintă o unitate din anunțul Trendyol.\n"
            "Exemplu: 'Pachet de 100' pe Trendyol → 'pachet de 50' în Oblio cu Buc=2."
        )
        buc_row.addWidget(self.buc)
        buc_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setObjectName("primary")
        save.setText(t("Link selected"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("Cancel"))
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.table, 1)
        layout.addLayout(buc_row)
        layout.addWidget(buttons)

        self._products: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        search = self.search.text().strip() or None
        self._products = self.repo.list_oblio_products(search=search)
        self.table.setRowCount(len(self._products))
        for row, p in enumerate(self._products):
            self.table.setItem(row, 0, QTableWidgetItem(p.get("code") or ""))
            self.table.setItem(row, 1, QTableWidgetItem(p.get("name") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(p.get("measuring_unit") or ""))

    def _try_accept(self) -> None:
        if self.chosen_oblio_id() is None:
            QMessageBox.information(self, t("Pick a product"),
                                    t("Select an Oblio product first."))
            return
        self.accept()

    def chosen_oblio_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        idx = rows[0].row()
        if 0 <= idx < len(self._products):
            return int(self._products[idx]["id"])
        return None

    def buc_value(self) -> int:
        return self.buc.value()
