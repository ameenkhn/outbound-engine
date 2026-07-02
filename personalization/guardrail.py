"""The P4 guardrail — the gate every generated message must clear before send.

DECISION P4 (plan-eng-review 2026-06-26): a message may go out **only if it
names a real, concrete signal from this lead's scraped data** — not just a
mail-merge of name + segment + niche. The whole premise of the engine (PRD §5)
is that personalization at this depth is what makes cold outbound land; a
message that only interpolates ``Hi {name}, as a {niche} creator...`` is
generic spam wearing a name tag, and P4 blocks it.

What counts as a CONCRETE signal (any ONE is enough to PASS):
  * a snippet of the lead's actual ``ad_text`` (their real ad copy),
  * their page ``category`` / ``subcategory`` (e.g. "Life Coach"),
  * their ``city``,
  * their concrete follower figure (``followers`` string / ``follower_count``),
  * a named social handle / platform they actually run.

What does NOT count (these are the mail-merge fields available for *every*
lead, so leaning on them alone proves nothing was personalized):
  * the lead's ``name`` / page name (``advertiser``) — a "Hi {name}" greeting
    is exactly the mail-merge case we block; the page name is the name,
  * the ``segment`` word ("creator" / "affiliate"),
  * the ``niche`` token alone (it's a coarse bucket the lead shares with
    thousands of others — naming "tarot" is not the same as quoting their ad).

The function is **pure and deterministic** (no model, no I/O) so it is trivially
and heavily unit-testable, and so the exact same verdict is reproducible at
audit time. It also enforces two compliance rules from PRD §15 / §17:
  * a mandatory **opt-out line** must be present (DPDP-friendly),
  * **no invented pricing / unverifiable claims** (basic lexical checks).

``passes_guardrail(body, lead_attributes) -> (bool, reason)``: ``reason`` is an
empty string on PASS, and a short human-readable cause on FAIL.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# A signal token must be at least this many characters to count as "concrete".
# Stops a 2-letter category fragment ("IN") from trivially matching.
MIN_SIGNAL_LEN = 4

# A matched ad_text snippet must be at least this many characters of overlap to
# count — a single shared common word ("now") is not evidence of personalization.
MIN_ADTEXT_SNIPPET_LEN = 12

# Words we never treat as a concrete signal even if they appear in attributes —
# they are generic to the whole funnel, not specific to this lead.
GENERIC_STOPWORDS = frozenset(
    {
        "creator",
        "affiliate",
        "affiliates",
        "course",
        "courses",
        "coaching",
        "coach",
        "online",
        "india",
        "indian",
        "business",
        "program",
        "programme",
        "certification",
        "certified",
        "now",
        "enroll",
        "enrol",
        "apply",
        "join",
        "learn",
        "exly",
        "platform",
        "creators",
    }
)

# Opt-out language — at least one of these phrasings must appear in the body.
OPT_OUT_PATTERNS = (
    "opt out",
    "opt-out",
    "unsubscribe",
    "reply stop",
    "reply 'stop'",
    'reply "stop"',
    "stop to opt out",
    "no longer wish to hear",
    "don't want to hear",
    "do not want to hear",
    "let me know and i'll stop",
    "let me know and i will stop",
    "tell me to stop",
    "won't reach out again",
    "will not reach out again",
)

# Invented-pricing / unverifiable-claim red flags. We do NOT let the model quote
# a number it can't ground; the value props deliberately carry no figures.
# A currency amount or a percentage in the body is treated as an invented claim.
_PRICE_PATTERNS = (
    re.compile(r"[₹$€£]\s?\d"),          # ₹49,999 / $99
    re.compile(r"\b\d+\s?%"),             # 50%, 30 %
    re.compile(r"\brs\.?\s?\d", re.I),    # Rs 999 / Rs. 999
    re.compile(r"\b\d[\d,]*\s?(?:inr|usd|rupees?|dollars?)\b", re.I),
)

# Superlative / guarantee claims we can't substantiate in cold copy.
_CLAIM_PATTERNS = (
    re.compile(r"\bguarantee(?:d|s)?\b", re.I),
    re.compile(r"\b(?:#?1|number one|no\.?\s?1)\b.*\b(?:platform|in india|best)\b", re.I),
    re.compile(r"\bbest\s+(?:platform|in india)\b", re.I),
    re.compile(r"\b100%\b"),
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'&]*", re.I)


def _norm(text: Optional[str]) -> str:
    """Lowercase + collapse whitespace; ``None`` -> empty string."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _words(text: Optional[str]) -> List[str]:
    """Tokenize into lowercase word-ish tokens."""
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _has_opt_out(body_l: str) -> bool:
    return any(p in body_l for p in OPT_OUT_PATTERNS)


def _invented_claim(body: str) -> Optional[str]:
    """Return a reason string if the body invents pricing / unverifiable claims."""
    for pat in _PRICE_PATTERNS:
        m = pat.search(body)
        if m:
            return "invented pricing/number in body (%r)" % m.group(0).strip()
    for pat in _CLAIM_PATTERNS:
        m = pat.search(body)
        if m:
            return "unverifiable claim in body (%r)" % m.group(0).strip()
    return None


def _candidate_signals(attrs: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Concrete, lead-specific signals drawn from scraped attributes.

    Returns ``(kind, value)`` pairs where ``kind`` is:
      * ``"token"`` — match at word boundaries (single words from
        category/subcategory/city — e.g. "Mumbai", "Life", "Coach"),
      * ``"whole"`` — match as a substring (multi-word category values like
        "life coach", follower figures like "12.5K", and social handles like
        "aanyacoaching").

    Sources are category/subcategory, city, the follower figure and social
    handles — everything EXCEPT name/page-name/segment/niche, minus the generic
    funnel stopwords. ``advertiser`` (the page name) is excluded because it
    equals the lead's name, and a "Hi {name}" greeting is the very mail-merge
    case P4 blocks. Crucially, **social handles are only added as whole-string
    signals, never split into word tokens** — splitting "aanya-coaching" into
    "aanya" would let a bare "Hi Aanya" greeting sneak through. ``ad_text`` is
    handled separately (snippet overlap).
    """
    signals: List[Tuple[str, str]] = []

    # Word-level sources: category / (sub)category / city. Both their individual
    # words (token match) and the whole multi-word value (substring match).
    # ``sub_category`` (underscore) is included so imported-lead fields count.
    for key in ("category", "subcategory", "sub_category", "city"):
        v = attrs.get(key)
        if not (isinstance(v, str) and v.strip()):
            continue
        for tok in _words(v):
            if len(tok) >= MIN_SIGNAL_LEN and tok not in GENERIC_STOPWORDS:
                signals.append(("token", tok))
        whole = _norm(v)
        if len(whole) >= MIN_SIGNAL_LEN and " " in whole:
            signals.append(("whole", whole))

    # Follower / audience figures: human strings ("12.5K", "8000+ coaches; 36K IG")
    # kept as distinctive whole substrings.
    for key in ("followers", "audience_size"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            whole = _norm(v)
            if len(whole) >= MIN_SIGNAL_LEN:
                signals.append(("whole", whole))
    fc = attrs.get("follower_count")
    if isinstance(fc, (int, float)) and fc and len(str(int(fc))) >= MIN_SIGNAL_LEN:
        signals.append(("whole", str(int(fc))))

    # Social handles: whole-handle substring only — NEVER tokenized, so a handle
    # that embeds the lead's name can't match a name-only greeting.
    socials = attrs.get("socials")
    if isinstance(socials, dict):
        for handle in socials.values():
            if isinstance(handle, str) and handle.strip():
                whole = _norm(handle)
                if len(whole) >= MIN_SIGNAL_LEN:
                    signals.append(("whole", whole))

    # Dedupe, preserve order.
    seen = set()
    out: List[Tuple[str, str]] = []
    for s in signals:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _adtext_overlap(body_l: str, ad_text: Optional[str]) -> Optional[str]:
    """Return the matched snippet if the body echoes a real chunk of the lead's
    ad copy, else ``None``.

    Requires a contiguous run of non-generic ad words (>= MIN_ADTEXT_SNIPPET_LEN
    chars) to appear in the body — so quoting a real phrase from their ad passes,
    but a single shared common word does not.
    """
    if not ad_text:
        return None
    ad_words = [w for w in _words(ad_text)]
    if not ad_words:
        return None

    # Slide a window of 2..5 consecutive ad words and look for it in the body.
    n = len(ad_words)
    for size in (5, 4, 3, 2):
        for i in range(0, n - size + 1):
            window = ad_words[i : i + size]
            # Skip windows that are entirely generic/stopwords — no signal.
            if all(w in GENERIC_STOPWORDS for w in window):
                continue
            phrase = " ".join(window)
            if len(phrase) < MIN_ADTEXT_SNIPPET_LEN:
                continue
            if phrase in body_l:
                return phrase
    return None


def find_concrete_signal(
    body: str, lead_attributes: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Return the first concrete signal the body references, or ``None``.

    A "concrete signal" is a lead-specific detail from scraped attributes
    (category, advertiser, city, follower figure, a social handle) or a quoted
    snippet of the lead's real ``ad_text``. Name / segment / niche do NOT count.
    Exposed separately so tests (and callers) can introspect *what* matched.
    """
    attrs = lead_attributes or {}
    body_l = _norm(body)
    if not body_l:
        return None

    # 1) A quoted chunk of their real ad copy is the strongest signal.
    snippet = _adtext_overlap(body_l, attrs.get("ad_text"))
    if snippet:
        return "ad_text snippet: %r" % snippet

    # 2) Otherwise, any concrete attribute signal echoed in the body.
    for kind, value in _candidate_signals(attrs):
        if kind == "whole":
            if value in body_l:
                return "attribute value: %r" % value
        else:  # "token" — match at word boundaries
            if re.search(r"\b" + re.escape(value) + r"\b", body_l):
                return "attribute token: %r" % value
    return None


def passes_guardrail(
    body: str, lead_attributes: Optional[Dict[str, Any]]
) -> Tuple[bool, str]:
    """The P4 gate. Return ``(ok, reason)``.

    PASS (``True, ""``) requires ALL of:
      1. a non-empty body,
      2. a mandatory opt-out line present,
      3. no invented pricing / unverifiable claims, and
      4. at least one CONCRETE signal from the lead's scraped data referenced
         (beyond name / segment / niche) — this is what blocks mail-merge.

    On FAIL, ``ok`` is ``False`` and ``reason`` names the first failing rule, so
    the caller can log exactly why a message was rejected.
    """
    if not body or not body.strip():
        return False, "empty body"

    # Compliance gates first — cheap and unambiguous.
    body_l = _norm(body)
    if not _has_opt_out(body_l):
        return False, "missing mandatory opt-out line"

    claim = _invented_claim(body)
    if claim is not None:
        return False, claim

    # The P4 core: must reference a real scraped signal, not just mail-merge.
    signal = find_concrete_signal(body, lead_attributes)
    if signal is None:
        return False, (
            "no concrete lead signal referenced (mail-merge only: name/segment/"
            "niche are not enough)"
        )

    return True, ""
