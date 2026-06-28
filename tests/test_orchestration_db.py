"""Integration tests for Lane C against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set (same guard as
``test_schema_db.py``). Each test builds the FULL schema — 0001 (frozen) then
0002 (orchestration) — in a throwaway ``test_orch_<pid>`` schema via search_path
and drops it afterward, so it never touches ``public`` and is fully repeatable.

Covers the Lane-C guarantees:
  * enqueue is idempotent on idempotency_key (second enqueue is a no-op).
  * claim_due returns a pending due job, marks it 'claimed'; a second concurrent
    claim (separate connection/txn) does NOT return the same job (SKIP LOCKED).
  * the dispatch path SKIPS a job whose identity has an identity-wide
    suppression row, and also skips a channel-specific suppression for THIS
    channel (6A, re-checked at dispatch).
  * rate_limit.check_and_increment returns False at the cap and does not
    over-increment.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres \\
        .venv/bin/pytest tests/test_orchestration_db.py
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres orchestration tests",
)

psycopg = pytest.importorskip("psycopg")

from orchestration import queue, rate_limit, tasks  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "data" / "migrations"
SQL_0001 = (MIGRATIONS / "0001_init_schema.sql").read_text()
SQL_0002 = (MIGRATIONS / "0002_orchestration.sql").read_text()


def _connect(schema: str):
    import data.db as db

    c = psycopg.connect(db.get_dsn())
    with c.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
    c.commit()
    return c


@pytest.fixture()
def schema_name():
    """Create an isolated schema with the full 0001+0002 stack; drop it after."""
    import data.db as db

    schema = f"test_orch_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    setup = psycopg.connect(db.get_dsn())
    try:
        with setup.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(SQL_0001)
            cur.execute(SQL_0002)
        setup.commit()
        yield schema
    finally:
        setup.rollback()
        with setup.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        setup.commit()
        setup.close()


@pytest.fixture()
def conn(schema_name):
    c = _connect(schema_name)
    try:
        yield c
    finally:
        c.close()


# ---- fixtures: seed a lead + channel + message ------------------------------

def _seed_message(cur, identity_key="ik-1", channel_type="email", handle=None):
    """Insert a lead, a channel, and a message; return (message_id, channel_id)."""
    handle = handle or f"{uuid.uuid4().hex[:8]}@x.com"
    cur.execute(
        "INSERT INTO leads (identity_key, segment, status) VALUES (%s, 'creator', 'queued') RETURNING id",
        (identity_key,),
    )
    lead_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO channels (lead_id, type, handle) VALUES (%s, %s, %s) RETURNING id",
        (lead_id, channel_type, handle),
    )
    channel_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO messages (lead_id, channel_id, body) VALUES (%s, %s, 'hi') RETURNING id",
        (lead_id, channel_id),
    )
    message_id = cur.fetchone()[0]
    return message_id, channel_id


# ---- enqueue idempotency ----------------------------------------------------

def test_enqueue_is_idempotent_on_idempotency_key(conn):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-enq")
    conn.commit()

    first = queue.enqueue(conn, message_id, channel_id, "ik-enq", "idem-AAA")
    second = queue.enqueue(conn, message_id, channel_id, "ik-enq", "idem-AAA")

    assert first is not None, "first enqueue should create a job"
    assert second is None, "second enqueue with same idempotency_key is a no-op"

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM send_jobs WHERE idempotency_key = 'idem-AAA'")
        assert cur.fetchone()[0] == 1, "exactly one row for the idempotency_key"


# ---- claim_due + SKIP LOCKED -----------------------------------------------

def test_claim_due_returns_and_marks_claimed(conn):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-claim")
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-claim", "idem-CLAIM")

    claimed = queue.claim_due(conn, limit=10)
    ids = [j["idempotency_key"] for j in claimed]
    assert "idem-CLAIM" in ids
    job = next(j for j in claimed if j["idempotency_key"] == "idem-CLAIM")
    assert job["status"] == "claimed"
    assert job["attempts"] == 1
    assert job["claimed_at"] is not None


def test_concurrent_claim_skips_locked_job(conn, schema_name):
    """A job claimed (and held in an open txn) by conn A must NOT be returned to
    a concurrent claim from conn B — that's FOR UPDATE SKIP LOCKED."""
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-skip")
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-skip", "idem-SKIP")

    # Connection A: claim inside a transaction WITHOUT committing, so the row
    # lock from FOR UPDATE / the UPDATE is still held.
    conn_a = _connect(schema_name)
    conn_a.autocommit = False
    try:
        with conn_a.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id FROM send_jobs
                     WHERE status = 'pending' AND run_after <= now()
                     ORDER BY run_after
                     FOR UPDATE SKIP LOCKED
                     LIMIT 10
                )
                UPDATE send_jobs s SET status='claimed', claimed_at=now(),
                       attempts=s.attempts+1
                  FROM due WHERE s.id = due.id
                RETURNING s.idempotency_key
                """
            )
            a_claimed = [r[0] for r in cur.fetchall()]
        assert "idem-SKIP" in a_claimed  # A grabbed it
        # ... A has NOT committed; the row is locked & now 'claimed' within A's txn.

        # Connection B: concurrent claim. It must skip the locked row.
        conn_b = _connect(schema_name)
        try:
            b_claimed = queue.claim_due(conn_b, limit=10)
            b_keys = [j["idempotency_key"] for j in b_claimed]
            assert "idem-SKIP" not in b_keys, "SKIP LOCKED must hide A's in-flight job from B"
        finally:
            conn_b.close()
    finally:
        conn_a.rollback()
        conn_a.close()


# ---- 6A dispatch-time suppression re-check ---------------------------------

def test_dispatch_skips_identity_wide_suppression_6a(conn):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-optout", channel_type="email")
        # identity-wide opt-out (channel_type NULL) — blocks every channel.
        cur.execute(
            "INSERT INTO suppression (identity_key, reason) VALUES ('ik-optout', 'optout')"
        )
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-optout", "idem-OPTOUT")
    [job] = [j for j in queue.claim_due(conn) if j["idempotency_key"] == "idem-OPTOUT"]

    outcome = tasks.dispatch_one(conn, job)
    assert outcome == "skipped"

    after = queue.get_job(conn, job["id"])
    assert after["status"] == "skipped"
    # an optout/skip event was recorded, no send happened.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events WHERE message_id = %s AND type = 'optout'", (message_id,))
        assert cur.fetchone()[0] == 1


def test_dispatch_skips_channel_specific_suppression_6a(conn):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-bounce", channel_type="email")
        # channel-specific hardbounce on EMAIL — blocks this email job.
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) "
            "VALUES ('ik-bounce', 'email', 'hardbounce')"
        )
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-bounce", "idem-BOUNCE")
    [job] = [j for j in queue.claim_due(conn) if j["idempotency_key"] == "idem-BOUNCE"]

    outcome = tasks.dispatch_one(conn, job)
    assert outcome == "skipped"
    assert queue.get_job(conn, job["id"])["status"] == "skipped"


def test_dispatch_does_not_skip_unrelated_channel_suppression_6a(conn):
    """A whatsapp-scoped suppression must NOT skip an email job for the same
    identity (channel-specific means channel-specific)."""
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-wa", channel_type="email")
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) "
            "VALUES ('ik-wa', 'whatsapp', 'complaint')"
        )
    conn.commit()
    assert tasks.is_suppressed(conn, "ik-wa", channel_id) is False


# ---- rate limit -------------------------------------------------------------

def test_rate_limit_blocks_at_cap_without_over_incrementing(conn):
    wd = date.today()
    key = f"email:example.com:{wd.isoformat()}"
    cap = 3

    results = [rate_limit.check_and_increment(conn, key, cap, wd) for _ in range(5)]
    # First 3 allowed, the rest blocked.
    assert results == [True, True, True, False, False]
    # And the counter never exceeded the cap.
    assert rate_limit.current_count(conn, key) == cap


def test_rate_limit_zero_cap_never_allows(conn):
    key = f"email:zero.com:{date.today().isoformat()}"
    assert rate_limit.check_and_increment(conn, key, 0) is False
    assert rate_limit.current_count(conn, key) == 0


# ---- crash-safety: reclaim a stale claimed job ------------------------------

def test_stale_claimed_job_is_reclaimable(conn):
    """A job stuck in 'claimed' past the visibility timeout (worker crashed) is
    re-handed-out by claim_due."""
    with conn.cursor() as cur:
        message_id, channel_id = _seed_message(cur, identity_key="ik-stale")
    conn.commit()
    job_id = queue.enqueue(conn, message_id, channel_id, "ik-stale", "idem-STALE")
    # Simulate a crash: force the job 'claimed' with an old claimed_at.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE send_jobs SET status='claimed', claimed_at = now() - interval '1 hour' WHERE id = %s",
            (job_id,),
        )
    conn.commit()

    reclaimed = queue.claim_due(conn, limit=10, visibility_timeout=300)
    keys = [j["idempotency_key"] for j in reclaimed]
    assert "idem-STALE" in keys, "stale claimed job should be reclaimable after the visibility timeout"
