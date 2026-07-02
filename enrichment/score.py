"""Rules-based ICP score — the committed v1 formula (L2).

ONE auditable place defines the score. The point values live in the module-level
:data:`WEIGHTS` dict and the two config lists (:data:`TARGET_ICP_NICHES`,
:data:`enrichment.enrich.COMPETITOR_TOOLS`), which are the committed v1 defaults.
At runtime :func:`score_lead` also accepts ``weights`` / ``niches`` /
``competitor_tools`` overrides so the CRM's editable ``scoring_config`` row drives
the live scorer (:mod:`enrichment.run` loads it and passes it in); the constants
here are the fallback when no config is present.

The contract (frozen as v1):

  HARD GATES -> icp_score = 0 (excluded from priority_rank, never dispatched):
    * no reachable channel  (no email AND no whatsapp), OR
    * geo != 'IN'.

  Otherwise a weighted sum, capped at 100:
    * signal_richness : ad_text +25, category +10, >=1 social +5   (max 40)
    * follower_band   : nano +5, micro +20, mid +25, macro +15
    * niche/category in the target-ICP list                         +20 else 0
    * segment clearly creator|affiliate +10, ambiguous             +5
    * competitor-tool hint present                                  +10
    * verified email present (not just phone)                      +5

The score is a pure function of (lead attributes, follower_count, geo, segment,
niche) plus the lead's channels. No DB, no I/O — :mod:`enrichment.run` does the
DB work and hands plain mappings in here.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from enrichment.enrich import (
    COMPETITOR_TOOLS,
    competitor_tool_hint,
    contactability,
    follower_band,
    segment_fit,
    signal_richness,
)

# ---------------------------------------------------------------------------
# Config constants (tune these — the logic below reads them, never hard-codes).
# ---------------------------------------------------------------------------

# A niche or category that lands in this list is a target-ICP match (+20). Kept
# lowercase; matching is case-insensitive and substring-aware so "Fitness Coach"
# matches "fitness". Tweak freely as the ICP sharpens.
TARGET_ICP_NICHES = (
    "fitness",
    "yoga",
    "wellness",
    "nutrition",
    "finance",
    "trading",
    "stock market",
    "education",
    "coaching",
    "edtech",
    "language",
    "music",
    "dance",
    "astrology",
    "spirituality",
    "cooking",
    "beauty",
    "fashion",
    "photography",
    "design",
    "marketing",
    "career",
    "study abroad",
    "mental health",
)

# Re-exported so callers can see "the competitor list" alongside the niche list
# from a single import site. The detector itself lives in enrichment.enrich.
COMPETITOR_TOOL_NAMES = COMPETITOR_TOOLS

# The single source of truth for every point value in the v1 formula.
WEIGHTS = {
    # signal richness (capped at SIGNAL_MAX below)
    "signal_ad_text": 25,
    "signal_category": 10,
    "signal_social": 5,
    "signal_max": 40,
    # follower-band fit
    "band_nano": 5,
    "band_micro": 20,
    "band_mid": 25,
    "band_macro": 15,
    # niche / category in target-ICP list
    "niche_match": 20,
    # segment clarity
    "segment_clear": 10,
    "segment_ambiguous": 5,
    # competitor-tool hint present in ad copy
    "competitor_hint": 10,
    # a verified (deliverable, opted-in) email — not just a phone
    "verified_email": 5,
    # overall cap
    "score_cap": 100,
}


def _niche_match(niche: Optional[str], category: Optional[str], niches=None) -> bool:
    """True if the lead's niche or category hits the target-ICP list.

    ``niches`` overrides :data:`TARGET_ICP_NICHES` (e.g. from ``scoring_config``);
    falsy ``niches`` uses the default list.
    """
    targets = niches or TARGET_ICP_NICHES
    for value in (niche, category):
        if not value:
            continue
        text = str(value).lower()
        for target in targets:
            if str(target).lower() in text:
                return True
    return False


def _has_verified_email(channels: Optional[List[Any]]) -> bool:
    """True if at least one email channel is deliverable and not opted out.

    "Verified" here means a reachable email specifically (vs. only a phone) —
    the channel exists, is deliverable, and the person hasn't opted out.
    """
    for ch in channels or []:
        ctype = ch.get("type") if isinstance(ch, Mapping) else getattr(ch, "type", None)
        if ctype != "email":
            continue
        deliverable = (
            ch.get("deliverable", True) if isinstance(ch, Mapping)
            else getattr(ch, "deliverable", True)
        )
        opted_out = (
            ch.get("opted_out", False) if isinstance(ch, Mapping)
            else getattr(ch, "opted_out", False)
        )
        handle = ch.get("handle") if isinstance(ch, Mapping) else getattr(ch, "handle", None)
        if deliverable and not opted_out and handle and str(handle).strip():
            return True
    return False


def _lead_get(lead: Any, key: str, default: Any = None) -> Any:
    if isinstance(lead, Mapping):
        return lead.get(key, default)
    return getattr(lead, key, default)


def score_lead(
    lead: Any,
    channels: Optional[List[Any]],
    weights: Optional[Mapping[str, Any]] = None,
    niches: Optional[List[str]] = None,
    competitor_tools: Optional[List[str]] = None,
) -> int:
    """Compute the 0-100 ICP score for a lead given its channels.

    ``lead`` is any mapping/row exposing ``attributes`` (JSONB dict),
    ``follower_count``, ``geo``, ``segment``, ``niche``. ``channels`` is the
    list of that lead's channel mappings. Returns an int in [0, 100]; a gated
    lead returns 0.

    Optional ``weights`` / ``niches`` / ``competitor_tools`` override the module
    defaults — this is how the CRM's editable ``scoring_config`` reaches the
    scorer (``enrichment.run`` loads the row and passes them in). ``weights`` is
    merged over the defaults, so a partial config still scores correctly; falsy
    values fall back to the committed v1 constants.
    """
    attrs = _lead_get(lead, "attributes") or {}
    geo = _lead_get(lead, "geo")
    segment = _lead_get(lead, "segment")
    niche = _lead_get(lead, "niche")
    follower_count = _lead_get(lead, "follower_count")

    reach = contactability(lead, channels)

    # ---- HARD GATES -------------------------------------------------------
    # No reachable channel, or geo outside India -> not a target. Score 0; the
    # run step then excludes these from priority_rank entirely.
    if not reach["reachable"]:
        return 0
    if (geo or "").upper() != "IN":
        return 0

    # Merge config over defaults so a partial scoring_config still scores fully.
    w = {**WEIGHTS, **(weights or {})}
    total = 0

    # ---- signal richness (capped at signal_max) ---------------------------
    sig = signal_richness(attrs)
    signal_points = 0
    if sig["has_ad_text"]:
        signal_points += w["signal_ad_text"]
    if sig["has_category"]:
        signal_points += w["signal_category"]
    if sig["has_social"]:
        signal_points += w["signal_social"]
    total += min(signal_points, w["signal_max"])

    # ---- follower-band fit -------------------------------------------------
    band = follower_band(follower_count)
    band_points = {
        "nano": w["band_nano"],
        "micro": w["band_micro"],
        "mid": w["band_mid"],
        "macro": w["band_macro"],
    }
    if band is not None:
        total += band_points[band]

    # ---- niche / category in target-ICP list ------------------------------
    category = attrs.get("category") if isinstance(attrs, Mapping) else None
    if _niche_match(niche, category, niches):
        total += w["niche_match"]

    # ---- segment clarity ---------------------------------------------------
    if segment_fit(attrs, segment) == "clear":
        total += w["segment_clear"]
    else:
        total += w["segment_ambiguous"]

    # ---- competitor-tool hint ---------------------------------------------
    ad_text = attrs.get("ad_text") if isinstance(attrs, Mapping) else None
    if competitor_tool_hint(ad_text, competitor_tools) is not None:
        total += w["competitor_hint"]

    # ---- verified email present (not just phone) --------------------------
    if _has_verified_email(channels):
        total += w["verified_email"]

    # ---- cap ---------------------------------------------------------------
    return min(total, w["score_cap"])
