"""Shared HTTP robustness helper for the provider-backed source adapters.

The Instagram / LinkedIn / web-search clients all hit third-party HTTP providers
that occasionally 429, 5xx, or drop the connection. Rather than each client
re-implementing retry logic, they route GETs through :func:`request_with_retry`,
which gives them:

  * **Exponential backoff + jitter** between attempts (bounded).
  * **Retry-After awareness** — if the provider sends the header on a 429/503 we
    honour it instead of guessing.
  * **Retry on transient failures only** — 429, 5xx, and network/timeout errors.
    4xx other than 429 fail fast (a bad request won't fix itself).
  * **Per-request User-Agent rotation** from a small realistic pool.
  * **Optional outbound proxy** from ``SCRAPER_PROXIES`` (comma-separated; one is
    chosen per call) — reuses the same env var the Meta scraper documents, so
    proxy config is uniform across the whole sourcing layer.

After the final attempt the underlying error is raised; callers map that to their
own ``RateLimited`` / control-flow as needed (the adapters already do). ``httpx``
is imported lazily so importing a client never requires it.

Env knobs (all optional, sensible defaults):
  SOURCING_MAX_RETRIES   (default 3)     attempts beyond the first
  SOURCING_BACKOFF_BASE  (default 1.0)   seconds, base of the exponential
  SOURCING_BACKOFF_MAX   (default 30.0)  seconds, per-sleep cap
  SCRAPER_PROXIES        (default empty) comma-separated proxy URLs

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import os
import random
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("sourcing.http")

# A small pool of realistic desktop UAs; one is picked per request.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class RetryableHTTPError(Exception):
    """Raised internally to signal a status that should be retried."""

    def __init__(self, status: int, retry_after: Optional[float] = None) -> None:
        super().__init__("retryable HTTP status {0}".format(status))
        self.status = status
        self.retry_after = retry_after


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _pick_proxy() -> Optional[str]:
    raw = os.environ.get("SCRAPER_PROXIES", "").strip()
    if not raw:
        return None
    proxies = [p.strip() for p in raw.split(",") if p.strip()]
    return random.choice(proxies) if proxies else None


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)  # delta-seconds form
    except (TypeError, ValueError):
        return None  # HTTP-date form is rare here; fall back to backoff


def request_with_retry(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20.0,
    max_retries: Optional[int] = None,
    retry_statuses=(429, 500, 502, 503, 504),
):
    """GET ``url`` with retries/backoff/jitter, proxy + UA rotation.

    Returns the ``httpx.Response`` on the first non-retryable outcome (any 2xx,
    or a 4xx other than 429). Raises the last error if every attempt fails.
    """
    import httpx  # lazy

    attempts = (max_retries if max_retries is not None else _int_env("SOURCING_MAX_RETRIES", 3)) + 1
    base = _float_env("SOURCING_BACKOFF_BASE", 1.0)
    cap = _float_env("SOURCING_BACKOFF_MAX", 30.0)

    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        hdrs = dict(headers or {})
        hdrs.setdefault("User-Agent", random.choice(_USER_AGENTS))
        proxy = _pick_proxy()
        client_kwargs: Dict[str, Any] = {"timeout": timeout}
        if proxy:
            client_kwargs["proxies"] = proxy
        try:
            with httpx.Client(**client_kwargs) as client:
                resp = client.get(url, params=params, headers=hdrs)
            if resp.status_code in retry_statuses:
                ra = _parse_retry_after(resp.headers.get("Retry-After"))
                raise RetryableHTTPError(resp.status_code, ra)
            return resp  # success or a fail-fast 4xx — caller inspects status
        except (RetryableHTTPError, httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break  # out of attempts — raise below
            # Honour Retry-After if present, else exponential backoff + jitter.
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None:
                sleep = min(retry_after, cap)
            else:
                sleep = min(cap, base * (2 ** attempt)) * (0.5 + random.random() / 2)
            logger.info(
                "sourcing.http: retry %d/%d after %.1fs (%s)",
                attempt + 1, attempts - 1, sleep, type(exc).__name__,
            )
            time.sleep(sleep)

    assert last_exc is not None
    raise last_exc
