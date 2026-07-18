"""
Bundles tab — one Trendyol listing → many Oblio components.

Left pane: Trendyol listings (filterable).
Right pane: Oblio components attached to the selected Trendyol listing
            (each row has Oblio name+code, Buc spinbox, Remove button).
Add button: opens an Oblio-picker dialog (search + checkbox multi-select + Buc input).
"""
from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QSplitter, QStackedLayout,
    QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.storage.repo import Repo
from src.ui.models import DictRowsModel
from src.ui.widgets import (
    PILL_GREEN, PILL_GREY, PillDelegate, attach_search_debounce, enable_cell_copy,
    polish_table,
)


class BundlesView(QWidget):
    mappings_changed = Signal()
    listing_mapping_changed = Signal(int)

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel(t("Bundles"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "One Trendyol listing that's a kit of multiple Oblio products "
            "(e.g. nailgun + nails). Pick the Trendyol listing, attach each Oblio component with its Buc."
        ))
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        # ---- left pane: Trendyol listings ----
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search Trendyol listings…"))
        self.search.textChanged.connect(self._on_search_changed)

        self.only_bundles_chk = QCheckBox("Doar pachetele (cu componente)")
        self.only_bundles_chk.toggled.connect(lambda _: self.refresh())
        self.count_label = QLabel("")
        self.count_label.setObjectName("subtitle")
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.only_bundles_chk)
        filter_row.addStretch(1)
        filter_row.addWidget(self.count_label)

        def _txt(key):
            return lambda r, role: r.get(key, "") if role == Qt.ItemDataRole.DisplayRole else None

        def _components(r, role):
            # Rendered as a pill; plain text kept for sorting.
            if role == Qt.ItemDataRole.DisplayRole:
                return str(r.get("components_count", 0))
            return None

        def _components_pill(row: dict) -> tuple[str, str, str] | None:
            n = row.get("components_count", 0)
            if n:
                return (f"{n} componente" if n > 1 else "1 componentă", *PILL_GREEN)
            return ("—", *PILL_GREY)

        self._listings_model = DictRowsModel(columns=[
            (t("Title"),         _txt("title")),
            (t("Barcode"),       _txt("barcode")),
            (t("Product code"),  _txt("product_main_id")),
            (t("# components"),  _components),
        ])
        self._listings_proxy = QSortFilterProxyModel(self)
        self._listings_proxy.setSourceModel(self._listings_model)
        self._listings_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._listings_proxy.setFilterKeyColumn(-1)

        self.listings_table = QTableView()
        self.listings_table.setModel(self._listings_proxy)
        self.listings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.listings_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.listings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listings_table.setSortingEnabled(True)
        self.listings_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        polish_table(self.listings_table)
        enable_cell_copy(self.listings_table)
        self.listings_table.setItemDelegateForColumn(
            3, PillDelegate(_components_pill, self.listings_table))
        self.listings_table.selectionModel().selectionChanged.connect(self._reload_components_for_selected)

        # ---- right pane: components ----
        self.components_table = QTableWidget(0, 4)
        self.components_table.setHorizontalHeaderLabels(
            [t("Oblio product"), t("Code"), t("Buc"), ""]
        )
        self.components_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.components_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enable_cell_copy(self.components_table)
        self.components_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.components_table.setColumnWidth(2, 80)
        self.components_table.setAlternatingRowColors(True)
        self.components_table.setShowGrid(False)
        self.components_table.verticalHeader().setVisible(False)
        self.components_table.verticalHeader().setDefaultSectionSize(34)

        self.selected_label = QLabel(t("Select a Trendyol listing on the left."))
        self.selected_label.setObjectName("subtitle")

        add_btn = QPushButton(t("Add Oblio component…"))
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._open_add_dialog)

        # Right-pane empty state (no selection / not a bundle yet).
        self.right_empty = QLabel("")
        self.right_empty.setObjectName("emptyState")
        self.right_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.right_empty.setWordWrap(True)
        self._right_stack = QStackedLayout()
        self._right_stack.addWidget(self.components_table)  # 0
        self._right_stack.addWidget(self.right_empty)       # 1

        # ---- layout ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 8, 12)
        left_layout.addWidget(title)
        left_layout.addWidget(subtitle)
        left_layout.addWidget(self.search)
        left_layout.addLayout(filter_row)
        left_layout.addWidget(self.listings_table, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 12, 16, 12)
        right_layout.addWidget(self.selected_label)
        right_layout.addWidget(add_btn)
        right_layout.addLayout(self._right_stack, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._listings_cache: list[dict] = []

    # ---- view API ----
    def refresh(self) -> None:
        self._listings_cache = self.repo.list_trendyol_listings()
        counts = self.repo.count_components_per_listing()
        only_bundles = self.only_bundles_chk.isChecked()
        rows = []
        for l in self._listings_cache:
            n = counts.get(l["id"], 0)
            if only_bundles and not n:
                continue
            rows.append({
                "id": l["id"],
                "title": l.get("title") or "",
                "barcode": l.get("barcode") or "",
                "product_main_id": l.get("product_main_id") or "",
                "components_count": n,
            })
        self._listings_model.set_rows(rows)
        self._listings_proxy.setFilterFixedString(self.search.text().strip())
        n_bundles = sum(1 for c in counts.values() if c)
        self.count_label.setText(
            f"{len(self._listings_cache)} anunțuri · {n_bundles} pachete"
        )
        self._reload_components_for_selected()

    def _on_search_changed(self, text: str) -> None:
        self._listings_proxy.setFilterFixedString(text or "")

    def _selected_listing(self) -> dict | None:
        sel = self.listings_table.selectionModel().selectedRows() if self.listings_table.selectionModel() else []
        if not sel:
            return None
        src_idx = self._listings_proxy.mapToSource(sel[0])
        row = self._listings_model.row_at(src_idx.row())
        if not row:
            return None
        for l in self._listings_cache:
            if l["id"] == row["id"]:
                return l
        return None

    def _reload_components_for_selected(self) -> None:
        listing = self._selected_listing()
        if not listing:
            self.components_table.setRowCount(0)
            self.selected_label.setText(t("Select a Trendyol listing on the left."))
            self.right_empty.setText("Selectează un anunț Trendyol din stânga\npentru a-i vedea componentele.")
            self._right_stack.setCurrentIndex(1)
            return
        self.selected_label.setText(t("Components for: ") + (listing.get("title") or ""))
        comps = self.repo.get_bundle_components(listing["id"])
        if not comps:
            self.right_empty.setText(
                "Acest anunț nu e (încă) un pachet.\n"
                "Apasă „Adaugă componentă Oblio…” pentru a-l transforma în pachet."
            )
            self._right_stack.setCurrentIndex(1)
            return
        self._right_stack.setCurrentIndex(0)
        self.components_table.setRowCount(len(comps))
        for row, c in enumerate(comps):
            name = c.get("oblio_name") or ""
            self.components_table.setItem(row, 0, QTableWidgetItem(name))
            self.components_table.setItem(row, 1, QTableWidgetItem(c.get("oblio_code") or ""))

            spin = QSpinBox()
            spin.setRange(1, 9999)
            spin.blockSignals(True)
            spin.setValue(int(c.get("units_per_listing") or 1))
            spin.blockSignals(False)
            spin.valueChanged.connect(
                lambda v, mid=c["id"], lid=listing["id"]: self._update_units(mid, v, lid)
            )
            self.components_table.setCellWidget(row, 2, spin)

            btn = QPushButton(t("Remove"))
            btn.setObjectName("danger")
            btn.clicked.connect(lambda _=False, mid=c["id"], lid=listing["id"]: self._remove(mid, lid))
            self.components_table.setCellWidget(row, 3, btn)

    def _update_units(self, mapping_id: int, units: int, listing_id: int) -> None:
        self.repo.update_bundle_units(mapping_id, units)
        self.listing_mapping_changed.emit(int(listing_id))

    def _remove(self, mapping_id: int, listing_id: int) -> None:
        self.repo.remove_bundle_component(mapping_id)
        self._reload_components_for_selected()
        self.refresh()
        self.listing_mapping_changed.emit(int(listing_id))

    def _open_add_dialog(self) -> None:
        listing = self._selected_listing()
        if not listing:
            QMessageBox.information(self, t("Pick a listing"),
                                    t("Select a Trendyol listing on the left first."))
            return
        dlg = _OblioPickerDialog(self.repo, listing, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen = dlg.chosen_oblio_ids()
            buc = dlg.buc_value()
            if chosen:
                self.repo.add_bundle_components(listing["id"], chosen, units_per_listing=buc)
                self.refresh()
                self.listing_mapping_changed.emit(int(listing["id"]))


class _OblioPickerDialog(QDialog):
    def __init__(self, repo: Repo, listing: dict, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.listing = listing
        self.setWindowTitle(t("Add Oblio component…"))
        self.resize(780, 560)

        title = QLabel(
            t("Choose Oblio products to add as components of: ")
            + f"<b>{listing.get('title') or ''}</b>"
        )
        title.setObjectName("title")
        title.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search Oblio products…"))
        attach_search_debounce(self.search, self._reload)

        self.selection_label = QLabel("")
        self.selection_label.setObjectName("subtitle")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", t("Code"), t("Name"), t("Unit")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        enable_cell_copy(self.table)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 36)
        # Native checkable items (below) instead of per-cell QCheckBox widgets —
        # thousands of embedded widgets per reload made typing in the search box
        # visibly laggy. blockSignals during _reload keeps this from firing there.
        self.table.itemChanged.connect(self._on_item_changed)

        buc_label = QLabel(t("Buc:"))
        self.buc = QSpinBox()
        self.buc.setRange(1, 9999)
        self.buc.setValue(1)
        buc_row = QHBoxLayout()
        buc_row.addWidget(buc_label)
        buc_row.addWidget(self.buc)
        buc_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setObjectName("primary")
        save.setText(t("Add"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addWidget(self.selection_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(buc_row)
        layout.addWidget(buttons)

        # Persistent selection across filter changes — the bug fix.
        # _row_to_oblio_id is the visible row → oblio_id map for the current filter.
        self._selected_ids: set[int] = set()
        self._row_to_oblio_id: list[int] = []
        # Hide oblio products that are already attached to this bundle.
        self._already_attached = {c["oblio_product_id"]
                                  for c in repo.get_bundle_components(listing["id"])}
        self._oblio_cache: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        search = self.search.text().strip() or None
        self._oblio_cache = self.repo.list_oblio_products(search=search)
        rows = [p for p in self._oblio_cache if p["id"] not in self._already_attached]
        self.table.blockSignals(True)  # suppress itemChanged while (re)populating
        self.table.setRowCount(len(rows))
        self._row_to_oblio_id = [p["id"] for p in rows]
        for row, p in enumerate(rows):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                                | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Checked if p["id"] in self._selected_ids
                                     else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, QTableWidgetItem(p.get("code") or ""))
            self.table.setItem(row, 2, QTableWidgetItem(p.get("name") or ""))
            self.table.setItem(row, 3, QTableWidgetItem(p.get("measuring_unit") or ""))
        self.table.blockSignals(False)
        self._update_selection_label()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or not (0 <= item.row() < len(self._row_to_oblio_id)):
            return
        oblio_id = self._row_to_oblio_id[item.row()]
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_ids.add(oblio_id)
        else:
            self._selected_ids.discard(oblio_id)
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        n = len(self._selected_ids)
        if n == 0:
            self.selection_label.setText("")
        else:
            self.selection_label.setText(f"{n} produs(e) selectat(e)")

    def chosen_oblio_ids(self) -> list[int]:
        return sorted(self._selected_ids)

    def buc_value(self) -> int:
        return self.buc.value()
