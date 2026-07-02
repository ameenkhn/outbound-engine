"""Enqueue one L8 pipeline cycle onto the ``app_jobs`` queue.

This is the tiny, cron-friendly entrypoint that makes the engine "always-on":
schedule it (Railway Cron, system cron, or a Celery beat entry) and each run
drops a ``pipeline_cycle`` job that the already-running ``app_jobs`` worker picks
up and executes (discover → score → personalize → optional gated send).

    # once, now
    python -m orchestration.enqueue_cycle --keywords "nlp coach,life coach India" --platform all

    # autopilot email send (also needs AUTOPILOT_SEND=1 in the WORKER's env)
    python -m orchestration.enqueue_cycle --keywords "nlp coach" --send --send-channel email --send-cap 25

Railway Cron example (daily 06:00 UTC), as the cron command:
    python -m orchestration.enqueue_cycle --keywords "nlp coach,life coach India" --platform all

Keeping enqueue (this) separate from execution (the worker) means a cron blip
never runs the heavy pipeline inline — it just leaves a durable job.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from data.db import connect


def enqueue_cycle(conn, payload: dict) -> int:
    """Insert a pending ``pipeline_cycle`` app_job; return its id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_jobs (kind, payload, requested_by) VALUES ('pipeline_cycle', %s::jsonb, %s) RETURNING id",
            (json.dumps(payload), "orchestration.enqueue_cycle"),
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    return job_id


def _parse_keywords(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="orchestration.enqueue_cycle",
                                 description="Enqueue one always-on pipeline cycle.")
    ap.add_argument("--keywords", help="comma-separated seed keywords (ad-hoc, no LLM spec)")
    ap.add_argument("--spec-id", type=int, default=None, help="use an approved target_spec instead of keywords")
    ap.add_argument("--platform", default="all", help="meta_ads|instagram|linkedin|youtube|all")
    ap.add_argument("--source-limit", type=int, default=50)
    ap.add_argument("--personalize-limit", type=int, default=50)
    ap.add_argument("--segment", default="creator")
    ap.add_argument("--send", action="store_true", help="request autopilot send (worker also needs AUTOPILOT_SEND=1)")
    ap.add_argument("--send-channel", default="email", choices=["email", "whatsapp"])
    ap.add_argument("--send-cap", type=int, default=25)
    args = ap.parse_args(argv)

    payload = {
        "platform": args.platform,
        "source_limit": args.source_limit,
        "personalize_limit": args.personalize_limit,
        "segment": args.segment,
        "send": bool(args.send),
        "send_channel": args.send_channel,
        "send_cap": args.send_cap,
    }
    if args.spec_id is not None:
        payload["spec_id"] = args.spec_id
    else:
        kws = _parse_keywords(args.keywords)
        if not kws:
            print("error: pass --keywords or --spec-id", file=sys.stderr)
            return 2
        payload["keywords"] = kws

    conn = connect()
    try:
        job_id = enqueue_cycle(conn, payload)
    finally:
        conn.close()
    print("enqueued pipeline_cycle app_job id={0} (worker will run it)".format(job_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
