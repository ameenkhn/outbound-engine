"""Web-search source adapter (L1 / M1).

A complementary discovery net: instead of one platform's API, it runs the spec's
keywords through a general web-search provider (Serper / SerpAPI / Bing — your
choice) with contact-intent suffixes ("… email", "… instagram", "… contact"),
then mines each result's title / URL / snippet for a reachable lead:

  * a social handle baked into the URL (instagram / linkedin / youtube / facebook),
  * an email or Indian phone sitting in the snippet.

It yields the same loader-ready candidate dicts every other adapter does
(``source='websearch'``; ``platform`` inferred from the result URL, else
``'web'``), so these leads flow through the identical resolver / dedupe path. No
DB writes here. This is breadth, not depth — it surfaces creators the
platform-specific adapters miss, and the L0 resolver merges the overlaps.

Seams (same shape as the other adapters):
  * :class:`WebSearchClient` — abstract provider client.
      - :class:`HttpWebSearchClient` — real impl over a configurable endpoint
        (``WEBSEARCH_API_BASE`` / ``WEBSEARCH_API_KEY``), routed through the
        shared retry/backoff helper. ``httpx`` is lazy; key read at call time.
      - :class:`FakeWebSearchClient` — deterministic, offline, for tests.

Rate-limit + resume cursor handling mirrors the other adapters. Python 3.9.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from sourcing.base import SourceAdapter

logger = logging.getLogger("sourcing.websearch")

DEFAULT_SEARCH_BUDGET = 8          # max result pages per run per spec

#: Contact-intent suffixes appended to each keyword to bias results toward
#: pages that actually expose a way to reach the creator.
INTENT_SUFFIXES = ("email", "contact", "instagram")


class RateLimited(Exception):
    """Provider 429 — control flow; the adapter saves a resume cursor."""


# ---------------------------------------------------------------------------
# Client interface + implementations
# ---------------------------------------------------------------------------

class WebSearchClient:
    """Abstract web-search provider client.

    ``search`` returns ``(results, next_cursor)`` for one page, where each result
    is a dict with ``title`` / ``url`` / ``snippet``. Raises :class:`RateLimited`
    on a provider 429.
    """

    def search(self, query: str, cursor: Optional[str] = None):
        raise NotImplementedError


class HttpWebSearchClient(WebSearchClient):
    """Real client over a configurable search provider (lazy ``httpx``).

    Expects ``GET {base}/search?q=<query>&cursor=<c>`` →
    ``{"results":[{"title","url","snippet"}], "next_cursor":<str|null>}``.
    Point it at your provider with ``WEBSEARCH_API_BASE`` / ``WEBSEARCH_API_KEY``;
    subclass and override :meth:`search` if your provider's shape differs.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self.timeout = timeout

    def _base(self) -> str:
        base = self._base_url or os.environ.get("WEBSEARCH_API_BASE")
        if not base:
            raise RuntimeError(
                "WEBSEARCH_API_BASE is not set; cannot call a web-search provider. "
                "Set it (and WEBSEARCH_API_KEY) or inject a client (FakeWebSearchClient in tests)."
            )
        return base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        key = self._api_key or os.environ.get("WEBSEARCH_API_KEY")
        return {"Authorization": "Bearer {0}".format(key)} if key else {}

    def search(self, query: str, cursor: Optional[str] = None):
        from sourcing._http import request_with_retry

        params: Dict[str, Any] = {"q": query}
        if cursor:
            params["cursor"] = cursor
        resp = request_with_retry(
            "{0}/search".format(self._base()),
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 429:
            raise RateLimited(query)
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("results", []) or []), data.get("next_cursor")


class FakeWebSearchClient(WebSearchClient):
    """Deterministic, offline client for tests.

    Built from a ``pages`` map (query -> list of page dicts), each page
    ``{"results": [{"title","url","snippet"}], "next": <cursor or None>}``. Set
    ``rate_limit_after`` to raise :class:`RateLimited` after N successful calls.
    """

    def __init__(
        self,
        pages: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        rate_limit_after: Optional[int] = None,
    ) -> None:
        self.pages = pages or {}
        self.rate_limit_after = rate_limit_after
        self.search_calls = 0

    def search(self, query: str, cursor: Optional[str] = None):
        if self.rate_limit_after is not None and self.search_calls >= self.rate_limit_after:
            raise RateLimited(query)
        self.search_calls += 1
        pages = self.pages.get(query, [])
        idx = int(cursor) if cursor else 0
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        return list(page.get("results", [])), page.get("next")


# ---------------------------------------------------------------------------
# Lead extraction from a search result
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Indian mobile in free text: optional +91/0 prefix, then a 6-9 lead and 10
# digits written contiguously OR in the common 5+5 grouping ("98765 43210").
_PHONE_RE = re.compile(r"(?:(?:\+?91|0)[\-\s]?)?[6-9]\d{4}[\-\s]?\d{5}")

_SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "in.linkedin.com": "linkedin",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "m.facebook.com": "facebook",
}


def _platform_and_handle(url: str) -> Tuple[Optional[str], Optional[str], bool]:
    """From a result URL return ``(platform, handle, is_facebook_page)``.

    ``handle`` is the first meaningful path segment for social hosts; for
    Facebook we flag it so the caller routes the URL into the ``page`` identity
    (the strong key) rather than a handle. Non-social hosts return all-None.
    """
    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
    except ValueError:
        return None, None, False
    host = parts.netloc.lower()
    platform = _SOCIAL_HOSTS.get(host)
    if not platform:
        return None, None, False
    if platform == "facebook":
        return "facebook", None, True
    segs = [s for s in parts.path.split("/") if s]
    # Skip channel-chrome prefixes (youtube /channel/<id>, linkedin /in/<slug>).
    prefixes = {"in", "company", "channel", "c", "user", "pub", "profile"}
    meaningful = [s for s in segs if s not in prefixes]
    handle = (meaningful[0] if meaningful else (segs[-1] if segs else None))
    return platform, handle, False


def result_to_candidate(
    result: Dict[str, Any], niche_hint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Turn one search result into a loader candidate, or None if unreachable.

    A result is only kept if it yields at least one identity signal: a Facebook
    page, an email, a phone, or a social handle.
    """
    url = str(result.get("url") or "").strip()
    title = str(result.get("title") or "").strip()
    snippet = str(result.get("snippet") or "")
    text = " ".join([title, snippet])

    platform, handle, is_fb_page = _platform_and_handle(url)

    email_m = _EMAIL_RE.search(text)
    phone_m = _PHONE_RE.search(text)
    email = email_m.group(0) if email_m else None
    phone = phone_m.group(0) if phone_m else None

    page = url if is_fb_page else None

    # Require at least one usable signal.
    if not (page or email or phone or handle):
        return None

    attributes = {
        "advertiser": title,
        "source_url": url,
        "snippet": snippet.strip(),
        "discovered_via": "websearch",
        "enriched": False,
    }
    attributes = {k: v for k, v in attributes.items() if v not in (None, "", [], {})}

    lead_fields = {
        "segment": "creator",
        "platform": platform or "web",
        "source": "websearch",
        "niche": niche_hint,
        "follower_band": None,
        "follower_count": None,
    }

    return {
        "page": page,
        "email": email,
        "phone": phone,
        "handle": handle,
        "attributes": attributes,
        "lead_fields": lead_fields,
        "channels": [],
    }


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

class WebSearchAdapter(SourceAdapter):
    """Source leads from general web search for an approved target_spec."""

    name = "websearch"

    def __init__(
        self,
        client: Optional[WebSearchClient] = None,
        search_budget: int = DEFAULT_SEARCH_BUDGET,
        intent_suffixes: Tuple[str, ...] = INTENT_SUFFIXES,
    ) -> None:
        self.client = client or HttpWebSearchClient()
        self.search_budget = search_budget
        self.intent_suffixes = intent_suffixes

    def _queries(self, keywords: List[str]) -> List[str]:
        """Expand each keyword with the contact-intent suffixes (deduped, order-stable)."""
        out: List[str] = []
        seen: "set[str]" = set()
        for kw in keywords:
            for suffix in self.intent_suffixes:
                q = "{0} {1}".format(kw, suffix).strip()
                if q not in seen:
                    seen.add(q)
                    out.append(q)
        return out

    def run(self, target_spec) -> Iterable[Dict[str, Any]]:
        if not self.require_approved(target_spec):
            logger.info("websearch: spec not approved; sourcing nothing")
            return

        keywords = _spec_keywords(target_spec)
        niche_hint = _spec_niche_hint(target_spec)
        queries = self._queries(keywords)
        cursor = _get_resume_cursor(target_spec)

        start_q_index = cursor.get("query_index", 0)
        page_cursor = cursor.get("page_cursor")

        seen_keys: "set[str]" = set()
        searches_done = 0

        try:
            for q_index in range(start_q_index, len(queries)):
                query = queries[q_index]
                token = page_cursor if q_index == start_q_index else None
                while True:
                    if searches_done >= self.search_budget:
                        _set_resume_cursor(
                            target_spec,
                            {"query_index": q_index, "page_cursor": token},
                            status="budget_paused",
                        )
                        logger.info("websearch: search budget reached; pausing at q_index=%s", q_index)
                        return
                    results, next_token = self.client.search(query, cursor=token)
                    searches_done += 1
                    for result in results:
                        cand = result_to_candidate(result, niche_hint=niche_hint)
                        if cand is None:
                            continue
                        key = _dedup_key(cand)
                        if key is not None and key in seen_keys:
                            continue
                        if key is not None:
                            seen_keys.add(key)
                        yield cand
                    if not next_token:
                        break
                    token = next_token
            _set_resume_cursor(target_spec, None, status="complete")
        except RateLimited:
            _set_resume_cursor(
                target_spec,
                {"query_index": q_index, "page_cursor": token},
                status="rate_limited",
            )
            logger.warning("websearch: rate-limited; resume cursor saved")
            return


def _dedup_key(cand: Dict[str, Any]) -> Optional[str]:
    for f in ("page", "email", "phone", "handle"):
        v = cand.get(f)
        if v:
            return "{0}:{1}".format(f, str(v).strip().lower())
    return None


# ---------------------------------------------------------------------------
# Enrichment fallback (token/credit-frugal): only fill what's MISSING.
#
# Broad web-search discovery is expensive, so the engine doesn't run it for every
# lead. Instead, after the platform adapters return their candidates, only the
# ones missing a reachable contact get a single targeted web lookup to fill the
# gap. These helpers implement that path; harvest_all orchestrates it.
# ---------------------------------------------------------------------------

def needs_enrichment(cand: Dict[str, Any], require=("email", "phone")) -> bool:
    """True if the candidate is missing any required contact field.

    Default: a lead is "incomplete" when it lacks an email OR a phone. Enriching
    these (and skipping the already-complete ones) is what keeps web-search spend
    proportional to the gaps, not the whole list.
    """
    return any(not cand.get(f) for f in require)


def _search_subject(cand: Dict[str, Any]) -> Optional[str]:
    """The best human/handle string to search the web for this lead by."""
    attrs = cand.get("attributes") or {}
    return (
        attrs.get("advertiser")
        or attrs.get("full_name")
        or cand.get("handle")
        or attrs.get("username")
        or attrs.get("public_id")
    )


def enrich_candidate(
    client: "WebSearchClient",
    cand: Dict[str, Any],
    niche_hint: Optional[str] = None,
) -> bool:
    """Run ONE targeted search to fill a candidate's missing email/phone in place.

    Returns True if anything was filled. Never overwrites an existing value —
    enrichment is strictly additive. A single search page is read (cost = 1).
    """
    subject = _search_subject(cand)
    if not subject:
        return False
    platform = (cand.get("lead_fields") or {}).get("platform") or ""
    query = " ".join(x for x in [str(subject), str(platform), "email contact"] if x).strip()

    results, _ = client.search(query)  # one page; caller bounds total calls
    filled = False
    for r in results:
        text = " ".join([str(r.get("title") or ""), str(r.get("snippet") or "")])
        if not cand.get("email"):
            m = _EMAIL_RE.search(text)
            if m:
                cand["email"] = m.group(0)
                filled = True
        if not cand.get("phone"):
            m = _PHONE_RE.search(text)
            if m:
                cand["phone"] = m.group(0)
                filled = True
        if cand.get("email") and cand.get("phone"):
            break
    if filled:
        attrs = cand.setdefault("attributes", {})
        attrs["contact_enriched_via"] = "websearch"
    return filled


def enrich_candidates(
    candidates: List[Dict[str, Any]],
    client: Optional["WebSearchClient"] = None,
    budget: int = 25,
    require=("email", "phone"),
) -> int:
    """Fill missing contacts on incomplete candidates, in place, up to ``budget``.

    Only candidates that :func:`needs_enrichment` (missing email/phone) consume a
    lookup; complete ones are skipped for free. Stops after ``budget`` searches so
    web-search spend is bounded per run. Returns how many candidates got a field
    filled. If the provider isn't configured / errors, enrichment aborts cleanly
    (the discovery results are still returned untouched).
    """
    if budget <= 0:
        return 0
    if client is None:
        client = HttpWebSearchClient()

    used = 0
    filled = 0
    for cand in candidates:
        if used >= budget:
            break
        if not needs_enrichment(cand, require):
            continue
        used += 1
        try:
            if enrich_candidate(client, cand):
                filled += 1
        except Exception as exc:  # provider 429 / not configured / network
            logger.warning("websearch enrichment aborted after %d lookups: %s", used, exc)
            break
    logger.info("websearch enrichment: %d lookup(s), %d lead(s) filled", used, filled)
    return filled


# ---------------------------------------------------------------------------
# spec accessors (work on a TargetSpec or a plain dict)
# ---------------------------------------------------------------------------

def _spec_attributes(target_spec) -> Dict[str, Any]:
    attrs = getattr(target_spec, "attributes", None)
    if attrs is None and isinstance(target_spec, dict):
        attrs = target_spec.get("attributes")
    if not isinstance(attrs, dict):
        attrs = {}
        if hasattr(target_spec, "attributes"):
            target_spec.attributes = attrs
        elif isinstance(target_spec, dict):
            target_spec["attributes"] = attrs
    return attrs


def _spec_keywords(target_spec) -> List[str]:
    if hasattr(target_spec, "keywords"):
        kws = target_spec.keywords()
    elif isinstance(target_spec, dict):
        kws = target_spec.get("expanded_keywords") or target_spec.get("seed_keywords") or []
    else:
        kws = []
    return [k for k in kws if k]


def _spec_niche_hint(target_spec) -> Optional[str]:
    filters = getattr(target_spec, "filters", None)
    if filters is None and isinstance(target_spec, dict):
        filters = target_spec.get("filters")
    if isinstance(filters, dict):
        segs = filters.get("segments") or []
        if segs and isinstance(segs[0], dict):
            return segs[0].get("name")
    return None


_RESUME_KEY = "websearch_resume"


def _get_resume_cursor(target_spec) -> Dict[str, Any]:
    cursor = _spec_attributes(target_spec).get(_RESUME_KEY) or {}
    return cursor if isinstance(cursor, dict) else {}


def _set_resume_cursor(target_spec, cursor: Optional[Dict[str, Any]], status: str) -> None:
    attrs = _spec_attributes(target_spec)
    attrs["websearch_status"] = status
    if cursor is None:
        attrs.pop(_RESUME_KEY, None)
    else:
        attrs[_RESUME_KEY] = cursor


# Register so the engine can discover this adapter by name / via enabled_adapters().
from sourcing.base import register as _register  # noqa: E402

_register("websearch", WebSearchAdapter)
