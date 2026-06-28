"""Integration tests for the L4 email dispatch wiring against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set (same guard + throwaway-
schema pattern as ``test_schema_db.py`` / ``test_orchestration_db.py``): builds
0001 (frozen) then 0002 (orchestration) in an isolated ``test_l4_<pid>`` schema
and drops it afterward.

Proves the end-to-end dispatch with a registered FakeTransport email adapter:
  * a due email send_job dispatches -> send_job flips to 'sent', a dispatch
    event is recorded, the warmup rate counter is incremented, AND the fake
    transport actually saw the send with the right recipient + idempotency_key;
  * a suppressed identity is SKIPPED (job 'skipped', no transport send).

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres \\
        .venv/bin/pytest tests/test_dispatch_db.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres dispatch tests",
)

psycopg = pytest.importorskip("psycopg")

from dispatch import registry  # noqa: E402
from dispatch.email.adapter import EmailAdapter, FakeTransport  # noqa: E402
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
    import data.db as db

    schema = f"test_l4_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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


@pytest.fixture()
def fake_email_adapter():
    """Register an email adapter backed by a FakeTransport for the duration of
    the test, restoring whatever was registered before (the real default)."""
    fake = FakeTransport()
    previous = registry.registered_channels().get("email")
    registry.register("email", EmailAdapter(transport=fake, from_addr="from@sender.test"))
    try:
        yield fake
    finally:
        if previous is not None:
            registry.register("email", previous)
        else:  # pragma: no cover - default is always present in practice
            registry.unregister("email")


def _seed_email_message(cur, identity_key, handle):
    """Insert a lead + email channel + message; return (message_id, channel_id)."""
    cur.execute(
        "INSERT INTO leads (identity_key, segment, status) VALUES (%s, 'creator', 'queued') RETURNING id",
        (identity_key,),
    )
    lead_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO channels (lead_id, type, handle) VALUES (%s, 'email', %s) RETURNING id",
        (lead_id, handle),
    )
    channel_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO messages (lead_id, channel_id, subject, body) "
        "VALUES (%s, %s, 'Subject line', 'hello body') RETURNING id",
        (lead_id, channel_id),
    )
    message_id = cur.fetchone()[0]
    return message_id, channel_id


def test_dispatch_email_send_marks_sent_records_event_and_rate(conn, fake_email_adapter):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_email_message(cur, "ik-send", "dest@example.com")
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-send", "idem-SEND")
    [job] = [j for j in queue.claim_due(conn) if j["idempotency_key"] == "idem-SEND"]

    cap = 100  # warmup cap high enough to allow this send
    outcome = tasks.dispatch_one(conn, job, warmup_cap=cap)
    assert outcome == "sent"

    # send_job flipped to terminal 'sent'.
    assert queue.get_job(conn, job["id"])["status"] == "sent"

    # The FakeTransport actually received the send with the right recipient,
    # subject, body, and idempotency_key (idempotency passed through to provider).
    assert len(fake_email_adapter.sent) == 1
    rec = fake_email_adapter.sent[0]
    assert rec["to"] == "dest@example.com"
    assert rec["subject"] == "Subject line"
    assert rec["idempotency_key"] == "idem-SEND"

    # A dispatch event was recorded for this message.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT type, meta FROM events WHERE message_id = %s ORDER BY id DESC LIMIT 1",
            (message_id,),
        )
        ev_type, ev_meta = cur.fetchone()
    assert ev_meta.get("idempotency_key") == "idem-SEND"
    assert ev_meta.get("stage") == "dispatched"

    # The warmup rate counter for this channel/day was incremented to 1.
    scope_key = f"channel:{channel_id}:{tasks._now().date().isoformat()}"
    assert rate_limit.current_count(conn, scope_key) == 1


def test_dispatch_suppressed_identity_is_skipped_no_send(conn, fake_email_adapter):
    with conn.cursor() as cur:
        message_id, channel_id = _seed_email_message(cur, "ik-supp", "blocked@example.com")
        # identity-wide opt-out blocks every channel for this identity.
        cur.execute(
            "INSERT INTO suppression (identity_key, reason) VALUES ('ik-supp', 'optout')"
        )
    conn.commit()
    queue.enqueue(conn, message_id, channel_id, "ik-supp", "idem-SUPP")
    [job] = [j for j in queue.claim_due(conn) if j["idempotency_key"] == "idem-SUPP"]

    outcome = tasks.dispatch_one(conn, job, warmup_cap=100)
    assert outcome == "skipped"
    assert queue.get_job(conn, job["id"])["status"] == "skipped"

    # No send reached the transport.
    assert fake_email_adapter.sent == []

    # An optout event was recorded for the skipped message.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM events WHERE message_id = %s AND type = 'optout'",
            (message_id,),
        )
        assert cur.fetchone()[0] == 1
