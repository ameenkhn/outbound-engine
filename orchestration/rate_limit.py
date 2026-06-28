"""Atomic per-bucket rate limiting for the warmup ramp / back-pressure.

A "bucket" is an opaque ``scope_key`` the caller picks, e.g.
``'email:domain.com:2026-06-26'`` (per sending domain per day). The cap (how
many sends that bucket may take today) is passed in by the caller — it comes
from the warmup schedule, not the DB, so the ramp can change without a
migration.

The core primitive is :func:`check_and_increment`, which must be *atomic*: under
concurrent workers it must never let more than ``cap`` sends through for a
bucket, and it must NOT increment when the cap is already hit.
"""
from __future__ import annotations

from datetime import date
from typing import Optional


def check_and_increment(
    conn,
    scope_key: str,
    cap: int,
    window_date: Optional[date] = None,
) -> bool:
    """Atomically reserve one unit of the ``scope_key`` budget.

    Returns ``True`` and increments the counter iff the bucket is still under
    ``cap``. Returns ``False`` and does NOT increment if the cap is already
    reached. Safe under concurrency: the upsert's ``WHERE count < cap`` guard
    means the increment and the check happen in one statement under a row lock,
    so two workers can't both push the same bucket past the cap.

    Implementation: ``INSERT ... ON CONFLICT (scope_key) DO UPDATE SET
    count = count + 1 WHERE rate_counters.count < cap``. On a brand-new bucket
    the INSERT lands at count=1 (allowed when cap>=1). On an existing bucket the
    conflicting UPDATE only fires while under cap; if the guard fails, no row is
    returned, signalling the cap was hit. ``RETURNING`` lets us distinguish the
    two outcomes without a second query.
    """
    if cap <= 0:
        # A non-positive cap means "nothing allowed"; never increment.
        return False

    wd = window_date or date.today()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rate_counters (scope_key, count, window_date)
            VALUES (%s, 1, %s)
            ON CONFLICT (scope_key) DO UPDATE
                SET count = rate_counters.count + 1
              WHERE rate_counters.count < %s
            RETURNING count
            """,
            (scope_key, wd, cap),
        )
        row = cur.fetchone()
    conn.commit()
    # A row is returned only when the INSERT happened or the guarded UPDATE
    # fired (i.e. we were under cap). No row => the WHERE guard blocked the
    # update => cap already hit => do not allow.
    return row is not None


def current_count(conn, scope_key: str) -> int:
    """Return the current count for a bucket (0 if it doesn't exist yet)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count FROM rate_counters WHERE scope_key = %s",
            (scope_key,),
        )
        row = cur.fetchone()
    return row[0] if row else 0
