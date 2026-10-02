import json
from datetime import datetime, timezone
from typing import Any, Iterable

from src.config import (
    DEFAULT_INVOICE_CURRENCY,
    DEFAULT_INVOICE_LANGUAGE,
    DEFAULT_INVOICE_SERIES,
    DEFAULT_POLL_SECONDS,
    DEFAULT_VAT_PERCENT,
)
from src.domain.order_status import InvoiceState
from src.storage.db import get_conn, tx
from src.storage import credentials


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SETUP_KEY = "setup_complete"


class Repo:
    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = get_conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with tx() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def is_setup_complete(self) -> bool:
        return self.get_setting(SETUP_KEY) == "1"

    def mark_setup_complete(self) -> None:
        self.set_setting(SETUP_KEY, "1")

    def reset_setup(self) -> None:
        self.set_setting(SETUP_KEY, "0")

    # ---------- configured values ----------
    def poll_seconds(self) -> int:
        return int(self.get_setting("poll_seconds", str(DEFAULT_POLL_SECONDS)))

    def default_vat(self) -> float:
        return float(self.get_setting("default_vat", str(DEFAULT_VAT_PERCENT)))

    def invoice_series(self) -> str:
        return self.get_setting("invoice_series", DEFAULT_INVOICE_SERIES)

    def invoice_language(self) -> str:
        return self.get_setting("invoice_language", DEFAULT_INVOICE_LANGUAGE)

    def invoice_currency(self) -> str:
        return self.get_setting("invoice_currency", DEFAULT_INVOICE_CURRENCY)

    def trendyol_base_url(self) -> str | None:
        return self.get_setting("trendyol_base_url")

    def trendyol_seller_id(self) -> str | None:
        return self.get_setting("trendyol_seller_id")

    def trendyol_integration_ref(self) -> str | None:
        return self.get_setting("trendyol_integration_ref")

    def oblio_email(self) -> str | None:
        return self.get_setting("oblio_email")

    def oblio_cif(self) -> str | None:
        return self.get_setting("oblio_cif")

    def oblio_workstation(self) -> str:
        return self.get_setting("oblio_workstation", "Sediu")

    def oblio_management(self) -> str | None:
        return self.get_setting("oblio_management")

    def oblio_dry_run(self) -> bool:
        return self.get_setting("oblio_dry_run", "0") == "1"

    def invoice_all_statuses(self) -> bool:
        """Settings → "Facturează comenzile cu orice status" (off by default)."""
        return self.get_setting("invoice_all_statuses", "0") == "1"

    # ---------- secrets ----------
    def set_trendyol_api_key(self, value: str) -> None: credentials.set_secret("trendyol_api_key", value)
    def set_trendyol_api_secret(self, value: str) -> None: credentials.set_secret("trendyol_api_secret", value)
    def set_oblio_secret(self, value: str) -> None: credentials.set_secret("oblio_secret", value)

    def trendyol_api_key(self) -> str | None: return credentials.get_secret("trendyol_api_key")
    def trendyol_api_secret(self) -> str | None: return credentials.get_secret("trendyol_api_secret")
    def oblio_secret(self) -> str | None: return credentials.get_secret("oblio_secret")

    # ---------- oblio products ----------
    def upsert_oblio_products(self, products: Iterable[dict[str, Any]]) -> int:
        """
        Upsert Oblio products by the stable Oblio-side id.
        Existing local rows keep their auto-increment PK so mappings survive re-syncs.

        Self-healing: when a fresh API row has no match by oblio_id, we fall back to
        matching by `code` then `name` against any pre-migration row that still has a
        NULL oblio_id, and link them. After processing the batch, we sweep for any
        remaining duplicate pairs (a NULL-oblio_id legacy row + a with-oblio_id row
        for the same code/name) and merge them — moving mappings from the legacy row
        to the canonical row, then deleting the legacy row. This collapses the duplicates
        introduced by the original migration without losing user mappings.
        """
        now = _now()
        written = 0
        with tx() as conn:
            for p in products:
                oblio_id = str(p.get("id")) if p.get("id") else None
                code = (p.get("code") or "").strip() or None
                name = p.get("name") or p.get("productName") or "(untitled)"
                payload = (
                    oblio_id,
                    code,
                    name,
                    p.get("measuringUnit") or p.get("um"),
                    _to_float(p.get("vatPercentage") or p.get("vat")),
                    p.get("currency"),
                    _to_float(p.get("price")),
                    json.dumps(p, ensure_ascii=False),
                    now,
                )

                existing = None
                if oblio_id:
                    existing = conn.execute(
                        "SELECT id FROM oblio_products WHERE oblio_id = ? LIMIT 1",
                        (oblio_id,),
                    ).fetchone()
                # Legacy fallback: match a pre-migration row (NULL oblio_id) by code or name
                # so we link it instead of creating a duplicate. Preserves mappings.
                if not existing and oblio_id and code:
                    existing = conn.execute(
                        "SELECT id FROM oblio_products WHERE oblio_id IS NULL AND code = ? LIMIT 1",
                        (code,),
                    ).fetchone()
                if not existing and oblio_id and name:
                    existing = conn.execute(
                        "SELECT id FROM oblio_products WHERE oblio_id IS NULL AND name = ? LIMIT 1",
                        (name,),
                    ).fetchone()

                if existing:
                    conn.execute(
                        "UPDATE oblio_products SET oblio_id=?, code=?, name=?, measuring_unit=?, "
                        "vat_percent=?, currency=?, price=?, raw_json=?, synced_at=? WHERE id=?",
                        payload + (existing["id"],),
                    )
                else:
                    conn.execute(
                        "INSERT INTO oblio_products(oblio_id, code, name, measuring_unit, vat_percent, currency, price, raw_json, synced_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        payload,
                    )
                written += 1

        return written

    def list_oblio_products(self, search: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM oblio_products"
        params: list[Any] = []
        if search:
            q += " WHERE name LIKE ? OR code LIKE ?"
            like = f"%{search}%"
            params.extend([like, like])
        q += " ORDER BY name COLLATE NOCASE"
        return [dict(r) for r in get_conn().execute(q, params).fetchall()]

    def get_oblio_product(self, product_id: int) -> dict[str, Any] | None:
        row = get_conn().execute("SELECT * FROM oblio_products WHERE id = ?", (product_id,)).fetchone()
        return dict(row) if row else None

    def get_oblio_product_by_code(self, code: str) -> dict[str, Any] | None:
        if not code:
            return None
        row = get_conn().execute(
            "SELECT * FROM oblio_products WHERE code = ? LIMIT 1", (code,)
        ).fetchone()
        return dict(row) if row else None

    def get_oblio_product_by_name(self, name: str) -> dict[str, Any] | None:
        if not name:
            return None
        row = get_conn().execute(
            "SELECT * FROM oblio_products WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        return dict(row) if row else None

    # ---------- trendyol listings ----------
    def upsert_trendyol_listings(self, listings: Iterable[dict[str, Any]]) -> int:
        """Upsert listings by stable key (product_main_id first, else barcode).
        Existing rows keep their id so mappings survive re-syncs."""
        now = _now()
        written = 0
        with tx() as conn:
            for l in listings:
                product_main_id = l.get("productMainId") or None
                barcode = l.get("barcode") or None
                stock_code = l.get("stockCode") or None

                existing = None
                if product_main_id:
                    existing = conn.execute(
                        "SELECT id FROM trendyol_listings WHERE product_main_id = ? LIMIT 1",
                        (str(product_main_id),),
                    ).fetchone()
                if not existing and barcode:
                    existing = conn.execute(
                        "SELECT id FROM trendyol_listings WHERE barcode = ? LIMIT 1",
                        (str(barcode),),
                    ).fetchone()

                payload = (
                    str(barcode) if barcode else None,
                    str(stock_code) if stock_code else None,
                    str(product_main_id) if product_main_id else None,
                    l.get("title") or l.get("productName") or "(untitled)",
                    l.get("brand"),
                    _to_float(l.get("listPrice")),
                    _to_float(l.get("salePrice")),
                    l.get("currencyType") or l.get("currency"),
                    json.dumps(l, ensure_ascii=False),
                    now,
                )
                if existing:
                    conn.execute(
                        "UPDATE trendyol_listings SET barcode=?, stock_code=?, product_main_id=?, "
                        "title=?, brand=?, list_price=?, sale_price=?, currency=?, raw_json=?, synced_at=? "
                        "WHERE id=?",
                        payload + (existing["id"],),
                    )
                else:
                    conn.execute(
                        "INSERT INTO trendyol_listings(barcode, stock_code, product_main_id, title, brand, list_price, sale_price, currency, raw_json, synced_at) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        payload,
                    )
                written += 1
        return written

    def list_trendyol_listings(self, search: str | None = None, unmapped_only: bool = False,
                                exclude_for_oblio_id: int | None = None) -> list[dict[str, Any]]:
        """
        `unmapped_only=True`: listing has NO mappings (neither variant nor bundle).
        `exclude_for_oblio_id=N`: listing has no variant mapping to Oblio N (used by
        the Variants pair dialog).
        """
        q = "SELECT t.* FROM trendyol_listings t"
        params: list[Any] = []
        conds: list[str] = []
        if unmapped_only:
            q += (
                " LEFT JOIN variant_mappings vm ON vm.trendyol_listing_id = t.id"
                " LEFT JOIN bundle_mappings  bm ON bm.trendyol_listing_id = t.id"
            )
            conds.append("vm.id IS NULL AND bm.id IS NULL")
        elif exclude_for_oblio_id is not None:
            q += (
                " LEFT JOIN variant_mappings vm "
                "        ON vm.trendyol_listing_id = t.id AND vm.oblio_product_id = ?"
            )
            params.append(exclude_for_oblio_id)
            conds.append("vm.id IS NULL")
        if search:
            conds.append("(t.title LIKE ? OR t.barcode LIKE ? OR t.stock_code LIKE ? OR t.product_main_id LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY t.title COLLATE NOCASE"
        return [dict(r) for r in get_conn().execute(q, params).fetchall()]

    # ---------- variant mappings (Oblio → many Trendyol) ----------
    def get_variants_for_oblio(self, oblio_product_id: int) -> list[dict[str, Any]]:
        rows = get_conn().execute(
            "SELECT vm.*, t.title, t.barcode, t.stock_code, t.product_main_id "
            "FROM variant_mappings vm JOIN trendyol_listings t ON t.id = vm.trendyol_listing_id "
            "WHERE vm.oblio_product_id = ? ORDER BY t.title COLLATE NOCASE",
            (oblio_product_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _bump_mappings_rev(conn) -> None:
        """Increment the mappings revision counter (a settings row). The invoice
        draft-refresh includes this in each draft's build_sig, so bumping it here
        marks every unsent draft stale exactly once per mapping change."""
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('mappings_rev', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
        )

    def mappings_rev(self) -> str:
        return self.get_setting("mappings_rev", "0")

    def add_variant_mappings(self, oblio_product_id: int, trendyol_listing_ids: Iterable[int],
                              units_per_listing: int = 1) -> int:
        """One Oblio + many Trendyol listings.
        A listing can be a variant of at most one Oblio (UNIQUE constraint), so adding
        the same listing under a different Oblio MOVES it. Also clears any bundle
        rows for the listing — a listing is variant OR bundle, never both."""
        now = _now()
        added = 0
        units = max(1, int(units_per_listing or 1))
        with tx() as conn:
            for lid in trendyol_listing_ids:
                conn.execute("DELETE FROM bundle_mappings WHERE trendyol_listing_id = ?", (lid,))
                try:
                    conn.execute(
                        "INSERT INTO variant_mappings(oblio_product_id, trendyol_listing_id, units_per_listing, created_at) "
                        "VALUES(?, ?, ?, ?)",
                        (oblio_product_id, lid, units, now),
                    )
                except Exception:
                    # listing already a variant — move/refresh.
                    conn.execute(
                        "UPDATE variant_mappings SET oblio_product_id=?, units_per_listing=? "
                        "WHERE trendyol_listing_id=?",
                        (oblio_product_id, units, lid),
                    )
                added += 1
            self._bump_mappings_rev(conn)
        return added

    def remove_variant_mapping(self, mapping_id: int) -> None:
        with tx() as conn:
            conn.execute("DELETE FROM variant_mappings WHERE id = ?", (mapping_id,))
            self._bump_mappings_rev(conn)

    def update_variant_units(self, mapping_id: int, units_per_listing: int) -> None:
        units = max(1, int(units_per_listing or 1))
        with tx() as conn:
            conn.execute("UPDATE variant_mappings SET units_per_listing = ? WHERE id = ?",
                         (units, mapping_id))
            self._bump_mappings_rev(conn)

    def count_variants_per_oblio(self) -> dict[int, int]:
        rows = get_conn().execute(
            "SELECT oblio_product_id, COUNT(*) AS cnt FROM variant_mappings GROUP BY oblio_product_id"
        ).fetchall()
        return {r["oblio_product_id"]: r["cnt"] for r in rows}

    # ---------- bundle mappings (Trendyol → many Oblio) ----------
    def get_bundle_components(self, trendyol_listing_id: int) -> list[dict[str, Any]]:
        rows = get_conn().execute(
            "SELECT bm.*, op.code AS oblio_code, op.name AS oblio_name, op.price AS oblio_price "
            "FROM bundle_mappings bm JOIN oblio_products op ON op.id = bm.oblio_product_id "
            "WHERE bm.trendyol_listing_id = ? ORDER BY op.name COLLATE NOCASE",
            (trendyol_listing_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_bundle_components(self, trendyol_listing_id: int, oblio_product_ids: Iterable[int],
                               units_per_listing: int = 1) -> int:
        """One Trendyol listing + many Oblio components.
        Also clears any variant row for the listing — variant OR bundle, never both."""
        now = _now()
        added = 0
        units = max(1, int(units_per_listing or 1))
        with tx() as conn:
            conn.execute("DELETE FROM variant_mappings WHERE trendyol_listing_id = ?",
                         (trendyol_listing_id,))
            for oid in oblio_product_ids:
                try:
                    conn.execute(
                        "INSERT INTO bundle_mappings(trendyol_listing_id, oblio_product_id, units_per_listing, created_at) "
                        "VALUES(?, ?, ?, ?)",
                        (trendyol_listing_id, oid, units, now),
                    )
                except Exception:
                    conn.execute(
                        "UPDATE bundle_mappings SET units_per_listing=? "
                        "WHERE trendyol_listing_id=? AND oblio_product_id=?",
                        (units, trendyol_listing_id, oid),
                    )
                added += 1
            self._bump_mappings_rev(conn)
        return added

    def remove_bundle_component(self, mapping_id: int) -> None:
        with tx() as conn:
            conn.execute("DELETE FROM bundle_mappings WHERE id = ?", (mapping_id,))
            self._bump_mappings_rev(conn)

    def update_bundle_units(self, mapping_id: int, units_per_listing: int) -> None:
        units = max(1, int(units_per_listing or 1))
        with tx() as conn:
            conn.execute("UPDATE bundle_mappings SET units_per_listing = ? WHERE id = ?",
                         (units, mapping_id))
            self._bump_mappings_rev(conn)

    def count_components_per_listing(self) -> dict[int, int]:
        rows = get_conn().execute(
            "SELECT trendyol_listing_id, COUNT(*) AS cnt FROM bundle_mappings GROUP BY trendyol_listing_id"
        ).fetchall()
        return {r["trendyol_listing_id"]: r["cnt"] for r in rows}

    # ---------- combined helpers ----------
    def list_all_mappings_with_oblio(self) -> list[dict[str, Any]]:
        """Combined view (variants + bundles) used by the Trendyol products view to
        show what an unmapped/mapped listing links to. Each row has `mapping_type`
        of 'variant' or 'bundle'."""
        rows = get_conn().execute(
            "SELECT 'variant' AS mapping_type, vm.id AS mapping_id, vm.trendyol_listing_id, "
            "       vm.units_per_listing, op.id AS oblio_product_id, "
            "       op.code AS oblio_code, op.name AS oblio_name "
            "FROM variant_mappings vm JOIN oblio_products op ON op.id = vm.oblio_product_id "
            "UNION ALL "
            "SELECT 'bundle' AS mapping_type, bm.id AS mapping_id, bm.trendyol_listing_id, "
            "       bm.units_per_listing, op.id AS oblio_product_id, "
            "       op.code AS oblio_code, op.name AS oblio_name "
            "FROM bundle_mappings bm JOIN oblio_products op ON op.id = bm.oblio_product_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_all_mappings(self) -> int:
        """Wipe every mapping (variants AND bundles). Returns total removed."""
        with tx() as conn:
            n_v = conn.execute("SELECT COUNT(*) FROM variant_mappings").fetchone()[0]
            n_b = conn.execute("SELECT COUNT(*) FROM bundle_mappings").fetchone()[0]
            conn.execute("DELETE FROM variant_mappings")
            conn.execute("DELETE FROM bundle_mappings")
            self._bump_mappings_rev(conn)
            return n_v + n_b

    # Placeholder values Trendyol fills in when a seller didn't provide a real SKU — these
    # are shared across many unrelated listings and must NOT be used for matching.
    _GARBAGE_KEYS = {"", "merchantsku", "stockcode", "productcode", "null", "none"}

    def _is_usable_key(self, value: str | None) -> bool:
        if value is None:
            return False
        v = str(value).strip().lower()
        return bool(v) and v not in self._GARBAGE_KEYS

    def resolve_line_mappings(self, barcode: str | None, stock_code: str | None,
                                product_main_id: str | None) -> list[dict[str, Any]]:
        """
        Find the Oblio mapping(s) for a single order line.
        Bundle wins over variant (a listing should be in only one of the two,
        but if both somehow exist, bundle is the more specific case).

        Returns a list. For variants it's always length 1; for bundles it's the
        number of components.
        """
        conn = get_conn()

        # Collect EVERY listing row matching any identifier, strongest key first.
        # Historic syncs can leave duplicate rows for the same physical product
        # (e.g. two rows sharing a barcode); with the old LIMIT-1 lookup, a
        # mapping edited on one twin was invisible when order lines resolved to
        # the other — the classic "I remapped it but it still uses the old
        # product" bug. Now the first candidate that actually HAS a mapping wins,
        # in deterministic id order.
        candidate_ids: list[int] = []
        for column, value in (("product_main_id", product_main_id),
                              ("barcode", barcode),
                              ("stock_code", stock_code)):
            if not self._is_usable_key(value):
                continue
            for row in conn.execute(
                f"SELECT id FROM trendyol_listings WHERE {column} = ? ORDER BY id",
                (str(value),),
            ).fetchall():
                if row["id"] not in candidate_ids:
                    candidate_ids.append(row["id"])
        if not candidate_ids:
            return []

        for listing_id in candidate_ids:
            # Bundle first (deterministic order so the last-row rounding remainder is stable).
            rows = conn.execute(
                "SELECT op.*, bm.vat_percent AS override_vat, bm.units_per_listing AS units_per_listing "
                "FROM bundle_mappings bm JOIN oblio_products op ON op.id = bm.oblio_product_id "
                "WHERE bm.trendyol_listing_id = ? ORDER BY bm.id",
                (listing_id,),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            rows = conn.execute(
                "SELECT op.*, vm.vat_percent AS override_vat, vm.units_per_listing AS units_per_listing "
                "FROM variant_mappings vm JOIN oblio_products op ON op.id = vm.oblio_product_id "
                "WHERE vm.trendyol_listing_id = ? LIMIT 1",
                (listing_id,),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        return []

    # Back-compat shim — older callers expect a single dict or None.
    def resolve_line_mapping(self, barcode: str | None, stock_code: str | None,
                              product_main_id: str | None) -> dict[str, Any] | None:
        ms = self.resolve_line_mappings(barcode, stock_code, product_main_id)
        return ms[0] if ms else None

    # ---------- orders ----------
    def upsert_order(self, order: dict[str, Any]) -> int:
        """
        Insert or update an order row — keyed by SHIPMENT PACKAGE id, since a
        Trendyol order can be split into several packages that each carry their
        own status/lines and get invoiced separately.

        The DO UPDATE is guarded by a WHERE that skips the write when the
        payload is byte-identical — so `fetched_at` means "when the package's
        content last CHANGED", not "when we last polled". The invoice
        draft-refresh uses that timestamp to skip untouched orders. (raw_json
        alone is a sufficient change proxy: every other stored column is
        derived from it.)
        """
        now = _now()
        with tx() as conn:
            cur = conn.execute(
                "INSERT INTO orders(package_id, order_number, status, buyer_name, buyer_email, "
                "  ship_address, ship_city, ship_country, currency, total_amount, "
                "  order_date, raw_json, fetched_at, "
                "  tr_invoice_link, tr_invoice_link_date_ms, tr_invoice_number) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(package_id) DO UPDATE SET "
                "    status = excluded.status, "
                "    raw_json = excluded.raw_json, "
                "    fetched_at = excluded.fetched_at, "
                "    tr_invoice_link = excluded.tr_invoice_link, "
                "    tr_invoice_link_date_ms = excluded.tr_invoice_link_date_ms, "
                "    tr_invoice_number = excluded.tr_invoice_number "
                "WHERE orders.raw_json IS NOT excluded.raw_json "
                "RETURNING id",
                (
                    order["package_id"],
                    order["order_number"],
                    order["status"],
                    order.get("buyer_name"),
                    order.get("buyer_email"),
                    order.get("ship_address"),
                    order.get("ship_city"),
                    order.get("ship_country"),
                    order.get("currency"),
                    _to_float(order.get("total_amount")),
                    order.get("order_date"),
                    json.dumps(order.get("raw", {}), ensure_ascii=False),
                    now,
                    order.get("tr_invoice_link"),
                    int(order["tr_invoice_link_date_ms"]) if order.get("tr_invoice_link_date_ms") else None,
                    order.get("tr_invoice_number"),
                ),
            )
            row = cur.fetchone()
            if row is not None:
                return row[0]
            # Conflict + unchanged payload → the guarded UPDATE was skipped and
            # RETURNING produced no row; the package already exists untouched.
            existing = conn.execute(
                "SELECT id FROM orders WHERE package_id = ?", (order["package_id"],)
            ).fetchone()
            return existing[0]

    def list_orders(self, state: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM orders"
        params: list[Any] = []
        if state:
            q += " WHERE invoice_state = ?"
            params.append(state)
        q += " ORDER BY order_date DESC, id DESC"
        return [dict(r) for r in get_conn().execute(q, params).fetchall()]

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        row = get_conn().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

    def set_order_state(self, order_id: int, state: str) -> None:
        with tx() as conn:
            conn.execute("UPDATE orders SET invoice_state = ? WHERE id = ?", (state, order_id))

    # ---------- invoices ----------
    def record_invoice(self, order_id: int, series: str, number: str, total: float | None,
                       currency: str | None, oblio_link: str | None, einvoice_link: str | None,
                       raw_response: dict[str, Any], build_sig: str | None = None) -> int:
        now = _now()
        with tx() as conn:
            cur = conn.execute(
                "INSERT INTO invoices(order_id, series, number, oblio_link, oblio_einvoice_link, total, currency, issued_at, raw_response, build_sig) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (order_id, series, number, oblio_link, einvoice_link, total, currency, now,
                 json.dumps(raw_response, ensure_ascii=False), build_sig),
            )
            invoice_id = cur.fetchone()[0]
            conn.execute(
                "UPDATE orders SET invoice_state = ? WHERE id = ?",
                (InvoiceState.INVOICED.value, order_id),
            )
            return invoice_id

    def refresh_local_draft(
        self,
        invoice_id: int,
        series: str,
        total: float | None,
        currency: str | None,
        raw_response: dict[str, Any],
        build_sig: str | None = None,
    ) -> bool:
        """
        Overwrite the series/total/currency/raw_response of a LOCAL draft (no
        oblio_link). No-op if the row has already been sent to Oblio — the WHERE
        clause guards against a race where a send lands between the caller's check
        and the update. Returns True if the row was updated.
        """
        with tx() as conn:
            cur = conn.execute(
                "UPDATE invoices SET series = ?, total = ?, currency = ?, raw_response = ?, "
                "build_sig = ? "
                "WHERE id = ? AND (oblio_link IS NULL OR oblio_link = '')",
                (series, total, currency, json.dumps(raw_response, ensure_ascii=False),
                 build_sig, invoice_id),
            )
            return cur.rowcount > 0

    def patch_draft_buyer(self, invoice_id: int, buyer: dict[str, Any]) -> bool:
        """
        Overwrite ONLY the `buyer` block inside a LOCAL draft's raw_response —
        used when the full rebuild is impossible (order no longer fully mapped)
        but the buyer address (județ!) can still be corrected. Lines, totals and
        build_sig are deliberately left untouched so a later mapping fix still
        triggers the full refresh_local_drafts rebuild.
        No-op (returns False) if the draft was already sent to Oblio, its record
        is unparseable, or the buyer block already matches.
        """
        with tx() as conn:
            row = conn.execute(
                "SELECT raw_response FROM invoices "
                "WHERE id = ? AND (oblio_link IS NULL OR oblio_link = '')",
                (invoice_id,),
            ).fetchone()
            if not row:
                return False
            try:
                record = json.loads(row["raw_response"] or "{}")
            except ValueError:
                return False
            if not isinstance(record, dict):
                return False
            if record.get("buyer") == buyer:
                return False  # already up to date — don't rewrite every tick
            record["buyer"] = buyer
            cur = conn.execute(
                "UPDATE invoices SET raw_response = ? "
                "WHERE id = ? AND (oblio_link IS NULL OR oblio_link = '')",
                (json.dumps(record, ensure_ascii=False), invoice_id),
            )
            return cur.rowcount > 0

    def list_stale_drafts(self, builder_rev: str, mappings_rev: str) -> list[dict[str, Any]]:
        """
        Unsent drafts whose build_sig no longer matches the current inputs
        (builder revision, mappings revision, or the order's content timestamp).
        The comparison runs entirely in SQL so unchanged drafts cost nothing —
        no JSON parsing, no preview rebuild.
        """
        q = (
            "SELECT i.id, i.series, i.number, i.order_id, o.fetched_at AS order_fetched_at "
            "FROM invoices i JOIN orders o ON o.id = i.order_id "
            "WHERE (i.oblio_link IS NULL OR i.oblio_link = '') "
            "  AND (i.build_sig IS NULL "
            "       OR i.build_sig != (? || ':' || ? || ':' || o.fetched_at))"
        )
        return [dict(r) for r in get_conn().execute(q, (builder_rev, mappings_rev)).fetchall()]

    def next_local_invoice_seq(self, date_part: str) -> int:
        """Next sequence number for LOC-<date>- local invoice numbers. MAX-based so
        deleting drafts can never reissue a previously-used number the same day."""
        prefix = f"LOC-{date_part}-"
        row = get_conn().execute(
            "SELECT MAX(CAST(substr(number, ?) AS INTEGER)) FROM invoices WHERE number LIKE ?",
            (len(prefix) + 1, prefix + "%"),
        ).fetchone()
        return int(row[0] or 0) + 1

    def update_invoice_oblio_response(
        self,
        invoice_id: int,
        oblio_link: str | None,
        einvoice_link: str | None,
        series: str | None,
        number: str | None,
        total: float | None,
        currency: str | None,
        raw_response: dict[str, Any],
    ) -> None:
        """After a successful Oblio POST: stamp the Oblio link/number on the local
        invoice row, overwrite raw_response with Oblio's payload, and clear any
        previous error so the row is now an Oblio-sent invoice.

        Also resets the Trendyol-notification state: the freshly-issued Oblio link
        is by definition not yet on Trendyol. On a first send that's a no-op (both
        columns already NULL); on a RE-send it un-sticks the idempotency latch so
        the new link gets pushed to Trendyol."""
        with tx() as conn:
            conn.execute(
                "UPDATE invoices SET oblio_link = ?, oblio_einvoice_link = ?, "
                "series = COALESCE(?, series), number = COALESCE(?, number), "
                "total = COALESCE(?, total), currency = COALESCE(?, currency), "
                "raw_response = ?, last_send_error = NULL, "
                "trendyol_link_sent_at = NULL, trendyol_link_error = NULL WHERE id = ?",
                (
                    oblio_link, einvoice_link, series, number, total, currency,
                    json.dumps(raw_response, ensure_ascii=False), invoice_id,
                ),
            )

    def mark_invoice_trendyol_link_sent(self, invoice_id: int) -> None:
        """Stamp the row as 'invoice link posted to Trendyol' and clear any
        previous Trendyol error."""
        with tx() as conn:
            conn.execute(
                "UPDATE invoices SET trendyol_link_sent_at = ?, trendyol_link_error = NULL "
                "WHERE id = ?",
                (_now(), invoice_id),
            )

    def record_invoice_trendyol_link_error(self, invoice_id: int, error: str) -> None:
        with tx() as conn:
            conn.execute(
                "UPDATE invoices SET trendyol_link_error = ? WHERE id = ?",
                (error[:1000], invoice_id),
            )

    def record_invoice_send_error(self, invoice_id: int, error: str) -> None:
        with tx() as conn:
            conn.execute(
                "UPDATE invoices SET last_send_error = ? WHERE id = ?",
                (error[:1000], invoice_id),  # cap to keep DB row small
            )

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        row = get_conn().execute(
            "SELECT i.*, o.order_number FROM invoices i JOIN orders o ON o.id = i.order_id "
            "WHERE i.id = ?", (invoice_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_invoices(self) -> list[dict[str, Any]]:
        # Pull order_date + status + tr_invoice_link alongside; the Invoices tab
        # needs them for display + sorting + the externally-invoiced filter.
        q = ("SELECT i.*, o.order_number, o.buyer_name, o.order_date, "
             "       o.status AS order_status, o.tr_invoice_link, o.tr_invoice_number "
             "FROM invoices i JOIN orders o ON o.id = i.order_id "
             "ORDER BY COALESCE(o.order_date, i.issued_at) DESC")
        return [dict(r) for r in get_conn().execute(q).fetchall()]

    def cleanup_externally_invoiced_drafts(self) -> int:
        """For every order that now has a Trendyol-side invoiceLink set, delete any
        LOCAL invoice we'd drafted (oblio_link IS NULL — we never POSTed our copy).
        Drafts where we already sent to Oblio are left alone (a real fiscal invoice
        exists on Oblio's side, the user must reconcile manually).
        Returns the number of drafts dropped."""
        with tx() as conn:
            rows = conn.execute(
                "SELECT i.id FROM invoices i JOIN orders o ON o.id = i.order_id "
                "WHERE i.oblio_link IS NULL "
                "  AND o.tr_invoice_link IS NOT NULL AND o.tr_invoice_link <> ''"
            ).fetchall()
            if not rows:
                return 0
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            order_ids = [r["order_id"] for r in conn.execute(
                f"SELECT order_id FROM invoices WHERE id IN ({placeholders})", ids
            ).fetchall()]
            conn.execute(f"DELETE FROM invoices WHERE id IN ({placeholders})", ids)
            if order_ids:
                o_placeholders = ",".join("?" * len(order_ids))
                conn.execute(
                    f"UPDATE orders SET invoice_state = 'pending' WHERE id IN ({o_placeholders})",
                    order_ids,
                )
            return len(ids)

    def invalidate_local_invoices(self) -> int:
        """Delete every LOCAL invoice (oblio_link IS NULL) and reset the related order's
        invoice_state back to PENDING so the auto-invoice loop can rebuild them with
        the latest mappings. Real Oblio-issued invoices (oblio_link IS NOT NULL) stay.
        Returns how many local invoices were dropped."""
        with tx() as conn:
            order_ids = [r["order_id"] for r in conn.execute(
                "SELECT order_id FROM invoices WHERE oblio_link IS NULL"
            ).fetchall()]
            if not order_ids:
                return 0
            conn.execute("DELETE FROM invoices WHERE oblio_link IS NULL")
            placeholders = ",".join("?" * len(order_ids))
            conn.execute(
                f"UPDATE orders SET invoice_state = ? WHERE id IN ({placeholders})",
                [InvoiceState.PENDING.value, *order_ids],
            )
            return len(order_ids)

    def find_oblio_product_for_listing(self, barcode: str | None, stock_code: str | None,
                                        product_main_id: str | None) -> dict[str, Any] | None:
        """Convenience: return ONE Oblio product for the given identifiers (variant only)."""
        q = ("SELECT op.* FROM oblio_products op "
             "JOIN trendyol_listings t ON (t.barcode = ? OR t.stock_code = ? OR t.product_main_id = ?) "
             "JOIN variant_mappings vm ON vm.trendyol_listing_id = t.id AND vm.oblio_product_id = op.id "
             "LIMIT 1")
        row = get_conn().execute(q, (barcode, stock_code, product_main_id)).fetchone()
        return dict(row) if row else None

    def invalidate_local_invoices_for_listing(self, trendyol_listing_id: int) -> int:
        """
        Targeted variant of invalidate_local_invoices(): only invoices generated from
        orders that include the given Trendyol listing in their line items. Much
        cheaper than nuking everything when a single mapping changes.

        We match on the listing's `barcode` / `stock_code` / `product_main_id` against
        the order's raw JSON (LIKE search). False positives are possible but harmless —
        they get correctly rebuilt by auto_create_local_invoices().
        """
        listing = self.get_trendyol_listing(trendyol_listing_id)
        if not listing:
            return 0
        # json.dumps() emits "key": "value" with a space after the colon, but the
        # raw Trendyol payload stored unchanged uses "key":"value". Match both forms
        # so we don't miss either source.
        def _both(key: str, value: str, quoted: bool = True) -> list[str]:
            v = f'"{value}"' if quoted else value
            return [f'%"{key}":{v}%', f'%"{key}": {v}%']

        needles: list[str] = []
        if listing.get("barcode"):
            needles.extend(_both("sku", listing["barcode"]))
            needles.extend(_both("barcode", listing["barcode"]))
        if listing.get("stock_code"):
            needles.extend(_both("stockCode", listing["stock_code"]))
        if listing.get("product_main_id"):
            pmi = listing["product_main_id"]
            needles.extend(_both("productCode", pmi))            # quoted
            needles.extend(_both("productCode", pmi, quoted=False))  # bare numeric
        if not needles:
            return 0
        where = " OR ".join(["o.raw_json LIKE ?"] * len(needles))
        with tx() as conn:
            order_ids = [r["id"] for r in conn.execute(
                f"SELECT DISTINCT o.id FROM orders o JOIN invoices i ON i.order_id = o.id "
                f"WHERE i.oblio_link IS NULL AND ({where})",
                needles,
            ).fetchall()]
            if not order_ids:
                return 0
            placeholders = ",".join("?" * len(order_ids))
            conn.execute(
                f"DELETE FROM invoices WHERE oblio_link IS NULL AND order_id IN ({placeholders})",
                order_ids,
            )
            conn.execute(
                f"UPDATE orders SET invoice_state = ? WHERE id IN ({placeholders})",
                [InvoiceState.PENDING.value, *order_ids],
            )
            return len(order_ids)

    def get_trendyol_listing(self, listing_id: int) -> dict[str, Any] | None:
        row = get_conn().execute(
            "SELECT * FROM trendyol_listings WHERE id = ?", (listing_id,)
        ).fetchone()
        return dict(row) if row else None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
