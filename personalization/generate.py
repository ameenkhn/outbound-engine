"""Generation + the generate -> guardrail -> queue flow (M4).

Three concerns live here, each behind a clean seam (PRD §11: every layer exposes
an interface the next attaches to):

  1. :class:`Generator` — the abstract copy generator. Two implementations:
       * :class:`AnthropicGenerator` — the real Haiku-class model. ``anthropic``
         is imported lazily *inside* the call, ``ANTHROPIC_API_KEY`` is read at
         call time, and the model id is the Haiku-class
         ``claude-haiku-4-5-20251001``. Importing this module never requires the
         SDK or a key, so tests run without either.
       * :class:`FakeGenerator` — deterministic, offline. It echoes a real lead
         signal so its output passes the P4 guardrail, which makes the DB tests
         (and any dry run) work with no network and no key.

  2. :func:`generate_message` — build a prompt from the lead's attributes +
     the value-prop library, ask the generator, return ``{subject, body}``.

  3. :func:`personalize_and_queue` — the orchestrated path:
       generate -> run the P4 guardrail -> if PASS insert a ``messages`` row
       (``delivery_status='queued'`` with variant/angle) and enqueue a send job
       via ``orchestration.queue.enqueue`` with a **deterministic**
       idempotency_key; if FAIL, send nothing, log the reason, return the
       rejection. The idempotency_key makes a re-run a no-op (no double-send).

Pure-Python, psycopg/anthropic both lazy — import-safe with no deps installed.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from personalization.guardrail import passes_guardrail
from personalization.value_props import get_value_prop, pick_angle

logger = logging.getLogger("personalization.generate")

# Haiku-class model id for bulk generation (PRD §13: cheap/fast/good-enough).
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# A short, mandatory opt-out line we append so generated copy always clears the
# compliance half of the guardrail (PRD §15/§17 — DPDP-friendly, easy opt-out).
OPT_OUT_LINE = "If this isn't relevant, just reply STOP and I won't reach out again."


# ---------------------------------------------------------------------------
# Generator interface + implementations
# ---------------------------------------------------------------------------

class Generator:
    """Abstract copy generator. ``generate(prompt, system) -> {subject, body}``."""

    def generate(self, prompt: str, system: Optional[str] = None) -> Dict[str, str]:
        raise NotImplementedError


class AnthropicGenerator(Generator):
    """Real Haiku-class generator. Lazy ``import anthropic``; key read at call time.

    Neither importing this module nor constructing the object requires the SDK
    or ``ANTHROPIC_API_KEY`` — both are only needed when :meth:`generate` is
    actually called. Tests therefore never touch the network or a key.
    """

    def __init__(
        self,
        model: str = HAIKU_MODEL,
        max_tokens: int = 600,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key  # else read from env at call time

    def generate(self, prompt: str, system: Optional[str] = None) -> Dict[str, str]:
        import anthropic  # lazy: only needed for a real call

        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; cannot call the Anthropic API. "
                "Set it in the environment or pass api_key=..."
            )
        client = anthropic.Anthropic(api_key=api_key)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        text = _extract_text(resp)
        return _split_subject_body(text)


class FakeGenerator(Generator):
    """Deterministic, offline generator for tests and dry runs.

    It deliberately weaves a real concrete signal from the lead's attributes
    into the body so its output PASSES the P4 guardrail — letting the DB tests
    exercise the happy path with no model and no key. Set ``mail_merge_only=True``
    to instead produce a generic name/niche-only body that the guardrail BLOCKS,
    so tests can assert the rejection path writes/enqueues nothing.
    """

    def __init__(self, mail_merge_only: bool = False) -> None:
        self.mail_merge_only = mail_merge_only

    def generate(self, prompt: str, system: Optional[str] = None) -> Dict[str, str]:
        ctx = _parse_prompt_context(prompt)
        name = ctx.get("name") or "there"
        niche = ctx.get("niche") or "your space"
        wiifm = ctx.get("wiifm") or "Exly can help you grow."

        channel = ctx.get("channel") or "email"

        if self.mail_merge_only:
            # Pure mail-merge: name + niche only, no concrete scraped signal.
            body = (
                "Hi {name}, I came across your {niche} work and thought Exly "
                "might be a fit. {wiifm} {optout}"
            ).format(name=name, niche=niche, wiifm=wiifm, optout=OPT_OUT_LINE)
            return {"subject": "Quick idea for you", "body": body}

        # Personalized: echo a real concrete signal so P4 passes.
        signal = ctx.get("signal")
        if signal:
            opener = 'Hi {name}, saw "{signal}" — '.format(name=name, signal=signal)
        else:
            opener = "Hi {name}, ".format(name=name)

        if channel == "whatsapp":
            # Short, no subject, WhatsApp-style.
            body = (
                "{opener}love your {niche} work. Exly can help you sell & scale it. "
                "Open to a quick chat? (reply STOP to opt out)"
            ).format(opener=opener, niche=niche)
            return {"subject": "", "body": body}

        body = (
            "{opener}and given your work in {niche}, I thought Exly could help. "
            "{wiifm} {optout}"
        ).format(opener=opener, niche=niche, wiifm=wiifm, optout=OPT_OUT_LINE)
        return {"subject": "An idea for your {niche} offer".format(niche=niche), "body": body}


# ---------------------------------------------------------------------------
# Prompt building + parsing helpers
# ---------------------------------------------------------------------------

def _lead_attributes(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the attributes dict off a lead row/dict (the JSONB column)."""
    attrs = lead.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _best_signal(attrs: Dict[str, Any]) -> Optional[str]:
    """A short, human-quotable concrete signal for the prompt (and FakeGenerator).

    Prefers a trimmed snippet of the real ad_text, then category, advertiser or
    city. This is the detail the model is told to reference so the output can
    clear P4.
    """
    ad_text = attrs.get("ad_text")
    if isinstance(ad_text, str) and ad_text.strip():
        snippet = ad_text.strip()
        # Trim to a quotable phrase (first ~8 words) so it stays natural.
        words = snippet.split()
        return " ".join(words[:8])
    for key in ("subcategory", "sub_category", "category", "city", "audience_size"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    followers = attrs.get("followers")
    if isinstance(followers, str) and followers.strip():
        return "your {f} following".format(f=followers.strip())
    return None


def build_prompt(
    lead: Dict[str, Any], segment: str, angle: str, channel: str = "email"
) -> Dict[str, str]:
    """Build the ``{system, prompt}`` for the generator from lead + value props.

    ``channel`` shapes the output: 'email' → Subject + a short body; 'whatsapp'
    → one warm 2–3 sentence message, no subject, plain text, under ~350 chars.
    The prompt hands the model the lead's real scraped signals + the chosen
    value-prop and tells it to reference a concrete detail and include an opt-out,
    so the output clears the P4 guardrail on either channel.
    """
    attrs = _lead_attributes(lead)
    vp = get_value_prop(segment, angle)
    name = lead.get("name") or attrs.get("advertiser") or ""
    niche = lead.get("niche") or attrs.get("niche") or ""
    signal = _best_signal(attrs)

    # Compact, model-readable signal block.
    lines = []
    if name:
        lines.append("Name/page: {0}".format(name))
    if niche:
        lines.append("Niche: {0}".format(niche))
    for key in ("category", "subcategory", "sub_category", "city", "followers",
                "audience_size", "price", "notes"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            lines.append("{0}: {1}".format(key.replace("_", " ").capitalize(), v.strip()))
    ad_text = attrs.get("ad_text")
    if isinstance(ad_text, str) and ad_text.strip():
        lines.append('Their ad copy: "{0}"'.format(ad_text.strip()))
    signal_block = "\n".join(lines) if lines else "(no extra signals)"

    base_role = (
        "You are an outbound copywriter for Exly, an all-in-one platform for "
        "Indian course/coaching creators and affiliates. Reference a CONCRETE "
        "detail from the lead's scraped signals (their ad copy, category, city, "
        "or following) — never a generic greeting. Do NOT invent pricing, "
        "percentages, or guarantees. "
    )
    if channel == "whatsapp":
        system = base_role + (
            "Write ONE short WhatsApp message: 2–3 sentences, under ~350 "
            "characters, warm and human, plain text (no markdown, no subject). "
            "End with a soft opt-out like '(reply STOP to opt out)'. Return only "
            "the message text."
        )
        closing = (
            "Write the WhatsApp message now — short and natural. Reference this "
            "concrete signal: {signal}\nEnd with a soft opt-out."
        ).format(signal=signal or "(use any concrete signal above)")
    else:
        system = base_role + (
            "Write one short, warm, specific cold outreach email. Always include "
            "an opt-out line. Return 'Subject:' on the first line, then a blank "
            "line, then the body."
        )
        closing = (
            "Write the email now. Quote or clearly reference this concrete signal "
            "so it reads personalized: {signal}\n"
            "End with this exact opt-out line: {optout}"
        ).format(signal=signal or "(use any concrete signal above)", optout=OPT_OUT_LINE)

    prompt = (
        "Lead signals:\n{signals}\n\n"
        "Segment: {segment}\n"
        "Angle: {angle}\n"
        "Exly USP: {usp}\n"
        "How we differ: {diff}\n"
        "What's in it for them: {wiifm}\n\n"
        "{closing}"
    ).format(
        signals=signal_block,
        segment=segment,
        angle=angle,
        usp=vp["usp"],
        diff=vp["differentiator"],
        wiifm=vp["wiifm"],
        closing=closing,
    )

    # Stash a few fields the FakeGenerator can parse deterministically.
    prompt += "\n\n<<<CTX name={0}|niche={1}|signal={2}|wiifm={3}|channel={4}>>>".format(
        name, niche, signal or "", vp["wiifm"], channel
    )
    return {"system": system, "prompt": prompt}


def _parse_prompt_context(prompt: str) -> Dict[str, str]:
    """Extract the ``<<<CTX ...>>>`` block the FakeGenerator reads. Best-effort."""
    out: Dict[str, str] = {}
    start = prompt.find("<<<CTX ")
    if start == -1:
        return out
    end = prompt.find(">>>", start)
    if end == -1:
        return out
    blob = prompt[start + len("<<<CTX ") : end]
    for part in blob.split("|"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def _extract_text(resp: Any) -> str:
    """Concatenate text blocks from an Anthropic Messages response."""
    parts = []
    for block in getattr(resp, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _split_subject_body(text: str) -> Dict[str, str]:
    """Parse a ``Subject: ...`` first line + body out of model text."""
    subject = ""
    body = text.strip()
    lines = body.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()
    return {"subject": subject, "body": body}


# ---------------------------------------------------------------------------
# generate_message
# ---------------------------------------------------------------------------

def generate_message(
    lead: Dict[str, Any],
    segment: str,
    angle: Optional[str],
    generator: Generator,
    channel: str = "email",
) -> Dict[str, str]:
    """Generate ``{subject, body}`` for ``lead`` at ``segment``/``angle``.

    ``channel`` ('email' | 'whatsapp') shapes the copy — WhatsApp returns a short
    body and an empty subject. ``angle`` defaults to
    :func:`personalization.value_props.pick_angle`. The generator is injected so
    tests pass a :class:`FakeGenerator`.
    """
    if angle is None:
        angle = pick_angle(segment)
    built = build_prompt(lead, segment, angle, channel=channel)
    out = generator.generate(built["prompt"], system=built["system"])
    # Defensive defaults so downstream always has both keys.
    return {"subject": out.get("subject", ""), "body": out.get("body", "")}


# ---------------------------------------------------------------------------
# personalize_and_queue  (generate -> guardrail -> queue)
# ---------------------------------------------------------------------------

def default_generator() -> Generator:
    """Anthropic Haiku when ``ANTHROPIC_API_KEY`` is set, else the offline Fake.
    Lets the batch/CLI 'just work' in tests (Fake) and in prod (Haiku) with the
    same call site."""
    return AnthropicGenerator() if os.environ.get("ANTHROPIC_API_KEY") else FakeGenerator()


def make_idempotency_key(
    lead_id: int, channel_id: int, campaign_id: Any, angle: str
) -> str:
    """Deterministic idempotency key: ``lead:channel:campaign:angle``.

    Re-running ``personalize_and_queue`` for the same (lead, channel, campaign,
    angle) enqueues at most one send job — ``enqueue`` is ``ON CONFLICT DO
    NOTHING`` on this key (orchestration/queue.py)."""
    return "{lead}:{channel}:{campaign}:{angle}".format(
        lead=lead_id, channel=channel_id, campaign=campaign_id, angle=angle
    )


def personalize_and_queue(
    conn,
    lead_id: int,
    channel_id: int,
    segment: str,
    angle: Optional[str],
    campaign_id: Optional[int],
    generator: Generator,
) -> Dict[str, Any]:
    """Generate -> P4 guardrail -> (on PASS) write message + enqueue send.

    Steps:
      1. Load the lead (identity_key + attributes) by ``lead_id``.
      2. Generate ``{subject, body}`` via :func:`generate_message`.
      3. Run :func:`personalization.guardrail.passes_guardrail` against the
         lead's scraped attributes.
      4. **FAIL** -> nothing is written, nothing is enqueued; log the reason and
         return ``{"status": "rejected", "reason": ...}``.
      5. **PASS** -> INSERT a ``messages`` row (``delivery_status='queued'``,
         ``variant``/``angle`` set), then call ``orchestration.queue.enqueue``
         with a deterministic idempotency_key. Return
         ``{"status": "queued", "message_id": ..., "job_id": ...}``.

    Returns a dict describing the outcome (never raises on a guardrail FAIL —
    rejection is a normal, logged result).
    """
    from orchestration.queue import enqueue  # local import: keeps module import-light

    if angle is None:
        angle = pick_angle(segment)

    # 1) Load the lead.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT identity_key, attributes, niche FROM leads WHERE id = %s",
            (lead_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("no lead with id={0}".format(lead_id))
    identity_key, attributes, niche = row[0], row[1], row[2]
    attrs = attributes if isinstance(attributes, dict) else {}

    lead = {"id": lead_id, "attributes": attrs, "niche": niche}

    # 2) Generate.
    msg = generate_message(lead, segment, angle, generator)
    subject, body = msg["subject"], msg["body"]

    # 3) Guardrail (P4).
    ok, reason = passes_guardrail(body, attrs)
    if not ok:
        # 4) FAIL: write nothing, enqueue nothing.
        logger.info(
            "P4 guardrail REJECTED lead_id=%s angle=%s: %s", lead_id, angle, reason
        )
        return {
            "status": "rejected",
            "reason": reason,
            "lead_id": lead_id,
            "channel_id": channel_id,
            "angle": angle,
            "subject": subject,
            "body": body,
        }

    # 5) PASS: insert the message, then enqueue the send.
    variant = "{segment}:{angle}".format(segment=segment, angle=angle)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages
                (lead_id, channel_id, campaign_id, variant, angle, subject, body,
                 delivery_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')
            RETURNING id
            """,
            (lead_id, channel_id, campaign_id, variant, angle, subject, body),
        )
        message_id = cur.fetchone()[0]
    conn.commit()

    idempotency_key = make_idempotency_key(lead_id, channel_id, campaign_id, angle)
    job_id = enqueue(
        conn,
        message_id=message_id,
        channel_id=channel_id,
        identity_key=identity_key,
        idempotency_key=idempotency_key,
    )

    logger.info(
        "P4 guardrail PASSED lead_id=%s angle=%s -> message_id=%s job_id=%s",
        lead_id,
        angle,
        message_id,
        job_id,
    )
    return {
        "status": "queued",
        "message_id": message_id,
        "job_id": job_id,
        "lead_id": lead_id,
        "channel_id": channel_id,
        "angle": angle,
        "variant": variant,
        "subject": subject,
        "body": body,
        "idempotency_key": idempotency_key,
    }
