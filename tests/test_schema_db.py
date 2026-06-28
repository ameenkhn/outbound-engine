"""Integration tests for the L0 schema against a real Postgres.

Skipped automatically when ``DATABASE_URL`` is not set, so the no-DB structural
tests still run everywhere. Each test builds the schema in a throwaway
``test_l0_<pid>`` schema (via search_path) and drops it afterward, so it never
touches ``public`` and is fully repeatable.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres .venv/bin/pytest tests/test_schema_db.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres schema tests",
)

psycopg = pytest.importorskip("psycopg")

SQL = (Path(__file__).resolve().parent.parent / "data" / "migrations" / "0001_init_schema.sql").read_text()


@pytest.fixture()
def conn():
    """A connection with the schema built in an isolated, throwaway namespace."""
    import data.db as db

    schema = f"test_l0_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    c = psycopg.connect(db.get_dsn())
    try:
        with c.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(SQL)  # types + tables land in the throwaway schema
        c.commit()
        # keep search_path pinned for the test session
        with c.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        c.commit()
        yield c
    finally:
        c.rollback()
        with c.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        c.commit()
        c.close()


def _new_lead(cur, identity_key="ik-1"):
    cur.execute(
        "INSERT INTO leads (identity_key, segment, status) VALUES (%s, 'creator', 'new') RETURNING id",
        (identity_key,),
    )
    return cur.fetchone()[0]


def test_insert_lead_and_channel(conn):
    with conn.cursor() as cur:
        lead_id = _new_lead(cur)
        cur.execute(
            "INSERT INTO channels (lead_id, type, handle) VALUES (%s, 'email', 'a@x.com') RETURNING id",
            (lead_id,),
        )
        assert cur.fetchone()[0]
    conn.commit()


def test_lifecycle_enum_rejects_bad_status(conn):
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            cur.execute("INSERT INTO leads (identity_key, status) VALUES ('ik-bad', 'bogus')")
    conn.rollback()


def test_identity_key_is_unique_3c(conn):
    with conn.cursor() as cur:
        _new_lead(cur, "dup-key")
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _new_lead(cur, "dup-key")
    conn.rollback()


def test_channel_cascades_on_lead_delete(conn):
    with conn.cursor() as cur:
        lead_id = _new_lead(cur, "cascade-key")
        cur.execute("INSERT INTO channels (lead_id, type, handle) VALUES (%s, 'email', 'c@x.com')", (lead_id,))
        conn.commit()
        cur.execute("DELETE FROM leads WHERE id = %s", (lead_id,))
        conn.commit()
        cur.execute("SELECT count(*) FROM channels WHERE lead_id = %s", (lead_id,))
        assert cur.fetchone()[0] == 0


def test_6a_optout_must_be_identity_wide(conn):
    with conn.cursor() as cur:
        # opt-out with a channel_type set violates 6A
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO suppression (identity_key, channel_type, reason) VALUES ('ik', 'email', 'optout')"
            )
    conn.rollback()


def test_6a_bounce_must_be_channel_specific(conn):
    with conn.cursor() as cur:
        # hardbounce with NULL channel_type violates 6A
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "INSERT INTO suppression (identity_key, reason) VALUES ('ik', 'hardbounce')"
            )
    conn.rollback()


def test_6a_valid_suppressions_accepted(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO suppression (identity_key, reason) VALUES ('p1', 'optout')")  # identity-wide
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) VALUES ('p2', 'email', 'hardbounce')"
        )
        cur.execute(
            "INSERT INTO suppression (identity_key, channel_type, reason) VALUES ('p2', 'whatsapp', 'complaint')"
        )
    conn.commit()


def test_suppression_no_duplicate_identity_wide(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO suppression (identity_key, reason) VALUES ('dupp', 'optout')")
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("INSERT INTO suppression (identity_key, reason) VALUES ('dupp', 'optout')")
    conn.rollback()


def test_updated_at_bumps_on_update(conn):
    with conn.cursor() as cur:
        lead_id = _new_lead(cur, "touch-key")
        conn.commit()
        cur.execute("SELECT updated_at FROM leads WHERE id = %s", (lead_id,))
        before = cur.fetchone()[0]
        cur.execute("UPDATE leads SET status = 'queued' WHERE id = %s", (lead_id,))
        conn.commit()
        cur.execute("SELECT updated_at FROM leads WHERE id = %s", (lead_id,))
        after = cur.fetchone()[0]
        assert after >= before
