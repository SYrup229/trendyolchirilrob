from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.services import campaign_rebase as cr
from src.ui.widgets import BusyDialog, TaskRunner


class CampaignRebaseView(QWidget):
    def __init__(self):
        super().__init__()

        title = QLabel(t("Campaign rebase"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "Take a previous Trendyol campaign export (with Campaign Sale Prices set), "
            "and recompute prices for a new campaign with different discount and "
            "Trendyol-coverage parameters. Match by barcode, fall back to product code. "
            "Capped at Maximum Allowed Price minus 0.01 when needed."
        ))
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        # ---------- Excel A ----------
        self.a_path = QLineEdit()
        self.a_path.setPlaceholderText("Path to previous campaign Excel (with prices set)")
        a_btn = QPushButton(t("Browse…"))
        a_btn.clicked.connect(self._pick_a)
        a_row = QHBoxLayout()
        a_row.addWidget(self.a_path, 1)
        a_row.addWidget(a_btn)

        self.old_disc = _spin(0, 100, suffix=" %")
        self.old_cover = _spin(0, 100, suffix=" %", value=20)
        old_form = QFormLayout()
        old_form.addRow(t("Old campaign discount"), self.old_disc)
        old_form.addRow(t("Old Trendyol coverage"), self.old_cover)

        a_box = QGroupBox(t("Excel A — previous campaign (source of prices)"))
        a_lay = QVBoxLayout(a_box)
        a_lay.addLayout(a_row)
        a_lay.addLayout(old_form)

        # ---------- Excel B / generate ----------
        self.have_b = QCheckBox(t("I'll upload my own Excel B (new-campaign template from Trendyol)"))
        self.have_b.setChecked(True)
        self.have_b.toggled.connect(self._toggle_b)

        self.b_path = QLineEdit()
        self.b_path.setPlaceholderText("Path to new-campaign template Excel (Campaign Sale Price column will be filled)")
        b_btn = QPushButton(t("Browse…"))
        b_btn.clicked.connect(self._pick_b)
        b_row = QHBoxLayout()
        b_row.addWidget(self.b_path, 1)
        b_row.addWidget(b_btn)

        self.b_widgets_holder = QWidget()
        b_holder_lay = QVBoxLayout(self.b_widgets_holder)
        b_holder_lay.setContentsMargins(0, 6, 0, 0)
        b_holder_lay.addLayout(b_row)

        b_box = QGroupBox(t("Excel B — new-campaign template"))
        b_lay = QVBoxLayout(b_box)
        b_lay.addWidget(self.have_b)
        b_lay.addWidget(self.b_widgets_holder)

        # ---------- New campaign params ----------
        self.new_disc = _spin(0, 100, suffix=" %", value=45)
        self.new_cover = _spin(0, 100, suffix=" %", value=30)
        new_form = QFormLayout()
        new_form.addRow(t("New campaign discount"), self.new_disc)
        new_form.addRow(t("New Trendyol coverage"), self.new_cover)

        new_box = QGroupBox(t("New campaign parameters"))
        new_lay = QVBoxLayout(new_box)
        new_lay.addLayout(new_form)

        # ---------- Compute / save row ----------
        self.compute_btn = QPushButton(t("Compute"))
        self.compute_btn.setObjectName("primary")
        self.compute_btn.clicked.connect(self._compute)

        self.save_btn = QPushButton(t("Save as…"))
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_as)

        action_row = QHBoxLayout()
        action_row.addWidget(self.compute_btn)
        action_row.addWidget(self.save_btn)
        action_row.addStretch(1)

        # ---------- Summary + preview table ----------
        self.summary = QLabel("")
        self.summary.setWordWrap(True)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            t("Barcode"), t("Product code"), t("Name"),
            t("Old CSP"), t("New CSP"), t("Max allowed"),
            t("Status"), t("Keep"),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        check_all_btn = QPushButton(t("Check all"))
        check_all_btn.clicked.connect(lambda: self._set_all_kept(True))
        uncheck_all_btn = QPushButton(t("Uncheck all"))
        uncheck_all_btn.clicked.connect(lambda: self._set_all_kept(False))

        self.keep_count_lbl = QLabel("")
        self.keep_count_lbl.setObjectName("subtitle")

        keep_row = QHBoxLayout()
        keep_row.addWidget(check_all_btn)
        keep_row.addWidget(uncheck_all_btn)
        keep_row.addWidget(self.keep_count_lbl)
        keep_row.addStretch(1)

        # ---------- Layout ----------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(a_box)
        layout.addWidget(b_box)
        layout.addWidget(new_box)
        layout.addLayout(action_row)
        layout.addWidget(self.summary)
        layout.addLayout(keep_row)
        layout.addWidget(self.table, 1)

        self._source_doc: cr.ExcelDoc | None = None
        self._target_doc: cr.ExcelDoc | None = None
        self._result: cr.RebaseResult | None = None

    # ----- view contract -----
    def refresh(self) -> None:
        # Nothing to refresh; this view holds in-memory state until user resets.
        pass

    # ----- file pickers -----
    def _pick_a(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Excel A (previous campaign)", "", "Excel (*.xlsx)")
        if not path:
            return
        self.a_path.setText(path)
        params = cr.parse_filename_params(path)
        if params:
            self.old_disc.setValue(params[0])
            self.old_cover.setValue(params[1])

    def _pick_b(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Excel B (new-campaign template)", "", "Excel (*.xlsx)")
        if not path:
            return
        self.b_path.setText(path)
        params = cr.parse_filename_params(path)
        if params:
            self.new_disc.setValue(params[0])
            self.new_cover.setValue(params[1])

    def _toggle_b(self, on: bool) -> None:
        self.b_widgets_holder.setVisible(on)

    # ----- compute -----
    def _compute(self) -> None:
        a = self.a_path.text().strip()
        if not a:
            QMessageBox.warning(self, "Excel A required", "Pick the previous-campaign Excel first.")
            return
        b = ""
        if self.have_b.isChecked():
            b = self.b_path.text().strip()
            if not b:
                QMessageBox.warning(self, "Excel B required",
                                    "Pick the new-campaign template, or uncheck the option to generate.")
                return
        params = dict(
            old_discount=self.old_disc.value(),
            old_ty_cover=self.old_cover.value(),
            new_discount=self.new_disc.value(),
            new_ty_cover=self.new_cover.value(),
        )

        # Reading and rebasing thousands of rows is CPU/IO heavy — run off the UI thread.
        def job(progress):
            progress("Reading Excel A…")
            source = cr.read_excel(a)
            target = None
            if b:
                progress("Reading Excel B…")
                target = cr.read_excel(b)
            progress("Computing new prices…")
            result = cr.rebase(source=source, target=target, **params)
            return source, target, result

        dlg = BusyDialog("Computing", "Starting…", self)
        runner = TaskRunner(job, self)
        runner.progress.connect(dlg.set_message)

        def on_done(payload):
            source, target, result = payload
            self._source_doc = source
            self._target_doc = target
            self._result = result
            dlg.accept()
            self._render_result()
            self.save_btn.setEnabled(True)

        def on_failed(msg):
            dlg.reject()
            QMessageBox.critical(self, "Compute failed", msg)

        runner.done.connect(on_done)
        runner.failed.connect(on_failed)
        runner.start()
        dlg.exec()

    def _render_result(self) -> None:
        r = self._result
        if r is None:
            return
        self.summary.setText(
            f"Total rows: {len(r.rows)}   |   "
            f"Matched: {r.matched_count}   |   "
            f"Unmatched: {r.unmatched_count}   |   "
            f"Capped at max−0.01: {r.capped_count}"
        )
        self.table.setRowCount(len(r.rows))
        for row, rr in enumerate(r.rows):
            self.table.setItem(row, 0, QTableWidgetItem(rr.barcode or ""))
            self.table.setItem(row, 1, QTableWidgetItem(rr.product_code or ""))
            self.table.setItem(row, 2, QTableWidgetItem(rr.name or ""))
            self.table.setItem(row, 3, QTableWidgetItem(_fmt(rr.old_csp)))
            self.table.setItem(row, 4, QTableWidgetItem(_fmt(rr.new_csp)))
            self.table.setItem(row, 5, QTableWidgetItem(_fmt(rr.max_allowed)))

            if not rr.matched:
                status, color = "Unmatched", Qt.GlobalColor.gray
            elif rr.capped:
                status, color = "Capped", Qt.GlobalColor.yellow
            else:
                status, color = "OK", Qt.GlobalColor.green
            it = QTableWidgetItem(status)
            it.setForeground(color)
            it.setToolTip(rr.note)
            self.table.setItem(row, 6, it)

            cb = QCheckBox()
            cb.setChecked(True)
            cb.toggled.connect(self._update_keep_count)
            wrapper = QWidget()
            hb = QHBoxLayout(wrapper)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.addStretch(1)
            hb.addWidget(cb)
            hb.addStretch(1)
            self.table.setCellWidget(row, 7, wrapper)

        self._update_keep_count()

    # ----- keep checkboxes -----
    def _row_checkbox(self, row: int) -> QCheckBox | None:
        wrapper = self.table.cellWidget(row, 7)
        return wrapper.findChild(QCheckBox) if wrapper else None

    def _set_all_kept(self, kept: bool) -> None:
        for row in range(self.table.rowCount()):
            cb = self._row_checkbox(row)
            if cb:
                cb.setChecked(kept)

    def _kept_row_indices(self) -> set[int]:
        if self._result is None:
            return set()
        kept: set[int] = set()
        for row in range(self.table.rowCount()):
            cb = self._row_checkbox(row)
            if cb and cb.isChecked():
                kept.add(self._result.rows[row].target_row_index)
        return kept

    def _update_keep_count(self) -> None:
        if self._result is None:
            self.keep_count_lbl.setText("")
            return
        kept = sum(
            1 for row in range(self.table.rowCount())
            if (cb := self._row_checkbox(row)) and cb.isChecked()
        )
        self.keep_count_lbl.setText(f"Keeping {kept} of {len(self._result.rows)} products")

    # ----- save -----
    def _save_as(self) -> None:
        if self._result is None:
            return
        kept = self._kept_row_indices()
        if not kept:
            QMessageBox.warning(self, "Nothing to save", "All products are unchecked. Check at least one row.")
            return
        default_name = self._suggested_filename()
        path, _ = QFileDialog.getSaveFileName(self, "Save new-campaign Excel", default_name, "Excel (*.xlsx)")
        if not path:
            return
        try:
            if self._target_doc is not None:
                out = cr.write_filled_excel(self._target_doc, self._result, Path(path), kept_row_indices=kept)
            else:
                out = cr.write_generated_excel(self._source_doc, self._result, Path(path), kept_row_indices=kept)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        QMessageBox.information(self, "Saved", f"Wrote {out}\n\nKept {len(kept)} products in the new campaign.")

    def _suggested_filename(self) -> str:
        d = int(self.new_disc.value())
        c = int(self.new_cover.value())
        return f"{d}--discount---{c}--trendyol-coverage.xlsx"


def _spin(low: float, high: float, suffix: str = "", value: float = 0.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(low, high)
    s.setDecimals(2)
    s.setSuffix(suffix)
    s.setValue(value)
    return s


def _fmt(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else ""
