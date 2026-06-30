import itertools
import time
import uuid
from typing import Any

import requests

from src.api._retry import with_retries
from src.config import OBLIO_BASE_URL
from src.logging_setup import get_oblio_logger, redact_pii


class OblioError(Exception):
    pass


_req_counter = itertools.count(1)


class OblioClient:
    def __init__(self, email: str, secret: str, cif: str, base_url: str = OBLIO_BASE_URL):
        self.email = email
        self.secret = secret
        self.cif = cif
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()
        self._log = get_oblio_logger()
        self._log.info("client init base_url=%s email=%s cif=%s", self.base_url, email, cif)

    def _authenticate(self) -> None:
        url = f"{self.base_url}/authorize/token"
        req_id = _req_id()
        self._log.info("[%s] POST %s grant_type=client_credentials", req_id, url)
        resp = self._session.post(
            url,
            data={"client_id": self.email, "client_secret": self.secret, "grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        body_preview = (resp.text or "")[:500]
        self._log.info("[%s] <- %s body[:500]=%s", req_id, resp.status_code,
                       "<redacted-token-response>" if resp.ok else body_preview)
        if not resp.ok:
            raise OblioError(f"Authentication failed ({resp.status_code}): {body_preview} [log id {req_id}]")
        data = resp.json()
        self._token = data.get("access_token")
        if not self._token:
            raise OblioError(f"No access_token in response: {data} [log id {req_id}]")
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600)) - 60

    def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_expires_at:
            self._authenticate()

    def _headers(self) -> dict[str, str]:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req_id = _req_id()
        self._log.debug("[%s] GET %s params=%s", req_id, url, params)

        def call() -> requests.Response:
            return self._session.get(url, headers=self._headers(), params=params, timeout=60)

        try:
            resp = with_retries(call, logger=self._log, op=f"OB[{req_id}] GET {path}")
            # 401 after a successful retry path means the token went stale unexpectedly
            # — drop it and try one more time with a fresh token.
            if resp.status_code == 401:
                self._log.warning("[%s] got 401, refreshing token and retrying once", req_id)
                self._token = None
                resp = call()
        except requests.RequestException as e:
            self._log.error("[%s] network error after retries: %s", req_id, e)
            raise OblioError(f"Oblio network error: {e}") from e
        return _parse(resp, req_id, self._log)

    def _post(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        req_id = _req_id()
        self._log.debug("[%s] POST %s body=%s", req_id, url, redact_pii(str(_redact_body(json_body))[:800]))
        headers = self._headers() | {"Content-Type": "application/json"}

        def call() -> requests.Response:
            return self._session.post(url, headers=headers, json=json_body, timeout=60)

        try:
            resp = with_retries(call, logger=self._log, op=f"OB[{req_id}] POST {path}")
            if resp.status_code == 401:
                self._log.warning("[%s] got 401, refreshing token and retrying once", req_id)
                self._token = None
                headers = self._headers() | {"Content-Type": "application/json"}
                resp = self._session.post(url, headers=headers, json=json_body, timeout=60)
        except requests.RequestException as e:
            self._log.error("[%s] network error after retries: %s", req_id, e)
            raise OblioError(f"Oblio network error: {e}") from e
        return _parse(resp, req_id, self._log)

    # ---- ops ----
    def test_connection(self) -> dict[str, Any]:
        self._authenticate()
        return {"ok": True}

    def list_companies(self) -> list[dict[str, Any]]:
        data = self._get("/nomenclature/companies")
        return data.get("data") or []

    def list_products(self, name: str | None = None) -> list[dict[str, Any]]:
        # Oblio caps /nomenclature/products at 250 rows per call; paginate via ?offset=
        all_products: list[dict[str, Any]] = []
        offset = 0
        PAGE_SIZE = 250
        while True:
            params: dict[str, Any] = {"cif": self.cif, "offset": offset}
            if name:
                params["name"] = name
            data = self._get("/nomenclature/products", params=params)
            chunk = data.get("data") or []
            if not chunk:
                break
            all_products.extend(chunk)
            if len(chunk) < PAGE_SIZE:
                break
            offset += len(chunk)
            if offset > 500_000:  # hard safety net
                break
        return all_products

    def list_series(self) -> list[dict[str, Any]]:
        data = self._get("/nomenclature/series", params={"cif": self.cif})
        return data.get("data") or []

    def list_management(self) -> list[dict[str, Any]]:
        data = self._get("/nomenclature/management", params={"cif": self.cif})
        return data.get("data") or []

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/docs/invoice", payload)


def _req_id() -> str:
    return f"{next(_req_counter):04d}-{uuid.uuid4().hex[:6]}"


def _redact_body(body: Any) -> Any:
    if not isinstance(body, dict):
        return body
    redacted = {}
    for k, v in body.items():
        if k.lower() in ("client_secret", "secret", "password", "access_token"):
            redacted[k] = f"<redacted len={len(str(v))}>"
        else:
            redacted[k] = v
    return redacted


def _parse(resp: requests.Response, req_id: str, log) -> dict[str, Any]:
    body_preview = redact_pii((resp.text or "")[:800])
    if resp.ok:
        log.debug("[%s] <- %s body[:800]=%s", req_id, resp.status_code, body_preview)
    else:
        log.warning("[%s] <- %s body[:800]=%s", req_id, resp.status_code, body_preview)
    try:
        body = resp.json()
    except ValueError:
        raise OblioError(f"Non-JSON response ({resp.status_code}): {body_preview} [log id {req_id}]")
    if not resp.ok or (body.get("status") and body["status"] >= 400):
        msg = body.get("statusMessage") or body.get("message") or body_preview
        raise OblioError(f"Oblio API error ({resp.status_code}): {msg} [log id {req_id}]")
    return body
