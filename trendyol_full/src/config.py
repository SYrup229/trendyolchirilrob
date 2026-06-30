import os
from pathlib import Path

APP_NAME = "Trendyol ↔ Oblio"
ORG_NAME = "TrendyolFull"

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICON_PATH = ASSETS / "icon.ico"

APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
DATA_DIR = APPDATA / "TrendyolFull"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "trendyol_full.sqlite3"

DEFAULT_POLL_SECONDS = 600
DEFAULT_VAT_PERCENT = 21
DEFAULT_INVOICE_SERIES = "TNDY"
DEFAULT_INVOICE_LANGUAGE = "RO"
DEFAULT_INVOICE_CURRENCY = "RON"

OBLIO_BASE_URL = "https://www.oblio.eu/api"
