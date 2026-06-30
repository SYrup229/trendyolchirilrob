"""Tiny retry/backoff helper for HTTP calls. No external deps."""
import logging
import time
from typing import Callable, TypeVar

import requests

T = TypeVar("T")

# Status codes we'll retry: throttling and transient server errors.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    logger: logging.Logger | None = None,
    op: str = "HTTP",
) -> T:
    """
    Run `fn` with exponential backoff. Retries on requests.RequestException
    (network errors) and on HTTP responses whose status_code is in RETRY_STATUS.
    Re-raises the last exception or returns the last response.

    For HTTP retries we honour the Retry-After header if Trendyol/Oblio sends one.
    """
    delay = base_delay
    last_exc: BaseException | None = None
    last_resp: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
        except requests.RequestException as e:
            last_exc = e
            if attempt == attempts:
                raise
            sleep_for = delay
            if logger:
                logger.warning("%s attempt %d/%d network error: %s — retrying in %.1fs",
                               op, attempt, attempts, e, sleep_for)
            time.sleep(sleep_for)
            delay = min(delay * 2, max_delay)
            continue

        if isinstance(result, requests.Response) and result.status_code in RETRY_STATUS:
            last_resp = result
            if attempt == attempts:
                return result
            retry_after = _parse_retry_after(result.headers.get("Retry-After"))
            sleep_for = retry_after if retry_after is not None else delay
            if logger:
                logger.warning("%s attempt %d/%d -> %d, retrying in %.1fs",
                               op, attempt, attempts, result.status_code, sleep_for)
            time.sleep(sleep_for)
            delay = min(delay * 2, max_delay)
            continue

        return result

    # Unreachable in practice — both branches above either return or raise.
    if last_exc:
        raise last_exc
    return last_resp  # type: ignore[return-value]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
