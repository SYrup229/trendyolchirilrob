import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from src.config import APP_NAME, ORG_NAME, ICON_PATH
from src.logging_setup import get_app_logger
from src.storage.db import init_db
from src.storage.repo import Repo
from src.ui.main_window import MainWindow
from src.ui.setup_wizard import SetupWizard


def _install_excepthook() -> None:
    log = get_app_logger()
    def handler(exc_type, exc, tb):
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = handler


def main() -> int:
    _install_excepthook()
    log = get_app_logger()
    log.info("app start")

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    init_db()
    repo = Repo()

    if not repo.is_setup_complete():
        wizard = SetupWizard(repo)
        if wizard.exec() != SetupWizard.DialogCode.Accepted:
            return 0
        repo.mark_setup_complete()

    window = MainWindow(repo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
