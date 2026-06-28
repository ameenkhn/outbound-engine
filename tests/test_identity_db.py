"""Postgres integration tests for the composite identity resolver (T2).

Skipped when DATABASE_URL is unset. Builds the frozen schema in a throwaway
``test_l0_<pid>_<uuid>`` namespace (same pattern as test_schema_db.py) so it
never touches public and is repeatable.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres .venv/bin/pytest tests/test_identity_db.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres resolver tests",
)

psycopg = pytest.importorskip("psycopg")

from data.identity import build_candidate, resolve  # noqa: E402

SQL = (Path(__file__).resolve().parent.parent / "data" / "migrations" / "0001_init_schema.sql").read_text()


@pytest.fixture()
def conn():
    import data.db as db

    schema = f"test_l0_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    c = psycopg.connect(db.get_dsn())
    try:
        with c.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(SQL)
        c.commit()
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


def _resolve(conn, **raw):
    with conn.cursor() as cur:
        lead_id, created = resolve(cur, build_candidate(raw))
    conn.commit()
    return lead_id, created


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_same_creator_across_many_ads_is_one_lead(conn):
    # Same page seen three times (varying casing / /about / extra channels).
    id1, c1 = _resolve(conn, page="https://www.facebook.com/AanyaCoaching",
                       email="hello@aanya.coach")
    id2, c2 = _resolve(conn, page="facebook.com/AanyaCoaching/about",
                       phone="+91 98765 43210")
    id3, c3 = _resolve(conn, page="AanyaCoaching", handle="@aanyacoaching")
    assert id1 == id2 == id3
    assert (c1, c2, c3) == (True, False, False)
    assert _count(conn, "leads") == 1
    # email + whatsapp + linkedin channels all hang off the one lead
    with conn.cursor() as cur:
        cur.execute("SELECT type, handle FROM channels WHERE lead_id = %s ORDER BY type", (id1,))
        rows = cur.fetchall()
    assert ("email", "hello@aanya.coach") in rows
    assert ("whatsapp", "+919876543210") in rows
    assert ("linkedin", "aanyacoaching") in rows


def test_different_pages_sharing_email_do_not_merge(conn):
    # Two distinct creators that share one agency email.
    id1, _ = _resolve(conn, page="facebook.com/TarotByRhea", email="shared@agency.example.org")
    id2, created2 = _resolve(conn, page="facebook.com/NumeroByDev", email="shared@agency.example.org")
    assert id1 != id2, "different non-null pages must never merge on a shared email"
    assert created2 is True
    assert _count(conn, "leads") == 2
    # The shared email channel stays on whichever lead claimed it first (UNIQUE
    # on (type, handle)) — it is NOT duplicated.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM channels WHERE type='email' AND handle='shared@agency.example.org'")
        assert cur.fetchone()[0] == 1


def test_email_only_then_page_backfills_same_lead(conn):
    # First seen with only an email (no page); later the page is discovered.
    id1, c1 = _resolve(conn, email="solo@creator.in")
    assert c1 is True
    id2, c2 = _resolve(conn, email="solo@creator.in", page="facebook.com/SoloCreator")
    assert id2 == id1 and c2 is False
    assert _count(conn, "leads") == 1
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref FROM leads WHERE id = %s", (id1,))
        assert cur.fetchone()[0] == "facebook.com/solocreator"


def test_resolve_is_idempotent_on_repeat(conn):
    raw = dict(page="facebook.com/RepeatMe", email="a@b.com", phone="9876543210")
    id1, c1 = _resolve(conn, **raw)
    id2, c2 = _resolve(conn, **raw)
    assert id1 == id2
    assert (c1, c2) == (True, False)
    assert _count(conn, "leads") == 1
    # email + whatsapp once each, no duplicates
    assert _count(conn, "channels") == 2


def test_attributes_are_merged_additively(conn):
    id1, _ = _resolve(conn, page="facebook.com/AttrCo",
                      attributes={"ad_text": "first copy", "category": "Coach"})
    # second sighting adds a new attribute, does not blank existing ones
    _resolve(conn, page="facebook.com/AttrCo", attributes={"city": "Mumbai"})
    with conn.cursor() as cur:
        cur.execute("SELECT attributes FROM leads WHERE id = %s", (id1,))
        attrs = cur.fetchone()[0]
    assert attrs.get("ad_text") == "first copy"
    assert attrs.get("category") == "Coach"
    assert attrs.get("city") == "Mumbai"


def test_candidate_with_no_signal_raises(conn):
    from data.identity import Candidate
    with conn.cursor() as cur:
        with pytest.raises(ValueError):
            resolve(cur, Candidate())
    conn.rollback()
