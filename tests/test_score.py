"""Unit tests for the rules-based ICP score (no DB).

Covers every weight component in isolation, the 100 cap, both hard gates,
follower-band cutoffs as they flow into the score, the signal_richness max of
40, and a fully-loaded lead scoring high. The score is a pure function so these
need no Postgres.
"""
from __future__ import annotations

from enrichment.score import WEIGHTS, score_lead


# A reachable, IN, otherwise-empty baseline lead. Each test adds exactly one
# signal so the delta isolates that weight.
def _base_lead(**over):
    lead = {
        "attributes": {},
        "follower_count": None,
        "geo": "IN",
        "segment": None,   # -> ambiguous (+5) unless overridden
        "niche": None,
    }
    lead.update(over)
    return lead


def _email_channel(handle="lead@example.com"):
    return [{"type": "email", "handle": handle, "deliverable": True, "opted_out": False}]


def _whatsapp_channel(handle="+919812345678"):
    return [{"type": "whatsapp", "handle": handle, "deliverable": True, "opted_out": False}]


# ---------------------------------------------------------------------------
# Hard gates -> 0
# ---------------------------------------------------------------------------

def test_gate_no_reachable_channel_scores_zero():
    lead = _base_lead(attributes={"ad_text": "x", "category": "fitness"})
    assert score_lead(lead, []) == 0
    # A linkedin-only channel is NOT reachable for the dispatch gate.
    only_linkedin = [{"type": "linkedin", "handle": "in/jane", "deliverable": True, "opted_out": False}]
    assert score_lead(lead, only_linkedin) == 0


def test_gate_geo_not_in_scores_zero():
    lead = _base_lead(geo="US", attributes={"ad_text": "x"})
    assert score_lead(lead, _email_channel()) == 0
    # case-insensitive: 'in' lower-cased still passes the gate, 'GB' fails
    assert score_lead(_base_lead(geo="in"), _email_channel()) > 0
    assert score_lead(_base_lead(geo="GB"), _email_channel()) == 0


# ---------------------------------------------------------------------------
# signal_richness component (max 40)
# ---------------------------------------------------------------------------

def test_signal_ad_text_weight():
    # baseline (whatsapp, no email) scores only segment ambiguous (+5).
    base = score_lead(_base_lead(), _whatsapp_channel())
    with_ad = score_lead(_base_lead(attributes={"ad_text": "great course"}), _whatsapp_channel())
    assert with_ad - base == WEIGHTS["signal_ad_text"]


def test_signal_category_weight():
    base = score_lead(_base_lead(), _whatsapp_channel())
    # use a non-ICP category so the niche_match (+20) doesn't also fire.
    with_cat = score_lead(_base_lead(attributes={"category": "zzz-unmatched"}), _whatsapp_channel())
    assert with_cat - base == WEIGHTS["signal_category"]


def test_signal_social_weight():
    base = score_lead(_base_lead(), _whatsapp_channel())
    with_social = score_lead(_base_lead(attributes={"socials": ["https://ig.com/x"]}), _whatsapp_channel())
    assert with_social - base == WEIGHTS["signal_social"]


def test_signal_richness_capped_at_40():
    # ad_text(25) + category(10) + social(5) = 40 exactly == signal_max.
    attrs = {"ad_text": "x", "category": "zzz-unmatched", "socials": ["a", "b", "c"]}
    base = score_lead(_base_lead(), _whatsapp_channel())
    full = score_lead(_base_lead(attributes=attrs), _whatsapp_channel())
    assert full - base == WEIGHTS["signal_max"] == 40


# ---------------------------------------------------------------------------
# follower-band fit
# ---------------------------------------------------------------------------

def test_follower_band_weights_via_score():
    base = score_lead(_base_lead(), _whatsapp_channel())  # no count -> no band points
    nano = score_lead(_base_lead(follower_count=500), _whatsapp_channel())
    micro = score_lead(_base_lead(follower_count=10_000), _whatsapp_channel())
    mid = score_lead(_base_lead(follower_count=500_000), _whatsapp_channel())
    macro = score_lead(_base_lead(follower_count=5_000_000), _whatsapp_channel())
    assert nano - base == WEIGHTS["band_nano"]
    assert micro - base == WEIGHTS["band_micro"]
    assert mid - base == WEIGHTS["band_mid"]
    assert macro - base == WEIGHTS["band_macro"]


def test_follower_band_cutoffs_via_score():
    # boundary: 999 nano, 1000 micro, 99_999 micro, 100_000 mid,
    # 999_999 mid, 1_000_000 macro.
    base = score_lead(_base_lead(), _whatsapp_channel())

    def band_points(n):
        return score_lead(_base_lead(follower_count=n), _whatsapp_channel()) - base

    assert band_points(999) == WEIGHTS["band_nano"]
    assert band_points(1_000) == WEIGHTS["band_micro"]
    assert band_points(99_999) == WEIGHTS["band_micro"]
    assert band_points(100_000) == WEIGHTS["band_mid"]
    assert band_points(999_999) == WEIGHTS["band_mid"]
    assert band_points(1_000_000) == WEIGHTS["band_macro"]


# ---------------------------------------------------------------------------
# niche / category match
# ---------------------------------------------------------------------------

def test_niche_match_weight():
    base = score_lead(_base_lead(), _whatsapp_channel())
    matched = score_lead(_base_lead(niche="Fitness Coaching"), _whatsapp_channel())
    assert matched - base == WEIGHTS["niche_match"]


def test_niche_match_from_category_attribute():
    base = score_lead(_base_lead(), _whatsapp_channel())
    # category "fitness" both adds signal_category(+10) AND niche_match(+20).
    matched = score_lead(_base_lead(attributes={"category": "fitness"}), _whatsapp_channel())
    assert matched - base == WEIGHTS["signal_category"] + WEIGHTS["niche_match"]


def test_non_icp_niche_does_not_add_niche_points():
    base = score_lead(_base_lead(), _whatsapp_channel())
    unmatched = score_lead(_base_lead(niche="underwater basket weaving"), _whatsapp_channel())
    assert unmatched == base


# ---------------------------------------------------------------------------
# segment clarity
# ---------------------------------------------------------------------------

def test_segment_clear_vs_ambiguous():
    ambiguous = score_lead(_base_lead(segment=None), _whatsapp_channel())
    clear = score_lead(_base_lead(segment="creator"), _whatsapp_channel())
    # both add segment points; clear adds 10, ambiguous adds 5 -> delta 5.
    assert clear - ambiguous == WEIGHTS["segment_clear"] - WEIGHTS["segment_ambiguous"]
    affiliate = score_lead(_base_lead(segment="affiliate"), _whatsapp_channel())
    assert affiliate == clear
    # an unknown segment string is ambiguous, not clear.
    junk = score_lead(_base_lead(segment="influencer"), _whatsapp_channel())
    assert junk == ambiguous


# ---------------------------------------------------------------------------
# competitor-tool hint
# ---------------------------------------------------------------------------

def test_competitor_hint_weight():
    base = score_lead(_base_lead(attributes={"ad_text": "join my course"}), _whatsapp_channel())
    with_hint = score_lead(
        _base_lead(attributes={"ad_text": "migrate off Kajabi now"}), _whatsapp_channel()
    )
    assert with_hint - base == WEIGHTS["competitor_hint"]


# ---------------------------------------------------------------------------
# verified email present (not just phone)
# ---------------------------------------------------------------------------

def test_verified_email_weight():
    # same lead, whatsapp-only vs email: email adds verified_email(+5).
    wa = score_lead(_base_lead(), _whatsapp_channel())
    em = score_lead(_base_lead(), _email_channel())
    assert em - wa == WEIGHTS["verified_email"]


def test_phone_only_does_not_get_verified_email_points():
    # whatsapp passes the reachability gate but earns no verified_email bonus.
    wa = score_lead(_base_lead(), _whatsapp_channel())
    # only the segment-ambiguous baseline (+5), no email bonus.
    assert wa == WEIGHTS["segment_ambiguous"]


# ---------------------------------------------------------------------------
# 100 cap + fully-loaded
# ---------------------------------------------------------------------------

def test_fully_loaded_lead_scores_high_and_caps_at_100():
    lead = _base_lead(
        attributes={
            "ad_text": "ditch Teachable, switch to us",  # +25 signal, +10 competitor
            "category": "fitness",                        # +10 signal, +20 niche
            "socials": ["ig", "yt"],                      # +5 signal
        },
        follower_count=150_000,  # mid band +25
        segment="creator",       # clear +10
        niche="fitness",         # niche +20 (already from category, single fire)
    )
    score = score_lead(lead, _email_channel())  # verified email +5
    # raw would exceed 100; the cap clamps it.
    assert score == WEIGHTS["score_cap"] == 100


def test_high_but_uncapped_lead_is_between():
    # a solidly-good lead that does NOT hit the cap, to prove the sum is real.
    lead = _base_lead(
        attributes={"category": "yoga"},  # +10 signal +20 niche
        follower_count=10_000,            # micro +20
        segment="affiliate",             # clear +10
    )
    score = score_lead(lead, _email_channel())  # verified email +5
    # 10 + 20 + 20 + 10 + 5 = 65
    assert score == 65
    assert 0 < score < 100
