"""Inbound DB handlers — turn an ESP signal into durable engine state.

Each handler takes an open psycopg connection (the caller owns the transaction
boundary / connection reuse, exactly like ``orchestration/queue.py``) and writes
to the FROZEN 0001 tables only: ``events``, ``suppression``, ``leads.status``,
``channels.deliverable``. Nothing here edits the schema.

Two entry points:

  * :func:`handle_inbound_email` — a reply landed. Look the lead up by its email
    channel, log a 'reply' event, and then branch on opt-out:
        - opt-out  => log an 'optout' event, INSERT an IDENTITY-WIDE suppression
          (reason='optout', channel_type NULL — decision 6A), set the lead
          ``opted_out``. The person is now blocked on every channel.
        - otherwise => stop-on-reply: set the lead ``replied`` (the follow-up
          engine already excludes replied/opted_out) and return a HUMAN-HANDOFF
          payload (lead summary + booking link) for the caller to drop into a
          human inbox.

  * :func:`handle_bounce` — a hardbounce/complaint landed for a channel handle.
    Log a 'bounce'/'complaint' event, INSERT a CHANNEL-SPECIFIC suppression
    (reason=kind, channel_type set — decision 6A), and flip the channel
    ``deliverable = FALSE``. Blocks that channel, not the person.

Idempotency: re-processing the same signal does not corrupt state. Suppression
inserts use ``ON CONFLICT DO NOTHING`` against the partial unique indexes from
0001 (one identity-wide row; one per channel), and the status flips are
into terminal-ish states, so a duplicate webhook delivery is a near no-op
(it may append a second event row, which is acceptable for an append-only
audit log).
"""
from __future__ import annotations

import json
from typing import Optional

from data.normalize import normalize_email

from .classify import classify_intent, classify_sentiment, is_optout


# ---------------------------------------------------------------------------
# Lead / channel lookup
# ---------------------------------------------------------------------------


def _find_channel_by_handle(conn, handle: str, channel_type: str):
    """Resolve (lead_id, identity_key, channel_id) from a (type, handle) pair.

    Returns ``None`` if no such channel exists (an inbound signal for a handle
    we never sent to — e.g. a forwarded reply). ``channels (type, handle)`` is
    globally unique in 0001, so this is at most one row.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.lead_id, l.identity_key
              FROM channels c
              JOIN leads l ON l.id = c.lead_id
             WHERE c.type = %s AND c.handle = %s
            """,
            (channel_type, handle),
        )
        row = cur.fetchone()
    if row is None:
        return None
    channel_id, lead_id, identity_key = row
    return {"channel_id": channel_id, "lead_id": lead_id, "identity_key": identity_key}


def _insert_event(
    conn,
    lead_id: int,
    channel_id: Optional[int],
    event_type: str,
    *,
    intent: Optional[str] = None,
    sentiment: Optional[str] = None,
    meta: Optional[dict] = None,
) -> int:
    """Append a row to the frozen ``events`` log and return its id.

    ``meta`` is serialized with ``json.dumps`` for the JSONB column (matching
    ``orchestration/tasks._emit_event``). Does not commit — the caller commits
    once at the end of the handler so the whole inbound action is atomic.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (lead_id, channel_id, type, intent, sentiment, meta)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (lead_id, channel_id, event_type, intent, sentiment, json.dumps(meta or {})),
        )
        return cur.fetchone()[0]


def _lead_summary(conn, lead_id: int) -> dict:
    """Compact lead snapshot for the human-handoff payload.

    Pulls the columns a human needs to triage a warm reply fast (who, what
    segment/niche, how strong the ICP signal is) without making the caller
    re-query.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, identity_key, segment, niche, platform,
                   follower_band, icp_score, status, source
              FROM leads
             WHERE id = %s
            """,
            (lead_id,),
        )
        row = cur.fetchone()
    if row is None:  # pragma: no cover - lead_id came from a live FK
        return {"lead_id": lead_id}
    cols = (
        "lead_id",
        "identity_key",
        "segment",
        "niche",
        "platform",
        "follower_band",
        "icp_score",
        "status",
        "source",
    )
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Inbound email reply
# ---------------------------------------------------------------------------


def handle_inbound_email(
    conn,
    from_email: str,
    body: str,
    *,
    booking_link: str,
) -> dict:
    """Process an inbound email reply from *from_email*.

    Resolves the lead by its email channel, writes a 'reply' event (with intent
    + sentiment from :mod:`inbound.classify`), then branches:

      * **opt-out** (``is_optout(body)``): also writes an 'optout' event, inserts
        an IDENTITY-WIDE suppression (reason='optout', ``channel_type`` NULL —
        decision 6A) for the lead's ``identity_key``, and sets ``status =
        'opted_out'``. Returns ``{"action": "opted_out", ...}`` — no handoff.

      * **warm reply** (anything else): sets ``status = 'replied'`` (stop-on-reply
        — the follow-up engine excludes replied/opted_out) and returns a
        human-handoff payload: ``{"action": "handoff", "lead": {...},
        "booking_link": ..., "intent": ..., "reply_excerpt": ...}`` for the
        caller to route into a human inbox.

    Returns ``{"action": "ignored", "reason": "unknown_sender"}`` when the email
    doesn't map to any known channel (e.g. a stray/forwarded reply), so the
    caller can decide whether to alert. Commits once at the end so the event +
    suppression + status flip land atomically.
    """
    normalized = normalize_email(from_email)
    if not normalized:
        return {"action": "ignored", "reason": "unparseable_sender", "from_email": from_email}

    found = _find_channel_by_handle(conn, normalized, "email")
    if found is None:
        return {"action": "ignored", "reason": "unknown_sender", "from_email": normalized}

    lead_id = found["lead_id"]
    channel_id = found["channel_id"]
    identity_key = found["identity_key"]

    intent = classify_intent(body)
    sentiment = classify_sentiment(body)
    optout = is_optout(body)

    # Always record the inbound reply itself.
    _insert_event(
        conn,
        lead_id,
        channel_id,
        "reply",
        intent=intent,
        sentiment=sentiment,
        meta={"source": "inbound_email", "from_email": normalized, "optout": optout},
    )

    if optout:
        # Decision 6A: opt-out is IDENTITY-WIDE — blocks the person everywhere.
        _insert_event(
            conn,
            lead_id,
            channel_id,
            "optout",
            intent="unsubscribe",
            sentiment="negative",
            meta={"source": "inbound_email", "from_email": normalized},
        )
        with conn.cursor() as cur:
            # Identity-wide suppression: channel_type NULL. Idempotent via the
            # partial unique index suppression_identity_wide_uniq.
            cur.execute(
                """
                INSERT INTO suppression (identity_key, channel_type, reason, note)
                VALUES (%s, NULL, 'optout', %s)
                ON CONFLICT DO NOTHING
                """,
                (identity_key, f"inbound email opt-out from {normalized}"),
            )
            # Also flag the email channel itself as opted_out (cheap, local hint;
            # the suppression row is the source of truth re-checked at dispatch).
            cur.execute(
                "UPDATE channels SET opted_out = TRUE WHERE id = %s",
                (channel_id,),
            )
            cur.execute(
                "UPDATE leads SET status = 'opted_out' WHERE id = %s",
                (lead_id,),
            )
        conn.commit()
        return {
            "action": "opted_out",
            "lead_id": lead_id,
            "identity_key": identity_key,
            "intent": intent,
        }

    # Warm reply: stop-on-reply + hand off to a human.
    with conn.cursor() as cur:
        # Don't clobber a lead that already advanced past 'replied' (e.g. a human
        # already moved them to in_conversation/demo_booked). Only advance from
        # the pre-reply states so a duplicate webhook can't regress the funnel.
        cur.execute(
            """
            UPDATE leads
               SET status = 'replied'
             WHERE id = %s
               AND status IN ('new', 'queued', 'contacted')
            """,
            (lead_id,),
        )
    conn.commit()

    summary = _lead_summary(conn, lead_id)
    excerpt = (body or "").strip()
    if len(excerpt) > 500:
        excerpt = excerpt[:500] + "…"
    return {
        "action": "handoff",
        "lead": summary,
        "lead_id": lead_id,
        "identity_key": identity_key,
        "channel": {"type": "email", "handle": normalized},
        "intent": intent,
        "sentiment": sentiment,
        "reply_excerpt": excerpt,
        "booking_link": booking_link,
    }


# ---------------------------------------------------------------------------
# Bounce / complaint
# ---------------------------------------------------------------------------

# kind -> (event_type, suppression_reason). Both kinds are CHANNEL-SPECIFIC
# under 6A (channel_type set), unlike opt-out which is identity-wide.
_BOUNCE_KINDS = {
    "hardbounce": ("bounce", "hardbounce"),
    "complaint": ("complaint", "complaint"),
}


def handle_bounce(conn, channel_handle: str, channel_type: str, kind: str) -> dict:
    """Process a hard bounce or spam complaint for a channel.

    *kind* must be ``'hardbounce'`` or ``'complaint'``. Writes the matching
    event ('bounce' / 'complaint'), inserts a CHANNEL-SPECIFIC suppression
    (reason=kind, ``channel_type`` set — decision 6A) for the lead's
    ``identity_key``, and marks the channel ``deliverable = FALSE``.

    Unlike opt-out this blocks only the affected channel: a hardbounce on email
    stops email but leaves WhatsApp/LinkedIn open. Returns a small status dict.
    Idempotent: the suppression insert is ``ON CONFLICT DO NOTHING`` against
    suppression_per_channel_uniq.
    """
    if kind not in _BOUNCE_KINDS:
        raise ValueError(
            f"handle_bounce: kind must be one of {sorted(_BOUNCE_KINDS)}, got {kind!r}"
        )
    event_type, reason = _BOUNCE_KINDS[kind]

    # Normalize an email handle to match how it was stored; other channel types
    # are looked up as-given (their normalization lives in their own adapters).
    handle = channel_handle
    if channel_type == "email":
        handle = normalize_email(channel_handle) or channel_handle

    found = _find_channel_by_handle(conn, handle, channel_type)
    if found is None:
        return {"action": "ignored", "reason": "unknown_channel", "handle": handle}

    lead_id = found["lead_id"]
    channel_id = found["channel_id"]
    identity_key = found["identity_key"]

    _insert_event(
        conn,
        lead_id,
        channel_id,
        event_type,
        meta={"source": "inbound_bounce", "kind": kind, "handle": handle},
    )

    with conn.cursor() as cur:
        # Channel-specific suppression: channel_type SET (6A). Idempotent.
        cur.execute(
            """
            INSERT INTO suppression (identity_key, channel_type, reason, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (identity_key, channel_type, reason, f"{kind} on {channel_type}:{handle}"),
        )
        # Mark the channel undeliverable so nothing re-queues to it.
        cur.execute(
            "UPDATE channels SET deliverable = FALSE WHERE id = %s",
            (channel_id,),
        )
    conn.commit()

    return {
        "action": "suppressed_channel",
        "lead_id": lead_id,
        "identity_key": identity_key,
        "channel_type": channel_type,
        "reason": reason,
    }
