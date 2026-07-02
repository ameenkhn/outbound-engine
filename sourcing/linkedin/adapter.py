"""LinkedIn source adapter (L1 / M1).

Implements :class:`sourcing.base.SourceAdapter`: from an APPROVED target_spec it
searches LinkedIn for ICP people/creators by keyword, fetches their public
profile details, and yields candidate dicts the L0 loader/resolver consumes
(``platform='linkedin'``, carrying ``target_spec_id``). No DB access here — the
loader resolves the candidates, exactly like the YouTube, Meta and Instagram
adapters.

ToS / compliance (see ``README.md`` and PRD §13)
------------------------------------------------
LinkedIn is **sourcing / enrichment ONLY** by default. Automated connection
requests and DMs violate LinkedIn's ToS, so outreach over LinkedIn stays
manual / human-in-the-loop until a compliant path exists — this adapter never
sends anything, it only surfaces leads. As with Instagram, LinkedIn has no open
people-search API, so real sourcing runs through a third-party provider (a
LinkedIn search/enrichment API). The adapter talks to a small
:class:`LinkedInClient` seam so the provider is swappable and the whole thing is
unit-testable offline with :class:`FakeLinkedInClient`.

Two seams (mirrors the YouTube / Instagram adapters):

  * :class:`LinkedInClient` — abstract provider client.
      - :class:`HttpLinkedInClient` — real impl over a configurable provider
        endpoint (``LINKEDIN_API_BASE`` / ``LINKEDIN_API_KEY``). ``httpx`` is
        imported lazily and the key is read at call time, so importing this
        module never needs httpx or any credentials.
      - :class:`FakeLinkedInClient` — deterministic, offline, for tests. Can be
        told to raise :class:`RateLimited` after N search pages to exercise the
        resume path.

RATE-LIMIT / RESUME HANDLING mirrors the other adapters: a per-run search
budget, username/slug dedupe across pages, and a resume cursor stashed on the
spec's ``attributes`` on a 429 so a later run continues instead of restarting.
Partial progress is always kept.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from sourcing.base import SourceAdapter

logger = logging.getLogger("sourcing.linkedin")

#: search paging defaults. Conservative paging keeps us inside provider limits.
DEFAULT_SEARCH_BUDGET = 5          # max search pages per run per spec
PROFILES_BATCH_SIZE = 25           # how many profiles we resolve per get_profiles call


class RateLimited(Exception):
    """Raised by a client when the provider returns 429 / a rate-limit.

    The adapter catches this, records a resume cursor, and stops cleanly — it is
    control flow, not a failure.
    """


# ---------------------------------------------------------------------------
# Client interface + implementations
# ---------------------------------------------------------------------------

class LinkedInClient:
    """Abstract LinkedIn provider client.

    ``search_people`` returns ``(slugs, next_cursor)`` for one search page, where
    a *slug* is the stable public-profile identifier (the ``/in/<slug>`` segment
    or a vanity username). ``get_profiles`` returns full profile resources for a
    batch of slugs. Both raise :class:`RateLimited` on a provider 429.
    """

    def search_people(self, query: str, cursor: Optional[str] = None):
        raise NotImplementedError

    def get_profiles(self, slugs: List[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError


class HttpLinkedInClient(LinkedInClient):
    """Real client over a configurable third-party provider (lazy ``httpx``).

    Provider-agnostic on purpose: point it at whichever LinkedIn search/enrichment
    API you use by setting ``LINKEDIN_API_BASE`` (and ``LINKEDIN_API_KEY``). The
    two methods expect the provider to expose:

      * ``GET {base}/search?q=<kw>&cursor=<c>`` -> ``{"people": [{"public_id": ...}],
        "next_cursor": <str|null>}``
      * ``GET {base}/profile?public_id=<slug>``  -> a profile object (see
        :func:`profile_to_candidate` for the fields we read).

    If your provider's shape differs, subclass and override the methods — the
    adapter only depends on the two signatures, not on any particular vendor.
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
        base = self._base_url or os.environ.get("LINKEDIN_API_BASE")
        if not base:
            raise RuntimeError(
                "LINKEDIN_API_BASE is not set; cannot call a LinkedIn provider. "
                "Set it (and LINKEDIN_API_KEY) in the environment or pass base_url=..., "
                "or inject a client (e.g. FakeLinkedInClient in tests)."
            )
        return base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        key = self._api_key or os.environ.get("LINKEDIN_API_KEY")
        return {"Authorization": "Bearer {0}".format(key)} if key else {}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # Route through the shared retry/backoff/proxy/UA helper (see sourcing._http).
        from sourcing._http import request_with_retry

        resp = request_with_retry(
            "{0}/{1}".format(self._base(), path),
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 429:
            raise RateLimited(path)
        resp.raise_for_status()
        return resp.json()

    def search_people(self, query: str, cursor: Optional[str] = None):
        params: Dict[str, Any] = {"q": query}
        if cursor:
            params["cursor"] = cursor
        data = self._get("search", params)
        slugs = []
        for p in data.get("people", []) or []:
            slug = (
                p.get("public_id") or p.get("public_identifier") or p.get("username")
                if isinstance(p, dict) else p
            )
            if slug:
                slugs.append(slug)
        return slugs, data.get("next_cursor")

    def get_profiles(self, slugs: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for slug in slugs:
            data = self._get("profile", {"public_id": slug})
            profile = data.get("data") or data.get("profile") or data
            if isinstance(profile, dict):
                out.append(profile)
        return out


def _li_slug_from_url(url: str) -> Optional[str]:
    """Extract the /in/<slug> public-profile id from a linkedin.com URL, or None."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
    except ValueError:
        return None
    if "linkedin.com" not in parts.netloc.lower():
        return None
    segs = [s for s in parts.path.split("/") if s]
    if "in" in segs:
        i = segs.index("in")
        if i + 1 < len(segs):
            return segs[i + 1].lower()
    return None


class PublicSearchLinkedInClient(LinkedInClient):
    """Key-less LinkedIn discovery via public web search (DuckDuckGo).

    Compliant by construction: it reads only public search-engine results — no
    LinkedIn login, no private API, no automation against LinkedIn itself, and it
    never sends anything (LinkedIn stays sourcing-only). Profile detail is mined
    from the public search title/snippet; set ``LINKEDIN_API_BASE`` for a richer
    provider. Caches page results by slug so :meth:`get_profiles` reuses the hit.
    """

    def __init__(self, timeout: float = 20.0) -> None:
        from sourcing.websearch.adapter import DuckDuckGoSearchClient

        self._ddg = DuckDuckGoSearchClient(timeout=timeout)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def search_people(self, query: str, cursor: Optional[str] = None):
        from sourcing.websearch.adapter import RateLimited as _WSRate

        q = "site:linkedin.com/in {0}".format(query)
        try:
            results, next_cursor = self._ddg.search(q, cursor=cursor)
        except _WSRate:
            raise RateLimited("search")
        slugs: List[str] = []
        for r in results:
            slug = _li_slug_from_url(str(r.get("url") or ""))
            if not slug:
                continue
            self._cache.setdefault(slug, r)
            slugs.append(slug)
        return slugs, next_cursor

    def get_profiles(self, slugs: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for slug in slugs:
            r = self._cache.get(slug, {})
            title = str(r.get("title") or "")
            snippet = str(r.get("snippet") or "")
            # LI titles look like "Full Name - Headline - Company | LinkedIn".
            head = title.split("|")[0].strip()
            bits = [b.strip() for b in head.split(" - ") if b.strip()]
            full_name = bits[0] if bits else slug
            headline = " - ".join(bits[1:]) if len(bits) > 1 else ""
            out.append({
                "public_id": slug,
                "full_name": full_name,
                "headline": headline,
                "summary": snippet,
                "email": _email_from_text(snippet),
                "profile_url": str(r.get("url") or ""),
            })
        return out


def default_linkedin_client() -> LinkedInClient:
    """Pick the LinkedIn backend: a configured paid provider if present, else the
    key-less public web-search client (compliant, sourcing-only, works today)."""
    if os.environ.get("LINKEDIN_API_BASE"):
        return HttpLinkedInClient()
    return PublicSearchLinkedInClient()


class FakeLinkedInClient(LinkedInClient):
    """Deterministic, offline client for tests.

    Built from a ``pages`` map (query -> list of page dicts), where each page is
    ``{"slugs": [...], "next": <cursor or None>}``, plus a ``profiles`` map
    (slug -> full profile resource). Set ``rate_limit_after`` to make
    ``search_people`` raise :class:`RateLimited` after N successful search calls,
    exercising the resume path.
    """

    def __init__(
        self,
        pages: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        rate_limit_after: Optional[int] = None,
    ) -> None:
        self.pages = pages or {}
        self.profiles = profiles or {}
        self.rate_limit_after = rate_limit_after
        self.search_calls = 0

    def search_people(self, query: str, cursor: Optional[str] = None):
        if self.rate_limit_after is not None and self.search_calls >= self.rate_limit_after:
            raise RateLimited("search")
        self.search_calls += 1
        pages = self.pages.get(query, [])
        idx = int(cursor) if cursor else 0
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        return list(page.get("slugs", [])), page.get("next")

    def get_profiles(self, slugs: List[str]) -> List[Dict[str, Any]]:
        return [self.profiles[s] for s in slugs if s in self.profiles]


# ---------------------------------------------------------------------------
# Follower band + field derivation
# ---------------------------------------------------------------------------

def follower_band(count: Optional[int]) -> Optional[str]:
    """Map a follower/connection count to a coarse band (L0 conventions)."""
    if count is None:
        return None
    try:
        n = int(count)
    except (TypeError, ValueError):
        return None
    if n < 1000:
        return "nano"          # <1k
    if n < 10000:
        return "micro"         # 1k–10k
    if n < 100000:
        return "mid"           # 10k–100k
    if n < 1000000:
        return "macro"         # 100k–1M
    return "mega"              # 1M+


_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _email_from_text(text: str) -> Optional[str]:
    m = _EMAIL_IN_TEXT.search(text or "")
    return m.group(0) if m else None


def _first(profile: Dict[str, Any], *keys: str) -> Optional[Any]:
    for k in keys:
        v = profile.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def profile_to_candidate(
    profile: Dict[str, Any], niche_hint: Optional[str] = None
) -> Dict[str, Any]:
    """Turn a LinkedIn profile resource into a loader candidate dict.

    Carries: the ``handle`` blocking key (public profile slug — the stable
    LinkedIn identity, stored as a 'linkedin' channel by the resolver), an
    ``email`` if the provider exposes a public/contact one, scalar lead fields
    (``follower_band``, ``follower_count``, ``niche`` from industry,
    ``platform='linkedin'``, ``source='linkedin'``), and rich signals in
    ``attributes`` (headline, company, location, industry) so L3 personalization
    can name a real signal.
    """
    slug = _first(profile, "public_id", "public_identifier", "username", "slug")
    full_name = (
        _first(profile, "full_name", "name")
        or " ".join(
            x for x in [_first(profile, "first_name", "firstName") or "",
                        _first(profile, "last_name", "lastName") or ""] if x
        ).strip()
    )
    headline = _first(profile, "headline", "occupation", "title") or ""
    summary = _first(profile, "summary", "about", "bio") or ""
    company = _first(profile, "current_company", "company", "company_name")
    location = _first(profile, "location", "geo", "city")
    industry = _first(profile, "industry", "industry_name")

    followers = _to_int(
        _first(profile, "follower_count", "followers", "connections", "connection_count")
    )

    email = (
        _first(profile, "email", "public_email", "contact_email")
        or _email_from_text(summary)
    )

    profile_url = _first(profile, "profile_url", "url", "linkedin_url")

    attributes = {
        "advertiser": full_name or slug,
        "public_id": slug,
        "full_name": full_name,
        "headline": headline.strip() if isinstance(headline, str) else headline,
        "summary": summary.strip() if isinstance(summary, str) else summary,
        "company": company,
        "location": location,
        "industry": industry,
        "profile_url": profile_url,
        "is_verified": _first(profile, "is_verified", "verified"),
        "enriched": True,
    }
    attributes = {k: v for k, v in attributes.items() if v not in (None, "", [], {})}

    lead_fields = {
        "segment": "creator",
        "platform": "linkedin",
        "source": "linkedin",
        "niche": (industry.lower() if isinstance(industry, str) and industry else niche_hint),
        "follower_band": follower_band(followers),
        "follower_count": followers,
    }

    # The slug is the stable LinkedIn identity. We carry it as the social
    # ``handle`` blocking key (resolver stores it as a 'linkedin' channel — the
    # only social channel_type in the frozen enum).
    return {
        "page": None,
        "email": email,
        "phone": _first(profile, "phone", "phone_number"),
        "handle": slug,
        "attributes": attributes,
        "lead_fields": lead_fields,
        "channels": [],
    }


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

class LinkedInAdapter(SourceAdapter):
    """Source people/creators from LinkedIn for an approved target_spec.

    Yields candidate dicts (no DB writes, no outreach — sourcing only).
    Rate-limit-aware: stops cleanly and records a resume cursor on the spec's
    ``attributes`` when the provider limit is hit.
    """

    name = "linkedin"

    def __init__(
        self,
        client: Optional[LinkedInClient] = None,
        search_budget: int = DEFAULT_SEARCH_BUDGET,
    ) -> None:
        # Paid provider if configured, else the key-less public-search client;
        # tests inject a FakeLinkedInClient.
        self.client = client or default_linkedin_client()
        self.search_budget = search_budget
        # Optional ``skip_known(handle) -> bool`` set by the orchestrator to skip
        # people already in the DB *before* the costly profile fetch.
        self.skip_known = None

    def run(self, target_spec) -> Iterable[Dict[str, Any]]:
        # GATE: only ever source an approved spec.
        if not self.require_approved(target_spec):
            logger.info("linkedin: spec not approved; sourcing nothing")
            return

        keywords = _spec_keywords(target_spec)
        niche_hint = _spec_niche_hint(target_spec)
        cursor = _get_resume_cursor(target_spec)

        start_kw_index = cursor.get("keyword_index", 0)
        page_cursor = cursor.get("page_cursor")

        seen_slugs: "set[str]" = set()
        searches_done = 0

        try:
            for kw_index in range(start_kw_index, len(keywords)):
                query = keywords[kw_index]
                token = page_cursor if kw_index == start_kw_index else None
                while True:
                    if searches_done >= self.search_budget:
                        _set_resume_cursor(
                            target_spec,
                            {"keyword_index": kw_index, "page_cursor": token},
                            status="budget_paused",
                        )
                        logger.info(
                            "linkedin: search budget reached; pausing at kw_index=%s", kw_index
                        )
                        return
                    slugs, next_token = self.client.search_people(query, cursor=token)
                    searches_done += 1
                    fresh = [s for s in slugs if s not in seen_slugs]
                    seen_slugs.update(fresh)
                    # COST SAVER: drop people already in the DB before fetching
                    # their full profile (only the fetch is billed).
                    skip = getattr(self, "skip_known", None)
                    to_fetch = [s for s in fresh if not (skip and skip(s))] if skip else fresh
                    for batch in _batched(to_fetch, PROFILES_BATCH_SIZE):
                        for profile in self.client.get_profiles(batch):
                            cand = profile_to_candidate(profile, niche_hint=niche_hint)
                            if cand.get("handle"):
                                yield cand
                    if not next_token:
                        break
                    token = next_token
            _set_resume_cursor(target_spec, None, status="complete")
        except RateLimited:
            _set_resume_cursor(
                target_spec,
                {"keyword_index": kw_index, "page_cursor": token},
                status="rate_limited",
            )
            logger.warning(
                "linkedin: rate-limited; marked spec partially-sourced, resume cursor saved"
            )
            return


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


_RESUME_KEY = "linkedin_resume"


def _get_resume_cursor(target_spec) -> Dict[str, Any]:
    cursor = _spec_attributes(target_spec).get(_RESUME_KEY) or {}
    return cursor if isinstance(cursor, dict) else {}


def _set_resume_cursor(target_spec, cursor: Optional[Dict[str, Any]], status: str) -> None:
    attrs = _spec_attributes(target_spec)
    attrs["linkedin_status"] = status
    if cursor is None:
        attrs.pop(_RESUME_KEY, None)
    else:
        attrs[_RESUME_KEY] = cursor


def _batched(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# Register so the engine can discover this adapter by name / via enabled_adapters().
from sourcing.base import register as _register  # noqa: E402

_register("linkedin", LinkedInAdapter)
