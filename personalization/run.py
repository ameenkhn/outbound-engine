"""Batch LLM personalization — write niche-tailored WhatsApp + email copy.

For each lead (that has a niche/signal and no copy yet), generate a personalized
**email** (subject + body) and a short **WhatsApp** message with the LLM, run the
P4 guardrail, and store the results on the lead's ``attributes``:

    attributes.msg_email_subject   attributes.msg_email_body   attributes.msg_whatsapp

Downstream:
  * the Smartlead push uses ``msg_email_*`` as campaign custom fields,
  * the WhatsApp (AiSensy) send uses ``msg_whatsapp`` as the template param.

Uses Claude **Haiku** (cheap + fast) via personalization.generate. No key set →
falls back to the offline FakeGenerator so dry runs work with no network.

    python -m personalization.run --limit 50 --niche coaching
    python -m personalization.run --limit 20 --dry-run

Python 3.9 compatible.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from personalization.generate import default_generator, generate_message
from personalization.guardrail import passes_guardrail

logger = logging.getLogger("personalization.run")


def personalize_lead(lead: Dict[str, Any], generator, segment: str = "creator") -> Dict[str, Any]:
    """Generate guardrail-checked email + WhatsApp copy for one lead.

    Returns ``{msg_email_subject, msg_email_body, msg_whatsapp, rejected: [...]}``.
    A channel whose copy fails the P4 guardrail is left out (and named in
    ``rejected``) rather than sent as weak copy.
    """
    attrs = lead.get("attributes") if isinstance(lead.get("attributes"), dict) else {}
    out: Dict[str, Any] = {"rejected": []}

    email = generate_message(lead, segment, None, generator, channel="email")
    ok, reason = passes_guardrail(email["body"], attrs)
    if ok:
        out["msg_email_subject"] = email["subject"]
        out["msg_email_body"] = email["body"]
    else:
        out["rejected"].append("email:" + reason)

    wa = generate_message(lead, segment, None, generator, channel="whatsapp")
    ok, reason = passes_guardrail(wa["body"], attrs)
    if ok:
        out["msg_whatsapp"] = wa["body"]
    else:
        out["rejected"].append("whatsapp:" + reason)

    return out


def _select_leads(conn, limit: int, niche: Optional[str]) -> List[Dict[str, Any]]:
    sql = (
        "SELECT id, identity_key, niche, attributes "
        "FROM leads "
        "WHERE (attributes->>'msg_whatsapp' IS NULL OR attributes->>'msg_email_body' IS NULL) "
    )
    params: List[Any] = []
    if niche:
        sql += "  AND niche ILIKE %s "
        params.append("%" + niche + "%")
    sql += "ORDER BY priority_rank ASC NULLS LAST LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _store(conn, lead_id: int, generated: Dict[str, Any]) -> None:
    patch = {k: v for k, v in generated.items() if k.startswith("msg_") and v}
    if not patch:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE leads SET attributes = COALESCE(attributes, '{}'::jsonb) || %s::jsonb WHERE id = %s",
            (json.dumps(patch), lead_id),
        )
    conn.commit()


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="personalization.run",
                                 description="Batch-generate niche WhatsApp + email copy.")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--niche", default=None)
    ap.add_argument("--segment", default="creator")
    ap.add_argument("--dry-run", action="store_true", help="print copy for the first lead; write nothing")
    args = ap.parse_args(argv)

    gen = default_generator()

    from data.db import connect
    conn = connect()
    try:
        rows = _select_leads(conn, args.limit, args.niche)
        if not rows:
            print("No leads need personalization.")
            return 0
        if args.dry_run:
            sample = personalize_lead(rows[0], gen, args.segment)
            print(json.dumps({"lead": rows[0].get("identity_key"), **sample}, indent=2, default=str))
            return 0
        done = 0
        for row in rows:
            generated = personalize_lead(row, gen, args.segment)
            _store(conn, row["id"], generated)
            done += 1
        print("Personalized {0} lead(s) (email + WhatsApp) and stored on attributes.".format(done))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
