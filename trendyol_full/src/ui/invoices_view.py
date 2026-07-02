import json
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableView, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.i18n import t
from src.domain.order_status import INVOICEABLE_TRENDYOL_STATUSES
from src.services.invoice_export import export_invoice_html, suggested_filename
from src.services.invoicing import notify_trendyol_of_invoice, send_local_invoice_to_oblio
from src.storage.repo import Repo
from src.ui.models import DictRowsModel


# (label, days from "now", or None for all-time)
DATE_RANGES: list[tuple[str, int | None]] = [
    (t("Last 30 days"), 30),
    (t("Last 60 days"), 60),
    (t("Last 90 days"), 90),
    (t("Last 6 months"), 183),
    (t("Last 1 year"), 365),
    (t("All time"), None),
]


class _InvoicesModel(DictRowsModel):
    """
    DictRowsModel + an opt-in checkbox column at index 0.

    Outside select-mode the column is hidden by the view (setColumnHidden), so the
    rest of the columns look unchanged. Inside select-mode, unsent rows (no
    oblio_link) become checkable; rows already sent to Oblio stay flat to make it
    visually obvious which rows are pickable.

    Checked state is stored by invoice id, not row index — sorting / re-rendering
    preserves the selection.
    """

    def __init__(self, columns, rows=None, parent=None):
        super().__init__(columns, rows, parent)
        self._select_mode: bool = False
        self._checked: set[int] = set()

    def set_select_mode(self, on: bool) -> None:
        self.beginResetModel()
        self._select_mode = on
        if not on:
            self._checked.clear()
        self.endResetModel()

    def select_mode(self) -> bool:
        return self._select_mode

    def checked_ids(self) -> set[int]:
        return set(self._checked)

    def _is_unsent(self, row: dict) -> bool:
        return not bool(row.get("oblio_link"))

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if index.isValid() and index.column() == 0 and self._select_mode:
            if role == Qt.ItemDataRole.CheckStateRole:
                row = self._rows[index.row()]
                if not self._is_unsent(row):
                    return None
                iid = row.get("id")
                return (Qt.CheckState.Checked if iid in self._checked
                        else Qt.CheckState.Unchecked)
            if role == Qt.ItemDataRole.DisplayRole:
                return ""
            return None
        return super().data(index, role)

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if (index.isValid() and index.column() == 0 and self._select_mode
                and role == Qt.ItemDataRole.CheckStateRole):
            row = self._rows[index.row()]
            if not self._is_unsent(row):
                return False
            iid = row.get("id")
            # Qt sends either the enum or the underlying int depending on platform.
            try:
                state = Qt.CheckState(value)
            except (ValueError, TypeError):
                return False
            if state == Qt.CheckState.Checked:
                self._checked.add(iid)
            else:
                self._checked.discard(iid)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True
        return False

    def flags(self, index: QModelIndex):
        base = super().flags(index)
        if index.isValid() and index.column() == 0 and self._select_mode:
            row = self._rows[index.row()]
            if self._is_unsent(row):
                return base | Qt.ItemFlag.ItemIsUserCheckable
        return base


class InvoicesView(QWidget):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel(t("Invoices"))
        title.setObjectName("title")
        subtitle = QLabel(t(
            "Auto-created from Trendyol orders that are Shipped or Delivered, with all lines mapped. "
            "Double-click a row to see the lines (Trendyol title → Oblio product substitution)."
        ))
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        # Date range filter
        self.range_combo = QComboBox()
        for label, _ in DATE_RANGES:
            self.range_combo.addItem(label)
        self.range_combo.setCurrentIndex(0)  # default: last 30 days
        self.range_combo.currentIndexChanged.connect(self._render)

        self.hide_sent_chk = QCheckBox(t("Hide already-sent invoices"))
        self.hide_sent_chk.toggled.connect(self._render)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(t("Show:")))
        filter_row.addWidget(self.range_combo)
        filter_row.addWidget(self.hide_sent_chk)
        filter_row.addStretch(1)

        # Virtualised model+view — fast even with thousands of invoices.
        def _src(r, role):
            if role == Qt.ItemDataRole.DisplayRole:
                return "Oblio" if r.get("oblio_link") else "Local"
            if role == Qt.ItemDataRole.ForegroundRole:
                return Qt.GlobalColor.cyan if r.get("oblio_link") else Qt.GlobalColor.lightGray
            return None

        def _txt(key):
            return lambda r, role: (r.get(key) or "") if role == Qt.ItemDataRole.DisplayRole else None

        def _total(r, role):
            if role != Qt.ItemDataRole.DisplayRole:
                return None
            return f"{r.get('total') or 0:.2f} {r.get('currency') or ''}".strip()

        def _order_date(r, role):
            if role == Qt.ItemDataRole.DisplayRole:
                return (r.get("order_date") or "")[:10]  # YYYY-MM-DD only
            return None

        def _status(r, role):
            s = (r.get("order_status") or "—").strip()
            if role == Qt.ItemDataRole.DisplayRole:
                return s
            if role == Qt.ItemDataRole.ForegroundRole:
                # Quick visual: shipped/delivered = green/cyan, anything else = grey.
                if s == "Delivered":
                    return Qt.GlobalColor.cyan
                if s == "Shipped":
                    return Qt.GlobalColor.green
                return Qt.GlobalColor.lightGray
            return None

        def _last_err(r, role):
            err = (r.get("last_send_error") or "").strip()
            if role == Qt.ItemDataRole.DisplayRole:
                return (err[:80] + "…") if len(err) > 80 else err
            if role == Qt.ItemDataRole.ToolTipRole:
                return err or None
            if role == Qt.ItemDataRole.ForegroundRole and err:
                return Qt.GlobalColor.red
            return None

        def _trendyol_notify_status(r, role):
            # Combines two cases:
            #   - trendyol_link_sent_at set → "✓ Trendyol notified"
            #   - error stamped → show first 80 chars of error
            #   - neither, but oblio_link exists → "Pending"
            #   - oblio_link missing → blank (we haven't even sent to Oblio)
            sent_at = (r.get("trendyol_link_sent_at") or "").strip()
            err = (r.get("trendyol_link_error") or "").strip()
            oblio = r.get("oblio_link") or ""
            if role == Qt.ItemDataRole.DisplayRole:
                if sent_at:
                    return "✓ " + sent_at[:10]
                if err:
                    return (err[:80] + "…") if len(err) > 80 else err
                if oblio:
                    return "În așteptare"
                return ""
            if role == Qt.ItemDataRole.ToolTipRole:
                return err or sent_at or None
            if role == Qt.ItemDataRole.ForegroundRole:
                if err:
                    return Qt.GlobalColor.red
                if sent_at:
                    return Qt.GlobalColor.green
                if oblio:
                    return Qt.GlobalColor.yellow
            return None

        self._model = _InvoicesModel(columns=[
            # Placeholder column 0 — hidden in normal mode, shows checkboxes for
            # unsent rows when the user enters "send multiple" mode.
            ("",                lambda r, role: None),
            (t("Source"),       _src),
            (t("Series"),       _txt("series")),
            (t("Number"),       _txt("number")),
            (t("Order #"),      _txt("order_number")),
            (t("Order date"),   _order_date),
            (t("Status"),       _status),
            (t("Buyer"),        _txt("buyer_name")),
            (t("Total"),        _total),
            (t("Issued locally"),     _txt("issued_at")),
            (t("Last error"),         _last_err),
            (t("Trendyol notify"),    _trendyol_notify_status),
            (t("Link"),         _txt("oblio_link")),
        ])
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # NoEditTriggers prevents text edit on double-click, but the model's
        # ItemIsUserCheckable flag still permits clicking the checkbox.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        # Buyer column stretches (now index 7 because of new checkbox column at 0).
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(0, True)  # hidden until "send multiple" mode is entered
        self.table.doubleClicked.connect(self._on_double_clicked)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        refresh_btn = QPushButton(t("Refresh"))
        refresh_btn.clicked.connect(self.refresh)

        self.export_btn = QPushButton(t("Export selected…"))
        self.export_btn.clicked.connect(self._export_selected)

        self.export_all_btn = QPushButton(t("Export visible to folder…"))
        self.export_all_btn.clicked.connect(self._export_visible)

        self.send_btn = QPushButton(t("Send to Oblio"))
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._send_selected)

        self.send_all_btn = QPushButton(t("Send all visible unsent"))
        self.send_all_btn.clicked.connect(self._send_all_visible)

        self.renotify_btn = QPushButton(t("Re-notify Trendyol"))
        self.renotify_btn.clicked.connect(self._renotify_selected)

        # Only visible when the selected row was already sent to Oblio. Issues a
        # NEW Oblio invoice for the same order (used after cancelling a wrong one).
        self.resend_btn = QPushButton(t("Resend invoice"))
        self.resend_btn.setStyleSheet(
            "background-color: #b26a00; color: white; font-weight: bold;"
        )
        self.resend_btn.setVisible(False)
        self.resend_btn.clicked.connect(self._resend_selected)

        self.open_btn = QPushButton(t("Open invoice"))
        self.open_btn.clicked.connect(self._open_selected)

        # Multi-select flow: entry button, plus the green Send / red Cancel pair
        # that swaps in once the user enters "pick mode".
        self.send_multi_btn = QPushButton(t("Send out multiple invoices"))
        self.send_multi_btn.setObjectName("primary")
        self.send_multi_btn.clicked.connect(self._enter_multi_mode)

        self.multi_send_btn = QPushButton(t("Send"))
        self.multi_send_btn.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.multi_send_btn.clicked.connect(self._send_multi)

        self.multi_cancel_btn = QPushButton(t("Cancel"))
        self.multi_cancel_btn.setStyleSheet(
            "background-color: #c62828; color: white; font-weight: bold; padding: 6px 16px;"
        )
        self.multi_cancel_btn.clicked.connect(self._exit_multi_mode)

        buttons_row = QHBoxLayout()
        buttons_row.addWidget(refresh_btn)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.export_all_btn)
        buttons_row.addWidget(self.export_btn)
        buttons_row.addWidget(self.open_btn)
        buttons_row.addWidget(self.renotify_btn)
        buttons_row.addWidget(self.resend_btn)
        buttons_row.addWidget(self.send_all_btn)
        buttons_row.addWidget(self.send_btn)
        buttons_row.addWidget(self.send_multi_btn)
        buttons_row.addWidget(self.multi_cancel_btn)
        buttons_row.addWidget(self.multi_send_btn)

        # Multi-mode buttons start hidden — they swap in once the user clicks
        # "Send out multiple invoices".
        self.multi_send_btn.setVisible(False)
        self.multi_cancel_btn.setVisible(False)

        self.summary = QLabel("")
        self.summary.setObjectName("subtitle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(filter_row)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons_row)

        self._all_rows: list[dict] = []
        self._visible_rows: list[dict] = []

    def refresh(self) -> None:
        self._all_rows = self.repo.list_invoices()
        self._render()

    def _render(self) -> None:
        days = DATE_RANGES[self.range_combo.currentIndex()][1]
        cutoff_iso: str | None = None
        if days is not None:
            cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")

        # Filter by the TRENDYOL order date (not when we created the local invoice),
        # so old re-invoiced orders don't suddenly appear in "Last 30 days".
        def in_range(r: dict) -> bool:
            if cutoff_iso is None:
                return True
            d = (r.get("order_date") or r.get("issued_at") or "")
            return d >= cutoff_iso

        # Always-on filter: only show invoices for orders in Shipped/Delivered.
        # Earlier statuses (Picking/Invoiced/etc.) and terminal-but-cancelled ones
        # (Cancelled/Returned/etc.) never appear here.
        def is_invoiceable(r: dict) -> bool:
            return (r.get("order_status") or "").strip() in INVOICEABLE_TRENDYOL_STATUSES

        # Hide rows for orders that already have a Trendyol-side invoiceLink unless
        # we own the link (i.e. WE successfully POSTed it via seller-invoice-links).
        def not_externally_invoiced(r: dict) -> bool:
            has_external = bool((r.get("tr_invoice_link") or "").strip())
            we_posted = bool((r.get("trendyol_link_sent_at") or "").strip())
            return (not has_external) or we_posted

        # Optional clutter filter: hide rows already sent to Oblio (done, nothing
        # left to do with them). Off by default.
        def passes_sent_filter(r: dict) -> bool:
            if not self.hide_sent_chk.isChecked():
                return True
            return not (r.get("oblio_link") or "").strip()

        rows = [
            r for r in self._all_rows
            if in_range(r) and is_invoiceable(r) and not_externally_invoiced(r)
            and passes_sent_filter(r)
        ]
        self._visible_rows = rows
        hidden_off_status = sum(1 for r in self._all_rows if not is_invoiceable(r))
        hidden_external = sum(
            1 for r in self._all_rows
            if is_invoiceable(r) and not not_externally_invoiced(r)
        )
        self.summary.setText(
            f"{len(rows)} invoice(s) shown · {len(self._all_rows)} total · "
            f"{hidden_off_status} hidden (not Shipped/Delivered) · "
            f"{hidden_external} hidden (externally invoiced)"
        )
        self._model.set_rows(rows)
        # Model reset drops the selection; the resend button is selection-driven.
        self.resend_btn.setVisible(False)

    def _on_selection_changed(self, *_args) -> None:
        data = self._selected_row()
        sent = bool(data and (data.get("oblio_link") or "").strip())
        self.resend_btn.setVisible(sent and not self._model.select_mode())

    def _on_double_clicked(self, _idx: QModelIndex) -> None:
        self._open_selected()

    def _selected_row(self) -> dict | None:
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not sel:
            return None
        return self._model.row_at(sel[0].row())

    def _open_selected(self) -> None:
        data = self._selected_row()
        if not data:
            QMessageBox.information(self, "Pick an invoice", "Select an invoice row first.")
            return
        link = data.get("oblio_link")
        if link:
            webbrowser.open(link)
        else:
            LocalInvoiceDialog(data, self).exec()

    def _export_selected(self) -> None:
        data = self._selected_row()
        if not data:
            QMessageBox.information(self, "Pick an invoice", "Select an invoice row first.")
            return
        default_name = suggested_filename(data)
        path, _ = QFileDialog.getSaveFileName(self, "Export invoice", default_name, "HTML (*.html)")
        if not path:
            return
        try:
            out = export_invoice_html(self.repo, data, Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        if QMessageBox.question(self, "Export complete",
                                f"Saved to {out}.\nOpen it now?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            webbrowser.open(out.as_uri())

    def _send_selected(self) -> None:
        data = self._selected_row()
        if not data:
            QMessageBox.information(self, t("Pick an invoice"), t("Select an invoice row first."))
            return
        if data.get("oblio_link"):
            QMessageBox.information(self, "Deja trimisă", "Această factură a fost deja trimisă către Oblio.")
            return
        self._do_send([data["id"]])

    def _send_all_visible(self) -> None:
        unsent = [r for r in self._visible_rows if not r.get("oblio_link")]
        if not unsent:
            QMessageBox.information(self, "Nimic de trimis",
                                    "Toate facturile vizibile au fost deja trimise către Oblio.")
            return
        dry = self.repo.oblio_dry_run()
        verb = "DRY-RUN" if dry else "trimite"
        ok = QMessageBox.question(
            self, "Confirmă trimiterea",
            f"Vrei să {verb} {len(unsent)} facturi către Oblio?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._do_send([r["id"] for r in unsent])

    def _do_send(self, invoice_ids: list[int]) -> None:
        dry = self.repo.oblio_dry_run()
        sent = 0
        failed: list[tuple[str, str]] = []
        for iid in invoice_ids:
            try:
                send_local_invoice_to_oblio(self.repo, iid, dry_run=dry)
                sent += 1
            except Exception as e:
                inv = self.repo.get_invoice(iid) or {}
                failed.append((inv.get("number") or str(iid), str(e)))
        verb = "DRY-RUN trimise" if dry else "trimise către Oblio"
        msg = f"{sent} {verb}."
        if failed:
            details = "\n".join(f"  • {n}: {e[:120]}" for n, e in failed[:10])
            msg += f"\n\n{len(failed)} au eșuat:\n{details}"
        QMessageBox.information(self, "Trimitere finalizată", msg)
        self.refresh()

    def _resend_selected(self) -> None:
        data = self._selected_row()
        if not data or not (data.get("oblio_link") or "").strip():
            QMessageBox.information(self, t("Pick an invoice"), t("Select an invoice row first."))
            return
        confirmed = QMessageBox.warning(
            self, "Confirmă retrimiterea",
            f"Retrimiți factura {data.get('series') or ''} {data.get('number') or ''} "
            f"pentru comanda {data.get('order_number') or ''}?\n\n"
            "Se va emite o factură NOUĂ în Oblio (cu număr nou) și link-ul nou va fi "
            "trimis către Trendyol.\n\n"
            "Asigură-te că factura veche a fost ANULATĂ în Oblio, altfel comanda va "
            "avea două facturi.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        dry = self.repo.oblio_dry_run()
        try:
            send_local_invoice_to_oblio(self.repo, data["id"], dry_run=dry, force_resend=True)
        except Exception as e:
            QMessageBox.critical(self, "Retrimitere eșuată", str(e))
            self.refresh()
            return
        if dry:
            QMessageBox.information(self, "Retrimitere (DRY-RUN)",
                                    "Payload-ul a fost doar logat — nimic trimis (Dry run activ).")
        else:
            fresh = self.repo.get_invoice(data["id"]) or {}
            QMessageBox.information(
                self, "Retrimitere reușită",
                f"Factură nouă emisă: {fresh.get('series') or ''} {fresh.get('number') or ''}.\n"
                "Link-ul nou a fost trimis către Trendyol (vezi coloana Notificare Trendyol).",
            )
        self.refresh()

    def _renotify_selected(self) -> None:
        data = self._selected_row()
        if not data:
            QMessageBox.information(self, t("Pick an invoice"), t("Select an invoice row first."))
            return
        if not data.get("oblio_link"):
            QMessageBox.information(
                self, "Trimite întâi la Oblio",
                "Trebuie să trimiți factura mai întâi la Oblio (apoi se notifică Trendyol)."
            )
            return
        try:
            notify_trendyol_of_invoice(self.repo, data["id"])
        except Exception as e:
            QMessageBox.critical(self, "Notificare Trendyol eșuată", str(e))
            self.refresh()
            return
        QMessageBox.information(self, "Notificare reușită", "Trendyol a fost notificat cu link-ul Oblio.")
        self.refresh()

    # ---- multi-select mode -------------------------------------------------
    def _enter_multi_mode(self) -> None:
        self._model.set_select_mode(True)
        self.table.setColumnHidden(0, False)
        self.table.setColumnWidth(0, 28)
        self._set_mode(multi=True)

    def _exit_multi_mode(self) -> None:
        self._model.set_select_mode(False)
        self.table.setColumnHidden(0, True)
        self._set_mode(multi=False)

    def _set_mode(self, multi: bool) -> None:
        # In multi-mode only the green Send + red Cancel are visible; everything
        # else hides so the user can't accidentally trigger a single-row action
        # while picking checkboxes.
        for w in (self.export_all_btn, self.export_btn, self.open_btn,
                  self.renotify_btn, self.send_all_btn, self.send_btn,
                  self.send_multi_btn):
            w.setVisible(not multi)
        self.multi_send_btn.setVisible(multi)
        self.multi_cancel_btn.setVisible(multi)
        # Resend is selection-driven, not mode-driven: always hidden in multi
        # mode, and on exit only shown if the selected row is a sent invoice.
        if multi:
            self.resend_btn.setVisible(False)
        else:
            self._on_selection_changed()

    def _send_multi(self) -> None:
        ids = sorted(self._model.checked_ids())
        if not ids:
            QMessageBox.information(self, t("Pick an invoice"),
                                    "Nu ai bifat nicio factură.")
            return
        dry = self.repo.oblio_dry_run()
        verb = "DRY-RUN" if dry else "trimite"
        ok = QMessageBox.question(
            self, "Confirmă trimiterea",
            f"Vrei să {verb} {len(ids)} facturi către Oblio?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        # Exit multi-mode first so _do_send → refresh() sees a clean UI state.
        self._exit_multi_mode()
        self._do_send(ids)

    def _export_visible(self) -> None:
        if not self._visible_rows:
            QMessageBox.information(self, "Nothing to export", "No invoices in the current date range.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for invoices")
        if not folder:
            return
        folder_p = Path(folder)
        ok = 0
        errors: list[str] = []
        for row in self._visible_rows:
            try:
                export_invoice_html(self.repo, row, folder_p / suggested_filename(row))
                ok += 1
            except Exception as e:
                errors.append(f"{row.get('number')}: {e}")
        msg = f"Exported {ok} invoice(s) to {folder_p}."
        if errors:
            msg += f"\n\n{len(errors)} failed:\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Export complete", msg)


class LocalInvoiceDialog(QDialog):
    def __init__(self, invoice_row: dict, parent=None):
        super().__init__(parent)
        self.invoice_row = invoice_row
        self.parent_view = parent
        try:
            record = json.loads(invoice_row.get("raw_response") or "{}")
        except ValueError:
            record = {}
        self.setWindowTitle(f"Invoice {record.get('series','')} {record.get('number','')}")
        self.resize(960, 600)

        buyer = record.get("buyer") or {}
        created_at = record.get("created_at") or ""
        created_line = f"<br><b>Creat local:</b> {created_at}" if created_at else ""
        top = QLabel(
            f"<b>Number:</b> {record.get('series','')} {record.get('number','')}<br>"
            f"<b>Data emiterii:</b> {record.get('issue_date','')}{created_line}<br>"
            f"<b>Order:</b> {record.get('order_number','')}  ·  "
            f"<b>Status:</b> {record.get('order_status') or '—'}<br>"
            f"<b>Buyer:</b> {buyer.get('name','')}<br>"
            f"<b>Email:</b> {buyer.get('email') or '—'}<br>"
            f"<b>Address:</b> {buyer.get('address') or '—'}, {buyer.get('city') or ''} {buyer.get('country') or ''}<br>"
            f"<b>Currency:</b> {record.get('currency','')}"
        )
        top.setTextFormat(Qt.TextFormat.RichText)
        top.setWordWrap(True)

        lines = record.get("lines") or []
        currency = record.get("currency") or ""
        # One row per product, plus an extra row showing the discount underneath when there is one.
        table_rows: list[dict] = []
        for l in lines:
            table_rows.append({"kind": "product", "data": l})
            if float(l.get("discount_per_unit") or 0) > 0:
                table_rows.append({"kind": "discount", "data": l})
        if float(record.get("shipping_amount") or 0) > 0:
            table_rows.append({"kind": "shipping", "data": {"amount": record["shipping_amount"]}})

        table = QTableWidget(len(table_rows), 8)
        table.setHorizontalHeaderLabels([
            "Trendyol title", "Trendyol barcode", "Oblio name", "Oblio code",
            "Qty", "Unit price", "VAT%", "Line total",
        ])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, r in enumerate(table_rows):
            l = r["data"]
            kind = r["kind"]
            if kind == "product":
                qty = int(l.get("quantity") or 0)
                list_unit = float(l.get("list_unit_price_gross") or l.get("unit_price_gross") or 0)
                list_total = round(list_unit * qty, 2)
                table.setItem(row, 0, QTableWidgetItem(l.get("original_title") or ""))
                table.setItem(row, 1, QTableWidgetItem(l.get("original_barcode") or ""))
                table.setItem(row, 2, QTableWidgetItem(l.get("oblio_name") or ""))
                table.setItem(row, 3, QTableWidgetItem(l.get("oblio_code") or ""))
                table.setItem(row, 4, QTableWidgetItem(str(qty)))
                table.setItem(row, 5, QTableWidgetItem(f"{list_unit:.2f}"))
                table.setItem(row, 6, QTableWidgetItem(f"{float(l.get('vat_percent') or 0):g}%"))
                table.setItem(row, 7, QTableWidgetItem(f"{list_total:.2f}"))
            elif kind == "discount":
                qty = int(l.get("quantity") or 0)
                disc_unit = float(l.get("discount_per_unit") or 0)
                disc_total = round(disc_unit * qty, 2)
                disc_item = QTableWidgetItem("↳ Reducere")
                disc_item.setForeground(Qt.GlobalColor.yellow)
                table.setItem(row, 0, disc_item)
                for col in (1, 2, 3):
                    table.setItem(row, col, QTableWidgetItem(""))
                table.setItem(row, 4, QTableWidgetItem(str(qty)))
                table.setItem(row, 5, QTableWidgetItem(f"−{disc_unit:.2f}"))
                table.setItem(row, 6, QTableWidgetItem(f"{float(l.get('vat_percent') or 0):g}%"))
                tot_item = QTableWidgetItem(f"−{disc_total:.2f}")
                tot_item.setForeground(Qt.GlobalColor.yellow)
                table.setItem(row, 7, tot_item)
            else:  # shipping
                amt = float(l.get("amount") or 0)
                table.setItem(row, 0, QTableWidgetItem("Transport"))
                table.setItem(row, 1, QTableWidgetItem(""))
                table.setItem(row, 2, QTableWidgetItem("Transport"))
                table.setItem(row, 3, QTableWidgetItem("SHIPPING"))
                table.setItem(row, 4, QTableWidgetItem("1"))
                table.setItem(row, 5, QTableWidgetItem(f"{amt:.2f}"))
                table.setItem(row, 6, QTableWidgetItem("21%"))
                table.setItem(row, 7, QTableWidgetItem(f"{amt:.2f}"))

        total_lbl = QLabel(
            f"<b>Total (VAT incl.):</b> {record.get('total_gross') or 0:.2f} {currency}"
        )
        total_lbl.setTextFormat(Qt.TextFormat.RichText)

        export_btn = QPushButton("Export as HTML…")
        export_btn.setObjectName("primary")
        export_btn.clicked.connect(self._export)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.addButton(export_btn, QDialogButtonBox.ButtonRole.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(top)
        layout.addWidget(table, 1)
        layout.addWidget(total_lbl)
        layout.addWidget(buttons)

    def _export(self) -> None:
        view: "InvoicesView | None" = self.parent_view if isinstance(self.parent_view, InvoicesView) else None
        if view is None:
            QMessageBox.warning(self, "Export unavailable", "Cannot resolve repository from this dialog.")
            return
        default_name = suggested_filename(self.invoice_row)
        path, _ = QFileDialog.getSaveFileName(self, "Export invoice", default_name, "HTML (*.html)")
        if not path:
            return
        try:
            out = export_invoice_html(view.repo, self.invoice_row, Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        if QMessageBox.question(self, "Export complete",
                                f"Saved to {out}.\nOpen it now?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                                ) == QMessageBox.StandardButton.Yes:
            webbrowser.open(out.as_uri())
