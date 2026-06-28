"""Integration tests for L2 enrichment + scoring + ranking against real Postgres.

Same guard + throwaway-schema pattern as ``test_schema_db.py``: builds 0001
(the frozen contract) in an isolated ``test_l2_<pid>`` schema and drops it after.
The root ``conftest.py`` loads ``.env`` before collection so ``DATABASE_URL`` is
visible and these tests RUN (they are not skipped when ``.env`` is configured).

Proves end-to-end:
  * varied leads + channels get the expected ``icp_score`` from the v1 formula;
  * ``priority_rank`` orders by icp_score DESC, then follower_count DESC, then id
    ASC — including a deterministic tie-break between two equal-score leads;
  * a gate-failed lead (no reachable channel) gets score 0 / NULL rank and is
    excluded from the queue;
  * re-running ``enrichment.run`` produces identical ranks (idempotent).

Run locally:
    DATABASE_URL=postgresql://localhost:5432/postgres \\
        .venv/bin/pytest tests/test_score_db.py
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping live-Postgres scoring tests",
)

psycopg = pytest.importorskip("psycopg")

from enrichment import run as run_mod  # noqa: E402

SQL = (Path(__file__).resolve().parent.parent / "data" / "migrations" / "0001_init_schema.sql").read_text()


@pytest.fixture()
def conn():
    """A connection with 0001 built in an isolated, throwaway schema."""
    import data.db as db

    schema = f"test_l2_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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


def _insert_lead(cur, identity_key, *, segment=None, niche=None, follower_count=None,
                 geo="IN", attributes=None, status="new"):
    cur.execute(
        """
        INSERT INTO leads (identity_key, segment, niche, follower_count, geo, attributes, status)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
        """,
        (
            identity_key,
            segment,
            niche,
            follower_count,
            geo,
            json.dumps(attributes or {}),
            status,
        ),
    )
    return cur.fetchone()[0]


def _add_channel(cur, lead_id, ctype, handle, *, deliverable=True, opted_out=False):
    cur.execute(
        """
        INSERT INTO channels (lead_id, type, handle, deliverable, opted_out)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (lead_id, ctype, handle, deliverable, opted_out),
    )


def _row(cur, lead_id):
    cur.execute(
        "SELECT icp_score, priority_rank, follower_band FROM leads WHERE id = %s",
        (lead_id,),
    )
    score, rank, band = cur.fetchone()
    return {"icp_score": score, "priority_rank": rank, "follower_band": band}


# ---------------------------------------------------------------------------

def test_scores_and_ranking_end_to_end(conn):
    with conn.cursor() as cur:
        # HIGH: fully loaded -> caps at 100, micro band.
        high = _insert_lead(
            cur, "ik-high", segment="creator", niche="fitness", follower_count=50_000,
            attributes={"ad_text": "switch off kajabi", "category": "fitness", "socials": ["ig", "yt"]},
        )
        _add_channel(cur, high, "email", "high@x.com")

        # MID: yoga category + mid band + affiliate + email = 10+20+25+10+5 = 70.
        mid = _insert_lead(
            cur, "ik-mid", segment="affiliate", niche="yoga", follower_count=500_000,
            attributes={"category": "yoga"},
        )
        _add_channel(cur, mid, "email", "mid@x.com")

        # LOW: whatsapp only, no signals, no niche, nano band, ambiguous segment
        #      = band_nano(5) + segment_ambiguous(5) = 10.
        low = _insert_lead(cur, "ik-low", follower_count=300)
        _add_channel(cur, low, "whatsapp", "+919800000001")

        # GATED-CHANNEL: rich signals but NO reachable channel -> score 0.
        gated_chan = _insert_lead(
            cur, "ik-gated-chan", segment="creator", niche="finance", follower_count=80_000,
            attributes={"ad_text": "teachable migration", "category": "finance"},
        )
        # only a linkedin channel -> not reachable
        _add_channel(cur, gated_chan, "linkedin", "in/somebody")

        # GATED-GEO: rich + reachable but geo != IN -> score 0.
        gated_geo = _insert_lead(
            cur, "ik-gated-geo", segment="creator", niche="fitness", follower_count=50_000,
            geo="US", attributes={"ad_text": "x", "category": "fitness"},
        )
        _add_channel(cur, gated_geo, "email", "us@x.com")
    conn.commit()

    summary = run_mod.run(conn)
    assert summary["eligible"] == 5
    assert summary["ranked"] == 3
    assert summary["gated"] == 2

    with conn.cursor() as cur:
        r_high = _row(cur, high)
        r_mid = _row(cur, mid)
        r_low = _row(cur, low)
        r_gc = _row(cur, gated_chan)
        r_gg = _row(cur, gated_geo)

    # exact scores from the v1 formula
    assert r_high["icp_score"] == 100
    assert r_mid["icp_score"] == 70
    assert r_low["icp_score"] == 10
    assert r_high["follower_band"] == "micro"
    assert r_mid["follower_band"] == "mid"
    assert r_low["follower_band"] == "nano"

    # gated leads: score 0, NULL rank, excluded.
    assert r_gc["icp_score"] == 0 and r_gc["priority_rank"] is None
    assert r_gg["icp_score"] == 0 and r_gg["priority_rank"] is None
    # follower_band is still derived for gated leads (it's a pure attribute).
    assert r_gc["follower_band"] == "micro"

    # ranking: high(100) -> mid(70) -> low(10).
    assert r_high["priority_rank"] == 1
    assert r_mid["priority_rank"] == 2
    assert r_low["priority_rank"] == 3


def test_tie_break_is_deterministic_by_follower_then_id(conn):
    with conn.cursor() as cur:
        # Three leads engineered to the SAME icp_score so only the tie-break
        # decides order: equal signals/segment/band, differing follower_count.
        # base score = category zzz (signal 10) + micro band (20) + creator (10)
        #            + verified email (5) = 45 for all three.
        attrs = {"category": "zzz-unmatched"}
        a = _insert_lead(cur, "ik-tie-a", segment="creator", follower_count=10_000, attributes=attrs)
        _add_channel(cur, a, "email", "a@x.com")
        b = _insert_lead(cur, "ik-tie-b", segment="creator", follower_count=20_000, attributes=attrs)
        _add_channel(cur, b, "email", "b@x.com")
        # c has the SAME follower_count as b -> id is the final tie-break.
        c = _insert_lead(cur, "ik-tie-c", segment="creator", follower_count=20_000, attributes=attrs)
        _add_channel(cur, c, "email", "c@x.com")
    conn.commit()

    run_mod.run(conn)

    with conn.cursor() as cur:
        ra, rb, rc = _row(cur, a), _row(cur, b), _row(cur, c)

    # all equal score
    assert ra["icp_score"] == rb["icp_score"] == rc["icp_score"] == 45
    # follower_count DESC: b & c (20k) outrank a (10k); among b & c, id ASC -> b<c.
    ranks = {a: ra["priority_rank"], b: rb["priority_rank"], c: rc["priority_rank"]}
    # b (lower id, 20k) first, then c (higher id, 20k), then a (10k) last.
    assert ranks[b] < ranks[c] < ranks[a]
    assert sorted(ranks.values()) == [1, 2, 3]


def test_rerun_is_idempotent(conn):
    with conn.cursor() as cur:
        ids = []
        ids.append(_insert_lead(
            cur, "ik-1", segment="creator", niche="fitness", follower_count=50_000,
            attributes={"ad_text": "kajabi", "category": "fitness"},
        ))
        ids.append(_insert_lead(cur, "ik-2", segment="affiliate", niche="yoga", follower_count=5_000,
                                attributes={"category": "yoga"}))
        ids.append(_insert_lead(cur, "ik-3", follower_count=200))
        # equal-score pair to exercise the tie-break under re-run
        ids.append(_insert_lead(cur, "ik-4", segment="creator", follower_count=20_000,
                                attributes={"category": "zzz"}))
        ids.append(_insert_lead(cur, "ik-5", segment="creator", follower_count=20_000,
                                attributes={"category": "zzz"}))
        for lid in ids:
            _add_channel(cur, lid, "email", f"lead{lid}@x.com")
    conn.commit()

    run_mod.run(conn)
    with conn.cursor() as cur:
        first = {lid: _row(cur, lid) for lid in ids}

    # Re-run twice more; ranks + scores must not move.
    run_mod.run(conn)
    run_mod.run(conn)
    with conn.cursor() as cur:
        again = {lid: _row(cur, lid) for lid in ids}

    assert first == again
    # ranks form a contiguous 1..N with no gaps/dupes among ranked leads.
    ranks = sorted(r["priority_rank"] for r in again.values() if r["priority_rank"] is not None)
    assert ranks == list(range(1, len(ranks) + 1))


def test_gate_failed_lead_excluded_from_priority_index_query(conn):
    """A no-channel lead is score 0 / NULL rank, so the queue query skips it."""
    with conn.cursor() as cur:
        good = _insert_lead(cur, "ik-good", segment="creator", niche="fitness",
                            follower_count=10_000, attributes={"category": "fitness"})
        _add_channel(cur, good, "email", "good@x.com")
        bad = _insert_lead(cur, "ik-bad", segment="creator", niche="fitness",
                           follower_count=10_000, attributes={"category": "fitness"})
        # no channel at all -> reachability gate fails.
    conn.commit()

    run_mod.run(conn)

    with conn.cursor() as cur:
        # This is the dispatcher's queue query shape: ordered, non-NULL rank,
        # eligible status. The gated lead must not appear.
        cur.execute(
            """
            SELECT id FROM leads
             WHERE status IN ('new','queued') AND priority_rank IS NOT NULL
             ORDER BY priority_rank ASC
            """
        )
        queue_ids = [r[0] for r in cur.fetchall()]
    assert good in queue_ids
    assert bad not in queue_ids
