"""The L5 follow-up cadence engine (M6), over the L0/Lane-C database.

``advance_cadences(conn, now)`` is the heartbeat: it scans for leads that are due
their next cadence touch and enqueues a ``send_job`` for it (through the frozen
``orchestration.queue.enqueue`` outbox). It is designed to be run on a schedule
(e.g. a Celery beat) and is **idempotent** — re-running over the same state never
double-enqueues, because every follow-up send carries a deterministic
``idempotency_key`` of ``f"followup:{lead_id}:{step}"`` and ``enqueue`` is
``ON CONFLICT DO NOTHING`` on that key.

ELIGIBILITY (a lead is advanced iff ALL hold):
  * ``leads.status = 'contacted'`` — the only state the cadence drives. Any of
    {replied, in_conversation, demo_booked, converted, dead, opted_out} (and
    new/queued) is excluded.
  * the lead has at least one prior send (a ``messages`` row) — the D0 outreach.
  * it is below ``MAX_TOUCHES`` total touches.
  * its last send is older than the next cadence interval (``cadence.is_due``).

STOP RULES (decision M6) — a lead is removed from the cadence the instant any of
these is true; checked in SQL so the scan never even considers it:
  * a ``reply`` event exists for the lead   -> stop-on-reply.
  * an ``optout`` event exists for the lead  -> stop-on-opt-out.
  * a matching ``suppression`` row exists: an IDENTITY-WIDE row
    (``channel_type IS NULL``, an opt-out blocks the person everywhere) OR a
    CHANNEL-SPECIFIC row whose ``channel_type`` matches the channel the next
    touch would send on. (Same 6A shape as ``orchestration.tasks.is_suppressed``,
    applied here pre-enqueue as well as at dispatch.)

All functions take an open psycopg connection; the caller owns the transaction /
connection lifetime (one connection per scheduler tick). ``now`` is passed in
(never read from the clock here) so runs are deterministic and Python-3.9 safe.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from orchestration.queue import enqueue
from personalization.generate import AnthropicGenerator, generate_message
from personalization.value_props import pick_angle

from . import cadence

# Statuses that REMOVE a lead from the cadence (anything that is not actively
# 'contacted'). Kept here as the canonical exclude list for readability; the
# scan itself simply requires status = 'contacted'.
TERMINAL_OR_ENGAGED_STATUSES = (
    "replied",
    "in_conversation",
    "demo_booked",
    "converted",
    "dead",
    "opted_out",
)

# Event types that, if present for a lead, stop the cadence immediately.
STOP_EVENT_TYPES = ("reply", "optout")


def _eligible_lead_rows(conn, now: datetime) -> List[dict]:
    """Return the per-lead state needed to decide the next touch.

    One row per lead that is in ``status='contacted'``, has at least one send,
    and has NO reply/optout event and NO matching suppression. The cadence math
    (is it actually due? are we under MAX_TOUCHES?) is applied in Python by the
    caller against this row's ``sent_count`` / ``last_sent_at`` — the SQL only
    does the cheap, set-based pruning (status + stop events + suppression).

    Each row dict has:
        lead_id, identity_key, segment, channel_id, sent_count, last_sent_at
    where channel_id / its type are the channel of the lead's most recent send
    (the next touch reuses that channel).
    """
    rows: List[dict] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH sent_messages AS (
                -- A message counts as a "send" for cadence math UNLESS it is an
                -- unsent (still 'queued') follow-up placeholder. The engine
                -- inserts an empty placeholder row at enqueue time (FK to
                -- send_jobs); counting it would inflate sent_count and advance
                -- last_sent_at to "now", so a re-run would mis-compute the next
                -- step instead of recognizing the step it already enqueued.
                -- Once the orchestrator actually sends that placeholder
                -- (delivery_status != 'queued'), it does count, so the cadence
                -- continues to the next step. Bug fix: idempotent re-runs.
                SELECT *
                  FROM messages m
                 WHERE NOT (COALESCE(m.angle, '') LIKE 'followup%'
                            AND m.delivery_status = 'queued')
            ),
            last_msg AS (
                -- the most recent send per lead (DISTINCT ON = newest row)
                SELECT DISTINCT ON (m.lead_id)
                       m.lead_id,
                       m.id          AS message_id,
                       m.channel_id  AS channel_id,
                       m.created_at  AS last_sent_at
                  FROM sent_messages m
                 ORDER BY m.lead_id, m.created_at DESC, m.id DESC
            ),
            send_counts AS (
                SELECT lead_id, count(*) AS sent_count
                  FROM sent_messages
                 GROUP BY lead_id
            )
            SELECT l.id            AS lead_id,
                   l.identity_key  AS identity_key,
                   l.segment::text AS segment,
                   lm.channel_id   AS channel_id,
                   ch.type::text   AS channel_type,
                   sc.sent_count   AS sent_count,
                   lm.last_sent_at AS last_sent_at
              FROM leads l
              JOIN last_msg     lm ON lm.lead_id = l.id
              JOIN send_counts  sc ON sc.lead_id = l.id
              JOIN channels     ch ON ch.id = lm.channel_id
             WHERE l.status = 'contacted'
               -- stop-on-reply / stop-on-opt-out: no such event may exist.
               AND NOT EXISTS (
                     SELECT 1 FROM events e
                      WHERE e.lead_id = l.id
                        AND e.type IN ('reply', 'optout')
                   )
               -- suppression (6A): identity-wide OR matching this channel.
               AND NOT EXISTS (
                     SELECT 1 FROM suppression s
                      WHERE s.identity_key = l.identity_key
                        AND (s.channel_type IS NULL
                             OR s.channel_type = ch.type)
                   )
            """
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            rows.append(dict(zip(cols, r)))
    return rows


def _create_followup_message(
    conn, lead_id: int, channel_id: int, step: int, segment, generator
) -> int:
    """Insert the follow-up ``messages`` row with REAL generated copy, return its id.

    ``send_jobs.message_id`` is a FK, so a row must exist before we enqueue. The
    body/subject are generated through the L3 personalization layer
    (:func:`personalization.generate.generate_message`) so a follow-up sends real,
    signal-grounded copy instead of a blank placeholder (the empty-body bug). The
    cadence marker stays in ``angle`` as ``followup_step_N`` — the engine keys on
    ``angle LIKE 'followup%'`` to keep unsent placeholders out of the cadence math
    — while the value-prop angle the copy used is recorded in ``variant``.
    """
    # Load the lead's scraped signals so the copy can reference them (clears P4).
    with conn.cursor() as cur:
        cur.execute("SELECT attributes, niche FROM leads WHERE id = %s", (lead_id,))
        row = cur.fetchone()
    attrs = row[0] if row and isinstance(row[0], dict) else {}
    niche = row[1] if row else None
    lead = {"id": lead_id, "attributes": attrs, "niche": niche}

    seg = segment or "creator"
    vp_angle = pick_angle(seg)
    msg = generate_message(lead, seg, vp_angle, generator)
    subject, body = msg.get("subject", ""), msg.get("body", "")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages
                (lead_id, channel_id, variant, angle, subject, body, delivery_status)
            VALUES (%s, %s, %s, %s, %s, %s, 'queued')
            RETURNING id
            """,
            (lead_id, channel_id, "followup:%s" % vp_angle,
             "followup_step_%d" % step, subject, body),
        )
        message_id = cur.fetchone()[0]
    conn.commit()
    return message_id


def advance_cadences(
    conn, now: datetime, max_touches: Optional[int] = None, generator=None
) -> dict:
    """Advance every eligible lead to its next cadence touch.

    For each lead that is ``status='contacted'``, has prior sends, has no
    reply/optout event and no matching suppression (all enforced in
    :func:`_eligible_lead_rows`), this computes the next step via
    :func:`cadence.next_step`. If a next step exists, is under the touch cap, and
    is DUE by ``now`` (its cadence interval has elapsed since the last send), it:

        1. creates a placeholder follow-up ``messages`` row, and
        2. enqueues a ``send_job`` for it via ``orchestration.queue.enqueue``
           with ``idempotency_key = f"followup:{lead_id}:{step}"`` and the
           computed ``run_after``.

    Idempotent: the deterministic idempotency_key means a job for
    ``(lead, step)`` can be enqueued only once; a re-run is a no-op for any lead
    whose next job already exists. (To avoid leaking an orphan ``messages`` row
    when the enqueue is a no-op, the message is only inserted once we know no job
    exists for that key.)

    Returns a summary dict: ``{"scanned", "enqueued", "skipped_not_due",
    "skipped_capped", "already_enqueued"}``.
    """
    summary = {
        "scanned": 0,
        "enqueued": 0,
        "skipped_not_due": 0,
        "skipped_capped": 0,
        "already_enqueued": 0,
    }

    # Default to the real Haiku-class generator (lazy: constructing it needs no
    # SDK or key; only an actual send-copy generation does). Tests inject a
    # FakeGenerator so the follow-up copy path runs offline.
    if generator is None:
        generator = AnthropicGenerator()

    for row in _eligible_lead_rows(conn, now):
        summary["scanned"] += 1
        lead_id = row["lead_id"]
        identity_key = row["identity_key"]
        segment = row["segment"]
        channel_id = row["channel_id"]
        sent_count = row["sent_count"]
        last_sent_at = row["last_sent_at"]

        # current_step is the index of the last touch sent. With `sent_count`
        # sends (D0 = step 0), the last step index is sent_count - 1.
        current_step = sent_count - 1

        nxt = cadence.next_step(
            current_step,
            last_sent_at,
            segment=segment,
            max_touches=max_touches,
        )
        if nxt is None:
            # No further step / touch cap reached -> lead falls out of cadence.
            summary["skipped_capped"] += 1
            continue

        next_idx, run_after = nxt

        # Due check: only enqueue once the cadence interval has elapsed.
        if run_after is not None and run_after > now:
            summary["skipped_not_due"] += 1
            continue

        idempotency_key = "followup:%d:%d" % (lead_id, next_idx)

        # Guard against creating an orphan message row when the job already
        # exists: only build the message + enqueue if no job carries this key.
        if _job_exists(conn, idempotency_key):
            summary["already_enqueued"] += 1
            continue

        message_id = _create_followup_message(
            conn, lead_id, channel_id, next_idx, segment, generator
        )
        job_id = enqueue(
            conn,
            message_id=message_id,
            channel_id=channel_id,
            identity_key=identity_key,
            idempotency_key=idempotency_key,
            run_after=run_after,
        )
        if job_id is None:
            # Lost a race: another tick enqueued the same key between our check
            # and insert. The idempotency_key kept it to one job; count it as
            # already-enqueued (the orphan message is harmless and unsent).
            summary["already_enqueued"] += 1
        else:
            summary["enqueued"] += 1

    return summary


def _job_exists(conn, idempotency_key: str) -> bool:
    """True iff a ``send_job`` with this idempotency_key already exists.

    Lets :func:`advance_cadences` avoid inserting a placeholder ``messages`` row
    that would otherwise be orphaned when ``enqueue`` no-ops on the unique
    idempotency_key. (Correctness still rests on the unique constraint; this is
    only to keep re-runs from littering message rows.)
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM send_jobs WHERE idempotency_key = %s LIMIT 1",
            (idempotency_key,),
        )
        return cur.fetchone() is not None
