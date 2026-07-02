"""L8 — the always-on orchestration loop ("one tick of the engine").

Ties the already-built layers into a single, idempotent cycle that can run on a
schedule (Railway Cron / Celery beat) so the funnel keeps filling itself:

    discover (L1)  ->  score + rank (L2)  ->  personalize copy (L3)  ->  [send (L4)]

Each step reuses the existing, tested building blocks — this module only
sequences them and adds the autonomy guardrails:

  * ``run_cycle`` runs discovery (``_do_source_run``), scoring (``enrichment.run``)
    and personalization (``personalization.run``) unconditionally; these are all
    safe, read/compute/store-only steps.
  * SENDING is different: cold outreach is consent- and policy-gated (DPDP;
    WhatsApp opt-in). So autopilot send is **off by default**. It only runs when
    the caller passes ``send=True`` AND the ``AUTOPILOT_SEND=1`` env flag is set
    (checked in the app-job handler), and even then every recipient must pass a
    per-channel consent + suppression + not-already-contacted check, under a hard
    per-run cap.

DB-touching but provider-agnostic: the actual send goes through the registered
``dispatch`` adapters (Resend for email, AiSensy for WhatsApp), so autopilot uses
the exact same providers as the Compose UI and logs to the same ``outreach``
table. Pure helpers (:func:`consent_ok`, :func:`copy_for`, :func:`fill`) are
unit-tested without a DB.

Python 3.9 compatible.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger("orchestration.pipeline")


# ---------------------------------------------------------------------------
# Pure helpers (no DB) — unit-tested directly
# ---------------------------------------------------------------------------

def consent_ok(channel: str, chan: Optional[Dict[str, Any]]) -> bool:
    """Is this channel row allowed to receive an autopilot send?

    email    → deliverable AND not opted-out
    whatsapp → deliverable AND not opted-out AND **opted-in** (opt-in-led, per policy)
    """
    if not chan:
        return False
    if chan.get("opted_out"):
        return False
    if not chan.get("deliverable", True):
        return False
    if channel == "whatsapp":
        return bool(chan.get("opted_in"))
    return True


def copy_for(channel: str, attrs: Dict[str, Any]):
    """Return ``(subject, body)`` for the channel from a lead's stored copy, or
    ``None`` if the personalized copy for that channel isn't there yet."""
    attrs = attrs or {}
    if channel == "email":
        body = attrs.get("msg_email_body")
        return (attrs.get("msg_email_subject") or "", body) if body else None
    body = attrs.get("msg_whatsapp")
    return ("", body) if body else None


def fill(text: str, name: str, niche: str) -> str:
    """Fill any residual {{first_name}} / {{niche}} placeholders (the LLM copy is
    usually already concrete; this is a safety net)."""
    first = (name or "").split(" ")[0] or "there"
    text = re.sub(r"\{\{\s*first_name\s*\}\}", first, text or "", flags=re.I)
    text = re.sub(r"\{\{\s*niche\s*\}\}", niche or "your niche", text, flags=re.I)
    return text


# ---------------------------------------------------------------------------
# Steps (DB-touching) — each returns a small summary dict
# ---------------------------------------------------------------------------

def _discover(conn, *, keywords, spec_id, platform, limit) -> Dict[str, Any]:
    """Run one source_run through the existing handler (lazy import avoids an
    import cycle with app_jobs)."""
    from orchestration.app_jobs import _do_source_run

    payload: Dict[str, Any] = {"platform": platform}
    if spec_id is not None:
        payload["spec_id"] = spec_id
    elif keywords:
        payload["keywords"] = list(keywords)
        if limit:
            payload["limit"] = limit
    else:
        return {"skipped": "no keywords or spec_id"}
    return _do_source_run(conn, payload)


def _score(conn) -> Dict[str, Any]:
    from enrichment.run import run as rescore

    return rescore(conn)


def _personalize(conn, *, limit, segment) -> Dict[str, Any]:
    from personalization.generate import default_generator
    from personalization.run import _select_leads, personalize_lead, _store

    gen = default_generator()
    rows = _select_leads(conn, limit, None)
    done = 0
    for row in rows:
        _store(conn, row["id"], personalize_lead(row, gen, segment))
        done += 1
    return {"personalized": done}


def autopilot_send(conn, *, channel: str, cap: int) -> Dict[str, Any]:
    """Send personalized copy to consented, not-yet-contacted leads on ``channel``.

    Every guardrail lives in the SQL selection + :func:`consent_ok`:
      * status in ('new','queued') — never re-touch an in-flight/contacted lead;
      * channel deliverable, not opted-out, (whatsapp) opted-in;
      * no identity-wide/channel suppression row (DPDP opt-out honored);
      * no prior outbound ``outreach`` row on this channel (idempotent dedupe).
    Bounded by ``cap`` per run. Logs every attempt to ``outreach`` and advances a
    sent lead new/queued → contacted.
    """
    if channel not in ("email", "whatsapp"):
        raise ValueError("channel must be 'email' or 'whatsapp'")
    if cap <= 0:
        return {"selected": 0, "sent": 0, "failed": 0}

    sql = """
        SELECT l.id, l.identity_key, l.niche, l.attributes,
               c.id AS channel_id, c.handle, c.deliverable, c.opted_in, c.opted_out
          FROM leads l
          JOIN channels c ON c.lead_id = l.id AND c.type = %s::channel_type_t
         WHERE l.status IN ('new','queued')
           AND c.opted_out = false
           AND c.deliverable = true
           AND (%s = 'email' OR c.opted_in = true)
           AND NOT EXISTS (
                 SELECT 1 FROM suppression s
                  WHERE s.identity_key = l.identity_key
                    AND (s.channel_type IS NULL OR s.channel_type = %s::channel_type_t))
           AND NOT EXISTS (
                 SELECT 1 FROM outreach o
                  WHERE o.lead_id = l.id AND o.channel = %s AND o.direction = 'out')
         ORDER BY l.priority_rank ASC NULLS LAST
         LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (channel, channel, channel, channel, cap))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    from dispatch import registry
    adapter = registry.get_adapter(channel)      # 'email'→Resend, 'whatsapp'→AiSensy
    send = getattr(adapter, "send", adapter)
    today = date.today().isoformat()

    sent = failed = 0
    for r in rows:
        chan = {"deliverable": r["deliverable"], "opted_in": r["opted_in"], "opted_out": r["opted_out"]}
        if not consent_ok(channel, chan):
            continue
        attrs = r.get("attributes") if isinstance(r.get("attributes"), dict) else {}
        got = copy_for(channel, attrs)
        if not got:
            continue
        name = (attrs.get("advertiser") or "")
        subject = fill(got[0], name, r.get("niche") or "")
        body = fill(got[1], name, r.get("niche") or "")
        idem = "autopilot:{0}:{1}:{2}".format(channel, r["id"], today)
        status, provider_id, err = "failed", None, None
        try:
            res = send(to=r["handle"], subject=subject or None, body=body, idempotency_key=idem)
            if isinstance(res, dict):
                status = res.get("status") or "failed"
                provider_id = res.get("provider_id")
                err = res.get("error")
            ok = status == "sent"
        except Exception as exc:  # provider/network error → logged as failed
            ok, err = False, str(exc)

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO outreach (lead_id, channel, direction, to_handle, subject, body,
                                          status, provider_id, error)
                     VALUES (%s,%s,'out',%s,%s,%s,%s,%s,%s)""",
                (r["id"], channel, r["handle"], subject or None, body,
                 "sent" if ok else "failed", provider_id, None if ok else err),
            )
            if ok:
                cur.execute("UPDATE leads SET status='contacted' WHERE id=%s AND status IN ('new','queued')", (r["id"],))
        conn.commit()
        sent += 1 if ok else 0
        failed += 0 if ok else 1

    return {"selected": len(rows), "sent": sent, "failed": failed}


# ---------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------

def run_cycle(
    conn,
    *,
    keywords: Optional[List[str]] = None,
    spec_id: Optional[int] = None,
    platform: str = "all",
    source_limit: int = 50,
    personalize_limit: int = 50,
    segment: str = "creator",
    send: bool = False,
    send_channel: str = "email",
    send_cap: int = 25,
) -> Dict[str, Any]:
    """Run one full pipeline tick and return a per-step summary.

    Discovery/scoring/personalization always run. Sending only runs when
    ``send=True`` (the app-job handler additionally requires ``AUTOPILOT_SEND=1``).
    Every step is wrapped so one failing step is recorded but doesn't abort the
    rest — the loop is meant to be resilient and re-runnable.
    """
    summary: Dict[str, Any] = {"platform": platform}

    def _step(name: str, fn):
        try:
            summary[name] = fn()
        except Exception as exc:  # noqa: BLE001 — record and continue
            logger.exception("pipeline step %s failed", name)
            summary[name] = {"error": str(exc)}

    _step("discover", lambda: _discover(conn, keywords=keywords, spec_id=spec_id,
                                        platform=platform, limit=source_limit))
    _step("score", lambda: _score(conn))
    _step("personalize", lambda: _personalize(conn, limit=personalize_limit, segment=segment))
    if send:
        _step("send", lambda: autopilot_send(conn, channel=send_channel, cap=send_cap))
    else:
        summary["send"] = {"skipped": "send disabled (default) — pipeline filled & queued, no autosend"}
    return summary
