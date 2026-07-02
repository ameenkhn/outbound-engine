"""Push CRM leads into a Smartlead campaign (cold-email hand-off).

Selects leads that have an email (and haven't been handed off yet), maps each to
Smartlead's lead shape with personalization fields, pushes them into the campaign
in batches, and marks them ``queued`` so they aren't pushed twice.

    # dry run — show what would be pushed
    python -m dispatch.smartlead.push --campaign <CAMPAIGN_ID> --limit 50 --dry-run

    # real push (needs SMARTLEAD_API_KEY + DATABASE_URL)
    python -m dispatch.smartlead.push --campaign <CAMPAIGN_ID> --limit 200 --niche coaching

Config: SMARTLEAD_API_KEY, SMARTLEAD_CAMPAIGN_ID (default campaign), DATABASE_URL.
Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from .client import BATCH_SIZE, FakeSmartleadClient, HttpSmartleadClient, SmartleadClient

logger = logging.getLogger("dispatch.smartlead.push")


def build_smartlead_lead(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a DB lead row to a Smartlead lead dict, or None if it has no email.

    ``row`` carries: ``email``, ``name`` (creator/company), ``niche``,
    ``sub_category``, ``identity_key``. The campaign's ``{{first_name}}`` /
    ``{{company_name}}`` / ``{{niche}}`` template variables read these.
    """
    email = (row.get("email") or "").strip()
    if not email:
        key = row.get("identity_key") or ""
        if key.startswith("email:"):
            email = key[len("email:"):]
    if not email or "@" not in email:
        return None

    name = (row.get("name") or "").strip()
    first_name = name.split()[0] if name else ""
    custom_fields: Dict[str, Any] = {}
    if row.get("niche"):
        custom_fields["niche"] = row["niche"]
    if row.get("sub_category"):
        custom_fields["sub_category"] = row["sub_category"]

    lead: Dict[str, Any] = {"email": email}
    if first_name:
        lead["first_name"] = first_name
    if name:
        lead["company_name"] = name
    if custom_fields:
        lead["custom_fields"] = custom_fields
    return lead


def push_leads(
    client: SmartleadClient, campaign_id: str, rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Map + push rows into the campaign in batches. Returns a stats dict."""
    leads = [m for m in (build_smartlead_lead(r) for r in rows) if m]
    pushed = 0
    for i in range(0, len(leads), BATCH_SIZE):
        batch = leads[i : i + BATCH_SIZE]
        client.add_leads(campaign_id, batch)
        pushed += len(batch)
    return {"eligible": len(rows), "mapped": len(leads), "pushed": pushed}


# ---------------------------------------------------------------------------
# CLI (DB-backed)
# ---------------------------------------------------------------------------

def _select_leads(conn, limit: int, niche: Optional[str]) -> List[Dict[str, Any]]:
    sql = (
        "SELECT l.id, l.identity_key, l.niche, "
        "       COALESCE(l.attributes->>'email', ce.handle) AS email, "
        "       l.attributes->>'advertiser' AS name, "
        "       l.attributes->>'sub_category' AS sub_category "
        "FROM leads l "
        "LEFT JOIN channels ce ON ce.lead_id = l.id AND ce.type = 'email' "
        "WHERE l.status = 'new' "
        "  AND (l.attributes->>'email' IS NOT NULL OR ce.handle IS NOT NULL "
        "       OR l.identity_key LIKE 'email:%') "
    )
    params: List[Any] = []
    if niche:
        sql += "  AND l.niche ILIKE %s "
        params.append("%" + niche + "%")
    sql += "ORDER BY l.priority_rank ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _mark_queued(conn, ids: List[int]) -> None:
    if not ids:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE leads SET status = 'queued' WHERE id = ANY(%s)", (ids,))
    conn.commit()


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="smartlead.push", description="Push leads into a Smartlead campaign.")
    ap.add_argument("--campaign", default=os.environ.get("SMARTLEAD_CAMPAIGN_ID"),
                    help="Smartlead campaign id (or SMARTLEAD_CAMPAIGN_ID)")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--niche", default=None, help="only leads whose niche matches")
    ap.add_argument("--dry-run", action="store_true", help="show what would be pushed; no API call, no status change")
    args = ap.parse_args(argv)

    if not args.campaign:
        print("error: --campaign (or SMARTLEAD_CAMPAIGN_ID) is required", file=sys.stderr)
        return 2

    from data.db import connect
    conn = connect()
    try:
        rows = _select_leads(conn, args.limit, args.niche)
        if args.dry_run:
            client: SmartleadClient = FakeSmartleadClient()
            stats = push_leads(client, args.campaign, rows)
            print("[dry-run] would push {mapped}/{eligible} leads to campaign {0}".format(
                args.campaign, **stats))
            return 0
        client = HttpSmartleadClient()
        stats = push_leads(client, args.campaign, rows)
        _mark_queued(conn, [r["id"] for r in rows if build_smartlead_lead(r)])
        print("Pushed {pushed} leads to Smartlead campaign {0} (marked queued).".format(
            args.campaign, **stats))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
