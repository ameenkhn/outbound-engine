"""Postgres integration: target_spec_id threads loader -> lead (live DB).

Builds 0001+0002+0003 in a throwaway schema. Asserts:
  * a candidate carrying a target_spec_id resolves to a lead whose
    leads.target_spec_id is set to that spec,
  * a candidate WITHOUT one still loads, with leads.target_spec_id = NULL,
  * the FK is ON DELETE SET NULL (deleting the spec nulls the backref, keeps the
    lead),
  * the YouTube adapter -> load_candidates path stamps the spec id end to end,
  * an UNAPPROVED spec yields nothing through an adapter (the gate), so no leads.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres target_spec_id tests",
)

psycopg = pytest.importorskip("psycopg")

from data.loader import load_candidates  # noqa: E402
from targeting.brain import run_mode_b, run_mode_a, approve, FakeBrain  # noqa: E402
from sourcing.youtube.adapter import YouTubeAdapter, FakeYouTubeClient  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent.parent / "data" / "migrations"
SQL_0001 = (MIGRATIONS / "0001_init_schema.sql").read_text()
SQL_0002 = (MIGRATIONS / "0002_orchestration.sql").read_text()
SQL_0003 = (MIGRATIONS / "0003_add_target_spec_id.sql").read_text()


@pytest.fixture()
def conn():
    import data.db as db

    schema = f"test_l1_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    c = psycopg.connect(db.get_dsn())
    try:
        with c.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(SQL_0001)
            cur.execute(SQL_0002)
            cur.execute(SQL_0003)
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


def _new_spec(conn):
    """Insert a minimal approved keyword spec, return its id (FK target)."""
    spec = run_mode_b(conn, seeds=["fitness coach"], brain=FakeBrain(inject_bad=False))
    return spec.id


def test_candidate_with_spec_id_sets_lead_target_spec_id(conn):
    spec_id = _new_spec(conn)
    cand = {
        "page": "facebook.com/withspec",
        "attributes": {"ad_text": "Join my fitness cohort"},
        "lead_fields": {"segment": "creator", "platform": "meta", "source": "meta_ads"},
    }
    stats = load_candidates([cand], conn=conn, target_spec_id=spec_id)
    assert stats["created"] == 1

    with conn.cursor() as cur:
        cur.execute("SELECT target_spec_id FROM leads WHERE source_ref = 'facebook.com/withspec'")
        assert cur.fetchone()[0] == spec_id


def test_candidate_without_spec_id_loads_with_null(conn):
    cand = {
        "page": "facebook.com/nospec",
        "attributes": {},
        "lead_fields": {"segment": "creator", "platform": "meta"},
    }
    stats = load_candidates([cand], conn=conn)  # no target_spec_id
    assert stats["created"] == 1

    with conn.cursor() as cur:
        cur.execute("SELECT target_spec_id FROM leads WHERE source_ref = 'facebook.com/nospec'")
        assert cur.fetchone()[0] is None


def test_fk_on_delete_set_null_keeps_lead(conn):
    spec_id = _new_spec(conn)
    cand = {"page": "facebook.com/fk", "attributes": {}, "lead_fields": {"segment": "creator"}}
    load_candidates([cand], conn=conn, target_spec_id=spec_id)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM target_specs WHERE id = %s", (spec_id,))
        conn.commit()
        cur.execute("SELECT target_spec_id FROM leads WHERE source_ref = 'facebook.com/fk'")
        row = cur.fetchone()
    assert row is not None          # lead survives
    assert row[0] is None           # backref nulled (ON DELETE SET NULL)


def test_youtube_adapter_to_loader_stamps_spec_id(conn):
    spec_id = _new_spec(conn)
    # Reload the spec as a TargetSpec so the adapter sees approved=True + keywords.
    from targeting.brain import load_spec

    spec = load_spec(conn, spec_id)
    ch = {
        "id": "UCyt1",
        "snippet": {"title": "Fit Coach", "description": "reach me at fit@coach.in",
                    "customUrl": "@fitcoach", "country": "IN"},
        "statistics": {"subscriberCount": "23000"},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Physical_fitness"]},
    }
    client = FakeYouTubeClient(
        pages={kw: [{"channel_ids": ["UCyt1"], "next": None}] for kw in spec.keywords()},
        channels={"UCyt1": ch},
    )
    cands = list(YouTubeAdapter(client=client).run(spec))
    assert cands, "approved spec should yield candidates"

    stats = load_candidates(cands, conn=conn, target_spec_id=spec.id)
    assert stats["created"] >= 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT target_spec_id, platform FROM leads WHERE platform = 'youtube'"
        )
        rows = cur.fetchall()
    assert rows
    assert all(r[0] == spec.id for r in rows)   # every youtube lead attributed to the spec


def test_unapproved_spec_yields_no_leads_through_adapter(conn):
    # A deep (Mode A) spec is unapproved until sign-off.
    spec = run_mode_a(conn, persona_text="unapproved persona", brain=FakeBrain())
    assert spec.approved is False

    client = FakeYouTubeClient(
        pages={kw: [{"channel_ids": ["UCx"], "next": None}] for kw in (spec.keywords() or ["x"])},
        channels={"UCx": {"id": "UCx", "snippet": {"title": "X"}, "statistics": {"subscriberCount": "100"}}},
    )
    cands = list(YouTubeAdapter(client=client).run(spec))
    assert cands == []                          # gate: nothing sourced

    # And after approval, sourcing works.
    approve(conn, spec.id)
    spec.approved = True
    cands2 = list(YouTubeAdapter(client=client).run(spec))
    assert cands2  # now yields
