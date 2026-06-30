import logging
import logging.handlers
import os
import re
from pathlib import Path

from src.config import DATA_DIR

LOG_DIR = DATA_DIR
TRENDYOL_LOG = LOG_DIR / "trendyol.log"
OBLIO_LOG = LOG_DIR / "oblio.log"
APP_LOG = LOG_DIR / "app.log"

# Default to WARNING — production-quiet. Flip via env var TRENDYOL_FULL_LOG=DEBUG
# (or INFO) when troubleshooting. Apps that need verbose by default can also call
# logger.setLevel(logging.DEBUG) explicitly.
_DEFAULT_LEVEL = getattr(
    logging,
    os.environ.get("TRENDYOL_FULL_LOG", "WARNING").upper(),
    logging.WARNING,
)

_FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_configured: set[str] = set()


def _get_logger(name: str, path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    if name in _configured:
        return logger
    logger.setLevel(_DEFAULT_LEVEL)
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(_FORMATTER)
    logger.addHandler(handler)
    _configured.add(name)
    return logger


def get_trendyol_logger() -> logging.Logger:
    return _get_logger("trendyol", TRENDYOL_LOG)


def get_oblio_logger() -> logging.Logger:
    return _get_logger("oblio", OBLIO_LOG)


def get_app_logger() -> logging.Logger:
    return _get_logger("app", APP_LOG)


def log_dir() -> Path:
    return LOG_DIR


# --- PII redaction for response bodies ---------------------------------

# Fields in API responses that carry buyer PII or our own credentials. We blank
# the value before the body hits disk.
_PII_FIELDS = (
    "customerEmail", "customerFirstName", "customerLastName", "fullName",
    "firstName", "lastName", "phone", "email",
    "address", "address1", "address2", "fullAddress", "shortAddress",
    "postalCode", "taxNumber", "access_token", "refresh_token",
    "client_secret", "secret",
)
_PII_RE = re.compile(
    r'"(' + "|".join(_PII_FIELDS) + r')"\s*:\s*"[^"]*"',
)


def redact_pii(body: str) -> str:
    """Replace string values of known PII fields with <redacted>. Safe to call on
    arbitrary text — non-matching content passes through unchanged."""
    if not body:
        return body
    return _PII_RE.sub(lambda m: f'"{m.group(1)}":"<redacted>"', body)
