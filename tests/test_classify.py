"""Unit tests for the pure inbound classifier (no DB).

These run everywhere — no Postgres, no network. They lock down the two decisions
the suppression / stop-on-reply logic hangs off: (1) opt-out detection must fire
on real intent-to-leave phrasings and must NOT fire on benign collocations
(a false opt-out permanently silences a warm lead); (2) intent labels match the
values L0 stores in ``events.intent``.
"""
from __future__ import annotations

import pytest

from inbound.classify import (
    INTENT_INTERESTED,
    INTENT_NOT_NOW,
    INTENT_OBJECTION,
    INTENT_QUESTION,
    INTENT_UNSUBSCRIBE,
    classify_intent,
    classify_sentiment,
    is_optout,
)


# --- opt-out: POSITIVE cases (must trigger) ---------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "STOP",
        "stop",
        "Please stop emailing me.",
        "stop contacting me",
        "Stop messaging me please",
        "unsubscribe",
        "Unsubscribe me from this list",
        "Please unsubscribe me.",
        "unsub",
        "I want to unsubscribe",
        "remove me from your list",
        "Please remove me.",
        "take me off your list",
        "opt out",
        "opt-out",
        "I'd like to opt out",
        "opting out now",
        "not interested",
        "Not interested, thanks.",
        "I'm no longer interested",
        "leave me alone",
        "do not contact me again",
        "don't contact me",
        "Do not email me.",
        "please don't email me anymore",
        "NOT INTERESTED!!!",
        "  STOP  ",
    ],
)
def test_is_optout_positive(body):
    assert is_optout(body) is True


# --- opt-out: NEGATIVE cases (must NOT trigger) -----------------------------

@pytest.mark.parametrize(
    "body",
    [
        "stop by my channel sometime!",
        "I'll stop by your booth at the event",
        "stopping by next week",
        "this is a non-stop operation",
        "We run a one-stop shop for creators",
        "Sounds interesting, tell me more",
        "I'm interested but not right now",
        "Can you remove the watermark from the deck?",  # 'remove' but not 'remove me'
        "How do I opt into your newsletter?",  # opt-in, not opt-out
        "Yes please, let's talk",
        "What's the pricing?",
        "I already use a competitor though",
        "Happy to hop on a call next quarter",
        "",
        "   ",
    ],
)
def test_is_optout_negative(body):
    assert is_optout(body) is False


def test_is_optout_handles_none_and_nonstr():
    assert is_optout(None) is False
    assert is_optout(12345) is False  # type: ignore[arg-type]


def test_is_optout_case_insensitive():
    assert is_optout("StOp EmAiLiNg Me") is True
    assert is_optout("UNSUBSCRIBE") is True


# --- intent classification --------------------------------------------------

def test_intent_unsubscribe_wins():
    # An opt-out phrase always classifies as unsubscribe, even amid other words.
    assert classify_intent("Thanks but please unsubscribe me") == INTENT_UNSUBSCRIBE
    assert classify_intent("STOP") == INTENT_UNSUBSCRIBE


@pytest.mark.parametrize(
    "body",
    [
        "I'm interested, let's talk",
        "Sounds great, book a call",
        "Keen to learn more!",
        "Yes please, sign me up",
        "Would love to chat about this",
    ],
)
def test_intent_interested(body):
    assert classify_intent(body) == INTENT_INTERESTED


@pytest.mark.parametrize(
    "body",
    [
        "How much does this cost?",
        "What is Exly exactly?",
        "Can you send me more details?",
        "When can we talk this week?",
    ],
)
def test_intent_question(body):
    assert classify_intent(body) == INTENT_QUESTION


@pytest.mark.parametrize(
    "body",
    [
        "This is too expensive for us",
        "We already use a competitor",
        "Not convinced this is a good fit",
        "Why should we switch from what we have?",
    ],
)
def test_intent_objection(body):
    assert classify_intent(body) == INTENT_OBJECTION


@pytest.mark.parametrize(
    "body",
    [
        "Not right now, maybe later",
        "Can you circle back next quarter?",
        "Reach out again later please",
        "Too busy right now, check back next month",
    ],
)
def test_intent_not_now(body):
    assert classify_intent(body) == INTENT_NOT_NOW


def test_intent_objection_outranks_question():
    # Objections often phrase as questions; the objection signal must win.
    assert classify_intent("Why is yours better? We already use a tool") == INTENT_OBJECTION


def test_intent_fallback_is_question():
    # Ambiguous human reply with no strong signal -> let a human look (question),
    # never silent disinterest.
    assert classify_intent("ok") == INTENT_QUESTION
    assert classify_intent("") == INTENT_QUESTION
    assert classify_intent(None) == INTENT_QUESTION


# --- sentiment (rides along on the reply event) -----------------------------

def test_sentiment_mapping():
    assert classify_sentiment("Please unsubscribe") == "negative"
    assert classify_sentiment("This is too expensive") == "negative"
    assert classify_sentiment("I'm interested, let's talk") == "positive"
    assert classify_sentiment("What's the pricing?") == "neutral"
