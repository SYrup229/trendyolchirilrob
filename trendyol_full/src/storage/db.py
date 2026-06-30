import sqlite3
import threading
from contextlib import contextmanager

from src.config import DB_PATH
from src.storage import migrations

_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # synchronous=NORMAL is safe with WAL on a single-user app and is much faster
    # for bulk INSERTs (Oblio sync writes thousands of rows).
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    key        TEXT PRIMARY KEY,
    ciphertext BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS oblio_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    oblio_id    TEXT,
    code        TEXT,
    name        TEXT NOT NULL,
    measuring_unit TEXT,
    vat_percent REAL,
    currency    TEXT,
    price       REAL,
    raw_json    TEXT NOT NULL,
    synced_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oblio_products_code ON oblio_products(code);
CREATE INDEX IF NOT EXISTS idx_oblio_products_name ON oblio_products(name);
-- NOTE: the unique index on oblio_id is created in _migrate() AFTER the column
-- is guaranteed to exist (old DBs are missing it).

CREATE TABLE IF NOT EXISTS trendyol_listings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode        TEXT,
    stock_code     TEXT,
    product_main_id TEXT,
    title          TEXT NOT NULL,
    brand          TEXT,
    list_price     REAL,
    sale_price     REAL,
    currency       TEXT,
    raw_json       TEXT NOT NULL,
    synced_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trendyol_listings_barcode ON trendyol_listings(barcode);
CREATE INDEX IF NOT EXISTS idx_trendyol_listings_stockcode ON trendyol_listings(stock_code);
CREATE INDEX IF NOT EXISTS idx_trendyol_listings_title ON trendyol_listings(title);

-- "Variant" mapping: one Oblio product is sold as several Trendyol listings
-- (the same physical item under different Trendyol titles). One listing belongs
-- to at most one variant chain.
CREATE TABLE IF NOT EXISTS variant_mappings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    oblio_product_id    INTEGER NOT NULL REFERENCES oblio_products(id) ON DELETE CASCADE,
    trendyol_listing_id INTEGER NOT NULL UNIQUE REFERENCES trendyol_listings(id) ON DELETE CASCADE,
    units_per_listing   INTEGER NOT NULL DEFAULT 1,
    vat_percent         REAL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vm_oblio ON variant_mappings(oblio_product_id);

-- "Bundle" mapping: one Trendyol listing is a composite that contains several
-- Oblio products (e.g. nailgun + nails). A listing can have many bundle
-- components, but the same (listing, oblio) pair only once.
CREATE TABLE IF NOT EXISTS bundle_mappings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trendyol_listing_id INTEGER NOT NULL REFERENCES trendyol_listings(id) ON DELETE CASCADE,
    oblio_product_id    INTEGER NOT NULL REFERENCES oblio_products(id) ON DELETE CASCADE,
    units_per_listing   INTEGER NOT NULL DEFAULT 1,
    vat_percent         REAL,
    created_at          TEXT NOT NULL,
    UNIQUE(trendyol_listing_id, oblio_product_id)
);
CREATE INDEX IF NOT EXISTS idx_bm_listing ON bundle_mappings(trendyol_listing_id);
CREATE INDEX IF NOT EXISTS idx_bm_oblio ON bundle_mappings(oblio_product_id);

CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number  TEXT UNIQUE NOT NULL,
    status        TEXT NOT NULL,
    buyer_name    TEXT,
    buyer_email   TEXT,
    ship_address  TEXT,
    ship_city     TEXT,
    ship_country  TEXT,
    currency      TEXT,
    total_amount  REAL,
    order_date    TEXT,
    raw_json      TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    invoice_state TEXT NOT NULL DEFAULT 'pending',
    -- External invoice attached to the order (might be ours after a successful
    -- seller-invoice-links POST, OR something attached outside this app — Oblio
    -- web UI, a different install before DB wipe, etc).
    tr_invoice_link         TEXT,
    tr_invoice_link_date_ms INTEGER,
    tr_invoice_number       TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(invoice_state);

CREATE TABLE IF NOT EXISTS invoices (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id                 INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
    series                   TEXT NOT NULL,
    number                   TEXT NOT NULL,
    oblio_link               TEXT,
    oblio_einvoice_link      TEXT,
    total                    REAL,
    currency                 TEXT,
    issued_at                TEXT NOT NULL,
    raw_response             TEXT NOT NULL,
    last_send_error          TEXT,
    trendyol_link_sent_at    TEXT,
    trendyol_link_error      TEXT
);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    migrations.run(conn)
    # Backfill _schema_migrations for existing DBs that already had these applied
    # via the old ad-hoc _migrate() function — they shouldn't be re-run.
    _backfill_legacy_migration_marks(conn)
    conn.commit()


def _backfill_legacy_migration_marks(conn: sqlite3.Connection) -> None:
    """Pre-migration-framework DBs may already have the columns from migrations
    001/002 applied. If so, record them as applied so we don't try to re-run."""
    cols_oblio = {r["name"] for r in conn.execute("PRAGMA table_info(oblio_products)").fetchall()}
    cols_map = {r["name"] for r in conn.execute("PRAGMA table_info(mappings)").fetchall()}
    legacy = []
    if "oblio_id" in cols_oblio:
        legacy.append(("001_oblio_id",))
    if "units_per_listing" in cols_map:
        legacy.append(("002_units_per_listing",))
    if legacy:
        conn.executemany(
            "INSERT OR IGNORE INTO _schema_migrations(name) VALUES(?)",
            legacy,
        )
