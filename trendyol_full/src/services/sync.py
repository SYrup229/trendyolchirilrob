from typing import Any, Callable

from src.api.oblio import OblioClient
from src.api.trendyol import TrendyolClient
from src.storage.repo import Repo


def build_oblio_client(repo: Repo) -> OblioClient:
    email = repo.oblio_email()
    secret = repo.oblio_secret()
    cif = repo.oblio_cif()
    if not (email and secret and cif):
        raise RuntimeError("Oblio credentials are not fully configured.")
    return OblioClient(email=email, secret=secret, cif=cif)


def build_trendyol_client(repo: Repo) -> TrendyolClient:
    base_url = repo.trendyol_base_url()
    seller_id = repo.trendyol_seller_id()
    api_key = repo.trendyol_api_key()
    api_secret = repo.trendyol_api_secret()
    integration_ref = repo.trendyol_integration_ref()
    if not (base_url and seller_id and api_key and api_secret):
        raise RuntimeError("Trendyol credentials / base URL are not fully configured.")
    return TrendyolClient(
        base_url=base_url, seller_id=seller_id, api_key=api_key, api_secret=api_secret,
        integration_ref=integration_ref,
    )


def sync_oblio_products(repo: Repo, progress: Callable[[str], None] | None = None) -> int:
    if progress: progress("Connecting to Oblio…")
    client = build_oblio_client(repo)
    if progress: progress("Fetching Oblio products…")
    products = client.list_products()
    if progress: progress(f"Saving {len(products)} Oblio products…")
    # Upsert (not replace) so mappings tied to existing oblio_products rows survive.
    return repo.upsert_oblio_products(products)


def sync_trendyol_listings(repo: Repo, progress: Callable[[str], None] | None = None) -> int:
    """
    Discover Trendyol listings from the seller's order history.

    Trendyol International's /product/sellers/{id}/products endpoint is empty for many
    international sellers, but /order/sellers/{id}/orders returns everything sold with
    full line metadata (productCode, sku, stockCode, productName). We build the listing
    catalog from there, upserting by stable keys so mappings survive re-syncs.

    Walks the orders endpoint in fixed monthly windows for the past 24 months — without
    explicit date ranges, Trendyol caps the result set to the last ~14 days (~2k rows),
    so older listings would never be discovered.
    """
    if progress: progress("Connecting to Trendyol…")
    client = build_trendyol_client(repo)
    if progress: progress("Scanning orders for listings (24 months back)…")

    seen: dict[str, dict[str, Any]] = {}
    order_count = 0
    for raw in client.iter_orders_extended(months_back=24, window_days=30):
        order_count += 1
        if progress and order_count % 200 == 0:
            progress(f"Scanned {order_count} orders, {len(seen)} unique listings so far…")
        for line in _raw_order_lines(raw):
            key = str(line.get("productCode") or line.get("contentId") or line.get("sku") or line.get("stockCode") or "").strip()
            if not key or key in seen:
                continue
            seen[key] = {
                "productMainId": str(line.get("productCode") or line.get("contentId") or "") or None,
                "barcode": str(line.get("sku") or "") or None,
                "stockCode": str(line.get("stockCode") or line.get("merchantSku") or "") or None,
                "title": line.get("productName") or "(untitled)",
                "brand": line.get("brand"),
                "listPrice": line.get("price") or line.get("amount"),
                "salePrice": line.get("price") or line.get("amount"),
                "currencyType": raw.get("currencyCode") or raw.get("currency"),
            }

    if progress: progress(f"Saving {len(seen)} listings…")
    return repo.upsert_trendyol_listings(seen.values())


def _raw_order_lines(raw: dict[str, Any]) -> list[dict[str, Any]]:
    lines = raw.get("lines") or raw.get("orderLines") or []
    if not lines:
        packages = raw.get("packages") or []
        for pkg in packages:
            lines.extend(pkg.get("lines") or [])
    return lines


def fetch_and_store_orders(repo: Repo, status: str | None = None) -> int:
    client = build_trendyol_client(repo)
    orders = client.list_orders(status=status)
    stored = 0
    # Also collect listings inline so the catalog auto-grows with new sales —
    # the alternative ("only the manual Settings → Re-sync touches listings")
    # left top-selling new products invisible until a manual re-sync.
    new_listings: dict[str, dict[str, Any]] = {}
    for raw in orders:
        normalized = _normalize_order(raw)
        if not normalized:
            continue
        repo.upsert_order(normalized)
        stored += 1
        for line in _raw_order_lines(raw):
            key = str(line.get("productCode") or line.get("contentId")
                      or line.get("sku") or line.get("stockCode") or "").strip()
            if not key or key in new_listings:
                continue
            new_listings[key] = {
                "productMainId": str(line.get("productCode") or line.get("contentId") or "") or None,
                "barcode": str(line.get("sku") or "") or None,
                "stockCode": str(line.get("stockCode") or line.get("merchantSku") or "") or None,
                "title": line.get("productName") or "(untitled)",
                "brand": line.get("brand"),
                "listPrice": line.get("price") or line.get("amount"),
                "salePrice": line.get("price") or line.get("amount"),
                "currencyType": raw.get("currencyCode") or raw.get("currency"),
            }
    if new_listings:
        # upsert is idempotent and FK-preserving — existing rows keep their id so
        # mappings tied to them survive.
        repo.upsert_trendyol_listings(new_listings.values())
    return stored


def _normalize_order(raw: dict[str, Any]) -> dict[str, Any] | None:
    order_number = raw.get("orderNumber") or raw.get("orderId")
    if not order_number:
        return None
    addr = raw.get("shipmentAddress") or raw.get("invoiceAddress") or {}
    customer_first = raw.get("customerFirstName") or addr.get("firstName") or ""
    customer_last = raw.get("customerLastName") or addr.get("lastName") or ""
    buyer_name = (f"{customer_first} {customer_last}").strip() or raw.get("customerEmail") or "Client Trendyol"
    return {
        "order_number": str(order_number),
        "status": raw.get("status") or raw.get("shipmentPackageStatus") or "Unknown",
        "buyer_name": buyer_name,
        "buyer_email": raw.get("customerEmail"),
        "ship_address": " ".join(filter(None, [addr.get("address1"), addr.get("address2")])) or None,
        "ship_city": addr.get("city"),
        "ship_country": addr.get("countryCode") or addr.get("country"),
        "currency": raw.get("currencyCode") or raw.get("currency"),
        "total_amount": raw.get("totalPrice") or raw.get("grossAmount"),
        "order_date": _ms_to_iso(raw.get("orderDate")),
        # External invoice attached to the order on Trendyol's side — captured every
        # poll, used by the Invoices tab to hide externally-issued invoices and by
        # auto_create_local_invoices to avoid double-issuing.
        "tr_invoice_link": raw.get("invoiceLink") or None,
        "tr_invoice_link_date_ms": raw.get("invoiceLinkSentDate"),
        "tr_invoice_number": raw.get("invoiceNumber") or None,
        "raw": raw,
    }


def _ms_to_iso(ms: Any) -> str | None:
    if not ms:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None
