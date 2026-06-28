"""Known-lead lookup for scrape-time skipping (cost saver).

The L0 resolver already guarantees *no duplicate rows* — re-scraping a known
creator merges instead of inserting. But a re-scrape still *pays* the provider to
fetch that creator's profile before the loader discards the duplicate. For
provider APIs billed per call, that's wasted spend.

This module loads the set of social handles we ALREADY have, so the source
adapters can skip them right after search — before the expensive per-profile
fetch — and only spend lookups on genuinely new people.

We key on the **handle** because that's the only identity signal an adapter has
at search time (the username / public-id), before it fetches details. A creator
we currently know only by email (no handle stored) won't be skipped at discovery,
but the loader still dedupes them at save — so correctness never depends on this;
it's purely an optimization.

Pure-ish: one read, no writes. ``normalize_handle`` keeps both sides canonical so
``@Maya`` / ``instagram.com/maya`` / ``maya`` all compare equal.
"""
from __future__ import annotations

from typing import Callable, Optional, Set

from .normalize import normalize_handle


def load_known_handles(conn) -> Set[str]:
    """Return the set of normalized social handles already present in the DB.

    Sources, unioned:
      * ``leads.identity_key`` rows of the form ``handle:<h>`` (handle-keyed leads),
      * ``channels.handle`` where ``type='linkedin'`` (the social channel type),
      * ``leads.attributes->>'username'`` / ``->>'public_id'`` (the social handle
        carried even when the lead is keyed by email/phone/page).
    """
    known: Set[str] = set()
    with conn.cursor() as cur:
        cur.execute("SELECT identity_key FROM leads")
        for (key,) in cur.fetchall():
            if key and isinstance(key, str) and key.startswith("handle:"):
                nh = normalize_handle(key[len("handle:"):])
                if nh:
                    known.add(nh)

        cur.execute("SELECT handle FROM channels WHERE type = 'linkedin'")
        for (handle,) in cur.fetchall():
            nh = normalize_handle(handle)
            if nh:
                known.add(nh)

        cur.execute(
            "SELECT attributes->>'username', attributes->>'public_id' FROM leads"
        )
        for username, public_id in cur.fetchall():
            for raw in (username, public_id):
                nh = normalize_handle(raw) if raw else None
                if nh:
                    known.add(nh)
    return known


def make_skip_predicate(conn) -> Callable[[Optional[str]], bool]:
    """Build a ``skip_known(handle) -> bool`` closure from a one-shot DB load.

    The adapters call this for each handle returned by search and skip the ones
    that come back True, avoiding the profile fetch. Loading once per run keeps it
    to a single query no matter how many handles are checked.
    """
    known = load_known_handles(conn)

    def skip_known(handle: Optional[str]) -> bool:
        if not handle:
            return False
        nh = normalize_handle(handle)
        return bool(nh and nh in known)

    return skip_known
