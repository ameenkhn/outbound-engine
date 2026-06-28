"""YouTube Data API v3 source adapter (L1 / M1).

Implements :class:`sourcing.base.SourceAdapter`: from an APPROVED target_spec it
searches YouTube for channels by keyword, fetches their stats/topics/about, and
yields candidate dicts the L0 loader/resolver consumes (``platform='youtube'``,
carrying ``target_spec_id``).

Two seams (mirrors the brain / generator pattern):

  * :class:`YouTubeClient` — abstract API client.
      - :class:`HttpxYouTubeClient` — the real impl. ``httpx`` is imported
        lazily, ``YOUTUBE_API_KEY`` is read at call time. Importing this module
        never needs httpx or a key.
      - :class:`FakeYouTubeClient` — deterministic, offline, for tests. Can be
        told to raise quotaExceeded after N search pages to exercise the resume
        path.

QUOTA HANDLING (the hard part — YouTube quota is the binding constraint):
  * Each spec has a per-run SEARCH BUDGET (max search.list pages). search.list
    costs 100 units/call, so we page conservatively and cache pages we've seen.
  * On a 403 quotaExceeded, we do NOT fail or drop work: we mark the spec
    *partially sourced* and stash a RESUME CURSOR (the next pageToken + which
    keyword we were on) in the spec's attributes, then stop cleanly. A later run
    resumes from the cursor instead of restarting.
  * The adapter yields whatever candidates it collected before the quota hit —
    partial progress is kept, never thrown away.

Python 3.9 compatible. No DB access here (the loader resolves the candidates).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from sourcing.base import SourceAdapter

logger = logging.getLogger("sourcing.youtube")

#: search.list defaults. Conservative paging keeps us inside the daily quota.
DEFAULT_SEARCH_BUDGET = 5          # max search.list pages per run per spec
SEARCH_PAGE_SIZE = 50             # max allowed by the API
CHANNELS_BATCH_SIZE = 50          # channels.list accepts up to 50 ids/call


class QuotaExceeded(Exception):
    """Raised by a client when the YouTube API returns 403 quotaExceeded.

    The adapter catches this, records a resume cursor, and stops cleanly — it is
    control flow, not a failure.
    """


# ---------------------------------------------------------------------------
# Client interface + implementations
# ---------------------------------------------------------------------------

class YouTubeClient:
    """Abstract YouTube Data API v3 client.

    ``search_channels`` returns ``(channel_ids, next_page_token)`` for one
    search.list page. ``get_channels`` returns full channel resources for a
    batch of ids. Both raise :class:`QuotaExceeded` on 403 quotaExceeded.
    """

    def search_channels(self, query: str, page_token: Optional[str] = None):
        raise NotImplementedError

    def get_channels(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError


class HttpxYouTubeClient(YouTubeClient):
    """Real client over the public Data API v3 (lazy ``httpx``; key at call time)."""

    BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 20.0) -> None:
        self._api_key = api_key
        self.timeout = timeout

    def _key(self) -> str:
        key = self._api_key or os.environ.get("YOUTUBE_API_KEY")
        if not key:
            raise RuntimeError(
                "YOUTUBE_API_KEY is not set; cannot call the YouTube Data API. "
                "Set it in the environment or pass api_key=..."
            )
        return key

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import httpx  # lazy: only needed for a real call

        params = dict(params)
        params["key"] = self._key()
        resp = httpx.get("{0}/{1}".format(self.BASE, path), params=params, timeout=self.timeout)
        if resp.status_code == 403 and _is_quota_error(resp):
            raise QuotaExceeded(path)
        resp.raise_for_status()
        return resp.json()

    def search_channels(self, query: str, page_token: Optional[str] = None):
        params = {
            "part": "snippet",
            "type": "channel",
            "q": query,
            "maxResults": SEARCH_PAGE_SIZE,
            "regionCode": "IN",
        }
        if page_token:
            params["pageToken"] = page_token
        data = self._get("search", params)
        ids = []
        for item in data.get("items", []):
            cid = (item.get("id") or {}).get("channelId") or item.get("snippet", {}).get("channelId")
            if cid:
                ids.append(cid)
        return ids, data.get("nextPageToken")

    def get_channels(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        if not channel_ids:
            return []
        data = self._get(
            "channels",
            {
                "part": "snippet,statistics,topicDetails,brandingSettings",
                "id": ",".join(channel_ids),
                "maxResults": CHANNELS_BATCH_SIZE,
            },
        )
        return data.get("items", [])


def _is_quota_error(resp) -> bool:
    """True if a 403 body looks like a quotaExceeded error."""
    try:
        body = resp.json()
    except Exception:
        return False
    for err in (body.get("error", {}) or {}).get("errors", []) or []:
        if err.get("reason") in ("quotaExceeded", "dailyLimitExceeded"):
            return True
    return False


class FakeYouTubeClient(YouTubeClient):
    """Deterministic, offline client for tests.

    Built from a ``pages`` map (query -> list of page dicts), where each page is
    ``{"channel_ids": [...], "next": <token or None>}``, plus a ``channels`` map
    (channel_id -> full channel resource). Set ``quota_after`` to make
    ``search_channels`` raise :class:`QuotaExceeded` after N successful search
    calls, exercising the resume path.
    """

    def __init__(
        self,
        pages: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        channels: Optional[Dict[str, Dict[str, Any]]] = None,
        quota_after: Optional[int] = None,
    ) -> None:
        self.pages = pages or {}
        self.channels = channels or {}
        self.quota_after = quota_after
        self.search_calls = 0

    def search_channels(self, query: str, page_token: Optional[str] = None):
        if self.quota_after is not None and self.search_calls >= self.quota_after:
            raise QuotaExceeded("search")
        self.search_calls += 1
        pages = self.pages.get(query, [])
        # page_token is the 1-based index of the page to return (as a string);
        # None means the first page.
        idx = int(page_token) if page_token else 0
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        return list(page.get("channel_ids", [])), page.get("next")

    def get_channels(self, channel_ids: List[str]) -> List[Dict[str, Any]]:
        return [self.channels[c] for c in channel_ids if c in self.channels]


# ---------------------------------------------------------------------------
# Subscriber band + niche derivation
# ---------------------------------------------------------------------------

def subscriber_band(count: Optional[int]) -> Optional[str]:
    """Map a subscriber count to a coarse follower band (matches L0 conventions)."""
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


# YouTube topicDetails topicCategories are Wikipedia URLs; the last path segment
# is a usable niche label (e.g. .../Lifestyle_(sociology) -> "Lifestyle").
def _niche_from_topics(topic_categories: List[str]) -> Optional[str]:
    for url in topic_categories or []:
        seg = str(url).rstrip("/").rsplit("/", 1)[-1]
        if not seg:
            continue
        label = seg.split("(")[0].replace("_", " ").strip()
        if label:
            return label.lower()
    return None


_EMAIL_IN_TEXT = None  # compiled lazily to keep import light


def _email_from_about(text: str) -> Optional[str]:
    """Pull the first email out of a channel's about/description text."""
    global _EMAIL_IN_TEXT
    if _EMAIL_IN_TEXT is None:
        import re
        _EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    m = _EMAIL_IN_TEXT.search(text or "")
    return m.group(0) if m else None


def channel_to_candidate(channel: Dict[str, Any], niche_hint: Optional[str] = None) -> Dict[str, Any]:
    """Turn a YouTube channel resource into a loader candidate dict.

    Carries: a ``handle`` blocking key (channel id), an ``email`` if one is in
    the about text, scalar lead fields (``follower_band``, ``follower_count``,
    ``niche``, ``platform='youtube'``, ``source='youtube'``), and rich signals
    in ``attributes`` so L3 personalization can name a real signal.
    """
    cid = channel.get("id") or ""
    snippet = channel.get("snippet", {}) or {}
    stats = channel.get("statistics", {}) or {}
    topics = (channel.get("topicDetails", {}) or {}).get("topicCategories", []) or []
    branding = (channel.get("brandingSettings", {}) or {}).get("channel", {}) or {}

    title = snippet.get("title") or ""
    description = snippet.get("description") or branding.get("description") or ""

    sub_count = stats.get("subscriberCount")
    try:
        sub_count = int(sub_count) if sub_count not in (None, "") else None
    except (TypeError, ValueError):
        sub_count = None

    niche = _niche_from_topics(topics) or niche_hint
    email = _email_from_about(description)
    custom_url = snippet.get("customUrl") or ""

    attributes = {
        "advertiser": title,
        "channel_id": cid,
        "channel_title": title,
        "description": description.strip(),
        "topic_categories": topics,
        "custom_url": custom_url,
        "video_count": stats.get("videoCount"),
        "view_count": stats.get("viewCount"),
        "country": snippet.get("country"),
        "enriched": True,
    }
    attributes = {k: v for k, v in attributes.items() if v not in (None, "", [], {})}

    lead_fields = {
        "segment": "creator",
        "platform": "youtube",
        "source": "youtube",
        "niche": niche,
        "follower_band": subscriber_band(sub_count),
        "follower_count": sub_count,
    }

    # The channel id is the stable YouTube identity. We carry it as the social
    # ``handle`` blocking key (resolver stores it as a 'linkedin'-typed social
    # channel per the frozen enum) AND keep it in attributes for clarity.
    return {
        "page": None,
        "email": email,
        "phone": None,
        "handle": custom_url or cid,
        "attributes": attributes,
        "lead_fields": lead_fields,
        "channels": [],
    }


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

class YouTubeAdapter(SourceAdapter):
    """Source creators from YouTube for an approved target_spec.

    Yields candidate dicts (no DB writes). Quota-aware: stops cleanly and records
    a resume cursor on the spec's ``attributes`` when the API quota is exhausted.
    """

    name = "youtube"

    def __init__(
        self,
        client: Optional[YouTubeClient] = None,
        search_budget: int = DEFAULT_SEARCH_BUDGET,
    ) -> None:
        # Default to the real client; tests inject a FakeYouTubeClient.
        self.client = client or HttpxYouTubeClient()
        self.search_budget = search_budget

    def run(self, target_spec) -> Iterable[Dict[str, Any]]:
        # GATE: only ever source an approved spec.
        if not self.require_approved(target_spec):
            logger.info("youtube: spec not approved; sourcing nothing")
            return

        keywords = _spec_keywords(target_spec)
        niche_hint = _spec_niche_hint(target_spec)
        cursor = _get_resume_cursor(target_spec)

        # Resume: skip keywords already finished, start mid-keyword at a token.
        start_kw_index = cursor.get("keyword_index", 0)
        page_token = cursor.get("page_token")

        seen_channel_ids: "set[str]" = set()
        searches_done = 0

        try:
            for kw_index in range(start_kw_index, len(keywords)):
                query = keywords[kw_index]
                token = page_token if kw_index == start_kw_index else None
                while True:
                    if searches_done >= self.search_budget:
                        # Budget for this run is spent — record where to resume.
                        _set_resume_cursor(
                            target_spec,
                            {"keyword_index": kw_index, "page_token": token},
                            status="budget_paused",
                        )
                        logger.info("youtube: search budget reached; pausing at kw_index=%s", kw_index)
                        return
                    channel_ids, next_token = self.client.search_channels(query, page_token=token)
                    searches_done += 1
                    # Cache/dedupe channel ids across pages and keywords.
                    fresh = [c for c in channel_ids if c not in seen_channel_ids]
                    seen_channel_ids.update(fresh)
                    for batch in _batched(fresh, CHANNELS_BATCH_SIZE):
                        for channel in self.client.get_channels(batch):
                            yield channel_to_candidate(channel, niche_hint=niche_hint)
                    if not next_token:
                        break  # keyword exhausted, move to the next
                    token = next_token
            # Completed every keyword within budget — clear any resume cursor.
            _set_resume_cursor(target_spec, None, status="complete")
        except QuotaExceeded:
            # 403 quotaExceeded mid-run: keep what we yielded, record the cursor,
            # mark partially sourced, and stop cleanly (NO exception escapes).
            _set_resume_cursor(
                target_spec,
                {"keyword_index": kw_index, "page_token": token},
                status="quota_exceeded",
            )
            logger.warning(
                "youtube: quotaExceeded; marked spec partially-sourced, resume cursor saved"
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


_RESUME_KEY = "youtube_resume"


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
    attrs["youtube_status"] = status
    if cursor is None:
        attrs.pop(_RESUME_KEY, None)
    else:
        attrs[_RESUME_KEY] = cursor


def _batched(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


# Register so the engine can discover this adapter by name / via enabled_adapters().
from sourcing.base import register as _register  # noqa: E402

_register("youtube", YouTubeAdapter)
