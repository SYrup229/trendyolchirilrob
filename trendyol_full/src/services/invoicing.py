import json
import traceback
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.api.oblio import OblioClient
from src.domain.order_status import INVOICEABLE_TRENDYOL_STATUSES, InvoiceState
from src.logging_setup import get_app_logger
from src.services.sync import build_oblio_client
from src.storage.repo import Repo


# Backwards-compat re-export — older callers and tests imported this constant.
INVOICEABLE_STATUSES = INVOICEABLE_TRENDYOL_STATUSES

# Bump when invoice-BUILDING logic changes (VAT maps, series maps, price split,
# payload shape…). Any unsent draft whose build_sig carries an older rev gets
# rebuilt on the next poller tick; matching drafts are skipped entirely.
# rev 2: buyer county (județ) added to the invoice address.
# rev 3: billing address (invoiceAddress) preferred over shipment; Bucharest
#        rule (județ=București, localitate=Sector N).
# rev 4: Bucharest orders without a sector get a stable pseudo-random Sector 1-6.
# rev 5: mapping resolution handles duplicate listing rows (twin-aware); rebuild
#        all drafts once so lines reflect the twin-aware resolution.
BUILDER_REV = "5"


def _build_sig(repo: Repo, order_fetched_at: str | None) -> str:
    return f"{BUILDER_REV}:{repo.mappings_rev()}:{order_fetched_at or ''}"


# Destination-country → invoice VAT rate. Applied to lines whose Oblio product
# has no VAT stored (bundles, or products imported before Oblio's cotaTVA was
# set). Each rate here MUST also exist as a configured "cota TVA" in the user's
# Oblio account, otherwise the Oblio API rejects the invoice.
# RO standard VAT is 21% since Aug 2025 (the account's 19% rate is named "Veche").
COUNTRY_VAT: dict[str, float] = {
    "RO": 21.0,
    "BG": 20.0,
    "GR": 24.0,
    "HU": 27.0,
    # NOTE: no 25% cota exists in the Oblio account yet — HR sends will raise the
    # "Cota TVA 25% nu este configurată" error until the user adds one in Oblio.
    "HR": 25.0,
}


def _vat_for_country(country: str | None, fallback: float) -> float:
    if not country:
        return fallback
    return COUNTRY_VAT.get(country.strip().upper(), fallback)


# Destination-country → Oblio invoice series. Every name here must exist as a
# Factura-type series in the Oblio account (Setări → Serii documente).
# Unknown countries fall back to the Settings "Invoice series" value.
COUNTRY_SERIES: dict[str, str] = {
    "RO": "ECH",
    "BG": "BG",
    "GR": "GR",
    "HU": "UNG",
    "HR": "HR",
}


def _series_for_country(repo: Repo, country: str | None) -> str:
    if country:
        mapped = COUNTRY_SERIES.get(country.strip().upper())
        if mapped:
            return mapped
    return repo.invoice_series()


@dataclass
class InvoicePreviewLine:
    listing_title: str
    barcode: str | None
    mapped_oblio_name: str | None
    mapped_oblio_code: str | None
    quantity: int
    list_unit_price_gross: float       # what the listing was priced at before discount
    discount_per_unit: float            # discount applied per unit
    unit_price_gross: float             # what the customer paid per unit (= list - discount)
    vat_percent: float
    line_total_gross: float             # qty * unit_price_gross
    is_mapped: bool
    reason: str | None = None


@dataclass
class InvoicePreview:
    order_id: int
    order_number: str
    order_status: str | None
    buyer_name: str
    buyer_email: str | None
    ship_address: str | None
    ship_city: str | None
    ship_state: str | None      # județ / county / region — required on RO invoices
    ship_country: str | None
    currency: str
    lines: list[InvoicePreviewLine]
    shipping_amount: float              # 0 if Trendyol bundles shipping into line prices
    total_gross: float                  # sum of line totals + shipping; what customer paid
    all_mapped: bool


def _resolve_billing_address(raw: dict[str, Any], order: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    """
    (address, city/localitate, state/județ, country) for the invoice's buyer.

    Prefers the customer's BILLING address (invoiceAddress) over the shipment
    one; falls back per-field to the stored order columns for legacy rows.
    County: RO fills `countyName` ("Dolj"), BG/GR fill `stateName`.
    Bucharest rule: județ = "București" and localitate = "Sector N" — the raw
    data has city="Bucuresti", countyName="București"/"Bucuresti" and a bare
    digit in `sector` (invoiceAddress only).
    """
    addr = raw.get("invoiceAddress") or {}
    if not (addr.get("address1") or addr.get("city")):
        addr = raw.get("shipmentAddress") or {}

    address = " ".join(filter(None, [addr.get("address1"), addr.get("address2")])).strip() \
        or order.get("ship_address")
    city = (addr.get("city") or "").strip() or order.get("ship_city")
    state = (addr.get("countyName") or addr.get("stateName") or "").strip() or None
    country = (addr.get("countryCode") or "").strip() or order.get("ship_country")

    if (country or "").upper() == "RO" and "bucure" in f"{city} {state or ''}".lower():
        state = "București"
        sector = str(addr.get("sector") or "").strip()
        if not sector:
            # Customer didn't fill in a sector. Per the user's rule, assign one
            # 1-6 — deterministically from the order number, so draft rebuilds
            # never reshuffle it (preview, payload and export always agree).
            key = str(order.get("order_number") or order.get("id") or "")
            sector = str(1 + zlib.crc32(key.encode()) % 6)
        city = sector if sector.lower().startswith("sector") else f"Sector {sector}"
    return address, city, state, country


def build_preview(repo: Repo, order_id: int) -> InvoicePreview:
    order = repo.get_order(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    raw = json.loads(order["raw_json"])
    # Pick VAT by destination country (RO/BG/GR), falling back to the Settings
    # "Default VAT" when the ship country isn't in COUNTRY_VAT. Per-product VAT
    # from Oblio still wins on a per-line basis below.
    country_vat = _vat_for_country(order.get("ship_country"), repo.default_vat())

    # Buyer address for the INVOICE: the customer's billing address
    # (invoiceAddress), with the shipment address only as fallback. They differ
    # on real orders, and the Bucharest `sector` exists only on invoiceAddress.
    bill_address, bill_city, bill_state, bill_country = _resolve_billing_address(raw, order)

    lines: list[InvoicePreviewLine] = []
    total_gross = 0.0
    all_mapped = True
    currency = order.get("currency") or raw.get("currencyCode") or repo.invoice_currency()

    for item in _order_lines(raw):
        # Trendyol order lines: `sku` holds the actual GTIN/barcode; `productCode` is the
        # numeric listing ID (== our product_main_id); `stockCode` / `merchantSku` are often
        # the literal placeholder "merchantSku" and must NOT be treated as a real stock code.
        barcode = item.get("sku") or item.get("barcode")
        stock_code = item.get("stockCode") or item.get("merchantSku")
        product_main_id = item.get("productCode") or item.get("contentId") or item.get("productMainId")
        order_qty = max(int(item.get("quantity") or 1), 1)
        title = item.get("productName") or item.get("title") or "(untitled)"

        # Trendyol's pricing fields per line:
        #   price            — per-unit gross (post-discount), when present
        #   amount           — line total gross (post-discount), pre-VAT split
        #   lineGrossAmount  — same as amount in current API responses
        #   discount         — line-level discount amount (total, not per-unit)
        # Trendyol-funded portion (totalTyDiscount / tyDiscount) is NOT subtracted from
        # the customer-facing price; it's a post-hoc reimbursement to the seller.
        line_total_gross_raw = _f(item.get("amount")) or _f(item.get("lineGrossAmount")) or 0.0
        per_unit_price = _f(item.get("price"))
        if per_unit_price is None and order_qty > 0:
            per_unit_price = line_total_gross_raw / order_qty
        per_unit_price = per_unit_price or 0.0

        # Customer-borne discount only — exclude TY-covered amounts.
        line_discount_total = _line_seller_discount(item)
        discount_per_trendyol_unit = (line_discount_total / order_qty) if order_qty > 0 else 0.0

        mappings = repo.resolve_line_mappings(barcode, stock_code, product_main_id)
        is_mapped = bool(mappings)
        if not is_mapped:
            all_mapped = False
            # Unmapped: a placeholder line so the UI can show what's blocking.
            lines.append(InvoicePreviewLine(
                listing_title=title,
                barcode=barcode,
                mapped_oblio_name=None,
                mapped_oblio_code=None,
                quantity=order_qty,
                list_unit_price_gross=round(per_unit_price + discount_per_trendyol_unit, 2),
                discount_per_unit=round(discount_per_trendyol_unit, 2),
                unit_price_gross=round(per_unit_price, 2),
                vat_percent=country_vat,
                line_total_gross=round(per_unit_price * order_qty, 2),
                is_mapped=False,
                reason="No mapping found for this listing.",
            ))
            continue

        # Customer-paid totals for this Trendyol order line.
        customer_paid_total = round(per_unit_price * order_qty, 2)
        customer_discount_total = round(line_discount_total, 2)

        # Weights for splitting across N Oblio components: oblio.price × buc per row.
        # If every weight is 0 (Oblio prices not set), fall back to equal split.
        weights: list[tuple[dict[str, Any], int, float]] = []
        weights_total = 0.0
        for m in mappings:
            buc = max(1, int(m.get("units_per_listing") or 1))
            op_price = float(m.get("price") or 0.0)
            w = op_price * buc
            weights.append((m, buc, w))
            weights_total += w
        if weights_total <= 0:
            weights = [(m, max(1, int(m.get("units_per_listing") or 1)), 1.0) for m in mappings]
            weights_total = float(len(weights))

        distributed_total = 0.0
        distributed_disc = 0.0
        for i, (m, buc, w) in enumerate(weights):
            is_last = (i == len(weights) - 1)
            if is_last:
                line_share = round(customer_paid_total - distributed_total, 2)
                disc_share = round(customer_discount_total - distributed_disc, 2)
            else:
                ratio = w / weights_total
                line_share = round(customer_paid_total * ratio, 2)
                disc_share = round(customer_discount_total * ratio, 2)
                distributed_total += line_share
                distributed_disc += disc_share

            invoice_qty = order_qty * buc
            if invoice_qty <= 0:
                invoice_qty = 1
            unit_price_gross = round(line_share / invoice_qty, 2)
            discount_per_unit = round(disc_share / invoice_qty, 2)
            list_unit_price_gross = round(unit_price_gross + discount_per_unit, 2)

            vat = country_vat
            override = m.get("override_vat")
            if override is not None:
                vat = float(override)
            elif m.get("vat_percent") is not None:
                vat = float(m["vat_percent"])

            line_total = round(unit_price_gross * invoice_qty, 2)
            total_gross += line_total
            lines.append(InvoicePreviewLine(
                listing_title=title,
                barcode=barcode,
                mapped_oblio_name=m.get("name"),
                mapped_oblio_code=m.get("code"),
                quantity=invoice_qty,
                list_unit_price_gross=list_unit_price_gross,
                discount_per_unit=discount_per_unit,
                unit_price_gross=unit_price_gross,
                vat_percent=vat,
                line_total_gross=line_total,
                is_mapped=True,
            ))

    shipping_amount = _detect_shipping(raw)
    total_gross = round(total_gross + shipping_amount, 2)

    return InvoicePreview(
        order_id=order["id"],
        order_number=order["order_number"],
        order_status=order.get("status"),
        buyer_name=order["buyer_name"] or "Client Trendyol",
        buyer_email=order["buyer_email"],
        ship_address=bill_address,
        ship_city=bill_city,
        ship_state=bill_state,
        ship_country=bill_country,
        currency=currency,
        lines=lines,
        shipping_amount=shipping_amount,
        total_gross=total_gross,
        all_mapped=all_mapped and len(lines) > 0,
    )


def _line_seller_discount(item: dict[str, Any]) -> float:
    """
    Customer-facing discount on this line, excluding any portion Trendyol covers.
    Trendyol API uses `discount` for the total discount and `tyDiscount` /
    `totalTyDiscount` for the part Trendyol funded; the seller-borne portion is the
    difference. If both are absent, fall back to 0.
    """
    total_disc = _f(item.get("discount")) or _f(item.get("lineTotalDiscount")) or 0.0
    ty_disc = _f(item.get("tyDiscount")) or _f(item.get("totalTyDiscount")) or 0.0
    return max(0.0, total_disc - ty_disc)


def _detect_shipping(raw: dict[str, Any]) -> float:
    """
    Trendyol generally rolls shipping into line prices for the customer. If a
    separate shipping/cargo charge is present in the order payload, return it so
    we can list it explicitly on the invoice. Otherwise return 0.0.
    """
    for key in ("cargoFee", "shippingPrice", "deliveryPrice", "shippingAmount", "cargoPrice"):
        v = _f(raw.get(key))
        if v and v > 0:
            return round(v, 2)
    return 0.0


def auto_create_local_invoices(repo: Repo) -> tuple[int, int]:
    """
    For every pending order with all lines mapped AND a dispatched status
    (Shipped, AtCollectionPoint, Delivered — see INVOICEABLE_TRENDYOL_STATUSES),
    create a LOCAL invoice (no Oblio POST) using Oblio product names/codes/VAT in
    place of the raw Trendyol line data.

    Earlier statuses (Created, Picking, ...) are skipped — the order may still be
    cancelled before shipping, and we don't want to issue invoices we'd have to
    credit-note immediately.
    Returns (created, still_pending).
    """
    log = get_app_logger()
    created = 0
    still_pending = 0
    for o in repo.list_orders(state=InvoiceState.PENDING.value):
        status = (o.get("status") or "").strip()
        if status not in INVOICEABLE_TRENDYOL_STATUSES:
            still_pending += 1
            continue
        # Skip orders that already have a Trendyol-side invoice link (someone else
        # — manual Oblio web UI, prior install, etc. — already invoiced this).
        # Our own POSTs to Trendyol happen AFTER Oblio writes oblio_link, so any
        # `tr_invoice_link` set on a still-PENDING order is by definition external.
        if (o.get("tr_invoice_link") or "").strip():
            log.info("auto-invoice: skipping order %s — externally invoiced (link=%s)",
                     o.get("order_number"), o.get("tr_invoice_link"))
            still_pending += 1
            continue
        try:
            preview = build_preview(repo, o["id"])
        except Exception as e:
            log.error("auto-invoice: build_preview failed for order %s: %s\n%s",
                      o.get("order_number"), e, traceback.format_exc())
            still_pending += 1
            continue
        if not preview.all_mapped:
            still_pending += 1
            continue
        try:
            _save_local_invoice(repo, preview)
            created += 1
        except Exception as e:
            log.error("auto-invoice: save failed for order %s: %s\n%s",
                      o.get("order_number"), e, traceback.format_exc())
            still_pending += 1
    return created, still_pending


def refresh_local_drafts(repo: Repo) -> int:
    """
    Rebuild raw_response + total for LOCAL drafts (no oblio_link) whose inputs
    changed since they were built — order content updated, mappings edited, or
    the builder logic itself revised (BUILDER_REV). Unchanged drafts are skipped
    via a pure-SQL build_sig comparison, so the steady-state cost per poll tick
    is one indexed query, not N× build_preview.
    Rows already sent to Oblio are never touched.
    Returns the number of drafts actually rewritten.
    """
    log = get_app_logger()
    mappings_rev = repo.mappings_rev()
    stale = repo.list_stale_drafts(BUILDER_REV, mappings_rev)
    if not stale:
        return 0
    log.info("refresh-local-drafts: %d stale draft(s) to rebuild", len(stale))
    refreshed = 0
    for inv in stale:
        try:
            preview = build_preview(repo, inv["order_id"])
        except Exception as e:
            log.warning("refresh-local-drafts: build_preview failed for invoice %s: %s",
                        inv.get("number"), e)
            continue
        if not preview.all_mapped:
            # Order has become unmappable since the draft was written — we can't
            # rebuild lines/totals, so leave those (and last_send_error) alone for
            # triage. But the buyer ADDRESS needs no mappings: patch just the
            # buyer block so address fixes (e.g. the județ field, BUILDER_REV 2)
            # reach every unsent draft. build_sig stays stale on purpose — once
            # the mappings are repaired, the full rebuild still happens.
            if repo.patch_draft_buyer(inv["id"], {
                "name": preview.buyer_name,
                "email": preview.buyer_email,
                "address": preview.ship_address,
                "city": preview.ship_city,
                "state": preview.ship_state,
                "country": preview.ship_country,
            }):
                log.info("refresh-local-drafts: patched buyer block only for "
                         "invoice %s (order not fully mapped)", inv.get("number"))
                refreshed += 1
            continue
        series = _series_for_country(repo, preview.ship_country)
        record = _local_invoice_record(
            preview,
            series=series,
            number=inv.get("number") or "",
            issue_dt=_issue_date_for(preview) or datetime.now(),
            created_at=datetime.now(),
        )
        if repo.refresh_local_draft(
            invoice_id=inv["id"],
            series=series,
            total=preview.total_gross,
            currency=preview.currency,
            raw_response=record,
            build_sig=_build_sig(repo, inv.get("order_fetched_at")),
        ):
            refreshed += 1
    return refreshed


def _save_local_invoice(repo: Repo, preview: InvoicePreview) -> None:
    now = datetime.now()
    number = _next_local_number(repo, now)
    series = _series_for_country(repo, preview.ship_country)
    # Use the Trendyol order date as the invoice's issue date when available;
    # falls back to "today" for legacy orders that never recorded a date.
    issue_dt = _issue_date_for(preview) or now
    invoice_record = _local_invoice_record(preview, series, number, issue_dt, now)
    order_row = repo.get_order(preview.order_id) or {}
    repo.record_invoice(
        order_id=preview.order_id,
        series=series,
        number=number,
        total=preview.total_gross,
        currency=preview.currency,
        oblio_link=None,
        einvoice_link=None,
        raw_response=invoice_record,
        build_sig=_build_sig(repo, order_row.get("fetched_at")),
    )


def _issue_date_for(preview: InvoicePreview) -> datetime | None:
    # `preview` doesn't carry order_date directly; pull from the raw order JSON.
    # (Cheap because we only call this once per invoice.)
    # Importing here avoids a cycle at module top.
    from src.storage.db import get_conn
    row = get_conn().execute(
        "SELECT order_date FROM orders WHERE id = ?", (preview.order_id,)
    ).fetchone()
    if not row or not row["order_date"]:
        return None
    try:
        # order_date is stored as ISO-8601 UTC (see sync._ms_to_iso). Parse just the date part.
        return datetime.fromisoformat(row["order_date"].replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_local_number(repo: Repo, now: datetime) -> str:
    date_part = now.strftime("%Y%m%d")
    seq = repo.next_local_invoice_seq(date_part)
    return f"LOC-{date_part}-{seq:04d}"


def _local_invoice_record(preview: InvoicePreview, series: str, number: str,
                          issue_dt: datetime, created_at: datetime) -> dict[str, Any]:
    return {
        "source": "local",
        "issue_date": issue_dt.strftime("%Y-%m-%d"),
        "created_at": created_at.strftime("%Y-%m-%d %H:%M"),
        "series": series,
        "number": number,
        "order_number": preview.order_number,
        "order_status": preview.order_status,
        "buyer": {
            "name": preview.buyer_name,
            "email": preview.buyer_email,
            "address": preview.ship_address,
            "city": preview.ship_city,
            "state": preview.ship_state,
            "country": preview.ship_country,
        },
        "currency": preview.currency,
        "shipping_amount": preview.shipping_amount,
        "total_gross": preview.total_gross,
        "lines": [
            {
                "original_title": l.listing_title,
                "original_barcode": l.barcode,
                "oblio_name": l.mapped_oblio_name,
                "oblio_code": l.mapped_oblio_code,
                "quantity": l.quantity,
                "list_unit_price_gross": l.list_unit_price_gross,
                "discount_per_unit": l.discount_per_unit,
                "unit_price_gross": l.unit_price_gross,
                "vat_percent": l.vat_percent,
                "line_total_gross": l.line_total_gross,
            }
            for l in preview.lines
        ],
    }


def plan_batch_stock(repo: Repo, client: OblioClient,
                     invoice_ids: list[int]) -> tuple[list[int], list[tuple[str, str]], str | None]:
    """
    Pre-flight stock check for a batch send.

    Oblio enforces stock at issue time (no negative stock on this account), and a
    batch can DRAIN a product mid-run: the first N invoices for a product pass,
    the rest fail with "nu are stoc suficient" even though the product "had
    stock" when the user checked. So: fetch live stock ONCE, walk the batch in
    order budgeting per-product quantities, and hold back invoices that would
    exceed what's available. Held invoices keep their draft + get a clear error
    stamped; everything else sends normally.

    Best-effort: if the stock snapshot can't be fetched, the whole batch is
    declared sendable (Oblio remains the authority) and a warning is returned so
    the user KNOWS the guard was off for this run.

    Returns (sendable_ids, held, warning) where held = [(invoice_number, reason)].
    """
    log = get_app_logger()
    try:
        products = client.list_products()
    except Exception as e:
        log.warning("preflight-stock: could not fetch Oblio products (%s) — skipping check", e)
        # Loud warning instead of a silent skip: without it, stock errors surface
        # at send time and nobody knows the guard was off for this batch.
        return list(invoice_ids), [], (
            "Nu am putut verifica stocul înainte de trimitere (Oblio limitează cererile) — "
            "batch-ul s-a trimis FĂRĂ verificare de stoc; eventualele erori de stoc apar la trimitere."
        )

    # Remaining stock budget, keyed by code and by name (lines may lack a code).
    # Only Marfa products WITH a stock record participate — services and
    # stockless records are Oblio's call.
    # One budget entry per product; code/name lookups alias to the same entry so
    # mixed matching can never double the available quantity.
    budget: dict[str, float] = {}
    label: dict[str, str] = {}
    alias: dict[str, str] = {}
    for p in products:
        if (p.get("productType") or "") != "Marfa":
            continue
        stock = p.get("stock") or []
        if not stock:
            continue
        qty = sum(float(s.get("quantity") or 0) for s in stock)
        code = (p.get("code") or "").strip().lower()
        name = (p.get("name") or "").strip()
        canonical = f"c:{code}" if code else f"n:{name.lower()}"
        budget[canonical] = qty
        # Include the code: Oblio catalogs accumulate near-identical duplicates
        # ("PLITA JAPONEZA" / "PLITA JAPONEZA 32CM" / "… 32CM 2"), and the code
        # is what tells the user WHICH one the invoice is actually mapped to.
        label[canonical] = f"{name} [{code}]" if (name and code) else (name or code)
        if code:
            alias[f"c:{code}"] = canonical
        if name:
            alias[f"n:{name.lower()}"] = canonical
    # Untouched copy: hold messages must show Oblio's ACTUAL stock at snapshot
    # time, not the leftover budget after the batch reserved from it — printing
    # the leftover reads as "Oblio has zero" and causes endless confusion.
    snapshot = dict(budget)

    def _budget_key(line: dict[str, Any]) -> str | None:
        code = (line.get("oblio_code") or "").strip().lower()
        if code and f"c:{code}" in alias:
            return alias[f"c:{code}"]
        name = (line.get("oblio_name") or "").strip().lower()
        if name and f"n:{name}" in alias:
            return alias[f"n:{name}"]
        return None

    sendable: list[int] = []
    held: list[tuple[str, str]] = []
    for iid in invoice_ids:
        inv = repo.get_invoice(iid)
        if not inv:
            continue
        try:
            lines = (json.loads(inv.get("raw_response") or "{}").get("lines")) or []
        except ValueError:
            lines = []
        # Which products would this invoice overdraw?
        shortages: list[str] = []
        needs: dict[str, float] = {}
        for l in lines:
            key = _budget_key(l)
            if key is None:
                continue
            needs[key] = needs.get(key, 0.0) + float(l.get("quantity") or 0)
        for key, need in needs.items():
            if budget[key] - need < 0:
                had = snapshot[key]
                reserved = had - budget[key]
                shortages.append(
                    f"{label[key]}: factura cere {need:g} buc, Oblio avea {had:g} la verificare"
                    + (f" din care {reserved:g} deja alocate facturilor anterioare din batch"
                       if reserved > 0 else "")
                )
        if shortages:
            msg = ("Amânată — stoc Oblio insuficient: " + "; ".join(shortages)
                   + ". Adaugă NIR în Oblio și retrimite.")
            repo.record_invoice_send_error(iid, msg)
            held.append((inv.get("number") or str(iid), msg))
            # Timestamped proof of what Oblio's stock snapshot said at hold time —
            # settles any later "but it HAD stock" question from app.log alone.
            log.info("preflight-stock: HOLD %s: %s", inv.get("number"), msg)
            continue
        # Reserve the stock for this invoice and let it through.
        for key, need in needs.items():
            budget[key] -= need
        sendable.append(iid)

    if held:
        log.info("preflight-stock: holding back %d invoice(s) for insufficient stock", len(held))
    return sendable, held, None


# Oblio-side transient failure: the backend sometimes cannot load the gestiune
# while under load and mislabels it as a stock problem. Retryable, unlike a
# genuine "produsul X nu are stoc suficient" verdict.
_TRANSIENT_OBLIO_FRAGMENT = "descarca gestiunea"


def send_local_invoice_to_oblio(
    repo: Repo,
    invoice_id: int,
    client: OblioClient | None = None,
    dry_run: bool = False,
    force_resend: bool = False,
) -> dict[str, Any]:
    """
    Take an already-existing LOCAL invoice row (i.e. drafted by auto_create_local_invoices)
    and POST it to Oblio. On success the same row is updated with Oblio's link, einvoice
    link, official series + number, and the raw response is overwritten with Oblio's.
    On Oblio failure, the error is stamped on the row's `last_send_error` column.

    Idempotent: if the row already has `oblio_link` set, returns the existing row
    untouched (no POST). Caller is responsible for displaying the dedupe outcome.

    `dry_run=True` builds and LOGS the Oblio payload but does NOT call Oblio. The
    invoice row is left unchanged. Used by the "Dry run" Settings toggle so you can
    verify what would be sent before flipping to real sends.

    `force_resend=True` bypasses both the oblio_link idempotency return and the
    external-invoice guard: used by the "Resend" button after the user confirms
    they cancelled the previous invoice in Oblio/Trendyol. A NEW Oblio invoice
    (new number) is issued and the row is re-stamped with it.
    """
    log = get_app_logger()
    inv = repo.get_invoice(invoice_id)
    if not inv:
        raise RuntimeError(f"Invoice {invoice_id} not found")
    if inv.get("oblio_link") and not force_resend:
        log.info("send: invoice %s already has oblio_link, skipping (idempotent)", invoice_id)
        return inv
    if force_resend:
        log.warning("send: FORCE RESEND for invoice %s (old link=%s)",
                    invoice_id, inv.get("oblio_link"))

    order_id = inv["order_id"]
    # External invoice guard: if the order already has a Trendyol-side invoiceLink
    # AND we haven't sent our own copy yet, someone else (manual Oblio UI / another
    # install) already issued the invoice. Refuse the Send to avoid a duplicate.
    # Skipped on force_resend — the link on the order is our own previous send,
    # and the user has explicitly confirmed the old invoice was cancelled.
    order_row = repo.get_order(order_id)
    if not force_resend and order_row and (order_row.get("tr_invoice_link") or "").strip():
        msg = (
            f"Refused: Trendyol order {order_row.get('order_number')} already has an "
            f"external invoice attached ({order_row.get('tr_invoice_number') or order_row.get('tr_invoice_link')}). "
            "Skipped to prevent a duplicate."
        )
        repo.record_invoice_send_error(invoice_id, msg)
        raise RuntimeError(msg)

    preview = build_preview(repo, order_id)
    if not preview.all_mapped:
        msg = "Order has unmapped lines; map them before sending."
        repo.record_invoice_send_error(invoice_id, msg)
        raise RuntimeError(msg)
    if not preview.lines:
        msg = "Order has no invoiceable lines."
        repo.record_invoice_send_error(invoice_id, msg)
        raise RuntimeError(msg)

    oblio = client or build_oblio_client(repo)
    try:
        payload = _build_oblio_payload(repo, preview, oblio)
    except Exception as e:
        repo.record_invoice_send_error(invoice_id, str(e))
        raise

    if dry_run:
        log.warning("DRY-RUN send for invoice %s (order %s): payload=%s",
                    invoice_id, inv.get("order_number"),
                    json.dumps(payload, ensure_ascii=False)[:1500])
        return {"dry_run": True, "payload": payload}

    try:
        response = oblio.create_invoice(payload)
    except Exception as e:
        log.error("send: Oblio rejected invoice %s: %s", invoice_id, e)
        repo.record_invoice_send_error(invoice_id, str(e))
        raise

    data = response.get("data") or {}
    repo.update_invoice_oblio_response(
        invoice_id=invoice_id,
        oblio_link=data.get("link"),
        einvoice_link=data.get("einvoice") or data.get("einvoiceLink"),
        series=data.get("seriesName") or None,
        number=str(data.get("number")) if data.get("number") is not None else None,
        total=_f(data.get("total")),
        currency=data.get("currency"),
        raw_response=response,
    )

    # Best-effort: push the Oblio link back to Trendyol so the customer can
    # download the PDF from their Trendyol UI. A failure here is logged but
    # does NOT undo the successful Oblio POST — the user can click
    # "Re-notify Trendyol" later.
    if not dry_run:
        try:
            notify_trendyol_of_invoice(repo, invoice_id)
        except Exception as e:
            log.warning("send: Oblio OK but Trendyol notification failed for invoice %s: %s",
                        invoice_id, e)
    return response


def notify_trendyol_of_invoice(
    repo: Repo,
    invoice_id: int,
    client: "TrendyolClient | None" = None,
) -> dict[str, Any]:
    """
    POST the Oblio invoice link back to Trendyol.

    Idempotent: if `trendyol_link_sent_at` is already set, returns without re-POSTing.
    Requires `oblio_link` to be set (we don't notify Trendyol before Oblio is done).
    On failure: stamps `trendyol_link_error` and re-raises.
    """
    from src.services.sync import build_trendyol_client  # local import to avoid cycle
    log = get_app_logger()
    inv = repo.get_invoice(invoice_id)
    if not inv:
        raise RuntimeError(f"Invoice {invoice_id} not found")
    if inv.get("trendyol_link_sent_at"):
        log.info("notify-trendyol: invoice %s already notified, skipping", invoice_id)
        return inv
    oblio_link = inv.get("oblio_link")
    if not oblio_link:
        msg = "Cannot notify Trendyol: invoice has no oblio_link yet (send to Oblio first)."
        repo.record_invoice_trendyol_link_error(invoice_id, msg)
        raise RuntimeError(msg)

    order = repo.get_order(inv["order_id"])
    if not order:
        msg = f"Invoice {invoice_id} references missing order {inv['order_id']}"
        repo.record_invoice_trendyol_link_error(invoice_id, msg)
        raise RuntimeError(msg)

    try:
        raw = json.loads(order["raw_json"])
    except (ValueError, TypeError):
        raw = {}
    shipment_package_id = raw.get("shipmentPackageId") or raw.get("id")
    if not shipment_package_id:
        msg = "Trendyol order has no shipmentPackageId; cannot notify."
        repo.record_invoice_trendyol_link_error(invoice_id, msg)
        raise RuntimeError(msg)

    series = inv.get("series") or ""
    number = inv.get("number") or ""
    invoice_number = f"{series} {number}".strip()
    currency = inv.get("currency") or order.get("currency") or "RON"

    # Trendyol wants epoch ms; fall back to "now" if the issue date can't be parsed.
    issue_iso = inv.get("issued_at") or order.get("order_date") or ""
    invoice_date_ms = _to_epoch_ms(issue_iso)

    try:
        ty = client or build_trendyol_client(repo)
        response = ty.send_invoice_link(
            invoice_link=oblio_link,
            shipment_package_id=shipment_package_id,
            invoice_number=invoice_number,
            invoice_date_ms=invoice_date_ms,
            currency=currency,
        )
    except Exception as e:
        log.error("notify-trendyol: invoice %s failed: %s", invoice_id, e)
        repo.record_invoice_trendyol_link_error(invoice_id, str(e))
        raise

    repo.mark_invoice_trendyol_link_sent(invoice_id)
    return response


def _to_epoch_ms(iso_string: str) -> int:
    """Parse an ISO-8601 string to epoch ms; falls back to now() on parse failure."""
    if iso_string:
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    return int(datetime.now().timestamp() * 1000)


def auto_notify_trendyol_pending(repo: Repo) -> tuple[int, int]:
    """Retry Trendyol notification for every Oblio-sent invoice that hasn't yet
    been pushed to Trendyol. Called from the poll loop and bulk Send All button."""
    log = get_app_logger()
    sent = 0
    failed = 0
    for inv in repo.list_invoices():
        if not inv.get("oblio_link"):
            continue
        if inv.get("trendyol_link_sent_at"):
            continue
        try:
            notify_trendyol_of_invoice(repo, inv["id"])
            sent += 1
        except Exception as e:
            log.error("auto-notify-trendyol: invoice %s failed: %s", inv.get("number"), e)
            failed += 1
    return sent, failed


def issue_invoice(repo: Repo, order_id: int, client: OblioClient | None = None) -> dict[str, Any]:
    preview = build_preview(repo, order_id)
    if not preview.all_mapped:
        raise RuntimeError("Order has unmapped lines; map them before issuing.")
    if not preview.lines:
        raise RuntimeError("Order has no invoiceable lines.")

    oblio = client or build_oblio_client(repo)
    payload = _build_oblio_payload(repo, preview, oblio)
    response = oblio.create_invoice(payload)

    data = response.get("data") or {}
    series = data.get("seriesName") or repo.invoice_series()
    number = str(data.get("number") or "")
    total = _f(data.get("total"))
    currency = data.get("currency") or preview.currency
    link = data.get("link")
    einvoice_link = data.get("einvoice") or data.get("einvoiceLink")

    repo.record_invoice(
        order_id=order_id,
        series=series,
        number=number,
        total=total,
        currency=currency,
        oblio_link=link,
        einvoice_link=einvoice_link,
        raw_response=response,
    )
    return response


# Account VAT rates fetched once per process, keyed by CIF. Rates change rarely
# (a company reconfigures them maybe once a year); a restart picks up changes.
_VAT_RATES_CACHE: dict[str, list[dict[str, Any]]] = {}


def _resolve_vat_name(client: OblioClient, percent: float) -> str:
    """
    Map a VAT percentage to the account's configured rate NAME (e.g. 20 → "bg",
    24 → "gr"). Oblio matches invoice lines against rates by name, so sending a
    bare percentage under the wrong name is rejected with
    "Produsul X are cota TVA N%, dar aceasta nu este setata in Oblio".

    If the rates list can't be fetched (offline dry-run, transient error), fall
    back to "Normala" — the previous hardcoded behavior. If the list IS known and
    the percentage isn't in it, raise with an actionable message instead of
    letting Oblio return its crypto-brief 400.
    """
    rates = _VAT_RATES_CACHE.get(client.cif)
    if rates is None:
        try:
            rates = client.list_vat_rates()
            _VAT_RATES_CACHE[client.cif] = rates
        except Exception as e:
            get_app_logger().warning("vat-rates fetch failed, falling back to 'Normala': %s", e)
            return "Normala"
    for r in rates:
        if abs(float(r.get("percent") or 0) - percent) < 0.01:
            return str(r.get("name"))
    raise RuntimeError(
        f"Cota TVA {percent:g}% nu este configurată în contul Oblio. "
        f"Adaug-o în Oblio (Setări → Cote TVA), apoi reîncearcă."
    )


def _build_oblio_payload(repo: Repo, preview: InvoicePreview, client: OblioClient) -> dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    client_block = {
        "name": preview.buyer_name,
        "email": preview.buyer_email or "",
        "address": preview.ship_address or "",
        "city": preview.ship_city or "",
        "state": preview.ship_state or "",   # județ — required on RO invoices
        "country": preview.ship_country or "",
        "save": 0,
        "autocomplete": 0,
    }
    products = []
    for l in preview.lines:
        # Each invoice line is emitted as TWO entries when there's a discount:
        #   1. The product at LIST price, NO discount field.
        #   2. A separate discount entry (just discount + discountType, no name/price)
        #      which Oblio applies to the immediately preceding product line.
        # Sending the discount on the product line itself triggers Oblio error 400
        # "Discount-ul trebuie aplicat dupa cel putin un produs care nu are discount".
        line_discount_total = round(l.discount_per_unit * l.quantity, 2)
        product_line: dict[str, Any] = {
            "name": l.mapped_oblio_name or l.listing_title,
            "code": l.mapped_oblio_code or "",
            "measuringUnit": "buc",
            "currency": preview.currency,
            "vatName": _resolve_vat_name(client, l.vat_percent),
            "vatPercentage": l.vat_percent,
            "vatIncluded": 1,
            "price": l.list_unit_price_gross,
            "quantity": l.quantity,
        }
        # Enrich from our synced Oblio catalog: Oblio rejects Marfa lines that
        # don't carry `management` (gestiune). Each Oblio product already knows
        # which gestiune it belongs to — pull it from raw_json.
        _enrich_product_line(repo, product_line, l.mapped_oblio_code, l.mapped_oblio_name)
        products.append(product_line)

        if line_discount_total > 0:
            # Separate discount entry — applies to the preceding product line above.
            products.append({
                "name": "Discount",
                "discount": line_discount_total,
                "discountType": "valoric",
            })

    payload: dict[str, Any] = {
        "cif": repo.oblio_cif(),
        "client": client_block,
        "issueDate": today,
        "seriesName": _series_for_country(repo, preview.ship_country),
        "language": repo.invoice_language(),
        "currency": preview.currency,
        "products": products,
        "mentions": f"Trendyol order {preview.order_number}",
    }
    # Invoice-level workStation / management act as defaults for any line that
    # didn't override them — keep them as a safety net.
    workstation = repo.oblio_workstation()
    if workstation:
        payload["workStation"] = workstation
    management = repo.oblio_management()
    if management:
        payload["management"] = management
    return payload


def _enrich_product_line(repo: Repo, line: dict[str, Any],
                          oblio_code: str | None, oblio_name: str | None) -> None:
    """Pull productType + management/workstation from the locally-synced Oblio
    product so Oblio doesn't reject the invoice with 'product has no Gestiune'.
    Silently skips if the product can't be found locally."""
    op = None
    if oblio_code:
        op = repo.get_oblio_product_by_code(oblio_code)
    if op is None and oblio_name:
        op = repo.get_oblio_product_by_name(oblio_name)
    if op is None:
        return
    try:
        raw = json.loads(op.get("raw_json") or "{}")
    except ValueError:
        return

    product_type = raw.get("productType")
    if product_type:
        line["productType"] = product_type

    # For goods (Marfa), grab the first stock entry's management/workStation.
    # Services (Serviciu) typically have no stock array; that's fine.
    stock = raw.get("stock") or []
    if stock:
        first = stock[0] or {}
        if first.get("management"):
            line["management"] = first["management"]
        if first.get("managementId"):
            line["managementId"] = first["managementId"]
        if first.get("workStation"):
            line["workStation"] = first["workStation"]


def _order_lines(raw: dict[str, Any]) -> list[dict[str, Any]]:
    lines = raw.get("lines") or raw.get("orderLines") or raw.get("packages") or []
    if isinstance(lines, list) and lines and "lines" in (lines[0] or {}):
        flat = []
        for pkg in lines:
            flat.extend(pkg.get("lines") or [])
        return flat
    return lines or []


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
