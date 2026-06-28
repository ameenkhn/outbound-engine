"""Pure, DB-free classification of inbound reply text.

Two responsibilities, both deterministic and side-effect free so they unit-test
trivially and behave identically in tests and production:

  * :func:`is_optout`      — does this body ask us to stop contacting them?
  * :func:`classify_intent` — coarse intent label for routing/learning.

Design rules (mirrors ``data/normalize.py``):
  * Pure: no DB, no I/O, no globals mutated. Same input -> same output.
  * Defensive on junk: ``None``/non-str inputs never raise.
  * Word-boundary safe: opt-out keywords match whole words/phrases only, so
    "please STOP emailing me" opts out but "stop by my channel sometime" and
    "this is a non-starter" do NOT. False opt-outs silence a warm lead forever,
    so the bias is to require an unambiguous intent-to-leave signal.

These rules are intentionally simple/explainable (regex over a keyword set), not
an LLM call. L6's Haiku-based classifier can layer on top later; this is the
deterministic floor that the suppression/stop-on-reply decisions hang off.
"""
from __future__ import annotations

import re
from typing import Optional

# --- intent labels (mirror events.intent in 0001_init_schema.sql) -----------
INTENT_INTERESTED = "interested"
INTENT_QUESTION = "question"
INTENT_OBJECTION = "objection"
INTENT_NOT_NOW = "not_now"
INTENT_UNSUBSCRIBE = "unsubscribe"


def _clean(body: Optional[str]) -> str:
    """Coerce to a lowercased str; never raise on junk input."""
    if body is None:
        return ""
    return str(body).lower()


# ---------------------------------------------------------------------------
# Opt-out detection
# ---------------------------------------------------------------------------
#
# Each entry is a regex matched against the lowercased body. Single words use
# \b word boundaries so they only fire as standalone words ("stop", not
# "stopwatch" or "stop by"). Multi-word phrases are matched as phrases. The
# negative lookahead on "stop"/"unsubscribe" keeps benign collocations like
# "stop by", "stopping by", "one-stop" from triggering a permanent opt-out.

_OPTOUT_PATTERNS = [
    # Bare imperative "stop", but NOT "stop by"/"stop the" (sentence continues),
    # and NOT a "stop" that's the tail of a compound like "non-stop",
    # "one-stop", "one stop shop", "non stop". The leading lookbehind blocks a
    # preceding hyphen or a "non"/"one" qualifier; the trailing lookahead blocks
    # "stop by"/"stop the"/"stop in".
    r"(?<![\w-])(?<!non[\s-])(?<!one[\s-])\bstop\b(?!\s+(?:by|the|sending\s+me\s+great|in|shop)\b)",
    # "stop emailing / contacting / messaging / texting me"
    r"\bstop\s+(?:emailing|e-mailing|contacting|messaging|texting|reaching)\b",
    # unsubscribe / unsub / unsubscribed
    r"\bunsubscrib(?:e|ed|ing)\b",
    r"\bunsub\b",
    # "remove me" (from your list)
    r"\bremove\s+me\b",
    r"\btake\s+me\s+off\b",
    # "opt out" / "opt-out" / "opting out"
    r"\bopt[\s-]?out\b",
    r"\bopting\s+out\b",
    # "not interested" / "no longer interested" / "not interested at all"
    r"\bnot\s+interested\b",
    r"\bno\s+longer\s+interested\b",
    # "leave me alone" / "don't contact me" / "do not contact me"
    r"\bleave\s+me\s+alone\b",
    r"\b(?:do\s+not|don'?t)\s+(?:contact|email|e-mail|message|text)\s+me\b",
    r"\b(?:do\s+not|don'?t)\s+(?:ever\s+)?(?:contact|email|e-mail|message|text)\b",
    # explicit "unsubscribe me"
    r"\bunsubscribe\s+me\b",
    # "no thanks, remove" style — covered by remove me / not interested above.
]

_OPTOUT_RE = re.compile("|".join(_OPTOUT_PATTERNS), re.IGNORECASE)


def is_optout(body: Optional[str]) -> bool:
    """True if *body* unambiguously asks us to stop contacting the person.

    Case-insensitive and word-boundary safe: "STOP", "please unsubscribe me",
    "not interested", "remove me from your list", "leave me alone" all opt out;
    "stop by my channel", "this is a non-starter", "I'm interested but not now"
    do NOT. A false positive permanently silences a warm lead, so the matcher
    only fires on an explicit intent-to-leave phrase.
    """
    text = _clean(body)
    if not text:
        return False
    return _OPTOUT_RE.search(text) is not None


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

# A question: ends with / contains a question mark, or opens with an
# interrogative, or asks about price/how/pricing.
_QUESTION_RE = re.compile(
    r"\?|"
    r"\b(?:how|what|when|where|which|who|why|can\s+you|could\s+you|"
    r"do\s+you|does\s+it|is\s+there|are\s+there|what'?s)\b",
    re.IGNORECASE,
)

# Objection: pushback signals (price, competitor, skepticism, already using).
_OBJECTION_RE = re.compile(
    r"\b(?:too\s+expensive|too\s+costly|can'?t\s+afford|"
    r"already\s+(?:use|using|have|with)|we\s+already|"
    r"not\s+(?:a\s+)?(?:good\s+)?fit|don'?t\s+(?:see\s+the\s+)?(?:need|value)|"
    r"not\s+convinced|skeptical|not\s+sure\s+(?:this|it)|"
    r"how\s+(?:is|are)\s+you\s+(?:different|better)|why\s+(?:should|would))\b",
    re.IGNORECASE,
)

# "Not now" — interested-in-principle but timing is off.
_NOT_NOW_RE = re.compile(
    r"\b(?:not\s+(?:right\s+)?now|not\s+(?:at\s+)?(?:the\s+)?(?:right\s+)?(?:moment|time)|"
    r"maybe\s+later|reach\s+out\s+(?:again\s+)?(?:later|next)|"
    r"circle\s+back|follow\s+up\s+(?:later|next)|"
    r"busy\s+(?:right\s+)?now|next\s+(?:quarter|month|week|year)|"
    r"check\s+back|some\s+other\s+time|later\s+this)\b",
    re.IGNORECASE,
)

# Positive intent: wants to talk / book / learn more / yes.
_INTERESTED_RE = re.compile(
    r"\b(?:interested|keen|let'?s\s+(?:talk|chat|do\s+it|connect)|"
    r"sounds\s+(?:good|great|interesting)|tell\s+me\s+more|"
    r"book\s+(?:a\s+)?(?:call|demo|time|slot)|set\s+up\s+(?:a\s+)?call|"
    r"would\s+love\s+to|love\s+to\s+(?:learn|hear|chat|talk)|"
    r"happy\s+to\s+(?:chat|talk|connect|hop\s+on)|"
    r"yes\s+please|sign\s+me\s+up|sounds\s+like\s+a\s+fit|"
    r"let\s+me\s+know\s+(?:a\s+)?(?:good\s+)?time)\b",
    re.IGNORECASE,
)


def classify_intent(body: Optional[str]) -> str:
    """Return a coarse intent label for *body*.

    One of: ``interested`` | ``question`` | ``objection`` | ``not_now`` |
    ``unsubscribe`` (matching the values L0 stores in ``events.intent``).

    Precedence is deliberate and tested:
      1. unsubscribe — an opt-out signal wins over everything (it stops sends).
      2. objection   — explicit pushback ("too expensive", "already use X")
         outranks a generic question, since objections often *contain* a
         question ("why is yours better?").
      3. not_now     — timing deferral ("circle back next quarter").
      4. interested  — positive intent to engage/book.
      5. question    — an open question with no stronger signal.
    Fallback when nothing matches is ``question`` (treat an ambiguous human
    reply as something a person should look at, never as silent disinterest).
    """
    text = _clean(body)
    if not text:
        return INTENT_QUESTION

    if is_optout(body):
        return INTENT_UNSUBSCRIBE
    if _OBJECTION_RE.search(text):
        return INTENT_OBJECTION
    if _NOT_NOW_RE.search(text):
        return INTENT_NOT_NOW
    if _INTERESTED_RE.search(text):
        return INTENT_INTERESTED
    if _QUESTION_RE.search(text):
        return INTENT_QUESTION
    return INTENT_QUESTION


def classify_sentiment(body: Optional[str]) -> str:
    """Coarse sentiment derived from intent, stored in ``events.sentiment``.

    Kept intentionally crude (positive | neutral | negative) — it rides along on
    the reply event so the funnel dashboard can colour replies without a second
    classifier pass. Opt-outs and objections read negative; interest reads
    positive; everything else neutral.
    """
    intent = classify_intent(body)
    if intent in (INTENT_UNSUBSCRIBE, INTENT_OBJECTION):
        return "negative"
    if intent == INTENT_INTERESTED:
        return "positive"
    return "neutral"
