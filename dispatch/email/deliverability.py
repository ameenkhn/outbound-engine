"""Email deliverability helpers — PURE functions, no I/O, no DB, no network.

Two responsibilities:

  1. :func:`warmup_cap` — the daily send budget for a *new sending domain/IP* as
     it ramps. Cold domains that blast thousands of mails on day one get junked
     or blocklisted; you earn reputation by starting small and increasing volume
     gradually while engagement stays healthy. This returns the allowed volume
     for a given day index on that ramp. The orchestrator (Lane C) passes the
     result to ``rate_limit.check_and_increment`` as the per-bucket cap, so the
     warmup schedule lives here (code) and can change with no DB migration.

  2. :func:`daily_scope_key` — the canonical ``rate_counters.scope_key`` for a
     (sending domain, date) bucket, so a day's sends for a domain all count
     against one row.

INFRA PREREQUISITES (NOT code — must be configured in DNS / the ESP):
  * SPF   — TXT record authorizing the sending hosts/ESP for the domain
            (e.g. "v=spf1 include:<esp> -all"). Stops the From-domain being
            spoofed and lets receivers verify the envelope sender.
  * DKIM  — the ESP signs each message with a private key; the matching public
            key is published as a DNS TXT record (selector._domainkey.<domain>).
            Proves the body/headers weren't tampered with in transit.
  * DMARC — TXT at _dmarc.<domain> (e.g. "v=DMARC1; p=quarantine; rua=...")
            telling receivers what to do when SPF/DKIM fail, and where to send
            aggregate reports. Start at p=none to monitor, then tighten.
  * Separate sending domain/subdomain — send cold outbound from a dedicated
            domain (or subdomain, e.g. mail.brand.com) NOT your primary corporate
            domain, so a reputation hit on cold outreach never poisons
            transactional / employee email. Each sending domain warms up
            independently (its own warmup_cap ramp + its own daily_scope_key).

None of those four are enforceable in Python here; they are listed so the
operator wires them before flipping real sending on. This module only governs
*volume pacing*, which is the part code can own.
"""
from __future__ import annotations

import datetime as _dt
from typing import Union

# ---- Warmup ramp ------------------------------------------------------------
# A conservative ~3-week ramp. day_index is 0-based (day 0 = first sending day).
# Values are the MAX sends allowed for the domain that day. The ramp is
# monotonic non-decreasing, starts low (reputation-safe), and plateaus at a
# steady-state daily cap. Tune per ESP guidance / observed engagement.
#
# Roughly: start ~25/day, ~double each few days through weeks 1-2, settle by
# week 3+. Keeping it as an explicit table (not a formula) makes the schedule
# auditable and trivially adjustable.
WARMUP_SCHEDULE = (
    25,    # day 0
    25,    # day 1
    50,    # day 2
    50,    # day 3
    100,   # day 4
    100,   # day 5
    150,   # day 6   (end of week 1)
    250,   # day 7
    400,   # day 8
    600,   # day 9
    800,   # day 10
    1000,  # day 11
    1300,  # day 12
    1600,  # day 13  (end of week 2)
    2000,  # day 14
    2500,  # day 15
    3000,  # day 16
    4000,  # day 17
    5000,  # day 18
    6500,  # day 19
    8000,  # day 20  (end of week 3)
)

# Steady-state daily cap once the ramp is complete (>= last ramp value).
STEADY_STATE_CAP = 10000


def warmup_cap(day_index: int) -> int:
    """Allowed daily send volume on the warmup ramp for ``day_index`` (0-based).

    * day_index < 0 is treated as day 0 (never negative).
    * Within the ramp, returns the scheduled value for that day.
    * Past the ramp, returns the steady-state cap.

    Guarantees (relied on by tests and by the rate limiter):
      - monotonic non-decreasing in day_index,
      - starts low (<= 50 on day 0) so a cold domain can't blast,
      - always a positive int.
    """
    if day_index < 0:
        day_index = 0
    if day_index < len(WARMUP_SCHEDULE):
        return WARMUP_SCHEDULE[day_index]
    return STEADY_STATE_CAP


def daily_scope_key(domain: str, date: Union[_dt.date, _dt.datetime, str]) -> str:
    """Canonical ``rate_counters.scope_key`` for one sending *domain* on one day.

    Format: ``email:<domain>:<YYYY-MM-DD>``. All sends for a domain on a date
    count against the same bucket, so the warmup cap is enforced per
    domain-per-day (matching how the warmup ramp is defined).

    ``date`` may be a ``date``/``datetime`` (its .date() is used) or an ISO
    ``YYYY-MM-DD`` string; the domain is lowercased for stability.
    """
    if isinstance(date, _dt.datetime):
        date_str = date.date().isoformat()
    elif isinstance(date, _dt.date):
        date_str = date.isoformat()
    else:
        date_str = str(date)
    return f"email:{domain.strip().lower()}:{date_str}"
