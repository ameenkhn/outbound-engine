"""Integration tests for inbound capture against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set (same guard as
``test_schema_db.py`` / ``test_orchestration_db.py``). Each test builds the FULL
schema — 0001 (frozen) then 0002 (orchestration) — in a throwaway
``test_inbound_<pid>`` schema via search_path and drops it afterward, so it never
touches ``public`` and is fully repeatable.

Covers the L6 inbound-capture guarantees:
  * an OPT-OUT reply writes a 'reply' + 'optout' event, inserts an IDENTITY-WIDE
    suppression (channel_type NULL, reason='optout' — decision 6A), and sets
    leads.status='opted_out'.
  * a NORMAL warm reply writes a 'reply' event, sets leads.status='replied'
    (stop-on-reply), and returns a human-handoff payload carrying the booking
    link + a lead summary.
  * a HARDBOUNCE writes a 'bounce' event, inserts a CHANNEL-SPECIFIC suppression
    (channel_type='email', reason='hardbounce' — 6A), and marks the channel
    deliverable=FALSE.
  * the 6A CHECK constraint is satisfied in every path (the inserts would raise
    CheckViolation otherwise — so a green run proves 6A compliance).

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres \\
        .venv/bin/pytest tests/test_inbound_db.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres inbound tests",
)

psycopg = pytest.importorskip("psycopg")

from inbound import handlers  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "data" / "migrations"
SQL_0001 = (MIGRATIONS / "0001_init_schema.sql").read_text()
SQL_0002 = (MIGRATIONS / "0002_orchestration.sql").read_text()

BOOKING_LINK = "https://cal.exly.com/demo/abc123"


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

    schema = f"test_inbound_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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


# ---- seed helpers ----------------------------------------------------------


def _seed_lead_email(cur, identity_key="ik-1", handle="lead@example.org", status="contacted"):
    """Insert a lead + an email channel; return (lead_id, channel_id)."""
    cur.execute(
        "INSERT INTO leads (identity_key, segment, niche, platform, follower_band, "
        "icp_score, status, source) "
        "VALUES (%s, 'creator', 'fitness', 'instagram', '10k-50k', 72, %s, 'meta_ads') "
        "RETURNING id",
        (identity_key, status),
    )
    lead_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO channels (lead_id, type, handle) VALUES (%s, 'email', %s) RETURNING id",
        (lead_id, handle),
    )
    channel_id = cur.fetchone()[0]
    return lead_id, channel_id


def _events(cur, lead_id):
    cur.execute("SELECT type, intent, sentiment FROM events WHERE lead_id = %s ORDER BY id", (lead_id,))
    return cur.fetchall()


def _lead_status(cur, lead_id):
    cur.execute("SELECT status FROM leads WHERE id = %s", (lead_id,))
    return cur.fetchone()[0]


# ---- opt-out reply ---------------------------------------------------------


def test_optout_reply_writes_identity_wide_suppression(conn):
    with conn.cursor() as cur:
        lead_id, channel_id = _seed_lead_email(cur, "ik-optout", "stop@example.org")
    conn.commit()

    result = handlers.handle_inbound_email(
        conn, "STOP@example.org", "Please STOP emailing me", booking_link=BOOKING_LINK
    )

    assert result["action"] == "opted_out"
    assert result["lead_id"] == lead_id

    with conn.cursor() as cur:
        # reply + optout events recorded.
        types = [r[0] for r in _events(cur, lead_id)]
        assert "reply" in types
        assert "optout" in types
        # status flipped.
        assert _lead_status(cur, lead_id) == "opted_out"
        # IDENTITY-WIDE suppression: channel_type IS NULL, reason 'optout' (6A).
        cur.execute(
            "SELECT channel_type, reason FROM suppression WHERE identity_key = %s",
            ("ik-optout",),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] is None  # identity-wide
        assert rows[0][1] == "optout"
        # email channel locally flagged opted_out.
        cur.execute("SELECT opted_out FROM channels WHERE id = %s", (channel_id,))
        assert cur.fetchone()[0] is True


def test_optout_reply_is_idempotent(conn):
    with conn.cursor() as cur:
        lead_id, _ = _seed_lead_email(cur, "ik-idem", "again@example.org")
    conn.commit()

    handlers.handle_inbound_email(conn, "again@example.org", "unsubscribe", booking_link=BOOKING_LINK)
    # Re-process the same opt-out: ON CONFLICT DO NOTHING keeps one suppression row.
    handlers.handle_inbound_email(conn, "again@example.org", "unsubscribe", booking_link=BOOKING_LINK)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM suppression WHERE identity_key = %s", ("ik-idem",))
        assert cur.fetchone()[0] == 1
        assert _lead_status(cur, lead_id) == "opted_out"


# ---- warm reply ------------------------------------------------------------


def test_warm_reply_sets_replied_and_returns_handoff(conn):
    with conn.cursor() as cur:
        lead_id, _ = _seed_lead_email(cur, "ik-warm", "warm@example.org")
    conn.commit()

    result = handlers.handle_inbound_email(
        conn, "Warm@example.org", "This sounds interesting, let's talk!", booking_link=BOOKING_LINK
    )

    # handoff payload shape.
    assert result["action"] == "handoff"
    assert result["booking_link"] == BOOKING_LINK
    assert result["lead_id"] == lead_id
    assert result["lead"]["identity_key"] == "ik-warm"
    assert result["lead"]["niche"] == "fitness"
    assert result["intent"] == "interested"
    assert "let's talk" in result["reply_excerpt"].lower()

    with conn.cursor() as cur:
        types = [r[0] for r in _events(cur, lead_id)]
        assert types == ["reply"]  # no optout event for a warm reply.
        assert _lead_status(cur, lead_id) == "replied"
        # No suppression for a warm reply.
        cur.execute("SELECT count(*) FROM suppression WHERE identity_key = %s", ("ik-warm",))
        assert cur.fetchone()[0] == 0


def test_warm_reply_does_not_regress_advanced_lead(conn):
    with conn.cursor() as cur:
        lead_id, _ = _seed_lead_email(
            cur, "ik-adv", "adv@example.org", status="in_conversation"
        )
    conn.commit()

    handlers.handle_inbound_email(conn, "adv@example.org", "one more question?", booking_link=BOOKING_LINK)

    with conn.cursor() as cur:
        # A late reply must NOT pull an in_conversation lead back to 'replied'.
        assert _lead_status(cur, lead_id) == "in_conversation"


def test_unknown_sender_is_ignored(conn):
    result = handlers.handle_inbound_email(
        conn, "nobody@nowhere.org", "hi", booking_link=BOOKING_LINK
    )
    assert result["action"] == "ignored"
    assert result["reason"] == "unknown_sender"


# ---- bounce / complaint ----------------------------------------------------


def test_hardbounce_writes_channel_specific_suppression(conn):
    with conn.cursor() as cur:
        lead_id, channel_id = _seed_lead_email(cur, "ik-bounce", "bounce@example.org")
    conn.commit()

    result = handlers.handle_bounce(conn, "bounce@example.org", "email", "hardbounce")

    assert result["action"] == "suppressed_channel"
    assert result["reason"] == "hardbounce"

    with conn.cursor() as cur:
        types = [r[0] for r in _events(cur, lead_id)]
        assert types == ["bounce"]
        # CHANNEL-SPECIFIC suppression: channel_type='email' SET, reason hardbounce (6A).
        cur.execute(
            "SELECT channel_type, reason FROM suppression WHERE identity_key = %s",
            ("ik-bounce",),
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "email"  # channel-specific, NOT identity-wide
        assert rows[0][1] == "hardbounce"
        # channel marked undeliverable.
        cur.execute("SELECT deliverable FROM channels WHERE id = %s", (channel_id,))
        assert cur.fetchone()[0] is False
        # lead status untouched (a bounce isn't an opt-out).
        assert _lead_status(cur, lead_id) == "contacted"


def test_complaint_writes_channel_specific_suppression(conn):
    with conn.cursor() as cur:
        lead_id, _ = _seed_lead_email(cur, "ik-compl", "spam@example.org")
    conn.commit()

    result = handlers.handle_bounce(conn, "spam@example.org", "email", "complaint")
    assert result["reason"] == "complaint"

    with conn.cursor() as cur:
        types = [r[0] for r in _events(cur, lead_id)]
        assert types == ["complaint"]
        cur.execute(
            "SELECT channel_type, reason FROM suppression WHERE identity_key = %s",
            ("ik-compl",),
        )
        ct, reason = cur.fetchone()
        assert ct == "email"
        assert reason == "complaint"


def test_bounce_unknown_channel_is_ignored(conn):
    result = handlers.handle_bounce(conn, "ghost@example.org", "email", "hardbounce")
    assert result["action"] == "ignored"


def test_bounce_rejects_bad_kind(conn):
    with pytest.raises(ValueError):
        handlers.handle_bounce(conn, "x@example.org", "email", "softbounce")


def test_bounce_is_idempotent(conn):
    with conn.cursor() as cur:
        _seed_lead_email(cur, "ik-bdup", "bdup@example.org")
    conn.commit()

    handlers.handle_bounce(conn, "bdup@example.org", "email", "hardbounce")
    handlers.handle_bounce(conn, "bdup@example.org", "email", "hardbounce")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM suppression WHERE identity_key = %s", ("ik-bdup",))
        assert cur.fetchone()[0] == 1
