"""
Tiny migration framework. Each migration is a function taking the connection.
We record successful runs in `_schema_migrations` so we never re-apply them.

Add new migrations by appending to MIGRATIONS at the bottom. Don't reorder or
rename existing entries — the name is the idempotency key.
"""
import sqlite3
from typing import Callable

Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


# --- migrations ---------------------------------------------------------

def _001_oblio_id(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "oblio_products", "oblio_id"):
        conn.execute("ALTER TABLE oblio_products ADD COLUMN oblio_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_oblio_products_oblioid "
        "ON oblio_products(oblio_id) WHERE oblio_id IS NOT NULL"
    )


def _002_units_per_listing(conn: sqlite3.Connection) -> None:
    # Fresh installs ship a SCHEMA that has already replaced the legacy single
    # `mappings` table with `variant_mappings` + `bundle_mappings`, so there's
    # nothing to alter here.
    if not _table_exists(conn, "mappings"):
        return
    if not _has_column(conn, "mappings", "units_per_listing"):
        conn.execute("ALTER TABLE mappings ADD COLUMN units_per_listing INTEGER NOT NULL DEFAULT 1")


def _003_multi_oblio_mappings(conn: sqlite3.Connection) -> None:
    """Drop UNIQUE(trendyol_listing_id) so one Trendyol listing can map to multiple
    Oblio products (composite/bundle invoicing). Replace with a UNIQUE(oblio, listing)
    pair so the same listing can't be linked to the same Oblio product twice."""
    # Fresh installs never had the legacy `mappings` table — see _002 note.
    if not _table_exists(conn, "mappings"):
        return
    # Detect: if the unique constraint is gone (e.g., fresh DB with new SCHEMA),
    # this migration is a no-op.
    idx_rows = conn.execute("PRAGMA index_list(mappings)").fetchall()
    legacy_unique_on_listing = False
    for ir in idx_rows:
        if not ir["unique"]:
            continue
        cols = [r["name"] for r in conn.execute(f"PRAGMA index_info({ir['name']})").fetchall()]
        if cols == ["trendyol_listing_id"]:
            legacy_unique_on_listing = True
            break
    if not legacy_unique_on_listing:
        # Either already migrated or a fresh schema — just make sure the pair-unique exists.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_oblio_listing "
            "ON mappings(oblio_product_id, trendyol_listing_id)"
        )
        return

    # SQLite doesn't support DROP CONSTRAINT — recreate the table.
    conn.execute("""
        CREATE TABLE mappings_new (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            oblio_product_id    INTEGER NOT NULL REFERENCES oblio_products(id) ON DELETE CASCADE,
            trendyol_listing_id INTEGER NOT NULL REFERENCES trendyol_listings(id) ON DELETE CASCADE,
            vat_percent         REAL,
            units_per_listing   INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT NOT NULL,
            UNIQUE(oblio_product_id, trendyol_listing_id)
        )
    """)
    conn.execute(
        "INSERT INTO mappings_new(id, oblio_product_id, trendyol_listing_id, vat_percent, units_per_listing, created_at) "
        "SELECT id, oblio_product_id, trendyol_listing_id, vat_percent, units_per_listing, created_at FROM mappings"
    )
    conn.execute("DROP TABLE mappings")
    conn.execute("ALTER TABLE mappings_new RENAME TO mappings")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mappings_oblio ON mappings(oblio_product_id)")


def _004_invoice_send_error(conn: sqlite3.Connection) -> None:
    """`last_send_error`: latest Oblio API error returned when trying to POST the
    invoice. NULL = never attempted, or attempted and succeeded (oblio_link set)."""
    if not _has_column(conn, "invoices", "last_send_error"):
        conn.execute("ALTER TABLE invoices ADD COLUMN last_send_error TEXT")


def _005_split_mappings(conn: sqlite3.Connection) -> None:
    """Split the single `mappings` table into two:
      - `variant_mappings`: one Oblio product → one or more Trendyol listings
        (the "same physical item under different Trendyol titles" case).
        UNIQUE(trendyol_listing_id) — a listing can be a variant of at most one Oblio.
      - `bundle_mappings`: one Trendyol listing → one or more Oblio components
        (the "Nailgun + Nails bundle" case).
        UNIQUE(trendyol_listing_id, oblio_product_id) — same component only once per bundle.

    A given Trendyol listing should be EITHER a variant OR a bundle, never both.
    The migration assigns each existing listing automatically based on whether it had
    one mapping (=> variant) or many (=> bundle)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS variant_mappings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            oblio_product_id    INTEGER NOT NULL REFERENCES oblio_products(id) ON DELETE CASCADE,
            trendyol_listing_id INTEGER NOT NULL UNIQUE REFERENCES trendyol_listings(id) ON DELETE CASCADE,
            units_per_listing   INTEGER NOT NULL DEFAULT 1,
            vat_percent         REAL,
            created_at          TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bundle_mappings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            trendyol_listing_id INTEGER NOT NULL REFERENCES trendyol_listings(id) ON DELETE CASCADE,
            oblio_product_id    INTEGER NOT NULL REFERENCES oblio_products(id) ON DELETE CASCADE,
            units_per_listing   INTEGER NOT NULL DEFAULT 1,
            vat_percent         REAL,
            created_at          TEXT NOT NULL,
            UNIQUE(trendyol_listing_id, oblio_product_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vm_oblio ON variant_mappings(oblio_product_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bm_listing ON bundle_mappings(trendyol_listing_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bm_oblio ON bundle_mappings(oblio_product_id)")

    # Migrate data from the legacy `mappings` table if it exists.
    legacy_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mappings'"
    ).fetchone() is not None
    if not legacy_exists:
        return

    listing_counts = {
        r["trendyol_listing_id"]: r["c"]
        for r in conn.execute(
            "SELECT trendyol_listing_id, COUNT(*) AS c FROM mappings GROUP BY trendyol_listing_id"
        ).fetchall()
    }
    for m in conn.execute("SELECT * FROM mappings").fetchall():
        target = "bundle_mappings" if listing_counts.get(m["trendyol_listing_id"], 0) > 1 \
                 else "variant_mappings"
        try:
            conn.execute(
                f"INSERT INTO {target}(oblio_product_id, trendyol_listing_id, "
                f"units_per_listing, vat_percent, created_at) VALUES(?, ?, ?, ?, ?)",
                (m["oblio_product_id"], m["trendyol_listing_id"],
                 m["units_per_listing"] or 1, m["vat_percent"], m["created_at"]),
            )
        except Exception:
            # Duplicate (oblio, listing) for bundle, or duplicate listing for variant.
            # Either is a legacy data oddity; skip the duplicate.
            pass

    conn.execute("DROP TABLE mappings")


def _006_trendyol_invoice_link(conn: sqlite3.Connection) -> None:
    """Track whether (and when) the Oblio invoice link was POSTed back to Trendyol.
    NULL `trendyol_link_sent_at` = not posted; non-NULL = success.
    `trendyol_link_error` holds the most recent error message from Trendyol if
    the POST failed (null after a later successful retry)."""
    if not _has_column(conn, "invoices", "trendyol_link_sent_at"):
        conn.execute("ALTER TABLE invoices ADD COLUMN trendyol_link_sent_at TEXT")
    if not _has_column(conn, "invoices", "trendyol_link_error"):
        conn.execute("ALTER TABLE invoices ADD COLUMN trendyol_link_error TEXT")


def _007_order_invoice_link(conn: sqlite3.Connection) -> None:
    """Capture Trendyol-side invoice attachment per order so we can:
      - Skip auto-creating local invoices for orders that have already been invoiced
        externally (manual Oblio web UI, another tool, prior install before DB wipe).
      - Hide such orders from the Invoices tab unless WE were the ones who attached
        the link (i.e. invoices.trendyol_link_sent_at is set)."""
    if not _has_column(conn, "orders", "tr_invoice_link"):
        conn.execute("ALTER TABLE orders ADD COLUMN tr_invoice_link TEXT")
    if not _has_column(conn, "orders", "tr_invoice_link_date_ms"):
        conn.execute("ALTER TABLE orders ADD COLUMN tr_invoice_link_date_ms INTEGER")
    if not _has_column(conn, "orders", "tr_invoice_number"):
        conn.execute("ALTER TABLE orders ADD COLUMN tr_invoice_number TEXT")


def _008_perf(conn: sqlite3.Connection) -> None:
    """Performance pass:
      - invoices.build_sig: fingerprint of the inputs a LOCAL draft was built from
        (builder rev : mappings rev : order fetched_at). Lets the poller skip
        rebuilding drafts whose inputs didn't change instead of rebuilding all of
        them every tick.
      - idx_trendyol_listings_pmid: resolve_line_mappings queries product_main_id
        FIRST for every invoice line; it was the only lookup key without an index.
      - idx_invoices_number: next-local-number generation does a LIKE 'LOC-date-%'
        prefix scan.
    """
    if not _has_column(conn, "invoices", "build_sig"):
        conn.execute("ALTER TABLE invoices ADD COLUMN build_sig TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trendyol_listings_pmid ON trendyol_listings(product_main_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(number)")


def _009_orders_per_package(conn: sqlite3.Connection) -> None:
    """Key orders by Trendyol SHIPMENT PACKAGE, not order number.

    Trendyol returns one entry per package and an order can be split into
    several packages (each with its own status, lines and quantities). With
    UNIQUE(order_number) all packages of a split order fought over one row —
    and because the feed is sorted newest-modified-first, the STALEST package
    was upserted last and won every poll. Split orders were stuck on the dead
    original package (e.g. UnPacked qty=5) and never became invoiceable.

    Rebuild: package_id column (backfilled from raw_json's $.id, falling back
    to order_number for legacy rows), UNIQUE on package_id, order_number kept
    as a plain indexed column. Row ids are preserved so invoices.order_id FKs
    stay valid.
    """
    if _has_column(conn, "orders", "package_id"):
        return
    # The rebuild DROPs the parent table of invoices.order_id. With
    # foreign_keys=ON, SQLite implicitly DELETEs parent rows on DROP TABLE —
    # which would CASCADE into invoices. The pragma is a no-op inside a
    # transaction, so close any open one first.
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("""
            CREATE TABLE orders_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id    TEXT NOT NULL UNIQUE,
                order_number  TEXT NOT NULL,
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
                tr_invoice_link         TEXT,
                tr_invoice_link_date_ms INTEGER,
                tr_invoice_number       TEXT
            )
        """)
        conn.execute("""
            INSERT INTO orders_new(id, package_id, order_number, status, buyer_name,
                buyer_email, ship_address, ship_city, ship_country, currency,
                total_amount, order_date, raw_json, fetched_at, invoice_state,
                tr_invoice_link, tr_invoice_link_date_ms, tr_invoice_number)
            SELECT id,
                   COALESCE(CAST(json_extract(raw_json, '$.id') AS TEXT), order_number),
                   order_number, status, buyer_name,
                   buyer_email, ship_address, ship_city, ship_country, currency,
                   total_amount, order_date, raw_json, fetched_at, invoice_state,
                   tr_invoice_link, tr_invoice_link_date_ms, tr_invoice_number
            FROM orders
        """)
        conn.execute("DROP TABLE orders")
        conn.execute("ALTER TABLE orders_new RENAME TO orders")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(invoice_state)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_number ON orders(order_number)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


MIGRATIONS: list[Migration] = [
    ("001_oblio_id", _001_oblio_id),
    ("002_units_per_listing", _002_units_per_listing),
    ("003_multi_oblio_mappings", _003_multi_oblio_mappings),
    ("004_invoice_send_error", _004_invoice_send_error),
    ("005_split_mappings", _005_split_mappings),
    ("006_trendyol_invoice_link", _006_trendyol_invoice_link),
    ("007_order_invoice_link", _007_order_invoice_link),
    ("008_perf", _008_perf),
    ("009_orders_per_package", _009_orders_per_package),
]


def run(conn: sqlite3.Connection) -> list[str]:
    """Apply any migrations not yet recorded as run. Returns the names that ran this call."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    applied = {r["name"] for r in conn.execute("SELECT name FROM _schema_migrations").fetchall()}
    ran: list[str] = []
    for name, fn in MIGRATIONS:
        if name in applied:
            continue
        fn(conn)
        conn.execute("INSERT INTO _schema_migrations(name) VALUES(?)", (name,))
        ran.append(name)
    return ran
