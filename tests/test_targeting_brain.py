"""Postgres integration tests for the AI Targeting brain (FakeBrain, live DB).

Builds the full schema stack — 0001 (frozen) + 0002 (orchestration) + 0003
(target_spec_id) — in a throwaway ``test_l1_<pid>`` schema, runs against live
Postgres via DATABASE_URL (.env), and drops the schema afterward. Skipped only
if DATABASE_URL is unset.

Asserts:
  * Mode B writes an APPROVED mode='keyword' spec, and the validation gate drops
    a bad expansion (over-broad / dup / too-short) before the write.
  * Mode A writes an UNAPPROVED mode='deep' spec with the right filters shape
    {segments:[{name,sub_niches,signals}], follower_bands, geo, platforms}.
  * approve(spec_id) flips approved -> TRUE.
  * Mode B dedupes a new expansion against keywords already in target_specs.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres targeting tests",
)

psycopg = pytest.importorskip("psycopg")

from targeting.brain import (  # noqa: E402
    FakeBrain,
    run_mode_a,
    run_mode_b,
    approve,
    load_spec,
)

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


def _spec_row(conn, spec_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT mode, seed_keywords, expanded_keywords, filters, approved, "
            "created_by_model FROM target_specs WHERE id = %s",
            (spec_id,),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Mode B
# ---------------------------------------------------------------------------

def test_mode_b_writes_approved_keyword_spec(conn):
    spec = run_mode_b(conn, seeds=["money mindset coach"], brain=FakeBrain(inject_bad=True))
    mode, seed, expanded, filters, approved, model = _spec_row(conn, spec.id)
    assert mode == "keyword"
    assert approved is True                       # Mode B is auto-approved
    assert model == "claude-sonnet-4-6"           # Sonnet-class
    assert "money mindset coach" in expanded
    assert filters.get("geo") == ["IN"]


def test_mode_b_validation_gate_drops_bad_expansion(conn):
    # FakeBrain(inject_bad=True) appends an over-broad single token ("india"),
    # an exact duplicate, and a too-short term. None may reach the spec.
    spec = run_mode_b(conn, seeds=["money mindset coach"], brain=FakeBrain(inject_bad=True))
    _, _, expanded, _, _, _ = _spec_row(conn, spec.id)

    assert "india" not in expanded                # over-broad single token dropped
    assert "x" not in expanded                    # too-short dropped
    # no duplicates persisted
    assert len(expanded) == len(set(expanded))
    # the validation drop log is stashed for inspection
    assert set(spec.attributes["dropped_in_validation"].values()) >= {"over_broad", "duplicate"}


def test_mode_b_dedupes_against_existing_specs(conn):
    b = FakeBrain(inject_bad=False)
    first = run_mode_b(conn, seeds=["tarot reading"], brain=b)
    _, _, first_expanded, _, _, _ = _spec_row(conn, first.id)
    assert first_expanded  # sanity

    # A second run with the same seeds should have everything deduped away
    # against the first spec's keywords -> nothing left -> ValueError (no empty
    # spec is ever written).
    with pytest.raises(ValueError):
        run_mode_b(conn, seeds=["tarot reading"], brain=b)


# ---------------------------------------------------------------------------
# Mode A
# ---------------------------------------------------------------------------

def test_mode_a_writes_unapproved_deep_spec_with_filters_shape(conn):
    persona = "Indian money-mindset and trauma-healing coaches selling cohorts"
    spec = run_mode_a(conn, persona_text=persona, brain=FakeBrain())
    mode, seed, expanded, filters, approved, model = _spec_row(conn, spec.id)

    assert mode == "deep"
    assert approved is False                      # deep mode needs human sign-off
    assert model == "claude-sonnet-4-6"

    # filters shape: {segments:[{name,sub_niches,signals}], follower_bands, geo, platforms}
    assert set(filters.keys()) == {"segments", "follower_bands", "geo", "platforms"}
    assert isinstance(filters["segments"], list) and filters["segments"]
    seg0 = filters["segments"][0]
    assert set(seg0.keys()) == {"name", "sub_niches", "signals"}
    assert seg0["name"]
    assert isinstance(seg0["sub_niches"], list)
    assert isinstance(seg0["signals"], list)
    assert filters["geo"] == ["IN"]
    assert "youtube" in filters["platforms"]
    # clarifying questions are stashed for the human reviewing the spec
    assert spec.attributes["clarifying_questions"]


def test_approve_flips_deep_spec(conn):
    spec = run_mode_a(conn, persona_text="some persona", brain=FakeBrain())
    assert spec.approved is False

    updated = approve(conn, spec.id)
    assert updated is True

    reloaded = load_spec(conn, spec.id)
    assert reloaded.approved is True


def test_approve_missing_spec_returns_false(conn):
    assert approve(conn, 999999) is False
