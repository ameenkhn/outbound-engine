"""Dispatch tasks — the worker side of Lane C.

These are plain Python functions that take an open psycopg connection so they
are fully unit-testable without Celery or Redis. They are *also* registered as
Celery tasks at the bottom of the module, but only when celery is installed
(guarded), so importing this module never requires a broker.

The dispatch pipeline (``dispatch_one``):

    claim already done by queue.claim_due  ->  the job is 'claimed'
      1. 6A suppression RE-CHECK at dispatch time (decision 6A): query the
         frozen ``suppression`` table by identity_key for an IDENTITY-WIDE row
         (channel_type IS NULL) OR a CHANNEL-SPECIFIC row matching this job's
         channel. If suppressed -> mark the job 'skipped', emit an 'optout'
         event, and DO NOT send. This is re-checked here (not at enqueue) so a
         suppression added after enqueue still stops the send.
      2. rate limit: check_and_increment on the warmup bucket. If the cap is
         hit -> reschedule the job (back to 'pending' with a short backoff);
         the budget unit was NOT consumed.
      3. send: call the channel-adapter stub ``send_via_channel(...)`` (L4 fills
         this in). It is keyed on idempotency_key so even a reclaimed retry
         can't double-send.
      4. record: mark_sent + insert a 'sent'-shaped event. Errors -> mark_failed
         with backoff.

Idempotency: the whole thing is safe to re-run for the same job because
(a) the job is claimed under a row lock, (b) mark_sent is a no-op on an already
-sent job, and (c) the real send is keyed on idempotency_key downstream.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import queue, rate_limit

# Backoff applied when a send fails transiently or the rate cap is hit.
RETRY_BACKOFF_SECONDS = 60
RATE_LIMITED_BACKOFF_SECONDS = 300


class SuppressedError(Exception):
    """Raised internally when a job is suppressed at dispatch (6A)."""


def is_suppressed(conn, identity_key: str, channel_id: int) -> bool:
    """Decision 6A, evaluated at DISPATCH time.

    A job is suppressed if EITHER:
      * an identity-wide suppression row exists for this identity_key
        (``channel_type IS NULL`` — an opt-out blocks the person everywhere), OR
      * a channel-specific suppression row exists whose ``channel_type`` matches
        the channel THIS job sends on (``channels.type`` for ``channel_id``;
        e.g. a hardbounce on email blocks email but not whatsapp).

    One query, no app-side logic: the OR is expressed in SQL so the decision is
    a single index lookup on ``suppression_identity_idx``.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
              FROM suppression s
             WHERE s.identity_key = %s
               AND (
                     s.channel_type IS NULL                         -- identity-wide (opt-out)
                  OR s.channel_type = (                             -- channel-specific
                        SELECT c.type FROM channels c WHERE c.id = %s
                     )
                   )
             LIMIT 1
            """,
            (identity_key, channel_id),
        )
        return cur.fetchone() is not None


def _emit_event(conn, message_id: int, channel_id: int, event_type: str, meta: dict) -> None:
    """Append a row to the frozen ``events`` log, resolving lead_id from the
    message. Used to record both 'sent' and suppression 'optout'/skip events so
    the funnel dashboard and learning loops see them."""
    with conn.cursor() as cur:
        cur.execute("SELECT lead_id FROM messages WHERE id = %s", (message_id,))
        row = cur.fetchone()
        if not row:
            return
        lead_id = row[0]
        import json

        cur.execute(
            """
            INSERT INTO events (lead_id, channel_id, message_id, type, meta)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (lead_id, channel_id, message_id, event_type, json.dumps(meta)),
        )
    conn.commit()


def send_via_channel(channel_id: int, message_id: int, idempotency_key: str, conn=None):
    """Channel-adapter seam — filled in by L4 (email adapter; WhatsApp/LinkedIn
    follow the same registry contract).

    Resolves the channel's *type* (channels.type), looks up the adapter
    registered for it in ``dispatch.registry``, loads the message's recipient
    handle + subject + body, and calls ``adapter.send(...)`` with the
    idempotency_key so the provider can dedupe and a reclaimed retry of a crashed
    send cannot double-send.

    Contract preserved for the caller (``dispatch_one``):
      * No adapter registered for this channel type  -> raise NotImplementedError
        (the caller reschedules with backoff, exactly as before L4 landed).
      * Adapter ran but the send failed (status != 'sent', or it raised)
        -> raise so the caller's ``except Exception`` reschedules with backoff.
        A 'sent' record is only written on a confirmed send.
      * Success -> return the adapter's result dict (provider_id, status, ...).

    ``conn`` (an open psycopg connection) is required to resolve the channel
    handle/message body. It is threaded through from ``dispatch_one``; callers
    that pass only the legacy 3 args get a clear error.
    """
    if conn is None:
        raise ValueError(
            "send_via_channel requires an open DB connection (conn=) to resolve "
            "the channel handle and message body"
        )

    # Resolve channel type + recipient handle, and the message subject/body.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT type, handle FROM channels WHERE id = %s", (channel_id,)
        )
        crow = cur.fetchone()
        if not crow:
            raise ValueError(f"channel {channel_id} not found")
        channel_type, handle = crow[0], crow[1]
        cur.execute(
            "SELECT subject, body FROM messages WHERE id = %s", (message_id,)
        )
        mrow = cur.fetchone()
        if not mrow:
            raise ValueError(f"message {message_id} not found")
        subject, body = mrow[0], mrow[1]

    # Look up the adapter. A missing adapter keeps the pre-L4 contract: raise
    # NotImplementedError so dispatch_one reschedules (does NOT mark failed-
    # terminal), letting the job retry once the channel is wired.
    from dispatch import registry as _registry

    try:
        adapter = _registry.get_adapter(channel_type)
    except KeyError as exc:
        raise NotImplementedError(
            f"no dispatch adapter registered for channel_type={channel_type!r} "
            f"(channel {channel_id}); register one in dispatch.registry"
        ) from exc

    # Adapters may be a callable or an object with .send(...). Normalize.
    send = getattr(adapter, "send", adapter)
    result = send(
        to=handle, subject=subject, body=body, idempotency_key=idempotency_key
    )

    # A non-'sent' status is a real failure: raise so the caller reschedules and
    # never records a 'sent' for a send that didn't happen.
    status = (result or {}).get("status") if isinstance(result, dict) else None
    if status != "sent":
        err = (result or {}).get("error") if isinstance(result, dict) else None
        raise RuntimeError(
            f"{channel_type} send failed (status={status!r})"
            + (f": {err}" if err else "")
        )
    return result


def _now() -> datetime:
    return datetime.now(timezone.utc)


def dispatch_one(conn, job: dict, warmup_cap: Optional[int] = None) -> str:
    """Dispatch a single already-claimed job. Returns the outcome string:
    'sent' | 'skipped' | 'rate_limited' | 'failed'.

    ``job`` is a dict as returned by :func:`queue.claim_due`. ``warmup_cap`` is
    the per-bucket cap from the warmup schedule; ``None`` disables rate limiting
    for this dispatch (used in tests / when no ramp is configured).
    """
    job_id = job["id"]
    message_id = job["message_id"]
    channel_id = job["channel_id"]
    identity_key = job["identity_key"]
    idempotency_key = job["idempotency_key"]

    # 1. 6A suppression re-check AT DISPATCH (not at enqueue).
    if is_suppressed(conn, identity_key, channel_id):
        queue.mark_skipped(conn, job_id, reason="suppressed_at_dispatch_6a")
        _emit_event(
            conn, message_id, channel_id, "optout",
            {"source": "dispatch_suppression_recheck", "decision": "6A"},
        )
        return "skipped"

    # 2. Rate limit / warmup back-pressure.
    if warmup_cap is not None:
        scope_key = f"channel:{channel_id}:{_now().date().isoformat()}"
        if not rate_limit.check_and_increment(conn, scope_key, warmup_cap):
            # Cap hit: reschedule, do NOT consume budget, do NOT mark failed.
            retry_at = _now() + timedelta(seconds=RATE_LIMITED_BACKOFF_SECONDS)
            queue.mark_failed(
                conn, job_id, error="rate_limited", retry_after=retry_at,
            )
            return "rate_limited"

    # 3. Send via the channel adapter (idempotent on idempotency_key).
    try:
        send_via_channel(channel_id, message_id, idempotency_key, conn=conn)
    except NotImplementedError:
        # Adapter not wired yet (L4). Surface as a terminal-ish failure without
        # spinning: reschedule once with backoff so it's retried when L4 lands.
        retry_at = _now() + timedelta(seconds=RETRY_BACKOFF_SECONDS)
        queue.mark_failed(
            conn, job_id, error="send_via_channel not implemented (awaiting L4)",
            retry_after=retry_at,
        )
        return "failed"
    except Exception as exc:  # transient send error -> retry with backoff
        retry_at = _now() + timedelta(seconds=RETRY_BACKOFF_SECONDS)
        queue.mark_failed(conn, job_id, error=str(exc), retry_after=retry_at)
        return "failed"

    # 4. Record success: mark sent + 'sent' event.
    queue.mark_sent(conn, job_id)
    _emit_event(conn, message_id, channel_id, "open",  # placeholder event shape; real send-event taxonomy is L4's
                {"idempotency_key": idempotency_key, "stage": "dispatched"})
    return "sent"


def dispatch_due_sends(conn=None, limit: int = 10, warmup_cap: Optional[int] = None) -> dict:
    """Claim a batch of due jobs and dispatch each. The Celery beat 'dispatch'
    task calls this. Opens its own connection if one isn't supplied.

    Returns a small summary dict (counts by outcome) for logging / health.
    """
    own_conn = conn is None
    if own_conn:
        from data.db import connect

        conn = connect()
    try:
        jobs = queue.claim_due(conn, limit=limit)
        summary = {"claimed": len(jobs), "sent": 0, "skipped": 0, "rate_limited": 0, "failed": 0}
        for job in jobs:
            outcome = dispatch_one(conn, job, warmup_cap=warmup_cap)
            summary[outcome] = summary.get(outcome, 0) + 1
        return summary
    finally:
        if own_conn:
            conn.close()


def run_pipeline_cycle(conn=None) -> dict:
    """Beat task: drop one L8 ``pipeline_cycle`` job onto the app_jobs queue.

    Reads its seed keywords / platform / send options from env so the schedule
    needs no arguments:
      * ``ORCH_PIPELINE_KEYWORDS`` — comma-separated (required to discover)
      * ``ORCH_PIPELINE_PLATFORM``  — default "all"
      * ``AUTOPILOT_SEND=1`` + ``ORCH_PIPELINE_SEND=1`` — opt into autopilot send
    Enqueue-only (never runs the heavy pipeline inline), mirroring the cron path.
    """
    import os

    own_conn = conn is None
    if own_conn:
        from data.db import connect

        conn = connect()
    try:
        from orchestration.enqueue_cycle import enqueue_cycle

        kws = [k.strip() for k in os.environ.get("ORCH_PIPELINE_KEYWORDS", "").split(",") if k.strip()]
        if not kws:
            return {"skipped": "ORCH_PIPELINE_KEYWORDS not set"}
        payload = {
            "keywords": kws,
            "platform": os.environ.get("ORCH_PIPELINE_PLATFORM", "all"),
            "send": os.environ.get("ORCH_PIPELINE_SEND") == "1",
            "send_channel": os.environ.get("ORCH_PIPELINE_SEND_CHANNEL", "email"),
            "send_cap": int(os.environ.get("ORCH_PIPELINE_SEND_CAP", "25")),
        }
        job_id = enqueue_cycle(conn, payload)
        return {"enqueued_pipeline_cycle": job_id}
    finally:
        if own_conn:
            conn.close()


def enqueue_due_work(conn=None) -> dict:
    """Beat task stub: turn due upstream work (messages that should be sent now)
    into pending ``send_jobs``. The real selection logic (which messages are due,
    how the idempotency_key is derived) is owned by the dispatch layer (L4) /
    follow-up scheduler; this is the registered seam the beat schedule fires.
    """
    # Intentionally a no-op stub for Lane C. Returning a summary keeps the beat
    # task contract (callable, returns a dict) stable for L4 to flesh out.
    return {"enqueued": 0, "note": "enqueue_due_work is a Lane-C stub; L4 wires message->send_job selection"}


# ---- Celery registration (guarded) -----------------------------------------
# Register the beat tasks on the real app only when celery is installed. The
# task wrappers open their own DB connection (no conn passed) so they run
# standalone in a worker.
from .celery_app import app, celery_available  # noqa: E402

if celery_available():  # pragma: no cover - exercised only with celery present

    @app.task(name="orchestration.tasks.dispatch_due_sends", bind=True)
    def _dispatch_due_sends_task(self, limit: int = 10, warmup_cap: Optional[int] = None):
        return dispatch_due_sends(limit=limit, warmup_cap=warmup_cap)

    @app.task(name="orchestration.tasks.enqueue_due_work", bind=True)
    def _enqueue_due_work_task(self):
        return enqueue_due_work()

    @app.task(name="orchestration.tasks.run_pipeline_cycle", bind=True)
    def _run_pipeline_cycle_task(self):
        return run_pipeline_cycle()
