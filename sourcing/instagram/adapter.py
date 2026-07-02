"""Instagram source adapter (L1 / M1).

Implements :class:`sourcing.base.SourceAdapter`: from an APPROVED target_spec it
searches Instagram for creator profiles by keyword/hashtag, fetches their
profile details, and yields candidate dicts the L0 loader/resolver consumes
(``platform='instagram'``, carrying ``target_spec_id``). No DB access here — the
loader resolves the candidates, exactly like the YouTube and Meta adapters.

ToS / compliance (see ``README.md`` and PRD §13)
------------------------------------------------
Instagram has no public discovery API: the official Graph API only reaches
accounts that have authorized your app, so it is no use for cold creator
discovery. Real sourcing therefore goes through a third-party provider
(Apify, RapidAPI, ScrapingBee, …). This adapter does NOT hard-code one — it
talks to a small :class:`InstagramClient` seam so the provider is swappable and
the whole thing is unit-testable offline with :class:`FakeInstagramClient`.
Sourcing only collects public profile data; outreach stays opt-in-led and is
handled by the dispatch layer, never here.

Two seams (mirrors the YouTube adapter):

  * :class:`InstagramClient` — abstract provider client.
      - :class:`HttpInstagramClient` — real impl over a configurable provider
        endpoint (``INSTAGRAM_API_BASE`` / ``INSTAGRAM_API_KEY``). ``httpx`` is
        imported lazily and the key is read at call time, so importing this
        module never needs httpx or any credentials.
      - :class:`FakeInstagramClient` — deterministic, offline, for tests. Can be
        told to raise :class:`RateLimited` after N search pages to exercise the
        resume path.

RATE-LIMIT / RESUME HANDLING (mirrors the YouTube quota story):
  * Each spec has a per-run SEARCH BUDGET (max search pages). We page
    conservatively and dedupe usernames across pages and keywords.
  * On a provider 429 / rate-limit, we do NOT fail or drop work: we mark the
    spec *partially sourced* and stash a RESUME CURSOR (the next page cursor +
    which keyword we were on) in the spec's ``attributes``, then stop cleanly.
    A later run resumes from the cursor instead of restarting.
  * The adapter yields whatever candidates it collected before the limit hit —
    partial progress is kept, never thrown away.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from sourcing.base import SourceAdapter

logger = logging.getLogger("sourcing.instagram")

#: search paging defaults. Conservative paging keeps us inside provider limits.
DEFAULT_SEARCH_BUDGET = 5          # max search pages per run per spec
PROFILES_BATCH_SIZE = 50           # how many usernames we resolve per get_profiles call


class RateLimited(Exception):
    """Raised by a client when the provider returns 429 / a rate-limit.

    The adapter catches this, records a resume cursor, and stops cleanly — it is
    control flow, not a failure.
    """


# ---------------------------------------------------------------------------
# Client interface + implementations
# ---------------------------------------------------------------------------

class InstagramClient:
    """Abstract Instagram provider client.

    ``search_users`` returns ``(usernames, next_cursor)`` for one search page.
    ``get_profiles`` returns full profile resources for a batch of usernames.
    Both raise :class:`RateLimited` on a provider 429 / rate-limit.
    """

    def search_users(self, query: str, cursor: Optional[str] = None):
        raise NotImplementedError

    def get_profiles(self, usernames: List[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError


class HttpInstagramClient(InstagramClient):
    """Real client over a configurable third-party provider (lazy ``httpx``).

    Provider-agnostic on purpose: point it at whichever Instagram scraping API
    you use by setting ``INSTAGRAM_API_BASE`` (and ``INSTAGRAM_API_KEY``). The
    two methods expect the provider to expose:

      * ``GET {base}/search?q=<kw>&cursor=<c>`` -> ``{"users": [{"username": ...}],
        "next_cursor": <str|null>}``
      * ``GET {base}/profile?username=<u>``      -> a profile object (see
        :func:`profile_to_candidate` for the fields we read).

    If your provider's shape differs, subclass and override ``_map_search`` /
    ``get_profiles`` — the adapter only depends on the two method signatures, not
    on any particular vendor.
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
        base = self._base_url or os.environ.get("INSTAGRAM_API_BASE")
        if not base:
            raise RuntimeError(
                "INSTAGRAM_API_BASE is not set; cannot call an Instagram provider. "
                "Set it (and INSTAGRAM_API_KEY) in the environment or pass base_url=..., "
                "or inject a client (e.g. FakeInstagramClient in tests)."
            )
        return base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        key = self._api_key or os.environ.get("INSTAGRAM_API_KEY")
        return {"Authorization": "Bearer {0}".format(key)} if key else {}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        # Route through the shared retry/backoff/proxy/UA helper. It already
        # retries transient 429/5xx/network errors; a 429 that survives every
        # retry is mapped to RateLimited so the adapter saves a resume cursor.
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

    def search_users(self, query: str, cursor: Optional[str] = None):
        params: Dict[str, Any] = {"q": query}
        if cursor:
            params["cursor"] = cursor
        data = self._get("search", params)
        usernames = []
        for u in data.get("users", []) or []:
            name = u.get("username") if isinstance(u, dict) else u
            if name:
                usernames.append(name)
        return usernames, data.get("next_cursor")

    def get_profiles(self, usernames: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for username in usernames:
            data = self._get("profile", {"username": username})
            # Providers commonly wrap the profile under "data"/"user"; unwrap.
            profile = data.get("data") or data.get("user") or data
            if isinstance(profile, dict):
                out.append(profile)
        return out


#: Instagram URL path prefixes that are NOT usernames (post/reel/chrome pages).
_RESERVED_IG = {
    "p", "reel", "reels", "explore", "tv", "stories", "accounts", "about",
    "directory", "developer", "legal", "privacy", "terms", "help", "web", "s",
}


def _ig_handle_from_url(url: str) -> Optional[str]:
    """Extract the @username from a public instagram.com result URL, or None."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url if "://" in url else "http://" + url)
    except ValueError:
        return None
    if "instagram.com" not in parts.netloc.lower():
        return None
    segs = [s for s in parts.path.split("/") if s]
    if not segs:
        return None
    return segs[0].lstrip("@").lower()


class PublicSearchInstagramClient(InstagramClient):
    """Key-less Instagram discovery via public web search (DuckDuckGo).

    Compliant by construction: it reads only public search-engine results —
    no Instagram login, no private/Graph API, no scraping behind authentication.
    Fidelity is lower than a paid provider (profile detail is mined from the
    search title/snippet, not a full profile resource), but it makes Instagram
    sourcing work TODAY with zero credentials. Set ``INSTAGRAM_API_BASE`` to a
    provider for richer profiles.

    It caches each page's results by username so :meth:`get_profiles` can build a
    lightweight profile from the same search hit the username came from.
    """

    def __init__(self, timeout: float = 20.0) -> None:
        from sourcing.websearch.adapter import DuckDuckGoSearchClient

        self._ddg = DuckDuckGoSearchClient(timeout=timeout)
        self._cache: Dict[str, Dict[str, Any]] = {}

    def search_users(self, query: str, cursor: Optional[str] = None):
        from sourcing.websearch.adapter import RateLimited as _WSRate

        q = "site:instagram.com {0}".format(query)
        try:
            results, next_cursor = self._ddg.search(q, cursor=cursor)
        except _WSRate:
            raise RateLimited("search")
        usernames: List[str] = []
        for r in results:
            u = _ig_handle_from_url(str(r.get("url") or ""))
            if not u or u in _RESERVED_IG:
                continue
            self._cache.setdefault(u, r)
            usernames.append(u)
        return usernames, next_cursor

    def get_profiles(self, usernames: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for u in usernames:
            r = self._cache.get(u, {})
            title = str(r.get("title") or "")
            snippet = str(r.get("snippet") or "")
            # IG titles look like "Full Name (@handle) • Instagram photos and videos".
            full_name = title.split("(@")[0].split("•")[0].strip() or u
            out.append({
                "username": u,
                "full_name": full_name,
                "biography": snippet,
                "business_email": _email_from_text(snippet),
                "external_url": str(r.get("url") or ""),
            })
        return out


def default_instagram_client() -> InstagramClient:
    """Pick the Instagram backend: a configured paid provider if present, else
    the key-less public web-search client (compliant, works out of the box)."""
    if os.environ.get("INSTAGRAM_API_BASE"):
        return HttpInstagramClient()
    return PublicSearchInstagramClient()


class FakeInstagramClient(InstagramClient):
    """Deterministic, offline client for tests.

    Built from a ``pages`` map (query -> list of page dicts), where each page is
    ``{"usernames": [...], "next": <cursor or None>}``, plus a ``profiles`` map
    (username -> full profile resource). Set ``rate_limit_after`` to make
    ``search_users`` raise :class:`RateLimited` after N successful search calls,
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

    def search_users(self, query: str, cursor: Optional[str] = None):
        if self.rate_limit_after is not None and self.search_calls >= self.rate_limit_after:
            raise RateLimited("search")
        self.search_calls += 1
        pages = self.pages.get(query, [])
        # cursor is the 1-based index of the page to return (as a string);
        # None means the first page.
        idx = int(cursor) if cursor else 0
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        return list(page.get("usernames", [])), page.get("next")

    def get_profiles(self, usernames: List[str]) -> List[Dict[str, Any]]:
        return [self.profiles[u] for u in usernames if u in self.profiles]


# ---------------------------------------------------------------------------
# Follower band + field derivation
# ---------------------------------------------------------------------------

def follower_band(count: Optional[int]) -> Optional[str]:
    """Map a follower count to a coarse band (matches L0 / YouTube conventions)."""
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
    """Pull the first email out of a profile's biography text."""
    m = _EMAIL_IN_TEXT.search(text or "")
    return m.group(0) if m else None


def _first(profile: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first present, non-empty value among ``keys`` (provider shims
    name the same field differently, e.g. follower_count vs edge_followed_by)."""
    for k in keys:
        v = profile.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    # Some providers nest the count under {"count": N}.
    if isinstance(value, dict):
        value = value.get("count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def profile_to_candidate(
    profile: Dict[str, Any], niche_hint: Optional[str] = None
) -> Dict[str, Any]:
    """Turn an Instagram profile resource into a loader candidate dict.

    Carries: the ``handle`` blocking key (username — the stable IG identity), an
    ``email`` (business email or one parsed from the bio), a ``phone`` (business
    number; the loader's clean_phone keeps only valid Indian mobiles), scalar
    lead fields (``follower_band``, ``follower_count``, ``niche``,
    ``platform='instagram'``, ``source='instagram'``), and rich signals in
    ``attributes`` so L3 personalization can name a real signal.
    """
    username = _first(profile, "username", "handle", "user_name")
    full_name = _first(profile, "full_name", "fullName", "name") or ""
    biography = _first(profile, "biography", "bio", "description") or ""
    external_url = _first(profile, "external_url", "externalUrl", "website") or ""
    category = _first(profile, "category_name", "category", "business_category_name")

    followers = _to_int(
        _first(profile, "follower_count", "followers", "edge_followed_by", "followersCount")
    )

    email = (
        _first(profile, "business_email", "public_email", "email")
        or _email_from_text(biography)
    )
    phone = _first(
        profile, "business_phone_number", "public_phone_number", "contact_phone_number", "phone"
    )

    ig_id = _first(profile, "id", "pk", "user_id")

    attributes = {
        "advertiser": full_name or username,
        "username": username,
        "full_name": full_name,
        "biography": biography.strip() if isinstance(biography, str) else biography,
        "external_url": external_url,
        "category": category,
        "ig_id": ig_id,
        "is_business": _first(profile, "is_business_account", "is_business", "isBusiness"),
        "is_verified": _first(profile, "is_verified", "verified", "isVerified"),
        "post_count": _to_int(_first(profile, "media_count", "posts_count", "post_count")),
        "enriched": True,
    }
    attributes = {k: v for k, v in attributes.items() if v not in (None, "", [], {})}

    lead_fields = {
        "segment": "creator",
        "platform": "instagram",
        "source": "instagram",
        "niche": (category.lower() if isinstance(category, str) and category else niche_hint),
        "follower_band": follower_band(followers),
        "follower_count": followers,
    }

    return {
        "page": None,
        "email": email,
        "phone": phone,
        "handle": username,
        "attributes": attributes,
        "lead_fields": lead_fields,
        "channels": [],
    }


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

class InstagramAdapter(SourceAdapter):
    """Source creators from Instagram for an approved target_spec.

    Yields candidate dicts (no DB writes). Rate-limit-aware: stops cleanly and
    records a resume cursor on the spec's ``attributes`` when the provider limit
    is hit.
    """

    name = "instagram"

    def __init__(
        self,
        client: Optional[InstagramClient] = None,
        search_budget: int = DEFAULT_SEARCH_BUDGET,
    ) -> None:
        # Paid provider if configured, else the key-less public-search client;
        # tests inject a FakeInstagramClient.
        self.client = client or default_instagram_client()
        self.search_budget = search_budget
        # Optional ``skip_known(handle) -> bool`` set by the orchestrator to skip
        # creators already in the DB *before* the costly profile fetch. Default
        # None = fetch everything (no DB coupling here).
        self.skip_known = None

    def run(self, target_spec) -> Iterable[Dict[str, Any]]:
        # GATE: only ever source an approved spec.
        if not self.require_approved(target_spec):
            logger.info("instagram: spec not approved; sourcing nothing")
            return

        keywords = _spec_keywords(target_spec)
        niche_hint = _spec_niche_hint(target_spec)
        cursor = _get_resume_cursor(target_spec)

        # Resume: skip keywords already finished, start mid-keyword at a cursor.
        start_kw_index = cursor.get("keyword_index", 0)
        page_cursor = cursor.get("page_cursor")

        seen_usernames: "set[str]" = set()
        searches_done = 0

        try:
            for kw_index in range(start_kw_index, len(keywords)):
                query = keywords[kw_index]
                token = page_cursor if kw_index == start_kw_index else None
                while True:
                    if searches_done >= self.search_budget:
                        # Budget for this run is spent — record where to resume.
                        _set_resume_cursor(
                            target_spec,
                            {"keyword_index": kw_index, "page_cursor": token},
                            status="budget_paused",
                        )
                        logger.info(
                            "instagram: search budget reached; pausing at kw_index=%s", kw_index
                        )
                        return
                    usernames, next_token = self.client.search_users(query, cursor=token)
                    searches_done += 1
                    # Dedupe usernames across pages and keywords.
                    fresh = [u for u in usernames if u not in seen_usernames]
                    seen_usernames.update(fresh)
                    # COST SAVER: drop creators already in the DB before the
                    # per-profile fetch (only the *fetch* is billed by providers).
                    skip = getattr(self, "skip_known", None)
                    to_fetch = [u for u in fresh if not (skip and skip(u))] if skip else fresh
                    for batch in _batched(to_fetch, PROFILES_BATCH_SIZE):
                        for profile in self.client.get_profiles(batch):
                            cand = profile_to_candidate(profile, niche_hint=niche_hint)
                            if cand.get("handle"):
                                yield cand
                    if not next_token:
                        break  # keyword exhausted, move to the next
                    token = next_token
            # Completed every keyword within budget — clear any resume cursor.
            _set_resume_cursor(target_spec, None, status="complete")
        except RateLimited:
            # Provider 429 mid-run: keep what we yielded, record the cursor, mark
            # partially sourced, and stop cleanly (NO exception escapes).
            _set_resume_cursor(
                target_spec,
                {"keyword_index": kw_index, "page_cursor": token},
                status="rate_limited",
            )
            logger.warning(
                "instagram: rate-limited; marked spec partially-sourced, resume cursor saved"
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
        # Pin it back so the resume cursor we write is observable by the caller.
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


_RESUME_KEY = "instagram_resume"


def _get_resume_cursor(target_spec) -> Dict[str, Any]:
    cursor = _spec_attributes(target_spec).get(_RESUME_KEY) or {}
    return cursor if isinstance(cursor, dict) else {}


def _set_resume_cursor(target_spec, cursor: Optional[Dict[str, Any]], status: str) -> None:
    """Record (or clear) the resume cursor + sourcing status on the spec.

    Writes to the in-memory spec's ``attributes`` so the orchestration layer can
    persist it back to ``target_specs.attributes``. We never touch the DB here —
    keeping the adapter DB-free and unit-testable.
    """
    attrs = _spec_attributes(target_spec)
    attrs["instagram_status"] = status
    if cursor is None:
        attrs.pop(_RESUME_KEY, None)
    else:
        attrs[_RESUME_KEY] = cursor


def _batched(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# Register so the engine can discover this adapter by name / via enabled_adapters().
from sourcing.base import register as _register  # noqa: E402

_register("instagram", InstagramAdapter)
