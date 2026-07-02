"""Pure derivations from a lead's raw signals (L2).

Every function here is PURE: it reads only what it is handed (a lead's
``attributes`` dict, its ``follower_count``, and — for contactability — its
channels) and returns a derived value. No DB, no I/O, no globals mutated. That
keeps them trivially unit-testable and makes the score computed identically at
backfill time and at re-run time.

A "lead" here is just a mapping (a dict or a psycopg row mapping) exposing the
columns from ``leads`` — we only ever read ``attributes``, ``follower_count``,
``segment``, ``niche`` from it. Channels are passed in explicitly (a list of
mappings with ``type`` / ``handle`` / ``deliverable`` / ``opted_out``) so this
module never touches the database itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

# ---------------------------------------------------------------------------
# Follower band
# ---------------------------------------------------------------------------
# Cutoffs (decision baked into L2):
#   nano  : < 1k
#   micro : 1k   .. < 100k
#   mid   : 100k .. < 1M
#   macro : >= 1M
NANO_MAX = 1_000
MICRO_MAX = 100_000
MID_MAX = 1_000_000


def follower_band(count: Optional[int]) -> Optional[str]:
    """Bucket a follower count into nano | micro | mid | macro.

    Returns ``None`` when the count is missing or non-positive — an unknown
    audience size is not a band, and the scorer treats it as "no band fit".
    """
    if count is None:
        return None
    try:
        n = int(count)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n < NANO_MAX:
        return "nano"
    if n < MICRO_MAX:
        return "micro"
    if n < MID_MAX:
        return "mid"
    return "macro"


# ---------------------------------------------------------------------------
# Signal richness
# ---------------------------------------------------------------------------

def _socials(attrs: Mapping[str, Any]) -> List[Any]:
    """Pull the socials out of attributes, tolerating list / dict / scalar shapes."""
    socials = attrs.get("socials")
    if not socials:
        return []
    if isinstance(socials, dict):
        # {"instagram": "...", "youtube": ""} — keep only the truthy values.
        return [v for v in socials.values() if v]
    if isinstance(socials, (list, tuple, set)):
        return [v for v in socials if v]
    return [socials]


def signal_richness(attrs: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Describe which scraped signals are present on a lead.

    Returns a small dict the scorer consumes directly:
      ``has_ad_text``  — non-empty ``ad_text``
      ``has_category`` — non-empty ``category``
      ``social_count`` — number of usable socials
      ``has_social``   — at least one usable social
    """
    attrs = attrs or {}
    ad_text = attrs.get("ad_text")
    category = attrs.get("category")
    socials = _socials(attrs)
    return {
        "has_ad_text": bool(ad_text and str(ad_text).strip()),
        "has_category": bool(category and str(category).strip()),
        "social_count": len(socials),
        "has_social": len(socials) >= 1,
    }


# ---------------------------------------------------------------------------
# Segment fit
# ---------------------------------------------------------------------------
# A lead's segment is "clear" when it is unambiguously creator or affiliate;
# everything else (missing, unknown string) is "ambiguous".
_KNOWN_SEGMENTS = ("creator", "affiliate")


def segment_fit(attrs: Optional[Mapping[str, Any]], segment: Optional[str]) -> str:
    """Classify how clearly we know the lead's segment.

    Precedence: the explicit ``segment`` column wins; if it is missing we fall
    back to ``attributes['segment']``. Returns ``"clear"`` when it resolves to a
    known segment, else ``"ambiguous"``.
    """
    attrs = attrs or {}
    value = segment if segment else attrs.get("segment")
    if value is None:
        return "ambiguous"
    if str(value).strip().lower() in _KNOWN_SEGMENTS:
        return "clear"
    return "ambiguous"


# ---------------------------------------------------------------------------
# Competitor-tool hint
# ---------------------------------------------------------------------------
# A small, configurable list of competitor course/community/checkout tools. If
# a lead's ad copy name-drops one, they already pay for tooling in our space —
# a strong buying signal. Tweak this list as the competitive set shifts.
COMPETITOR_TOOLS = (
    "kajabi",
    "teachable",
    "thinkific",
    "graphy",
    "rzp",
    "razorpay",
    "instamojo",
    "podia",
    "gumroad",
    "skool",
    "circle",
    "mighty networks",
    "exlyapp",  # legacy/alt brand spelling occasionally seen in copy
    "learnyst",
    "classplus",
)


def competitor_tool_hint(ad_text: Optional[str], tools=None) -> Optional[str]:
    """Scan ad copy for a known competitor-tool name.

    Returns the matched tool name (lowercased) or ``None`` when no hint is
    present. Matching is case-insensitive substring on the ad copy. ``tools``
    overrides the default :data:`COMPETITOR_TOOLS` list (e.g. from
    ``scoring_config`` so the CRM can tune it); falsy ``tools`` uses the default.
    """
    if not ad_text:
        return None
    haystack = str(ad_text).lower()
    for tool in (tools or COMPETITOR_TOOLS):
        if str(tool).lower() in haystack:
            return str(tool).lower()
    return None


# ---------------------------------------------------------------------------
# Contactability
# ---------------------------------------------------------------------------

def _channel_get(channel: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a channel whether it's a mapping or an object."""
    if isinstance(channel, Mapping):
        return channel.get(key, default)
    return getattr(channel, key, default)


def contactability(lead: Any, channels: Optional[List[Any]]) -> Dict[str, bool]:
    """Derive which reachable channels a lead has.

    ``lead`` is accepted for symmetry / future use; the decision is driven by
    the ``channels`` list. A channel counts as reachable when it is deliverable
    and not opted out. Returns::

        {"has_email": bool, "has_whatsapp": bool, "reachable": bool}

    ``reachable`` is the OR of email/whatsapp — the gate the scorer enforces.
    (LinkedIn is intentionally not a "reachable" channel for the dispatch gate.)
    """
    has_email = False
    has_whatsapp = False
    for ch in channels or []:
        ctype = _channel_get(ch, "type")
        deliverable = _channel_get(ch, "deliverable", True)
        opted_out = _channel_get(ch, "opted_out", False)
        if not deliverable or opted_out:
            continue
        handle = _channel_get(ch, "handle")
        if not (handle and str(handle).strip()):
            continue
        if ctype == "email":
            has_email = True
        elif ctype == "whatsapp":
            has_whatsapp = True
    return {
        "has_email": has_email,
        "has_whatsapp": has_whatsapp,
        "reachable": has_email or has_whatsapp,
    }
