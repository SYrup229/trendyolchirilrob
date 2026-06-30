import base64
import sys

try:
    import win32crypt  # type: ignore
    _HAS_DPAPI = True
except ImportError:
    _HAS_DPAPI = False

from src.storage.db import get_conn, tx

_ENTROPY = b"TrendyolFull.v1"


def _encrypt(plaintext: str) -> bytes:
    if _HAS_DPAPI:
        blob = win32crypt.CryptProtectData(plaintext.encode("utf-8"), None, _ENTROPY, None, None, 0)
        return blob
    return base64.b64encode(plaintext.encode("utf-8"))


def _decrypt(ciphertext: bytes) -> str:
    if _HAS_DPAPI:
        _, data = win32crypt.CryptUnprotectData(ciphertext, _ENTROPY, None, None, 0)
        return data.decode("utf-8")
    return base64.b64decode(ciphertext).decode("utf-8")


def set_secret(key: str, value: str) -> None:
    ct = _encrypt(value)
    with tx() as conn:
        conn.execute(
            "INSERT INTO secrets(key, ciphertext) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET ciphertext = excluded.ciphertext",
            (key, ct),
        )


def get_secret(key: str) -> str | None:
    row = get_conn().execute("SELECT ciphertext FROM secrets WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return _decrypt(row["ciphertext"])


def delete_secret(key: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM secrets WHERE key = ?", (key,))
