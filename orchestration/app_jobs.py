"""Consumer for the `app_jobs` command queue (migration 0004).

The CRM front end (web/) never runs the brain / scorer / scrapers itself — it
writes an `app_jobs` row and this consumer claims and executes it. Postgres is
the contract between the TS front end and the Python engine.

Run it once (cron / a Celery beat task) or loop:

    python -m orchestration.app_jobs --once       # drain currently-due jobs, exit
    python -m orchestration.app_jobs               # loop forever (poll every 5s)

Claim is crash-safe (SELECT ... FOR UPDATE SKIP LOCKED), mirroring
orchestration/queue.py. Each job is dispatched by `kind`:

    rescore       -> re-run the L2 scoring batch (enrichment.run)
    mode_b        -> targeting.brain.run_mode_b (keyword expansion, auto-approved)
    mode_a        -> targeting.brain.run_mode_a (persona -> unapproved deep spec)
    approve_spec  -> flip target_specs.approved = TRUE (pure DB)
    source_run    -> NOT WIRED HERE — see _do_source_run; the backend session that
                     owns sourcing/ must confirm the adapter entrypoint.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Optional

from data.db import connect


def _claim_one(conn) -> Optional[dict]:
    """Atomically claim the oldest due pending job. Returns it or None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH due AS (
                SELECT id FROM app_jobs
                 WHERE status = 'pending' AND run_after <= now()
                 ORDER BY run_after
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE app_jobs j
               SET status = 'claimed', claimed_at = now(), attempts = j.attempts + 1
              FROM due WHERE j.id = due.id
            RETURNING j.id, j.kind, j.payload
            """
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        return None
    return {"id": row[0], "kind": row[1], "payload": row[2] or {}}


def _finish(conn, job_id: int, ok: bool, result: Any = None, error: str = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app_jobs SET status = %s, result = %s, last_error = %s WHERE id = %s",
            ("done" if ok else "failed",
             json.dumps(result) if result is not None else None,
             error, job_id),
        )
    conn.commit()


# ---- per-kind handlers ------------------------------------------------------

def _do_rescore(conn, payload: dict) -> dict:
    # enrichment.run is the re-runnable batch (idempotent, deterministic ranks).
    proc = subprocess.run(
        [sys.executable, "-m", "enrichment.run"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"enrichment.run failed: {proc.stderr[-500:]}")
    return {"ran": "enrichment.run", "stdout_tail": proc.stdout[-300:]}


def _do_mode_b(conn, payload: dict) -> dict:
    from targeting.brain import AnthropicBrain, run_mode_b
    keywords = list(payload.get("keywords") or [])
    if not keywords:
        raise ValueError("mode_b requires payload.keywords")
    spec = run_mode_b(conn, keywords, brain=AnthropicBrain())
    return {"spec_id": spec.id, "expanded": len(spec.expanded_keywords), "approved": spec.approved}


def _do_mode_a(conn, payload: dict) -> dict:
    from targeting.brain import AnthropicBrain, run_mode_a
    persona = payload.get("persona")
    if not persona:
        raise ValueError("mode_a requires payload.persona")
    spec = run_mode_a(conn, persona, brain=AnthropicBrain())
    return {"spec_id": spec.id, "approved": spec.approved, "note": "needs sign-off"}


def _do_approve_spec(conn, payload: dict) -> dict:
    from targeting.brain import approve
    spec_id = int(payload["spec_id"])
    ok = approve(conn, spec_id)
    if not ok:
        raise ValueError(f"no target_spec id={spec_id}")
    return {"approved_spec": spec_id}


def _persist_spec_attributes(conn, spec_id: int, attributes: dict) -> None:
    """Write the spec's in-memory attributes (resume cursor + per-source status
    the adapters stamp during a run) back to target_specs.attributes."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE target_specs SET attributes = %s WHERE id = %s",
            (json.dumps(attributes or {}), spec_id),
        )
    conn.commit()


def _do_source_run(conn, payload: dict) -> dict:
    """Run one (or all) source adapters for an approved spec and load the leads.

    payload:
      * ``spec_id``  — the approved target_spec to source (required).
      * ``platform`` — "meta_ads" | "instagram" | "linkedin" | "youtube" | "all"
                       (default "all" — the full fan-out).

    Importing ``sourcing.harvest_all`` registers every adapter and gives us both
    the unified runner and the registry. Each adapter yields loader-ready
    candidate dicts; we resolve them through ``data.loader.load_candidates``
    (same false-merge-guarded path as Meta/YouTube), stamping ``target_spec_id``.
    Any resume cursor / per-source status the adapters wrote onto the spec is
    persisted back to ``target_specs.attributes`` so a later run continues.
    """
    from sourcing.harvest_all import harvest_all, ALL_SOURCES
    from sourcing.base import is_registered
    from targeting.brain import load_spec
    from data.loader import load_candidates
    from data.known import make_skip_predicate

    spec_id = int(payload["spec_id"])
    platform = (payload.get("platform") or "all").strip()

    spec = load_spec(conn, spec_id)
    if spec is None:
        raise ValueError(f"no target_spec id={spec_id}")
    if not spec.approved:
        raise ValueError(f"target_spec id={spec_id} is not approved — refusing to source")

    if platform == "all":
        sources = list(ALL_SOURCES)
    else:
        if not is_registered(platform):
            raise ValueError(
                f"unknown platform {platform!r}; known: {', '.join(ALL_SOURCES)} or 'all'"
            )
        sources = [platform]

    # COST SAVER: load the handles we already have so adapters skip them before
    # the billed per-profile fetch (dedupe at save still backstops correctness).
    skip_known = make_skip_predicate(conn)
    candidates, per_source = harvest_all(spec, sources=sources, skip_known=skip_known)

    # Resolve/dedupe into leads (reuses this job's connection + transaction).
    stats = load_candidates(candidates, conn=conn, target_spec_id=spec.id)

    # Persist resume cursor + per-source status the adapters stamped on the spec.
    _persist_spec_attributes(conn, spec_id, getattr(spec, "attributes", {}) or {})

    return {
        "spec_id": spec_id,
        "sources": sources,
        "per_source": per_source,
        "candidates": stats.get("candidates", 0),
        "created": stats.get("created", 0),
        "merged": stats.get("merged", 0),
        "skipped": stats.get("skipped", 0),
    }


HANDLERS = {
    "rescore": _do_rescore,
    "mode_b": _do_mode_b,
    "mode_a": _do_mode_a,
    "approve_spec": _do_approve_spec,
    "source_run": _do_source_run,
}


def run_once(conn) -> int:
    """Drain currently-due jobs. Returns the number processed."""
    n = 0
    while True:
        job = _claim_one(conn)
        if job is None:
            break
        n += 1
        handler = HANDLERS.get(job["kind"])
        if handler is None:
            _finish(conn, job["id"], ok=False, error=f"unknown kind: {job['kind']}")
            continue
        try:
            result = handler(conn, job["payload"])
            _finish(conn, job["id"], ok=True, result=result)
        except Exception as e:  # noqa: BLE001 — record any handler failure for the UI
            _finish(conn, job["id"], ok=False, error=f"{type(e).__name__}: {e}")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app_jobs", description="Drain the app_jobs queue.")
    ap.add_argument("--once", action="store_true", help="process due jobs once and exit")
    ap.add_argument("--interval", type=float, default=5.0, help="poll interval (loop mode)")
    args = ap.parse_args(argv)

    conn = connect()
    try:
        if args.once:
            print(f"app_jobs: processed {run_once(conn)} job(s)")
            return 0
        while True:
            run_once(conn)
            time.sleep(args.interval)
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
