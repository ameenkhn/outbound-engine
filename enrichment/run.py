"""Re-runnable batch CLI: enrich + score eligible leads, then rank them (L2).

    python -m enrichment.run

For every eligible lead (``status IN ('new','queued')``) this:
  1. joins the lead's channels,
  2. computes ``follower_band`` and the v1 ``icp_score`` (see
     :mod:`enrichment.enrich` / :mod:`enrichment.score`),
  3. writes ``icp_score`` + ``follower_band`` back to the row, and
  4. assigns ``priority_rank`` over the *scored, non-gated* leads with a
     deterministic order:  ``icp_score DESC, follower_count DESC NULLS LAST,
     id ASC``. Gate-failed leads (score 0) get ``priority_rank = NULL`` and are
     excluded from the queue.

Idempotency: the ranking order is total (the ``id ASC`` final tie-break is
unique), so re-running over an unchanged lead set produces identical ranks. The
first run backfills every existing eligible lead.

DB access goes through :func:`data.db.connect`. The whole pass runs in one
transaction so a partial failure leaves ranks untouched.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List

import data.db as db
from enrichment.enrich import follower_band
from enrichment.score import score_lead

ELIGIBLE_STATUSES = ("new", "queued")


def _fetch_eligible_leads(cur) -> List[Dict[str, Any]]:
    """Return eligible leads as plain dicts (id, scoring inputs)."""
    cur.execute(
        """
        SELECT id, identity_key, segment, niche, follower_count, geo, attributes
          FROM leads
         WHERE status IN ('new', 'queued')
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _fetch_channels_by_lead(cur, lead_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Map lead_id -> list of its channel dicts. One query, grouped in Python."""
    by_lead: Dict[int, List[Dict[str, Any]]] = {lid: [] for lid in lead_ids}
    if not lead_ids:
        return by_lead
    cur.execute(
        """
        SELECT lead_id, type, handle, deliverable, opted_in, opted_out
          FROM channels
         WHERE lead_id = ANY(%s)
        """,
        (lead_ids,),
    )
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        by_lead.setdefault(rec["lead_id"], []).append(rec)
    return by_lead


def run(conn=None) -> Dict[str, int]:
    """Enrich, score, and rank all eligible leads. Returns a small summary.

    If ``conn`` is provided it is used (and NOT closed — the caller owns it),
    which is what the DB tests rely on so the work lands in their throwaway
    schema. Otherwise a fresh connection is opened and closed here.
    """
    own_conn = conn is None
    if own_conn:
        conn = db.connect()
    try:
        with conn.cursor() as cur:
            leads = _fetch_eligible_leads(cur)
            channels_by_lead = _fetch_channels_by_lead(cur, [l["id"] for l in leads])

            scored: List[Dict[str, Any]] = []
            for lead in leads:
                channels = channels_by_lead.get(lead["id"], [])
                band = follower_band(lead.get("follower_count"))
                score = score_lead(lead, channels)
                scored.append(
                    {
                        "id": lead["id"],
                        "icp_score": score,
                        "follower_band": band,
                        "follower_count": lead.get("follower_count"),
                    }
                )

            # Write icp_score + follower_band for every eligible lead.
            for rec in scored:
                cur.execute(
                    "UPDATE leads SET icp_score = %s, follower_band = %s WHERE id = %s",
                    (rec["icp_score"], rec["follower_band"], rec["id"]),
                )

            # Rank only the non-gated leads (score > 0). Deterministic order:
            # icp_score DESC, follower_count DESC NULLS LAST, id ASC. The id
            # tie-break is unique -> total order -> idempotent ranks.
            rankable = [r for r in scored if r["icp_score"] > 0]
            rankable.sort(
                key=lambda r: (
                    -r["icp_score"],
                    # follower_count DESC NULLS LAST: present counts sort before
                    # NULL (1, value) < (0, ...) is false, so (0, -count) for
                    # present and (1, 0) for NULL puts NULLs last.
                    0 if r["follower_count"] is not None else 1,
                    -(r["follower_count"] or 0),
                    r["id"],
                )
            )

            ranked_ids = set()
            for rank, rec in enumerate(rankable, start=1):
                cur.execute(
                    "UPDATE leads SET priority_rank = %s WHERE id = %s",
                    (rank, rec["id"]),
                )
                ranked_ids.add(rec["id"])

            # Gate-failed (score 0) leads: NULL rank, excluded from the queue.
            for rec in scored:
                if rec["id"] not in ranked_ids:
                    cur.execute(
                        "UPDATE leads SET priority_rank = NULL WHERE id = %s",
                        (rec["id"],),
                    )

        conn.commit()
        return {
            "eligible": len(leads),
            "ranked": len(rankable),
            "gated": len(leads) - len(rankable),
        }
    finally:
        if own_conn:
            conn.close()


def main() -> int:
    summary = run()
    print(
        "enrichment.run: scored {eligible} eligible lead(s) — "
        "{ranked} ranked, {gated} gated (score 0, NULL rank).".format(**summary)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
