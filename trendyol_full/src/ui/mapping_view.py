from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.storage.repo import Repo
from src.ui.models import DictRowsModel


class MappingView(QWidget):
    # Emitted when full mapping list changes (pair/remove). `int` payload is unused
    # but reserved for future per-listing targeting.
    mappings_changed = Signal()
    # Emitted on lightweight per-row edits (buc, VAT). Payload = trendyol_listing_id
    # so the cascade can invalidate only invoices for that listing.
    listing_mapping_changed = Signal(int)

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        header = QVBoxLayout()
        title = QLabel(t("Variants"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "Same physical product sold under several Trendyol titles. "
            "Pick the Oblio product, attach all Trendyol variants to it."
        ))
        subtitle.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search Oblio products…"))
        self.search.textChanged.connect(self._on_search_changed)

        # Oblio products: virtualised model+view for catalogs of thousands of rows.
        def _txt(key):
            return lambda r, role: r.get(key, "") if role == Qt.ItemDataRole.DisplayRole else None

        def _linked(r, role):
            if role == Qt.ItemDataRole.DisplayRole:
                return str(r.get("linked_count", 0))
            if role == Qt.ItemDataRole.ForegroundRole and not r.get("linked_count"):
                return Qt.GlobalColor.gray
            return None

        self._products_model = DictRowsModel(columns=[
            (t("Code"),     _txt("code")),
            (t("Name"),     _txt("name")),
            (t("Unit"),     _txt("measuring_unit")),
            (t("# linked"), _linked),
        ])
        self._products_proxy = QSortFilterProxyModel(self)
        self._products_proxy.setSourceModel(self._products_model)
        self._products_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._products_proxy.setFilterKeyColumn(-1)

        self.products_table = QTableView()
        self.products_table.setModel(self._products_proxy)
        self.products_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.products_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.products_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.products_table.setSortingEnabled(True)
        self.products_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.products_table.verticalHeader().setDefaultSectionSize(22)
        self.products_table.doubleClicked.connect(self._open_pair_dialog)
        # Reload right pane when selection changes — selectionModel exists after setModel.
        self.products_table.selectionModel().selectionChanged.connect(
            self._reload_mappings_for_selected
        )

        pair_btn = QPushButton(t("Pair Trendyol listings…"))
        pair_btn.setObjectName("primary")
        pair_btn.clicked.connect(self._open_pair_dialog)

        self.mappings_table = QTableWidget(0, 6)
        self.mappings_table.setHorizontalHeaderLabels(
            [t("Trendyol title"), t("Barcode"), t("Stock code"), t("Buc"), t("TVA %"), ""]
        )
        self.mappings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mappings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mappings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mappings_table.setColumnWidth(3, 80)
        self.mappings_table.setColumnWidth(4, 90)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 8, 12)
        left_layout.addLayout(header)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.products_table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 12, 16, 12)
        self.selected_label = QLabel(t("Select an Oblio product on the left."))
        self.selected_label.setObjectName("subtitle")
        right_layout.addWidget(self.selected_label)
        right_layout.addWidget(pair_btn)
        right_layout.addWidget(self.mappings_table, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._products_cache: list[dict] = []

    def refresh(self) -> None:
        self._reload_products()

    def _on_search_changed(self, text: str) -> None:
        self._products_proxy.setFilterFixedString(text or "")

    def _reload_products(self) -> None:
        # Note: we always load the full catalog into the model; live filtering is done
        # by the proxy. Cheap because list_oblio_products is one indexed query.
        self._products_cache = self.repo.list_oblio_products()
        counts = self.repo.count_variants_per_oblio()
        rows = []
        for p in self._products_cache:
            rows.append({
                "id": p["id"],
                "code": p.get("code") or "",
                "name": p["name"],
                "measuring_unit": p.get("measuring_unit") or "",
                "linked_count": counts.get(p["id"], 0),
            })
        self._products_model.set_rows(rows)
        self._products_proxy.setFilterFixedString(self.search.text().strip())
        self._reload_mappings_for_selected()

    def _selected_product(self) -> dict | None:
        sel = self.products_table.selectionModel().selectedRows() if self.products_table.selectionModel() else []
        if not sel:
            return None
        src_idx = self._products_proxy.mapToSource(sel[0])
        row = self._products_model.row_at(src_idx.row())
        if not row:
            return None
        # Return the original Oblio product dict (cached) for downstream consumers.
        for p in self._products_cache:
            if p["id"] == row["id"]:
                return p
        return None

    def _reload_mappings_for_selected(self) -> None:
        product = self._selected_product()
        if not product:
            self.mappings_table.setRowCount(0)
            self.selected_label.setText("Select an Oblio product on the left.")
            return
        self.selected_label.setText(t("Linked Trendyol listings for: ") + product['name'])
        maps = self.repo.get_variants_for_oblio(product["id"])
        self.mappings_table.setRowCount(len(maps))
        for row, m in enumerate(maps):
            self.mappings_table.setItem(row, 0, QTableWidgetItem(m.get("title") or ""))
            self.mappings_table.setItem(row, 1, QTableWidgetItem(m.get("barcode") or ""))
            self.mappings_table.setItem(row, 2, QTableWidgetItem(m.get("stock_code") or ""))

            # Buc spinbox: how many Oblio units = 1 Trendyol unit on this listing.
            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.blockSignals(True)
            spin.setValue(int(m.get("units_per_listing") or 1))
            spin.blockSignals(False)  # only USER changes from here should fire the cascade
            spin.setToolTip(
                "Number of Oblio units that one Trendyol listing unit represents.\n"
                "Example: a 'Pack of 100' on Trendyol → '50-pack' in Oblio with Buc=2."
            )
            spin.valueChanged.connect(
                lambda v, mid=m["id"], lid=m.get("trendyol_listing_id") or 0: self._update_units(mid, v, lid)
            )
            self.mappings_table.setCellWidget(row, 3, spin)

            # Per-mapping VAT override. -1 means "use global default" (blank in UI).
            vat_spin = QDoubleSpinBox()
            vat_spin.setRange(-1.0, 100.0)
            vat_spin.setDecimals(2)
            vat_spin.setSpecialValueText("default")   # shown when value == minimum
            vat_spin.setSuffix(" %")
            v = m.get("vat_percent")
            vat_spin.blockSignals(True)
            vat_spin.setValue(float(v) if v is not None else -1.0)
            vat_spin.blockSignals(False)
            vat_spin.setToolTip(
                "VAT % for invoice lines from this listing.\n"
                "Set to 'default' to inherit the global VAT (Settings)."
            )
            vat_spin.valueChanged.connect(
                lambda v, mid=m["id"], lid=m.get("trendyol_listing_id") or 0: self._update_vat(mid, v, lid)
            )
            self.mappings_table.setCellWidget(row, 4, vat_spin)

            btn = QPushButton(t("Remove"))
            btn.setObjectName("danger")
            btn.clicked.connect(lambda _=False, mid=m["id"]: self._remove_mapping(mid))
            self.mappings_table.setCellWidget(row, 5, btn)

    def _update_units(self, mapping_id: int, units: int, listing_id: int) -> None:
        self.repo.update_variant_units(mapping_id, units)
        if listing_id:
            self.listing_mapping_changed.emit(int(listing_id))
        else:
            self.mappings_changed.emit()

    def _update_vat(self, mapping_id: int, vat: float, listing_id: int) -> None:
        # -1 (the special "default" sentinel) clears the override.
        # Variant mappings store vat_percent directly on the variant_mappings table.
        v = None if vat < 0 else float(vat)
        from src.storage.db import tx as _tx
        with _tx() as conn:
            conn.execute("UPDATE variant_mappings SET vat_percent = ? WHERE id = ?",
                         (v, mapping_id))
        if listing_id:
            self.listing_mapping_changed.emit(int(listing_id))
        else:
            self.mappings_changed.emit()

    def _remove_mapping(self, mapping_id: int) -> None:
        self.repo.remove_variant_mapping(mapping_id)
        self._reload_products()
        self.mappings_changed.emit()

    def _open_pair_dialog(self) -> None:
        product = self._selected_product()
        if not product:
            QMessageBox.information(self, "Pick a product", "Select an Oblio product first.")
            return
        dlg = PairDialog(self.repo, product, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected_ids = dlg.selected_listing_ids()
            if selected_ids:
                self.repo.add_variant_mappings(product["id"], selected_ids)
                self._reload_products()
                self.mappings_changed.emit()


class PairDialog(QDialog):
    def __init__(self, repo: Repo, oblio_product: dict, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.product = oblio_product
        self.setWindowTitle(f"Pair listings — {oblio_product['name']}")
        self.resize(780, 560)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Trendyol listings (title, barcode, stock code)…")
        self.search.textChanged.connect(self._reload)

        # Multi-Oblio is allowed: a listing already mapped to other Oblio products
        # should still be pickable. We only filter out listings already linked TO
        # THIS Oblio product (so we don't show the user duplicates).
        self.only_unmapped = QCheckBox("Hide listings already linked to this product")
        self.only_unmapped.setChecked(True)
        self.only_unmapped.stateChanged.connect(self._reload)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Title", "Barcode", "Stock code", "Brand"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_btn.setObjectName("primary")
        save_btn.setText("Link selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        title = QLabel(f"Linking to: <b>{oblio_product['name']}</b>")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.only_unmapped)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        self._listings: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        search = self.search.text().strip() or None
        hide_already_linked = self.only_unmapped.isChecked()
        exclude_id = self.product["id"] if hide_already_linked else None
        self._listings = self.repo.list_trendyol_listings(search=search, exclude_for_oblio_id=exclude_id)
        self.table.setRowCount(len(self._listings))
        for row, l in enumerate(self._listings):
            cb = QCheckBox()
            wrapper = QWidget()
            hb = QHBoxLayout(wrapper)
            hb.setContentsMargins(8, 0, 0, 0)
            hb.addWidget(cb)
            hb.addStretch(1)
            self.table.setCellWidget(row, 0, wrapper)
            self.table.setItem(row, 1, QTableWidgetItem(l.get("title") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(l.get("barcode") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(l.get("stock_code") or ""))
            self.table.setItem(row, 4, QTableWidgetItem(l.get("brand") or ""))

    def selected_listing_ids(self) -> list[int]:
        ids: list[int] = []
        for row in range(self.table.rowCount()):
            wrapper = self.table.cellWidget(row, 0)
            if wrapper is None:
                continue
            cb = wrapper.findChild(QCheckBox)
            if cb and cb.isChecked():
                ids.append(self._listings[row]["id"])
        return ids
