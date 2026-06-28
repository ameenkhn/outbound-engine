"""L0 comprehensive loader (task T3): ingest the scraper's ``_full.json``.

Reads the comprehensive Meta Ads scraper output, normalizes and de-dups each
advertiser into a :class:`~data.identity.Candidate`, and upserts leads +
channels via the composite resolver (:func:`data.identity.resolve`).

What it carries onto each lead (so L3 personalization can "name a real signal"):
  * ``attributes.ad_text``       — the actual ad copy (P4 hook)
  * ``attributes.category`` / ``subcategory``
  * ``attributes.followers``     — human-readable follower string
  * ``attributes.socials``       — {instagram,twitter,youtube,linkedin}
  * ``attributes.library_ids``   — every Meta Ad Library ID seen for this lead
  * ``attributes.search_queries``— the queries that surfaced this lead
  * ``attributes.enriched``      — True if advertiser_details were scraped, else
                                   False (a "bare" row from ad text only)
And the scalar lead columns: ``follower_count``, ``niche`` (the search niche),
``segment='creator'``, ``platform='meta'``, ``source='meta_ads'``,
``source_ref`` = normalized facebook page.

IDEMPOTENCY
-----------
Re-running the same file does not duplicate leads or channels: identity_key is
deterministic, the resolver upserts, and channel inserts use ON CONFLICT DO
NOTHING. Within a single file we also pre-aggregate all ads belonging to the
same advertiser (by normalized page, else email, else phone) so one creator
seen across N ads becomes ONE candidate.

CLI:
    python -m data.loader path/to/multi_keyword_..._full.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .identity import (
    EMAIL_CHANNEL,
    PHONE_CHANNEL,
    Candidate,
    build_candidate,
    resolve,
)

__all__ = [
    "load_json",
    "build_candidates",
    "load_file",
    "load_candidates",
]
from .normalize import (
    clean_phone,
    normalize_email,
    normalize_handle,
    normalize_page,
)

SOURCE = "meta_ads"
PLATFORM = "meta"


# ---------------------------------------------------------------------------
# Parsing the file
# ---------------------------------------------------------------------------

def load_json(path: str) -> List[Dict[str, Any]]:
    """Return the list of ad/advertiser objects from a ``_full.json`` file.

    Tolerates both the wrapped shape ``{"metadata": ..., "ads": [...]}`` and a
    bare top-level list, so partial checkpoints and trimmed fixtures both load.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        ads = raw.get("ads")
        if ads is None:
            raise ValueError(f"{path}: expected an 'ads' key or a top-level list")
        return ads
    if isinstance(raw, list):
        return raw
    raise ValueError(f"{path}: unexpected JSON shape {type(raw).__name__}")


def _as_list(value: Any) -> List[str]:
    """Coerce a possibly-string / possibly-list field into a list of strings.

    The scraper emits emails/phones/websites as lists, but defensive: a stray
    semicolon-joined string (from CSV-derived data) is split too."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    text = str(value).strip()
    if not text:
        return []
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    return [text]


# ---------------------------------------------------------------------------
# Per-ad -> raw signal extraction
# ---------------------------------------------------------------------------

def _extract_signals(ad: Dict[str, Any]) -> Dict[str, Any]:
    """Pull all blocking keys + carried signals out of one ad object.

    Returns a dict of normalized keys plus the raw multi-valued contact lists
    (so the aggregator can union them across ads of the same advertiser).
    """
    details = ad.get("advertiser_details") or {}
    enriched = bool(details) and details.get("page_visited", True) is not False

    page = normalize_page(ad.get("advertiser_page_url"))

    emails_raw = _as_list(details.get("emails")) + _as_list(ad.get("ad_emails"))
    phones_raw = _as_list(details.get("phones")) + _as_list(ad.get("ad_phones"))
    if details.get("whatsapp"):
        phones_raw += _as_list(details.get("whatsapp"))

    emails = [e for e in (normalize_email(x) for x in emails_raw) if e]
    phones = [p for p in (clean_phone(x) for x in phones_raw) if p]

    # Social handles. Prefer details, fall back to ad-level instagram.
    socials_src = {
        "instagram": details.get("instagram") or details.get("instagram_username") or ad.get("instagram_username"),
        "twitter": details.get("twitter") or details.get("twitter_username"),
        "youtube": details.get("youtube"),
        "linkedin": details.get("linkedin"),
    }
    socials = {}
    for k, v in socials_src.items():
        h = normalize_handle(v)
        if h:
            socials[k] = h
    # LinkedIn is the only social we can store as a channel (frozen enum).
    linkedin_handle = socials.get("linkedin")

    # Numeric follower count for the scalar column.
    follower_count = details.get("followers_count")
    try:
        follower_count = int(follower_count) if follower_count not in (None, "", 0) else None
    except (TypeError, ValueError):
        follower_count = None

    return {
        "page": page,
        "emails": emails,
        "phones": phones,
        "linkedin_handle": linkedin_handle,
        "socials": socials,
        "enriched": enriched,
        "advertiser": ad.get("advertiser") or details.get("page_name") or "",
        "ad_text": (ad.get("ad_text") or "").strip(),
        "category": details.get("category") or "",
        "subcategory": details.get("subcategory") or "",
        "followers": details.get("followers") or "",
        "follower_count": follower_count,
        "library_id": str(ad.get("library_id") or "").strip(),
        "search_query": ad.get("search_query") or "",
        "niche": ad.get("niche") or ad.get("search_niche") or "",
        "city": details.get("city") or "",
    }


def _agg_key(sig: Dict[str, Any]) -> Optional[str]:
    """Within-file aggregation key: same identity preference as identity_key
    (page > email > phone > linkedin handle). ``None`` if no signal at all."""
    if sig["page"]:
        return "page:" + sig["page"]
    if sig["emails"]:
        return "email:" + sig["emails"][0]
    if sig["phones"]:
        return "phone:" + sig["phones"][0]
    if sig["linkedin_handle"]:
        return "handle:" + sig["linkedin_handle"]
    return None


# ---------------------------------------------------------------------------
# Aggregate ads -> candidates
# ---------------------------------------------------------------------------

def build_candidates(
    ads: List[Dict[str, Any]], target_spec_id: Optional[int] = None
) -> List[Candidate]:
    """Collapse many ads into one candidate per advertiser, unioning signals.

    Pre-aggregating here means "same creator across many ads" resolves to ONE
    lead even before touching the DB, and keeps the DB round-trips to one
    resolve() call per distinct advertiser.

    ``target_spec_id`` (L1, optional) is stamped onto every candidate so the
    resolved lead carries the spec that sourced it. L0 callers omit it (None).
    """
    buckets: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    for ad in ads:
        sig = _extract_signals(ad)
        key = _agg_key(sig)
        if key is None:
            continue  # no usable contact signal — skip (can't be reached out to)

        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                "page": sig["page"],
                "emails": list(sig["emails"]),
                "phones": list(sig["phones"]),
                "linkedin_handle": sig["linkedin_handle"],
                "socials": dict(sig["socials"]),
                "enriched": sig["enriched"],
                "advertiser": sig["advertiser"],
                "ad_text": sig["ad_text"],
                "category": sig["category"],
                "subcategory": sig["subcategory"],
                "followers": sig["followers"],
                "follower_count": sig["follower_count"],
                "library_ids": [sig["library_id"]] if sig["library_id"] else [],
                "search_queries": [sig["search_query"]] if sig["search_query"] else [],
                "niche": sig["niche"],
                "city": sig["city"],
            }
        else:
            bucket["page"] = bucket["page"] or sig["page"]
            for e in sig["emails"]:
                if e not in bucket["emails"]:
                    bucket["emails"].append(e)
            for p in sig["phones"]:
                if p not in bucket["phones"]:
                    bucket["phones"].append(p)
            bucket["linkedin_handle"] = bucket["linkedin_handle"] or sig["linkedin_handle"]
            for k, v in sig["socials"].items():
                bucket["socials"].setdefault(k, v)
            bucket["enriched"] = bucket["enriched"] or sig["enriched"]
            # Prefer the longest ad_text as the personalization hook.
            if len(sig["ad_text"]) > len(bucket["ad_text"]):
                bucket["ad_text"] = sig["ad_text"]
            bucket["advertiser"] = bucket["advertiser"] or sig["advertiser"]
            bucket["category"] = bucket["category"] or sig["category"]
            bucket["subcategory"] = bucket["subcategory"] or sig["subcategory"]
            bucket["followers"] = bucket["followers"] or sig["followers"]
            bucket["follower_count"] = bucket["follower_count"] or sig["follower_count"]
            bucket["niche"] = bucket["niche"] or sig["niche"]
            bucket["city"] = bucket["city"] or sig["city"]
            if sig["library_id"] and sig["library_id"] not in bucket["library_ids"]:
                bucket["library_ids"].append(sig["library_id"])
            if sig["search_query"] and sig["search_query"] not in bucket["search_queries"]:
                bucket["search_queries"].append(sig["search_query"])

    candidates: List[Candidate] = []
    for bucket in buckets.values():
        candidates.append(_bucket_to_candidate(bucket, target_spec_id))
    return candidates


def _bucket_to_candidate(
    b: Dict[str, Any], target_spec_id: Optional[int] = None
) -> Candidate:
    """Turn an aggregated bucket into a resolver Candidate."""
    attributes = {
        "advertiser": b["advertiser"],
        "ad_text": b["ad_text"],
        "category": b["category"],
        "subcategory": b["subcategory"],
        "followers": b["followers"],
        "socials": b["socials"],
        "library_ids": b["library_ids"],
        "search_queries": b["search_queries"],
        "city": b["city"],
        "enriched": b["enriched"],
    }
    # Drop empty attribute values so attributes stays clean / merges are additive.
    attributes = {k: v for k, v in attributes.items() if v not in (None, "", [], {})}

    lead_fields = {
        "segment": "creator",
        "platform": PLATFORM,
        "source": SOURCE,
        "niche": b["niche"] or None,
        "follower_count": b["follower_count"],
    }

    # Extra email/phone channels beyond the primary blocking key.
    extra_channels = []
    for e in b["emails"][1:]:
        extra_channels.append((EMAIL_CHANNEL, e))
    for p in b["phones"][1:]:
        extra_channels.append((PHONE_CHANNEL, p))

    raw = {
        "page": b["page"],
        "email": b["emails"][0] if b["emails"] else None,
        "phone": b["phones"][0] if b["phones"] else None,
        "handle": b["linkedin_handle"],
        "attributes": attributes,
        "lead_fields": lead_fields,
        "channels": extra_channels,
        "target_spec_id": target_spec_id,
    }
    # build_candidate re-normalizes (no-op on already-normalized values) and is
    # the single construction path, keeping loader + resolver in lock-step.
    return build_candidate(raw)


# ---------------------------------------------------------------------------
# DB load
# ---------------------------------------------------------------------------

def load_file(path: str, conn=None, target_spec_id: Optional[int] = None) -> Dict[str, int]:
    """Load a ``_full.json`` into the lead DB. Returns a stats dict.

    If ``conn`` is None a connection is opened from DATABASE_URL via data.db and
    closed at the end; otherwise the caller's connection (and its transaction)
    is used and left open (tests pass their throwaway-schema connection here).

    ``target_spec_id`` (L1, optional) attributes every loaded lead to the
    approved target_spec that sourced the file. L0 callers omit it (None), in
    which case ``leads.target_spec_id`` stays NULL and nothing changes.
    """
    ads = load_json(path)
    candidates = build_candidates(ads, target_spec_id=target_spec_id)

    own_conn = False
    if conn is None:
        from .db import connect
        conn = connect()
        own_conn = True

    stats = {
        "ads": len(ads),
        "candidates": len(candidates),
        "created": 0,
        "merged": 0,
        "skipped": 0,
    }
    try:
        with conn.cursor() as cur:
            for cand in candidates:
                if cand.identity_key() is None:
                    stats["skipped"] += 1
                    continue
                _lead_id, created = resolve(cur, cand)
                if created:
                    stats["created"] += 1
                else:
                    stats["merged"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
    return stats


def load_candidates(candidates, conn=None, target_spec_id: Optional[int] = None) -> Dict[str, int]:
    """Resolve an iterable of candidate dicts (the shape adapters emit) into leads.

    L1 source adapters (YouTube, the Meta CSV bridge) yield normalized candidate
    *dicts* — the same loosely-typed shape :func:`data.identity.build_candidate`
    consumes (``page``/``email``/``phone``/``handle`` + ``attributes`` /
    ``lead_fields`` / extra ``channels``). This is the one entry point that turns
    those into resolved leads, stamping ``target_spec_id`` (overriding any value
    already on the dict) so every lead is attributed to the spec that sourced it.

    Mirrors :func:`load_file`'s connection ownership: pass ``conn`` to reuse a
    caller's transaction (tests), or omit it to open/close one from DATABASE_URL.
    """
    cands: List[Candidate] = []
    for raw in candidates:
        if not isinstance(raw, Candidate):
            raw = dict(raw)
            if target_spec_id is not None:
                raw["target_spec_id"] = target_spec_id
            cand = build_candidate(raw)
        else:
            cand = raw
            if target_spec_id is not None:
                cand.target_spec_id = target_spec_id
        cands.append(cand)

    own_conn = False
    if conn is None:
        from .db import connect
        conn = connect()
        own_conn = True

    stats = {"candidates": len(cands), "created": 0, "merged": 0, "skipped": 0}
    try:
        with conn.cursor() as cur:
            for cand in cands:
                if cand.identity_key() is None:
                    stats["skipped"] += 1
                    continue
                _lead_id, created = resolve(cur, cand)
                if created:
                    stats["created"] += 1
                else:
                    stats["merged"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Load a comprehensive _full.json into the L0 lead DB.")
    parser.add_argument("path", help="path to multi_keyword_..._full.json")
    args = parser.parse_args(argv)

    try:
        stats = load_file(args.path)
    except FileNotFoundError:
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 2
    print(
        "Loaded {ads} ads -> {candidates} candidates: "
        "{created} created, {merged} merged, {skipped} skipped".format(**stats)
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
