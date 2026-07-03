import itertools
import time
import uuid
from typing import Any, Iterator

import requests

from src.api._retry import with_retries
from src.logging_setup import get_trendyol_logger, redact_pii


_DAY_MS = 24 * 3600 * 1000


class TrendyolError(Exception):
    pass


_req_counter = itertools.count(1)


class TrendyolClient:
    """
    Client for Trendyol International seller API.

    The base_url differs between Trendyol Turkey and Trendyol International (RO/GR/BG),
    so it is supplied from configuration rather than hardcoded. Examples users may enter
    during setup:
      - https://apigw.trendyol.com/integration
      - https://api.trendyol.com/sapigw
    """

    def __init__(self, base_url: str, seller_id: str, api_key: str, api_secret: str,
                 integration_ref: str | None = None, user_agent: str | None = None):
        if not base_url:
            raise TrendyolError("Trendyol base URL is not configured.")
        self.base_url = base_url.rstrip("/")
        self.seller_id = str(seller_id)
        self._api_key_len = len(api_key or "")
        self._api_secret_len = len(api_secret or "")
        self._session = requests.Session()
        self._session.auth = (api_key, api_secret)
        # Trendyol International expects the integration reference code in the User-Agent
        # so its gateway can route the request to the correct tenant; falls back to
        # "SelfIntegration" for Turkey-only integrations.
        ua_suffix = (integration_ref or "").strip() or "SelfIntegration"
        self._user_agent = user_agent or f"{seller_id} - {ua_suffix}"
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        })
        self._log = get_trendyol_logger()
        self._log.info(
            "client init base_url=%s seller_id=%s user_agent=%r api_key_len=%d secret_len=%d",
            self.base_url, self.seller_id, self._user_agent, self._api_key_len, self._api_secret_len,
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req_id = f"{next(_req_counter):04d}-{uuid.uuid4().hex[:6]}"
        self._log.debug(
            "[%s] GET %s params=%s ua=%r",
            req_id, url, params, self._user_agent,
        )

        def call() -> requests.Response:
            return self._session.get(url, params=params, timeout=60)

        try:
            resp = with_retries(call, logger=self._log, op=f"TY[{req_id}] GET {path}")
        except requests.RequestException as e:
            self._log.error("[%s] network error after retries: %s", req_id, e)
            raise TrendyolError(f"Trendyol network error: {e}") from e

        body_preview = redact_pii((resp.text or "")[:800])
        cf_ray = resp.headers.get("CF-RAY") or resp.headers.get("cf-ray")
        if resp.ok:
            self._log.debug(
                "[%s] <- %s cf_ray=%s body[:800]=%s",
                req_id, resp.status_code, cf_ray, body_preview,
            )
        else:
            self._log.warning(
                "[%s] <- %s cf_ray=%s body[:800]=%s",
                req_id, resp.status_code, cf_ray, body_preview,
            )

        if not resp.ok:
            raise TrendyolError(
                f"Trendyol API error ({resp.status_code}) {url}: {body_preview[:500]} "
                f"[log id {req_id}]"
            )
        try:
            return resp.json()
        except ValueError:
            raise TrendyolError(
                f"Non-JSON response ({resp.status_code}) {url}: {body_preview[:500]} "
                f"[log id {req_id}]"
            )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req_id = f"{next(_req_counter):04d}-{uuid.uuid4().hex[:6]}"
        self._log.debug("[%s] POST %s body=%s", req_id, url, body)

        def call() -> requests.Response:
            return self._session.post(url, json=body, timeout=60)

        try:
            resp = with_retries(call, logger=self._log, op=f"TY[{req_id}] POST {path}")
        except requests.RequestException as e:
            self._log.error("[%s] network error after retries: %s", req_id, e)
            raise TrendyolError(f"Trendyol network error: {e}") from e

        body_preview = redact_pii((resp.text or "")[:800])
        cf_ray = resp.headers.get("CF-RAY") or resp.headers.get("cf-ray")
        if resp.ok:
            self._log.debug("[%s] <- %s cf_ray=%s body[:800]=%s",
                            req_id, resp.status_code, cf_ray, body_preview)
        else:
            self._log.warning("[%s] <- %s cf_ray=%s body[:800]=%s",
                              req_id, resp.status_code, cf_ray, body_preview)
        if not resp.ok:
            raise TrendyolError(
                f"Trendyol API error ({resp.status_code}) {url}: {body_preview[:500]} "
                f"[log id {req_id}]"
            )
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"_raw": body_preview}

    def send_invoice_link(
        self,
        *,
        invoice_link: str,
        shipment_package_id: int | str,
        invoice_number: str,
        invoice_date_ms: int,
        currency: str,
    ) -> dict[str, Any]:
        """
        POST the Oblio invoice URL back to Trendyol so the order is marked as
        invoiced on Trendyol's side and the customer can download the PDF from
        their Trendyol UI.

        Endpoint per Trendyol seller docs:
            POST /sellers/{sellerId}/seller-invoice-links

        Body fields (per Trendyol's docs for the cross-border integration):
            invoiceLink          — publicly fetchable URL (Oblio PDF link)
            shipmentPackageId    — Trendyol's shipment package id (= order's `id`/`shipmentPackageId`)
            invoiceNumber        — e.g. "ECH 56664" (Oblio series + number)
            invoiceDateTime      — epoch milliseconds
            currency             — ISO code, e.g. "RON"
        """
        body = {
            "invoiceLink": invoice_link,
            "shipmentPackageId": int(shipment_package_id),
            "invoiceNumber": invoice_number,
            "invoiceDateTime": int(invoice_date_ms),
            "currency": currency,
        }
        return self._post(f"/sellers/{self.seller_id}/seller-invoice-links", body)

    def test_connection(self) -> dict[str, Any]:
        # Hit the products endpoint with size=1 — it's the same path we use for real work,
        # so any base-URL/region/auth problem surfaces here with the real error payload.
        return self._get(
            f"/product/sellers/{self.seller_id}/products",
            params={"page": 0, "size": 1, "approved": "true"},
        )

    # ---- products ----
    def iter_products(self, approved_only: bool = True, page_size: int = 200) -> Iterator[dict[str, Any]]:
        path = f"/product/sellers/{self.seller_id}/products"
        page = 0
        while True:
            params: dict[str, Any] = {"page": page, "size": page_size}
            if approved_only:
                params["approved"] = "true"
            data = self._get(path, params=params)
            items = data.get("content") or []
            for item in items:
                yield item
            total_pages = int(data.get("totalPages") or 0)
            page += 1
            if not items or page >= total_pages:
                break

    def list_products(self, approved_only: bool = True) -> list[dict[str, Any]]:
        return list(self.iter_products(approved_only=approved_only))

    # ---- orders ----
    def iter_orders(self, status: str | None = None, start_date_ms: int | None = None,
                    end_date_ms: int | None = None, page_size: int = 200) -> Iterator[dict[str, Any]]:
        path = f"/order/sellers/{self.seller_id}/orders"
        page = 0
        while True:
            params: dict[str, Any] = {"page": page, "size": page_size, "orderByField": "PackageLastModifiedDate", "orderByDirection": "DESC"}
            if status:
                params["status"] = status
            if start_date_ms:
                params["startDate"] = start_date_ms
            if end_date_ms:
                params["endDate"] = end_date_ms
            data = self._get(path, params=params)
            items = data.get("content") or []
            for item in items:
                yield item
            total_pages = int(data.get("totalPages") or 0)
            page += 1
            if not items or page >= total_pages:
                break

    def list_orders(self, status: str | None = None, start_date_ms: int | None = None,
                    end_date_ms: int | None = None) -> list[dict[str, Any]]:
        return list(self.iter_orders(status=status, start_date_ms=start_date_ms, end_date_ms=end_date_ms))

    def iter_orders_extended(self, months_back: int = 24, window_days: int = 7,
                             page_size: int = 200) -> Iterator[dict[str, Any]]:
        """
        Walk Trendyol orders back in fixed time windows so we can build a complete
        catalog of historical listings, not just whatever the API returns by default.

        Trendyol's order endpoint without explicit dates is capped (typically the last
        ~14 days, max ~2k results). To go further back we pass `startDate`/`endDate`
        explicitly and chunk the query into `window_days`-day slices for `months_back`
        months of history. Duplicate orders across overlapping windows are deduped by
        order number on the way out.

        `window_days` must stay small: Trendyol SILENTLY truncates large date ranges
        rather than erroring. Measured on a real seller account (July 2026): a 30-day
        window returned 4302 of 6139 orders (~30% dropped), a 15-day window dropped
        127, while 7-day windows matched 2-day slices exactly (0 dropped, 3095 orders).
        """
        seen_orders: set[str] = set()
        end_ms = int(time.time() * 1000)
        total_windows = max(1, (months_back * 30) // window_days)
        # Stop after ~3 months of consecutive empty windows — sellers can pause for
        # weeks (holidays), so a handful of empty WEEKLY windows is not proof that
        # older history is empty.
        max_empty_windows = max(3, 90 // window_days)
        empty_in_a_row = 0
        for _ in range(total_windows):
            start_ms = end_ms - window_days * _DAY_MS
            window_count = 0
            for raw in self.iter_orders(start_date_ms=start_ms, end_date_ms=end_ms,
                                         page_size=page_size):
                # Dedupe by shipment PACKAGE id — an order split into several
                # packages must yield every package, not just the first seen.
                key = str(raw.get("id") or raw.get("orderNumber") or "")
                if key and key in seen_orders:
                    continue
                if key:
                    seen_orders.add(key)
                window_count += 1
                yield raw
            if window_count == 0:
                empty_in_a_row += 1
                if empty_in_a_row >= max_empty_windows:
                    break
            else:
                empty_in_a_row = 0
            end_ms = start_ms
