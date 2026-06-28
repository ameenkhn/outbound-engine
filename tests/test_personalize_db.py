"""Integration tests for personalize_and_queue against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set (so the no-DB suite still
runs everywhere). Each test builds the FULL schema (0001 + 0002) in a throwaway
``test_l3_<pid>`` namespace via search_path and drops it afterward — the same
isolation pattern as tests/test_schema_db.py.

Proves the generate -> guardrail -> queue contract end to end with a
FakeGenerator (no model, no key):
  * a PASSING (signal-citing) message writes a ``messages`` row AND enqueues a
    ``send_jobs`` row,
  * a FAILING (mail-merge-only) message writes nothing and enqueues nothing.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres .venv/bin/pytest tests/test_personalize_db.py
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres personalization tests",
)

psycopg = pytest.importorskip("psycopg")

from personalization.generate import (  # noqa: E402  (after importorskip guard)
    FakeGenerator,
    personalize_and_queue,
)

_MIG = Path(__file__).resolve().parent.parent / "data" / "migrations"
SQL_0001 = (_MIG / "0001_init_schema.sql").read_text()
SQL_0002 = (_MIG / "0002_orchestration.sql").read_text()

# A lead with real scraped signals so the personalized FakeGenerator passes P4.
LEAD_ATTRS = {
    "advertiser": "Aanya Coaching",
    "ad_text": "Enroll now in our ICF-accredited life coach certification. Batch starting soon.",
    "category": "Coach",
    "subcategory": "Life Coach",
    "followers": "12.5K",
    "follower_count": 12500,
    "city": "Mumbai",
    "socials": {"instagram": "aanyacoaching"},
}


@pytest.fixture()
def conn():
    """Connection with 0001 + 0002 built in an isolated throwaway schema."""
    import data.db as db

    schema = "test_l3_{0}_{1}".format(os.getpid(), uuid.uuid4().hex[:8])
    c = psycopg.connect(db.get_dsn())
    try:
        with c.cursor() as cur:
            cur.execute('CREATE SCHEMA "{0}"'.format(schema))
            cur.execute('SET search_path TO "{0}"'.format(schema))
            cur.execute(SQL_0001)
            cur.execute(SQL_0002)
        c.commit()
        with c.cursor() as cur:
            cur.execute('SET search_path TO "{0}"'.format(schema))
        c.commit()
        yield c
    finally:
        c.rollback()
        with c.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS "{0}" CASCADE'.format(schema))
        c.commit()
        c.close()


def _seed_lead_and_channel(conn, attrs):
    """Insert a lead (with attributes) + an email channel; return (lead_id, channel_id)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO leads (identity_key, segment, niche, status, attributes)
            VALUES (%s, 'creator', 'nlp_mindset', 'new', %s)
            RETURNING id
            """,
            ("ik-{0}".format(uuid.uuid4().hex[:8]), json.dumps(attrs)),
        )
        lead_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO channels (lead_id, type, handle) VALUES (%s, 'email', %s) RETURNING id",
            (lead_id, "lead-{0}@example.com".format(uuid.uuid4().hex[:6])),
        )
        channel_id = cur.fetchone()[0]
    conn.commit()
    return lead_id, channel_id


def _seed_campaign(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO campaigns (name, segment) VALUES ('L3 test', 'creator') RETURNING id"
        )
        cid = cur.fetchone()[0]
    conn.commit()
    return cid


def _counts(conn, lead_id):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM messages WHERE lead_id = %s", (lead_id,))
        n_msgs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM send_jobs WHERE message_id IN (SELECT id FROM messages WHERE lead_id = %s)", (lead_id,))
        n_jobs = cur.fetchone()[0]
    return n_msgs, n_jobs


# ---------------------------------------------------------------------------
# PASS path: writes a message + enqueues a job.
# ---------------------------------------------------------------------------

def test_passing_message_writes_row_and_enqueues(conn):
    lead_id, channel_id = _seed_lead_and_channel(conn, LEAD_ATTRS)
    campaign_id = _seed_campaign(conn)

    result = personalize_and_queue(
        conn,
        lead_id=lead_id,
        channel_id=channel_id,
        segment="creator",
        angle="cost_saving",
        campaign_id=campaign_id,
        generator=FakeGenerator(),  # personalized -> passes P4
    )

    assert result["status"] == "queued", result
    assert result["message_id"]
    assert result["job_id"]

    n_msgs, n_jobs = _counts(conn, lead_id)
    assert n_msgs == 1
    assert n_jobs == 1

    # The persisted row carries variant/angle and queued status.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT delivery_status, angle, variant, body FROM messages WHERE id = %s",
            (result["message_id"],),
        )
        status, angle, variant, body = cur.fetchone()
    assert status == "queued"
    assert angle == "cost_saving"
    assert variant == "creator:cost_saving"
    assert body and "STOP" in body  # opt-out line landed


def test_passing_idempotent_on_rerun(conn):
    """Same lead/channel/campaign/angle -> the enqueue is idempotent (no dup job)."""
    lead_id, channel_id = _seed_lead_and_channel(conn, LEAD_ATTRS)
    campaign_id = _seed_campaign(conn)

    r1 = personalize_and_queue(
        conn, lead_id, channel_id, "creator", "cost_saving", campaign_id, FakeGenerator()
    )
    r2 = personalize_and_queue(
        conn, lead_id, channel_id, "creator", "cost_saving", campaign_id, FakeGenerator()
    )
    assert r1["status"] == "queued"
    assert r2["status"] == "queued"
    # Both share the deterministic idempotency_key; the 2nd enqueue is a no-op.
    assert r1["idempotency_key"] == r2["idempotency_key"]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM send_jobs WHERE idempotency_key = %s", (r1["idempotency_key"],))
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# FAIL path: mail-merge writes nothing and enqueues nothing.
# ---------------------------------------------------------------------------

def test_mail_merge_message_writes_nothing(conn):
    lead_id, channel_id = _seed_lead_and_channel(conn, LEAD_ATTRS)
    campaign_id = _seed_campaign(conn)

    result = personalize_and_queue(
        conn,
        lead_id=lead_id,
        channel_id=channel_id,
        segment="creator",
        angle="cost_saving",
        campaign_id=campaign_id,
        generator=FakeGenerator(mail_merge_only=True),  # blocked by P4
    )

    assert result["status"] == "rejected", result
    assert result["reason"]

    n_msgs, n_jobs = _counts(conn, lead_id)
    assert n_msgs == 0, "mail-merge message must not be persisted"
    assert n_jobs == 0, "mail-merge message must not be enqueued"


def test_lead_with_no_signals_is_rejected(conn):
    """A lead with empty attributes can't be personalized -> rejected, nothing written."""
    lead_id, channel_id = _seed_lead_and_channel(conn, {})
    campaign_id = _seed_campaign(conn)

    result = personalize_and_queue(
        conn, lead_id, channel_id, "creator", "cost_saving", campaign_id, FakeGenerator()
    )
    assert result["status"] == "rejected", result
    n_msgs, n_jobs = _counts(conn, lead_id)
    assert n_msgs == 0
    assert n_jobs == 0


def test_default_angle_used_when_none(conn):
    """angle=None falls back to pick_angle('creator') == 'cost_saving'."""
    lead_id, channel_id = _seed_lead_and_channel(conn, LEAD_ATTRS)
    campaign_id = _seed_campaign(conn)
    result = personalize_and_queue(
        conn, lead_id, channel_id, "creator", None, campaign_id, FakeGenerator()
    )
    assert result["status"] == "queued"
    assert result["angle"] == "cost_saving"
