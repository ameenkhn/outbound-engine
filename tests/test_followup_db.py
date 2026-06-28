"""Integration tests for the L5 follow-up engine against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set (same guard as
``test_schema_db.py`` / ``test_orchestration_db.py``). Each test builds the FULL
schema — 0001 (frozen) then 0002 (orchestration) — in a throwaway
``test_l5_<pid>`` schema via search_path and drops it afterward, so it never
touches ``public`` and is fully repeatable.

Covers the M6 guarantees:
  * a 'contacted' lead past its D3 window gets its next step enqueued, with the
    deterministic idempotency_key and the channel of its last send.
  * a lead WITH a reply event is NOT advanced (stop-on-reply).
  * a lead WITH an optout event is NOT advanced (stop-on-opt-out).
  * a lead with an identity-wide suppression row is NOT advanced.
  * re-running advance_cadences does NOT double-enqueue (idempotency).
  * MAX_TOUCHES halts further sends.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres \\
        .venv/bin/pytest tests/test_followup_db.py
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres follow-up tests",
)

psycopg = pytest.importorskip("psycopg")

from followups import cadence, engine  # noqa: E402
from personalization.generate import FakeGenerator  # noqa: E402

# Offline generator so follow-up copy is generated with no model/key in tests.
GEN = FakeGenerator()

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
    import data.db as db

    schema = f"test_l5_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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


# ---- seed helpers -----------------------------------------------------------

def _seed_contacted_lead(
    cur,
    identity_key,
    *,
    status="contacted",
    channel_type="email",
    sends=1,
    last_sent_at=None,
    segment="creator",
):
    """Seed a lead + channel + ``sends`` messages. The LAST message is stamped
    with ``last_sent_at`` (defaults to 5 days ago). Returns (lead_id, channel_id).
    """
    last_sent_at = last_sent_at or (datetime.now(timezone.utc) - timedelta(days=5))
    cur.execute(
        "INSERT INTO leads (identity_key, segment, status) VALUES (%s, %s, %s) RETURNING id",
        (identity_key, segment, status),
    )
    lead_id = cur.fetchone()[0]
    handle = f"{uuid.uuid4().hex[:8]}@x.com"
    cur.execute(
        "INSERT INTO channels (lead_id, type, handle) VALUES (%s, %s, %s) RETURNING id",
        (lead_id, channel_type, handle),
    )
    channel_id = cur.fetchone()[0]
    # Insert `sends` messages; the last one carries last_sent_at. Older sends are
    # backdated further so DISTINCT ON picks the intended "last".
    for i in range(sends):
        ts = last_sent_at if i == sends - 1 else (last_sent_at - timedelta(days=10 * (sends - i)))
        cur.execute(
            "INSERT INTO messages (lead_id, channel_id, body, created_at) VALUES (%s, %s, %s, %s)",
            (lead_id, channel_id, "hi", ts),
        )
    return lead_id, channel_id


def _jobs_for(conn, idempotency_key):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_after, channel_id, identity_key FROM send_jobs WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        return cur.fetchall()


# ---- the happy path: a due contacted lead is advanced -----------------------

def test_contacted_lead_past_d3_is_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # one D0 send, 5 days ago -> the D3 follow-up (step 1) is overdue.
        lead_id, channel_id = _seed_contacted_lead(
            cur, "ik-due", sends=1, last_sent_at=now - timedelta(days=5)
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 1

    key = f"followup:{lead_id}:1"
    jobs = _jobs_for(conn, key)
    assert len(jobs) == 1, "exactly one follow-up job enqueued for step 1"
    _, run_after, job_channel, job_identity = jobs[0]
    assert job_channel == channel_id, "follow-up reuses the channel of the last send"
    assert job_identity == "ik-due"


def test_contacted_lead_not_yet_due_is_not_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # last send only 1 day ago -> D3 window has not elapsed.
        _seed_contacted_lead(cur, "ik-early", sends=1, last_sent_at=now - timedelta(days=1))
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 0
    assert summary["skipped_not_due"] == 1


# ---- stop-on-reply ----------------------------------------------------------

def test_lead_with_reply_event_is_not_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(cur, "ik-reply", sends=1, last_sent_at=now - timedelta(days=5))
        cur.execute("INSERT INTO events (lead_id, type) VALUES (%s, 'reply')", (lead_id,))
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["scanned"] == 0, "a replied lead is pruned from the scan entirely"
    assert summary["enqueued"] == 0
    assert _jobs_for(conn, f"followup:{lead_id}:1") == []


# ---- stop-on-opt-out (event) ------------------------------------------------

def test_lead_with_optout_event_is_not_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(cur, "ik-optev", sends=1, last_sent_at=now - timedelta(days=5))
        cur.execute("INSERT INTO events (lead_id, type) VALUES (%s, 'optout')", (lead_id,))
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["scanned"] == 0
    assert summary["enqueued"] == 0


# ---- suppression: identity-wide opt-out -------------------------------------

def test_lead_with_identity_wide_suppression_is_not_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(cur, "ik-supp", sends=1, last_sent_at=now - timedelta(days=5))
        # identity-wide opt-out (channel_type NULL) — blocks every channel.
        cur.execute("INSERT INTO suppression (identity_key, reason) VALUES ('ik-supp', 'optout')")
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["scanned"] == 0
    assert summary["enqueued"] == 0
    assert _jobs_for(conn, f"followup:{lead_id}:1") == []


def test_lead_with_channel_specific_suppression_is_not_advanced(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(
            cur, "ik-suppch", channel_type="email", sends=1, last_sent_at=now - timedelta(days=5)
        )
        # channel-specific hardbounce on EMAIL — blocks the email follow-up.
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) "
            "VALUES ('ik-suppch', 'email', 'hardbounce')"
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["scanned"] == 0
    assert summary["enqueued"] == 0


def test_unrelated_channel_suppression_does_not_block(conn):
    """A whatsapp-scoped suppression must NOT stop an EMAIL follow-up for the
    same identity (channel-specific means channel-specific)."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(
            cur, "ik-wa", channel_type="email", sends=1, last_sent_at=now - timedelta(days=5)
        )
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) "
            "VALUES ('ik-wa', 'whatsapp', 'complaint')"
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 1, "an unrelated-channel suppression does not stop the email cadence"


# ---- excluded statuses ------------------------------------------------------

def test_non_contacted_statuses_are_excluded(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        for st in ("new", "queued", "replied", "in_conversation",
                   "demo_booked", "converted", "dead", "opted_out"):
            _seed_contacted_lead(
                cur, f"ik-st-{st}", status=st, sends=1, last_sent_at=now - timedelta(days=5)
            )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["scanned"] == 0, "only 'contacted' leads are in the cadence"
    assert summary["enqueued"] == 0


# ---- idempotency: re-running does not double-enqueue ------------------------

def test_rerun_does_not_double_enqueue(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(cur, "ik-idem", sends=1, last_sent_at=now - timedelta(days=5))
    conn.commit()

    s1 = engine.advance_cadences(conn, now, generator=GEN)
    s2 = engine.advance_cadences(conn, now, generator=GEN)

    assert s1["enqueued"] == 1
    assert s2["enqueued"] == 0
    assert s2["already_enqueued"] == 1
    # exactly one job for step 1, and no extra orphan messages from the re-run.
    assert len(_jobs_for(conn, f"followup:{lead_id}:1")) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM messages WHERE lead_id = %s", (lead_id,))
        # 1 original D0 send + exactly 1 follow-up message (not 2).
        assert cur.fetchone()[0] == 2


# ---- MAX_TOUCHES halts further sends ----------------------------------------

def test_max_touches_halts_further_sends(conn):
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # MAX_TOUCHES (3) sends already done; last one well in the past.
        lead_id, _ = _seed_contacted_lead(
            cur, "ik-capped", sends=cadence.MAX_TOUCHES, last_sent_at=now - timedelta(days=30)
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 0
    assert summary["skipped_capped"] == 1
    # no step-3 job exists (there is no step 3).
    assert _jobs_for(conn, f"followup:{lead_id}:3") == []


def test_second_followup_enqueued_after_first(conn):
    """A lead that has had 2 sends (D0 + D3) past the D7 window gets step 2."""
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        lead_id, channel_id = _seed_contacted_lead(
            cur, "ik-second", sends=2, last_sent_at=now - timedelta(days=6)
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 1
    assert len(_jobs_for(conn, f"followup:{lead_id}:2")) == 1


# ---- empty-body fix: follow-ups generate real copy via L3 -------------------

def test_followup_message_has_generated_body(conn):
    """The follow-up message body is REAL generated copy (via L3), not blank.

    Regression test for the empty-body bug: the engine used to insert an empty
    placeholder ``messages`` row, so follow-up sends would be blank. Now it
    generates copy through ``personalization.generate`` (a FakeGenerator here).
    """
    now = datetime.now(timezone.utc)
    signal = "scale your yoga coaching business"
    with conn.cursor() as cur:
        lead_id, _ = _seed_contacted_lead(
            cur, "ik-body", sends=1, last_sent_at=now - timedelta(days=5)
        )
        # Give the lead a scraped signal so the generated copy can reference it.
        cur.execute(
            "UPDATE leads SET attributes = %s::jsonb, niche = %s WHERE id = %s",
            (json.dumps({"ad_text": signal, "category": "yoga"}), "yoga", lead_id),
        )
    conn.commit()

    summary = engine.advance_cadences(conn, now, generator=GEN)
    assert summary["enqueued"] == 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT body, angle, variant FROM messages "
            "WHERE lead_id = %s AND angle LIKE 'followup%%'",
            (lead_id,),
        )
        body, angle, variant = cur.fetchone()
    assert body and body.strip(), "follow-up body must NOT be empty (the fixed bug)"
    assert "STOP" in body, "generated copy carries the opt-out line"
    assert any(w in body for w in signal.split()[:3]), "copy references the lead's signal"
    assert angle == "followup_step_1"          # cadence marker preserved
    assert variant.startswith("followup:")     # value-prop angle recorded
