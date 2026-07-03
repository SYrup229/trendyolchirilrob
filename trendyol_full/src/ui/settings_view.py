import os
import subprocess
import sys

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from src.config import (
    DEFAULT_INVOICE_CURRENCY, DEFAULT_INVOICE_LANGUAGE, DEFAULT_INVOICE_SERIES,
    DEFAULT_POLL_SECONDS, DEFAULT_VAT_PERCENT,
)
from src.i18n import t
from src.logging_setup import log_dir
from src.services import sync as sync_svc
from src.storage.repo import Repo
from src.ui.widgets import BusyDialog, TaskRunner


class SettingsView(QWidget):
    poll_changed = Signal()
    data_synced = Signal()       # fired after Oblio/Trendyol re-sync completes
    mappings_reset = Signal(int) # fired after wiping mappings; payload = # removed

    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo

        title = QLabel(t("Settings"))
        title.setObjectName("title")

        self.poll = QSpinBox()
        self.poll.setRange(60, 24 * 3600)
        self.poll.setSuffix(" seconds")
        self.poll.setSingleStep(30)
        self.poll.setValue(int(repo.poll_seconds()))

        self.vat = QDoubleSpinBox()
        self.vat.setRange(0.0, 100.0)
        self.vat.setDecimals(2)
        self.vat.setSuffix(" %")
        self.vat.setValue(float(repo.default_vat()))

        # Editable combo: pre-fills with the user's saved series, and "Refresh from
        # Oblio" populates the dropdown with the actual list from their account.
        # Since the per-country automatic series (COUNTRY_SERIES), this value is only
        # the fallback for countries without a dedicated series.
        from src.services.invoicing import COUNTRY_SERIES
        self.series = QComboBox()
        self.series.setEditable(True)
        self.series.setMinimumWidth(240)
        auto_map = ", ".join(f"{c} → {s}" for c, s in COUNTRY_SERIES.items())
        self.series.setToolTip(
            f"Seria se alege automat după țara comenzii: {auto_map}. "
            f"Valoarea de aici se folosește doar pentru alte țări."
        )
        saved = repo.invoice_series()
        if saved:
            self.series.addItem(saved)
            self.series.setCurrentText(saved)

        self.series_refresh_btn = QPushButton(t("Refresh from Oblio"))
        self.series_refresh_btn.clicked.connect(self._refresh_series_from_oblio)
        series_row = QHBoxLayout()
        series_row.setContentsMargins(0, 0, 0, 0)
        series_row.addWidget(self.series, 1)
        series_row.addWidget(self.series_refresh_btn, 0)
        self._series_container = QWidget()
        self._series_container.setLayout(series_row)
        self.language = QLineEdit(repo.invoice_language())
        self.currency = QLineEdit(repo.invoice_currency())
        self.workstation = QLineEdit(repo.oblio_workstation())
        self.management = QLineEdit(repo.oblio_management() or "")

        self.company_name = QLineEdit(repo.get_setting("company_name") or "")
        self.company_address = QLineEdit(repo.get_setting("company_address") or "")
        self.company_extra = QLineEdit(repo.get_setting("company_extra") or "")

        self.oblio_dry_run = QCheckBox(t("Dry run (don't actually POST to Oblio)"))
        self.oblio_dry_run.setChecked(repo.oblio_dry_run())
        self.oblio_dry_run.setToolTip(
            "Scrie payload-ul Oblio în app.log fără a face cererea HTTP. "
            "Folosește pentru a verifica ce s-ar trimite, înainte de a trimite efectiv."
        )

        save_btn = QPushButton(t("Save settings"))
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)

        form = QFormLayout()
        form.addRow(t("Poll interval"), self.poll)
        form.addRow(t("Default VAT"), self.vat)
        form.addRow(t("Invoice series (other countries)"), self._series_container)
        form.addRow(t("Invoice language"), self.language)
        form.addRow(t("Invoice currency"), self.currency)
        form.addRow(t("Oblio workstation"), self.workstation)
        form.addRow(t("Oblio management (optional)"), self.management)
        form.addRow(t("Company name"), self.company_name)
        form.addRow(t("Company address"), self.company_address)
        form.addRow(t("Reg Com / IBAN / phone (optional)"), self.company_extra)
        form.addRow("", self.oblio_dry_run)

        # --- sync buttons ---
        sync_label = QLabel(t("Re-sync data"))
        sync_label.setObjectName("title")
        sync_hint = QLabel(t(
            "Pull the latest products/listings from Oblio and Trendyol. "
            "Existing mappings are preserved as long as the Trendyol listing keeps its ID."
        ))
        sync_hint.setObjectName("subtitle")
        sync_hint.setWordWrap(True)

        btn_sync_oblio = QPushButton(t("Re-sync Oblio products"))
        btn_sync_oblio.clicked.connect(self._sync_oblio)
        btn_sync_trendyol = QPushButton(t("Re-sync Trendyol listings"))
        btn_sync_trendyol.clicked.connect(self._sync_trendyol)
        btn_sync_both = QPushButton(t("Re-sync both"))
        btn_sync_both.setObjectName("primary")
        btn_sync_both.clicked.connect(self._sync_both)

        sync_row = QHBoxLayout()
        sync_row.addWidget(btn_sync_oblio)
        sync_row.addWidget(btn_sync_trendyol)
        sync_row.addWidget(btn_sync_both)
        sync_row.addStretch(1)

        # --- diagnostics ---
        diag_label = QLabel(t("Diagnostics"))
        diag_label.setObjectName("title")
        diag_hint = QLabel(
            "Cererile către API-ul Trendyol sunt înregistrate (fără credențiale) în acest folder. "
            "Trimite <b>trendyol.log</b> când ceri ajutor pentru o eroare de API."
        )
        diag_hint.setObjectName("subtitle")
        diag_hint.setWordWrap(True)

        from PySide6.QtCore import Qt
        path_label = QLabel(f"<code>{log_dir()}</code>")
        path_label.setTextFormat(Qt.TextFormat.RichText)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        btn_open_log = QPushButton(t("Open log folder"))
        btn_open_log.clicked.connect(self._open_log_folder)

        diag_row = QHBoxLayout()
        diag_row.addWidget(btn_open_log)
        diag_row.addStretch(1)

        # --- danger zone ---
        danger_label = QLabel(t("Reset"))
        danger_label.setObjectName("title")
        danger_hint = QLabel(t(
            "Wipe every mapping between Trendyol listings and Oblio products. "
            "All local invoices that were generated from those mappings will be deleted too. "
            "Settings, credentials, orders and Oblio/Trendyol catalogs are NOT touched."
        ))
        danger_hint.setObjectName("subtitle")
        danger_hint.setWordWrap(True)

        btn_reset_mappings = QPushButton(t("Reset all mappings…"))
        btn_reset_mappings.setObjectName("danger")
        btn_reset_mappings.clicked.connect(self._reset_mappings)

        danger_row = QHBoxLayout()
        danger_row.addWidget(btn_reset_mappings)
        danger_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(save_btn)
        layout.addSpacing(20)
        layout.addWidget(sync_label)
        layout.addWidget(sync_hint)
        layout.addLayout(sync_row)
        layout.addSpacing(20)
        layout.addWidget(diag_label)
        layout.addWidget(diag_hint)
        layout.addWidget(path_label)
        layout.addLayout(diag_row)
        layout.addSpacing(20)
        layout.addWidget(danger_label)
        layout.addWidget(danger_hint)
        layout.addLayout(danger_row)
        layout.addStretch(1)

    def refresh(self) -> None:
        self.poll.setValue(int(self.repo.poll_seconds()))
        self.vat.setValue(float(self.repo.default_vat()))
        saved = self.repo.invoice_series()
        if saved and self.series.findText(saved) == -1:
            self.series.addItem(saved)
        self.series.setCurrentText(saved)
        self.language.setText(self.repo.invoice_language())
        self.currency.setText(self.repo.invoice_currency())
        self.workstation.setText(self.repo.oblio_workstation())
        self.management.setText(self.repo.oblio_management() or "")
        self.company_name.setText(self.repo.get_setting("company_name") or "")
        self.company_address.setText(self.repo.get_setting("company_address") or "")
        self.company_extra.setText(self.repo.get_setting("company_extra") or "")
        self.oblio_dry_run.setChecked(self.repo.oblio_dry_run())

    def _save(self) -> None:
        repo = self.repo
        prev_poll = repo.poll_seconds()
        repo.set_setting("poll_seconds", str(self.poll.value()))
        repo.set_setting("default_vat", f"{self.vat.value():g}")
        repo.set_setting("invoice_series", self.series.currentText().strip() or DEFAULT_INVOICE_SERIES)
        repo.set_setting("invoice_language", self.language.text().strip() or DEFAULT_INVOICE_LANGUAGE)
        repo.set_setting("invoice_currency", self.currency.text().strip() or DEFAULT_INVOICE_CURRENCY)
        repo.set_setting("oblio_workstation", self.workstation.text().strip() or "Sediu")
        repo.set_setting("oblio_management", self.management.text().strip())
        repo.set_setting("company_name", self.company_name.text().strip())
        repo.set_setting("company_address", self.company_address.text().strip())
        repo.set_setting("company_extra", self.company_extra.text().strip())
        repo.set_setting("oblio_dry_run", "1" if self.oblio_dry_run.isChecked() else "0")
        if self.poll.value() != prev_poll:
            self.poll_changed.emit()
        QMessageBox.information(self, "Salvat", "Setările au fost salvate.")

    def _sync_oblio(self) -> None:
        self._run_sync(lambda p: (sync_svc.sync_oblio_products(self.repo, progress=p), 0), "Sincronizare produse Oblio…")

    def _sync_trendyol(self) -> None:
        self._run_sync(lambda p: (0, sync_svc.sync_trendyol_listings(self.repo, progress=p)), "Sincronizare produse Trendyol…")

    def _sync_both(self) -> None:
        def job(progress):
            a = sync_svc.sync_oblio_products(self.repo, progress=progress)
            b = sync_svc.sync_trendyol_listings(self.repo, progress=progress)
            return a, b
        self._run_sync(job, "Sincronizare…")

    def _refresh_series_from_oblio(self) -> None:
        """Fetch the user's Factura series from Oblio and populate the dropdown."""
        from src.services.sync import build_oblio_client
        try:
            client = build_oblio_client(self.repo)
            series_list = client.list_series()
        except Exception as e:
            QMessageBox.critical(self, t("Refresh from Oblio"),
                                 f"Nu am putut prelua seriile din Oblio:\n{e}")
            return

        # Keep only Factura-type series (Oblio also returns Proforma/Aviz/etc).
        factura = [s for s in series_list
                   if str(s.get("type") or "").lower() in ("factura", "factură")]
        if not factura:
            QMessageBox.information(self, t("Refresh from Oblio"),
                                    "Oblio nu are configurată nicio serie de tip Factură.")
            return

        # Preserve the current value if it's still valid; otherwise pick the first.
        current = self.series.currentText().strip()
        names = [str(s.get("name") or "") for s in factura if s.get("name")]
        self.series.clear()
        self.series.addItems(names)
        if current in names:
            self.series.setCurrentText(current)
        else:
            self.series.setCurrentIndex(0)

        QMessageBox.information(self, t("Refresh from Oblio"),
                                f"Am încărcat {len(names)} serii. Alege una și apasă "
                                f"\"{t('Save settings')}\".")

    def _reset_mappings(self) -> None:
        # Count both tables — variants (Oblio → many Trendyol) and bundles
        # (Trendyol → many Oblio).
        n_v = sum(self.repo.count_variants_per_oblio().values())
        n_b = sum(self.repo.count_components_per_listing().values())
        total = n_v + n_b
        if total == 0:
            QMessageBox.information(self, t("Reset"), "Nu există mapări de șters.")
            return
        confirmed = QMessageBox.warning(
            self,
            t("Reset"),
            f"Această acțiune șterge {n_v} variante + {n_b} componente de pachete "
            f"({total} mapări) și toate facturile locale derivate. Sigur continui?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        removed = self.repo.delete_all_mappings()
        QMessageBox.information(self, t("Reset"), f"Șterse {removed} mapări (variante + pachete).")
        self.mappings_reset.emit(removed)

    def _open_log_folder(self) -> None:
        path = str(log_dir())
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.warning(self, "Nu pot deschide folderul", f"{path}\n\n{e}")

    def _run_sync(self, fn, start_msg: str) -> None:
        dlg = BusyDialog("Sincronizare", start_msg, self)
        runner = TaskRunner(fn, self)
        runner.progress.connect(dlg.set_message)

        def on_done(result):
            dlg.accept()
            n_oblio, n_tr = result
            parts = []
            if n_oblio: parts.append(f"{n_oblio} produse Oblio")
            if n_tr: parts.append(f"{n_tr} anunțuri Trendyol")
            QMessageBox.information(self, "Sincronizare finalizată",
                                    "Salvate: " + (", ".join(parts) if parts else "0 elemente") + ".")
            # Tell the main window the catalog has new data so it can refresh dependent views.
            self.data_synced.emit()

        def on_failed(msg):
            dlg.reject()
            QMessageBox.critical(self, "Sincronizare eșuată", msg)

        runner.done.connect(on_done)
        runner.failed.connect(on_failed)
        runner.start()
        dlg.exec()
