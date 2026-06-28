"""Unit tests for the P4 guardrail (no DB).

The guardrail's whole job: PASS only when a message names a real scraped signal
beyond name/segment/niche (blocking mail-merge), an opt-out line is present, and
no pricing/claims are invented. These tests pin every branch of that contract.
"""
from __future__ import annotations

import pytest

from personalization.guardrail import find_concrete_signal, passes_guardrail

OPT_OUT = "Reply STOP to opt out and I won't reach out again."

# A representative scraped lead (shape mirrors leads.attributes JSONB from loader).
ATTRS = {
    "advertiser": "Aanya Coaching",
    "ad_text": "Enroll now in our ICF-accredited life coach certification. Batch starting soon.",
    "category": "Coach",
    "subcategory": "Life Coach",
    "followers": "12.5K",
    "follower_count": 12500,
    "city": "Mumbai",
    "socials": {"instagram": "aanyacoaching", "linkedin": "aanya-coaching"},
    "niche": "nlp_mindset",
}


# ---------------------------------------------------------------------------
# The core P4 rule: mail-merge BLOCKED, concrete-signal PASSES.
# ---------------------------------------------------------------------------

def test_mail_merge_only_is_blocked():
    """Name + niche only, no scraped signal -> FAIL (the headline P4 case)."""
    body = (
        "Hi Aanya, as an nlp_mindset creator I think Exly could help you grow. "
        + OPT_OUT
    )
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is False
    assert "concrete" in reason.lower() or "mail-merge" in reason.lower()


def test_citing_ad_text_snippet_passes():
    """Quoting a real chunk of the lead's ad copy -> PASS."""
    body = (
        "Hi Aanya, I saw your ad about your ICF-accredited life coach "
        "certification and thought Exly could help you launch the next batch. "
        + OPT_OUT
    )
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason
    assert reason == ""


def test_citing_category_passes():
    """Naming the scraped category ('Life Coach') -> PASS."""
    body = (
        "Hi Aanya, as a Life Coach building your practice, Exly bundles your "
        "tools in one place. " + OPT_OUT
    )
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_citing_city_passes():
    body = "Hi Aanya, fellow Mumbai folks are loving Exly. " + OPT_OUT
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_citing_follower_figure_passes():
    body = "Hi Aanya, with your 12.5K following you could monetise faster on Exly. " + OPT_OUT
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_citing_social_handle_passes():
    body = "Hi Aanya, loved aanyacoaching on Instagram — Exly could help. " + OPT_OUT
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_page_name_greeting_alone_does_not_pass():
    """The page name (advertiser) equals the lead name — a greeting using it is
    still mail-merge, so it does NOT count as a concrete signal on its own."""
    body = "Hi Aanya Coaching, Exly could help you scale. " + OPT_OUT
    ok, reason = passes_guardrail(body, {"advertiser": "Aanya Coaching"})
    assert ok is False
    assert "concrete" in reason.lower() or "mail-merge" in reason.lower()


# ---------------------------------------------------------------------------
# Opt-out gate.
# ---------------------------------------------------------------------------

def test_missing_opt_out_fails_even_with_signal():
    """A perfectly personalized body with NO opt-out -> FAIL."""
    body = "Hi Aanya, I saw your Life Coach certification ad — Exly could help."
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is False
    assert "opt-out" in reason.lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "Reply STOP to opt out.",
        "You can unsubscribe anytime.",
        "Just reply stop and I won't reach out again.",
        "If you'd rather not hear from me, let me know and I'll stop.",
    ],
)
def test_various_opt_out_phrasings_accepted(phrase):
    body = "Hi Aanya, your Life Coach certification caught my eye. " + phrase
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


# ---------------------------------------------------------------------------
# Invented pricing / unverifiable claims.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "Exly costs only ₹999 a month",
        "Save 50% versus your current tool",
        "Just Rs. 499 to start",
        "Pay 2999 INR and you're set",
    ],
)
def test_invented_pricing_fails(bad):
    body = "Hi Aanya, your Life Coach certification looks great. {0}. {1}".format(bad, OPT_OUT)
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is False
    assert "pricing" in reason.lower() or "number" in reason.lower()


@pytest.mark.parametrize(
    "bad",
    [
        "Exly is guaranteed to double your sales",
        "We're the best platform in India",
        "Get 100% of your audience converting",
    ],
)
def test_unverifiable_claims_fail(bad):
    body = "Hi Aanya, your Life Coach certification looks great. {0}. {1}".format(bad, OPT_OUT)
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is False
    assert "claim" in reason.lower() or "pricing" in reason.lower() or "number" in reason.lower()


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------

def test_empty_body_fails():
    ok, reason = passes_guardrail("", ATTRS)
    assert ok is False
    assert "empty" in reason.lower()


def test_whitespace_only_body_fails():
    ok, reason = passes_guardrail("   \n  ", ATTRS)
    assert ok is False
    assert "empty" in reason.lower()


def test_no_attributes_means_no_signal_possible():
    """With empty attributes, even a friendly body can't cite a signal -> FAIL."""
    body = "Hi there, Exly could help you grow. " + OPT_OUT
    ok, reason = passes_guardrail(body, {})
    assert ok is False
    assert "concrete" in reason.lower() or "mail-merge" in reason.lower()


def test_none_attributes_handled():
    body = "Hi there, Exly could help. " + OPT_OUT
    ok, reason = passes_guardrail(body, None)
    assert ok is False


def test_niche_token_alone_does_not_count():
    """Mentioning only the coarse niche bucket is NOT a concrete signal."""
    attrs = {"niche": "occult", "category": "Education"}
    body = "Hi, as someone in occult I think Exly could help. " + OPT_OUT
    ok, reason = passes_guardrail(body, attrs)
    # 'occult' is the niche bucket, not in our concrete-signal sources -> FAIL.
    assert ok is False


def test_segment_word_alone_does_not_count():
    body = "Hi, as a creator you'd love Exly. " + OPT_OUT
    ok, reason = passes_guardrail(body, {"category": "Coach"})
    # 'creator' is a generic stopword; 'Coach' is a generic stopword too -> FAIL.
    assert ok is False


def test_generic_shared_ad_word_does_not_pass():
    """A single shared common word ('now') from ad_text is not enough."""
    attrs = {"ad_text": "Enroll now and apply today"}
    body = "Hi there, do it now! Exly could help. " + OPT_OUT
    ok, reason = passes_guardrail(body, attrs)
    assert ok is False


def test_real_distinctive_phrase_from_ad_passes():
    attrs = {"ad_text": "Master numerology with our practitioner certification programme."}
    body = (
        "Hi, your focus on numerology with a practitioner pathway is exactly the "
        "kind of offer Exly hosts well. " + OPT_OUT
    )
    ok, reason = passes_guardrail(body, attrs)
    assert ok is True, reason


def test_find_concrete_signal_introspection():
    """find_concrete_signal returns *what* matched (useful for callers/tests)."""
    body = "Hi Aanya, your Life Coach certification ad caught my eye. " + OPT_OUT
    sig = find_concrete_signal(body, ATTRS)
    assert sig is not None
    assert "life coach" in sig.lower() or "ad_text" in sig.lower()


def test_find_concrete_signal_none_when_mail_merge():
    body = "Hi Aanya, as a creator you'd love Exly."
    assert find_concrete_signal(body, ATTRS) is None or "ad_text" not in (find_concrete_signal(body, ATTRS) or "")


def test_passing_message_with_signal_and_optout_and_no_price():
    """Full happy path: signal + opt-out + no invented numbers -> PASS."""
    body = (
        "Hi Aanya, your Life Coach certification batch in Mumbai looks great — "
        "Exly bundles your course host, payments and booking in one place so you "
        "keep more of each enrolment. " + OPT_OUT
    )
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_case_insensitive_signal_match():
    body = "Hi, your LIFE COACH practice is exactly Exly's sweet spot. " + OPT_OUT
    ok, reason = passes_guardrail(body, ATTRS)
    assert ok is True, reason


def test_attributes_missing_optional_keys_ok():
    """Partial attributes (only category) still allow a signal match."""
    attrs = {"category": "Astrologer"}
    body = "Hi, your work as an Astrologer is great — Exly could help. " + OPT_OUT
    ok, reason = passes_guardrail(body, attrs)
    assert ok is True, reason
