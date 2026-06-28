"""Postgres-backed durable outbox queue for outbound sends.

This is the source of truth for *what gets sent and what was sent*. Celery/Redis
only decides *when* a worker looks for work; the actual queue lives in the
``send_jobs`` table (migration 0002) so a crash can never lose a job or
double-send one.

Crash-safe claim->send->record contract:
  1. ``enqueue(...)`` inserts a 'pending' row. It is idempotent on
     ``idempotency_key`` (``ON CONFLICT DO NOTHING``) — enqueue the same logical
     send a hundred times, get one row.
  2. ``claim_due(limit)`` atomically hands each due row to exactly one worker
     using ``SELECT ... FOR UPDATE SKIP LOCKED``, flips it to 'claimed', sets
     ``claimed_at = now()`` and bumps ``attempts``. Two concurrent workers never
     get the same job. It also reclaims stale 'claimed' rows whose
     ``claimed_at`` is older than the visibility timeout (the previous worker
     crashed mid-send).
  3. The worker performs the send, then calls ``mark_sent`` (terminal success)
     or ``mark_failed`` (reschedule with backoff, or terminal failure once
     attempts are exhausted). A row left 'claimed' by a crash is simply
     reclaimed later — and because the channel adapter is itself keyed on
     ``idempotency_key``, the retry can't double-send.

All functions take an open psycopg connection so the caller controls
transaction boundaries and connection reuse (one connection per worker loop).
"""
from __future__ import annotations

from typing import Any, List, Optional

# Default visibility timeout (seconds). A job 'claimed' longer than this is
# assumed orphaned by a crashed worker and is eligible for reclaim.
DEFAULT_VISIBILITY_TIMEOUT = 300

# Columns returned by claim_due, in order, so callers can build dicts.
JOB_COLUMNS = (
    "id",
    "message_id",
    "channel_id",
    "identity_key",
    "idempotency_key",
    "status",
    "attempts",
    "claimed_at",
    "run_after",
    "last_error",
    "created_at",
)


def _row_to_job(row: Any) -> dict:
    return dict(zip(JOB_COLUMNS, row))


def enqueue(
    conn,
    message_id: int,
    channel_id: int,
    identity_key: str,
    idempotency_key: str,
    run_after=None,
) -> Optional[int]:
    """Enqueue a send. Idempotent on ``idempotency_key``.

    Returns the new job id, or ``None`` if a job with this ``idempotency_key``
    already exists (the insert was a no-op). The ``ON CONFLICT DO NOTHING`` plus
    the UNIQUE constraint are what make repeated enqueues safe — this is the
    front half of the no-double-send guarantee.

    ``run_after`` (a tz-aware datetime) schedules the earliest dispatch time;
    omit it to make the job immediately due.
    """
    with conn.cursor() as cur:
        if run_after is None:
            cur.execute(
                """
                INSERT INTO send_jobs
                    (message_id, channel_id, identity_key, idempotency_key, status)
                VALUES (%s, %s, %s, %s, 'pending')
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (message_id, channel_id, identity_key, idempotency_key),
            )
        else:
            cur.execute(
                """
                INSERT INTO send_jobs
                    (message_id, channel_id, identity_key, idempotency_key, status, run_after)
                VALUES (%s, %s, %s, %s, 'pending', %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (message_id, channel_id, identity_key, idempotency_key, run_after),
            )
        row = cur.fetchone()
    conn.commit()
    return row[0] if row else None


def claim_due(
    conn,
    limit: int = 10,
    visibility_timeout: int = DEFAULT_VISIBILITY_TIMEOUT,
) -> List[dict]:
    """Atomically claim up to ``limit`` due jobs for this worker.

    "Due" = ``status='pending' AND run_after <= now()``, OR a stale
    ``status='claimed'`` job whose ``claimed_at`` is older than
    ``visibility_timeout`` seconds (its worker crashed). Selected rows are locked
    with ``FOR UPDATE SKIP LOCKED`` so a second concurrent ``claim_due`` skips
    them entirely and never returns the same job twice.

    Each claimed row is flipped to 'claimed', stamped with ``claimed_at=now()``
    and has ``attempts`` incremented (so a job that keeps crashing eventually
    exhausts its retries). Returns the claimed jobs as dicts (post-update state).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH due AS (
                SELECT id
                FROM send_jobs
                WHERE (status = 'pending' AND run_after <= now())
                   OR (status = 'claimed'
                       AND claimed_at < now() - (%s * INTERVAL '1 second'))
                ORDER BY run_after
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE send_jobs s
               SET status     = 'claimed',
                   claimed_at = now(),
                   attempts   = s.attempts + 1
              FROM due
             WHERE s.id = due.id
            RETURNING s.id, s.message_id, s.channel_id, s.identity_key,
                      s.idempotency_key, s.status, s.attempts, s.claimed_at,
                      s.run_after, s.last_error, s.created_at
            """,
            (visibility_timeout, limit),
        )
        rows = cur.fetchall()
    conn.commit()
    return [_row_to_job(r) for r in rows]


def mark_sent(conn, job_id: int) -> None:
    """Terminal success: flip a 'claimed' job to 'sent'. Only ever called after
    the channel adapter confirms the send, so 'sent' always means delivered-to-
    provider. Idempotent: re-marking an already-'sent' job is a no-op."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE send_jobs
               SET status = 'sent', last_error = NULL, claimed_at = NULL
             WHERE id = %s AND status IN ('claimed', 'sent')
            """,
            (job_id,),
        )
    conn.commit()


def mark_failed(
    conn,
    job_id: int,
    error: str,
    retry_after=None,
    max_attempts: int = 5,
) -> str:
    """Record a failed attempt.

    If ``retry_after`` is given AND the job still has attempts left
    (``attempts < max_attempts``), the job goes back to 'pending' with
    ``run_after = retry_after`` (caller computes the backoff) so it is retried.
    Otherwise it becomes terminal 'failed'. Returns the resulting status
    ('pending' or 'failed') so the worker can log it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT attempts FROM send_jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        attempts = row[0] if row else max_attempts
        will_retry = retry_after is not None and attempts < max_attempts
        if will_retry:
            cur.execute(
                """
                UPDATE send_jobs
                   SET status = 'pending', last_error = %s,
                       run_after = %s, claimed_at = NULL
                 WHERE id = %s
                """,
                (error, retry_after, job_id),
            )
            new_status = "pending"
        else:
            cur.execute(
                """
                UPDATE send_jobs
                   SET status = 'failed', last_error = %s, claimed_at = NULL
                 WHERE id = %s
                """,
                (error, job_id),
            )
            new_status = "failed"
    conn.commit()
    return new_status


def mark_skipped(conn, job_id: int, reason: str) -> None:
    """Terminal skip: the job was intentionally not sent (e.g. suppressed at
    dispatch per 6A). Records the reason in ``last_error`` for the audit trail."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE send_jobs
               SET status = 'skipped', last_error = %s, claimed_at = NULL
             WHERE id = %s
            """,
            (reason, job_id),
        )
    conn.commit()


def get_job(conn, job_id: int) -> Optional[dict]:
    """Fetch a single job by id (mostly for tests / introspection)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, message_id, channel_id, identity_key, idempotency_key,
                   status, attempts, claimed_at, run_after, last_error, created_at
              FROM send_jobs
             WHERE id = %s
            """,
            (job_id,),
        )
        row = cur.fetchone()
    return _row_to_job(row) if row else None
