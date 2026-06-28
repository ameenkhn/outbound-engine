"""Unit tests for the pure enrichment derivations (no DB).

Covers follower_band boundaries, competitor-tool detection, signal_richness,
segment_fit, and contactability. All pure — no Postgres needed.
"""
from __future__ import annotations

from enrichment.enrich import (
    competitor_tool_hint,
    contactability,
    follower_band,
    segment_fit,
    signal_richness,
)


# ---------------------------------------------------------------------------
# follower_band boundaries
# ---------------------------------------------------------------------------

def test_follower_band_boundaries():
    assert follower_band(1) == "nano"
    assert follower_band(999) == "nano"
    assert follower_band(1_000) == "micro"        # 1k is the nano/micro edge
    assert follower_band(99_999) == "micro"
    assert follower_band(100_000) == "mid"        # 100k is the micro/mid edge
    assert follower_band(999_999) == "mid"
    assert follower_band(1_000_000) == "macro"    # 1M is the mid/macro edge
    assert follower_band(50_000_000) == "macro"


def test_follower_band_missing_or_junk_is_none():
    assert follower_band(None) is None
    assert follower_band(0) is None
    assert follower_band(-10) is None
    assert follower_band("not a number") is None


def test_follower_band_accepts_numeric_strings():
    assert follower_band("5000") == "micro"


# ---------------------------------------------------------------------------
# competitor-tool detection
# ---------------------------------------------------------------------------

def test_competitor_tool_hint_detects_known_tools():
    assert competitor_tool_hint("We are moving off Kajabi this month") == "kajabi"
    assert competitor_tool_hint("built on TEACHABLE") == "teachable"
    assert competitor_tool_hint("payments via razorpay") == "razorpay"
    # multi-word tool name
    assert competitor_tool_hint("hosted on Mighty Networks") == "mighty networks"


def test_competitor_tool_hint_absent():
    assert competitor_tool_hint("just a normal course ad, no tools named") is None
    assert competitor_tool_hint("") is None
    assert competitor_tool_hint(None) is None


# ---------------------------------------------------------------------------
# signal_richness
# ---------------------------------------------------------------------------

def test_signal_richness_flags():
    rich = signal_richness({"ad_text": "hi", "category": "fitness", "socials": ["a", "b"]})
    assert rich["has_ad_text"] is True
    assert rich["has_category"] is True
    assert rich["has_social"] is True
    assert rich["social_count"] == 2

    empty = signal_richness({})
    assert empty["has_ad_text"] is False
    assert empty["has_category"] is False
    assert empty["has_social"] is False
    assert empty["social_count"] == 0

    assert signal_richness(None)["has_ad_text"] is False


def test_signal_richness_socials_shapes():
    # dict-of-socials: only truthy values count.
    d = signal_richness({"socials": {"instagram": "x", "youtube": "", "twitter": None}})
    assert d["social_count"] == 1
    # blank ad_text is not a signal.
    assert signal_richness({"ad_text": "   "})["has_ad_text"] is False
    # a bare scalar social counts as one.
    assert signal_richness({"socials": "https://ig.com/x"})["social_count"] == 1


# ---------------------------------------------------------------------------
# segment_fit
# ---------------------------------------------------------------------------

def test_segment_fit():
    assert segment_fit({}, "creator") == "clear"
    assert segment_fit({}, "affiliate") == "clear"
    assert segment_fit({}, None) == "ambiguous"
    assert segment_fit({}, "influencer") == "ambiguous"
    # falls back to attributes when the column is empty.
    assert segment_fit({"segment": "creator"}, None) == "clear"
    # column wins over attributes.
    assert segment_fit({"segment": "nonsense"}, "affiliate") == "clear"


# ---------------------------------------------------------------------------
# contactability
# ---------------------------------------------------------------------------

def test_contactability_email_and_whatsapp():
    chans = [
        {"type": "email", "handle": "a@x.com", "deliverable": True, "opted_out": False},
        {"type": "whatsapp", "handle": "+919812345678", "deliverable": True, "opted_out": False},
    ]
    out = contactability({}, chans)
    assert out == {"has_email": True, "has_whatsapp": True, "reachable": True}


def test_contactability_none_reachable():
    assert contactability({}, [])["reachable"] is False
    assert contactability({}, None)["reachable"] is False
    # linkedin doesn't count toward the dispatch gate.
    ln = [{"type": "linkedin", "handle": "in/jane", "deliverable": True, "opted_out": False}]
    assert contactability({}, ln)["reachable"] is False


def test_contactability_skips_undeliverable_and_optedout():
    chans = [
        {"type": "email", "handle": "dead@x.com", "deliverable": False, "opted_out": False},
        {"type": "whatsapp", "handle": "+919812345678", "deliverable": True, "opted_out": True},
    ]
    out = contactability({}, chans)
    assert out["has_email"] is False
    assert out["has_whatsapp"] is False
    assert out["reachable"] is False


def test_contactability_skips_blank_handle():
    chans = [{"type": "email", "handle": "  ", "deliverable": True, "opted_out": False}]
    assert contactability({}, chans)["has_email"] is False
