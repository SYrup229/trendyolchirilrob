import hashlib

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget, QWizard, QWizardPage,
)


def _fp(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]

from src.api.oblio import OblioClient
from src.api.trendyol import TrendyolClient
from src.config import APP_NAME
from src.i18n import t
from src.services import sync as sync_svc
from src.storage.repo import Repo
from src.ui.style import APP_STYLESHEET
from src.ui.widgets import BusyDialog, TaskRunner


class SetupWizard(QWizard):
    def __init__(self, repo: Repo, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.setWindowTitle(f"{APP_NAME} — Configurare")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.resize(720, 520)
        self.setStyleSheet(APP_STYLESHEET)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        # Qt's stock wizard buttons render in English (no Qt translations loaded).
        self.setButtonText(QWizard.WizardButton.NextButton, "Înainte >")
        self.setButtonText(QWizard.WizardButton.BackButton, "< Înapoi")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finalizează")
        self.setButtonText(QWizard.WizardButton.CancelButton, t("Cancel"))

        self.page_welcome = WelcomePage()
        self.page_oblio = OblioPage(repo)
        self.page_trendyol = TrendyolPage(repo)
        self.page_sync = InitialSyncPage(repo)

        self.addPage(self.page_welcome)
        self.addPage(self.page_oblio)
        self.addPage(self.page_trendyol)
        self.addPage(self.page_sync)


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle(t("Welcome"))
        self.setSubTitle(t("Let's connect your Oblio account and Trendyol seller account."))
        layout = QVBoxLayout(self)
        msg = QLabel(
            "Acest wizard va:\n"
            "  1. Cere credențialele Oblio API și codul fiscal (CIF).\n"
            "  2. Cere credențialele Trendyol International API și URL-ul de bază.\n"
            "  3. Importa produsele Oblio și anunțurile Trendyol.\n\n"
            "Poți rula acest pas din nou ulterior din Setări."
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)


class OblioPage(QWizardPage):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.setTitle(t("Oblio connection"))
        self.setSubTitle(t("Credentials are stored locally, encrypted."))

        self.email = QLineEdit(repo.oblio_email() or "")
        self.secret = QLineEdit(repo.oblio_secret() or "")
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.cif = QLineEdit(repo.oblio_cif() or "")
        self.workstation = QLineEdit(repo.oblio_workstation() or "Sediu")
        self.management = QLineEdit(repo.oblio_management() or "")
        self.status = QLabel("")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow(t("Account email"), self.email)
        form.addRow(t("API secret"), self.secret)
        form.addRow(t("Company CIF"), self.cif)
        form.addRow(t("Workstation (workStation)"), self.workstation)
        form.addRow(t("Management (gestiune, optional)"), self.management)

        test_btn = QPushButton(t("Test connection"))
        test_btn.clicked.connect(self._test)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(test_btn)
        layout.addWidget(self.status)
        layout.addStretch(1)

        # If the current credentials match the last successfully-tested fingerprint,
        # don't force the user to click "Test connection" again.
        self._tested = self._fingerprint() == repo.get_setting("oblio_tested_fp")
        if self._tested:
            self.status.setText(t("Connected previously — Next to continue, or re-test if anything changed."))
            self.status.setObjectName("badge-ok")
        # any field edit invalidates the cached pass
        for w in (self.email, self.secret, self.cif):
            w.textChanged.connect(self._mark_untested)

    def _fingerprint(self) -> str:
        return _fp([self.email.text().strip(), self.secret.text().strip(), self.cif.text().strip()])

    def _mark_untested(self) -> None:
        if self._tested:
            self._tested = False
            self.status.setText(t("Credentials changed — please re-test."))
            self.status.setObjectName("badge-warn")
            self.style().polish(self.status)
            self.completeChanged.emit()

    def _test(self) -> None:
        email = self.email.text().strip()
        secret = self.secret.text().strip()
        cif = self.cif.text().strip()
        if not (email and secret and cif):
            self.status.setText(t("Please fill email, secret and CIF."))
            self.status.setObjectName("badge-err")
            self.style().polish(self.status)
            return
        try:
            client = OblioClient(email=email, secret=secret, cif=cif)
            client.test_connection()
        except Exception as e:
            self.status.setText(f"Failed: {e}")
            self.status.setObjectName("badge-err")
            self.style().polish(self.status)
            return
        self.status.setText(t("Connected successfully."))
        self.status.setObjectName("badge-ok")
        self.style().polish(self.status)
        self.repo.set_setting("oblio_tested_fp", self._fingerprint())
        self._tested = True
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return self._tested

    def validatePage(self) -> bool:  # noqa: N802
        self.repo.set_setting("oblio_email", self.email.text().strip())
        self.repo.set_setting("oblio_cif", self.cif.text().strip())
        self.repo.set_setting("oblio_workstation", self.workstation.text().strip() or "Sediu")
        self.repo.set_setting("oblio_management", self.management.text().strip())
        self.repo.set_oblio_secret(self.secret.text().strip())
        return True


class TrendyolPage(QWizardPage):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.setTitle(t("Trendyol connection"))
        self.setSubTitle(t("Enter the base URL and your seller credentials for Trendyol International."))

        self.base_url = QLineEdit(repo.trendyol_base_url() or "https://apigw.trendyol.com/integration")
        self.seller_id = QLineEdit(repo.trendyol_seller_id() or "")
        self.integration_ref = QLineEdit(repo.trendyol_integration_ref() or "")
        self.api_key = QLineEdit(repo.trendyol_api_key() or "")
        self.api_secret = QLineEdit(repo.trendyol_api_secret() or "")
        self.api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow(t("Base URL"), self.base_url)
        form.addRow(t("Seller ID (ID entitate)"), self.seller_id)
        form.addRow(t("Integration reference (Cod referință)"), self.integration_ref)
        form.addRow(t("API key (Cheie API)"), self.api_key)
        form.addRow(t("API secret (Secret API)"), self.api_secret)

        hint = QLabel(
            "Sfat: Trendyol International (RO/GR/BG) folosește prefixul <b>/integration/international</b>. "
            "Trendyol Turcia folosește <b>/integration</b>. Dacă primești eroarea 556 'Service Unavailable' "
            "de la Cloudflare, ai nimerit tenant-ul greșit — verifică să ai <b>/international</b> în URL."
        )
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")

        test_btn = QPushButton(t("Test connection"))
        test_btn.clicked.connect(self._test)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(test_btn)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self._tested = self._fingerprint() == repo.get_setting("trendyol_tested_fp")
        if self._tested:
            self.status.setText(t("Connected previously — Next to continue, or re-test if anything changed."))
            self.status.setObjectName("badge-ok")
        for w in (self.base_url, self.seller_id, self.integration_ref, self.api_key, self.api_secret):
            w.textChanged.connect(self._mark_untested)

    def _fingerprint(self) -> str:
        return _fp([
            self.base_url.text().strip(), self.seller_id.text().strip(),
            self.integration_ref.text().strip(),
            self.api_key.text().strip(), self.api_secret.text().strip(),
        ])

    def _mark_untested(self) -> None:
        if self._tested:
            self._tested = False
            self.status.setText(t("Credentials changed — please re-test."))
            self.status.setObjectName("badge-warn")
            self.style().polish(self.status)
            self.completeChanged.emit()

    def _test(self) -> None:
        base = self.base_url.text().strip()
        sid = self.seller_id.text().strip()
        key = self.api_key.text().strip()
        sec = self.api_secret.text().strip()
        ref = self.integration_ref.text().strip() or None
        if not (base and sid and key and sec):
            self.status.setText(t("Please fill all fields (integration reference is optional for Trendyol TR)."))
            self.status.setObjectName("badge-err")
            self.style().polish(self.status)
            return
        try:
            client = TrendyolClient(base_url=base, seller_id=sid, api_key=key, api_secret=sec, integration_ref=ref)
            client.test_connection()
        except Exception as e:
            self.status.setText(f"Failed: {e}")
            self.status.setObjectName("badge-err")
            self.style().polish(self.status)
            return
        self.status.setText(t("Connected successfully."))
        self.status.setObjectName("badge-ok")
        self.style().polish(self.status)
        self.repo.set_setting("trendyol_tested_fp", self._fingerprint())
        self._tested = True
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return self._tested

    def validatePage(self) -> bool:  # noqa: N802
        self.repo.set_setting("trendyol_base_url", self.base_url.text().strip())
        self.repo.set_setting("trendyol_seller_id", self.seller_id.text().strip())
        self.repo.set_setting("trendyol_integration_ref", self.integration_ref.text().strip())
        self.repo.set_trendyol_api_key(self.api_key.text().strip())
        self.repo.set_trendyol_api_secret(self.api_secret.text().strip())
        return True


class InitialSyncPage(QWizardPage):
    def __init__(self, repo: Repo):
        super().__init__()
        self.repo = repo
        self.setTitle(t("Initial sync"))
        self.setSubTitle(t("Pull your Oblio products and Trendyol listings now."))

        self.status = QLabel(t("Ready to sync."))
        self.status.setWordWrap(True)
        self.sync_btn = QPushButton(t("Run initial sync"))
        self.sync_btn.setObjectName("primary")
        self.sync_btn.clicked.connect(self._run_sync)

        layout = QVBoxLayout(self)
        layout.addWidget(self.sync_btn)
        layout.addWidget(self.status)
        layout.addStretch(1)

        self._done = False

    def isComplete(self) -> bool:  # noqa: N802
        return self._done

    def _run_sync(self) -> None:
        repo = self.repo
        dlg = BusyDialog(t("Initial sync"), "Pornire…", self)

        def job(progress):
            progress("Pulling Oblio products…")
            n_oblio = sync_svc.sync_oblio_products(repo, progress=progress)
            progress("Pulling Trendyol listings…")
            n_tr = sync_svc.sync_trendyol_listings(repo, progress=progress)
            return n_oblio, n_tr

        runner = TaskRunner(job, self)
        runner.progress.connect(dlg.set_message)

        def on_done(result):
            n_oblio, n_tr = result
            dlg.accept()
            self.status.setText(f"Pulled {n_oblio} Oblio products and {n_tr} Trendyol listings.")
            self.status.setObjectName("badge-ok")
            self.style().polish(self.status)
            self._done = True
            self.completeChanged.emit()

        def on_failed(msg):
            dlg.reject()
            QMessageBox.critical(self, "Sincronizare eșuată", msg)
            self.status.setText(f"Failed: {msg}")
            self.status.setObjectName("badge-err")
            self.style().polish(self.status)

        runner.done.connect(on_done)
        runner.failed.connect(on_failed)
        runner.start()
        dlg.exec()
