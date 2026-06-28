"""Postgres integration tests for the comprehensive loader (T3).

Skipped when DATABASE_URL is unset. Builds the frozen schema in a throwaway
namespace and loads tests/fixtures/sample_full.json through the real resolver.

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres .venv/bin/pytest tests/test_loader_db.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres loader tests",
)

psycopg = pytest.importorskip("psycopg")

from data.loader import load_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SQL = (ROOT / "data" / "migrations" / "0001_init_schema.sql").read_text()
FIXTURE = str(Path(__file__).resolve().parent / "fixtures" / "sample_full.json")


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


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_loader_creates_expected_leads(conn):
    stats = load_file(FIXTURE, conn=conn)
    # 6 ads -> 5 candidates (the two Aanya ads collapse into one).
    assert stats["ads"] == 6
    assert stats["candidates"] == 5
    assert stats["created"] == 5
    assert _count(conn, "leads") == 5


def test_same_creator_across_ads_is_one_lead(conn):
    load_file(FIXTURE, conn=conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leads WHERE source_ref = 'facebook.com/aanyacoaching'")
        assert cur.fetchone()[0] == 1
        # both library ids carried into attributes
        cur.execute("SELECT attributes FROM leads WHERE source_ref = 'facebook.com/aanyacoaching'")
        attrs = cur.fetchone()[0]
    assert set(attrs.get("library_ids", [])) == {"1001", "1002"}


def test_shared_email_distinct_pages_do_not_merge(conn):
    load_file(FIXTURE, conn=conn)
    # TarotByRhea and NumeroByDev share shared@agency.example.org but differ by page.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM leads WHERE source_ref IN "
                    "('facebook.com/tarotbyrhea', 'facebook.com/numerobydev')")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM channels WHERE type='email' AND handle='shared@agency.example.org'")
        # the shared email is a single channel row owned by one lead
        assert cur.fetchone()[0] == 1


def test_ad_text_and_signals_land_in_attributes(conn):
    load_file(FIXTURE, conn=conn)
    with conn.cursor() as cur:
        cur.execute("SELECT attributes, follower_count, niche, segment, platform, source "
                    "FROM leads WHERE source_ref = 'facebook.com/aanyacoaching'")
        attrs, fc, niche, segment, platform, source = cur.fetchone()
    assert "ICF-accredited life coach certification" in attrs["ad_text"]
    assert attrs["category"] == "Coach"
    assert attrs["enriched"] is True
    assert fc == 12500
    assert niche == "nlp_mindset"
    assert segment == "creator"
    assert platform == "meta"
    assert source == "meta_ads"


def test_bare_row_flagged_not_enriched(conn):
    load_file(FIXTURE, conn=conn)
    with conn.cursor() as cur:
        cur.execute("SELECT attributes FROM leads WHERE source_ref = 'facebook.com/bareleadco'")
        attrs = cur.fetchone()[0]
    # no advertiser_details -> enriched flag is False (or absent-as-falsey)
    assert attrs.get("enriched", False) is False


def test_junk_phone_rejected_but_lead_kept_via_page(conn):
    load_file(FIXTURE, conn=conn)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM leads WHERE source_ref = 'facebook.com/junkphoneco'")
        assert cur.fetchone() is not None
        # the 12-digit junk number must NOT have become a whatsapp channel
        cur.execute("SELECT count(*) FROM channels WHERE handle LIKE '%100200300400%'")
        assert cur.fetchone()[0] == 0


def test_loader_is_idempotent_on_rerun(conn):
    s1 = load_file(FIXTURE, conn=conn)
    leads_after_first = _count(conn, "leads")
    channels_after_first = _count(conn, "channels")

    s2 = load_file(FIXTURE, conn=conn)
    # second run merges everything, creates nothing new
    assert s2["created"] == 0
    assert s2["merged"] == s1["created"]
    assert _count(conn, "leads") == leads_after_first
    assert _count(conn, "channels") == channels_after_first
