"""Composite identity resolver for L0 leads (decision 3C / task T2).

THE PROBLEM
-----------
The same creator shows up across many ads, many search queries, and many runs.
Sometimes we see their Facebook page, sometimes only an email or a phone, and
the spelling/casing varies. We want exactly ONE ``leads`` row per real person,
with every contact point hanging off it as a ``channels`` row.

THE APPROACH (blocking keys, not O(n^2))
----------------------------------------
Each candidate carries up to four normalized *blocking keys*:
  * page   -> normalized facebook page  (PRIMARY identity)
  * email  -> normalized email
  * phone  -> Indian E.164 phone
  * handle -> normalized social handle (stored as a 'linkedin' channel)

To resolve a candidate we do a handful of indexed lookups (UNIQUE constraints
on ``leads.identity_key`` and ``channels(type, handle)``, plus an index-friendly
equality on ``leads.source_ref``). We never scan all leads. If any lookup hits
an existing lead, we MERGE into it; otherwise we CREATE one.

THE FALSE-MERGE GUARD
---------------------
A shared email or phone is a WEAK signal: agencies, VAs, and link-in-bio tools
reuse one inbox/number across many distinct creators. So:

  * The PAGE is the strong identity. If a candidate has a page and an existing
    lead has that exact normalized page (``source_ref``), they are the same.
  * A shared email/phone/handle is treated as a *merge hint* only. We will
    attach the channel and, if the matched lead has no page yet (or the same
    page), merge. But we NEVER merge two rows that both have a NON-NULL page and
    those pages DIFFER. In that case the contact point is ambiguous: we keep the
    leads separate and attach the channel to the lead we are currently building
    (creating it if needed), leaving the conflicting channel on whichever lead
    already owns it (the channels UNIQUE constraint keeps a handle on one lead).

This makes "same creator across many ads -> ONE lead" while guaranteeing
"two creators that merely share an email do NOT collapse into one".

IDENTITY KEY
------------
``leads.identity_key`` is deterministic and stable, preferring the strongest
available signal: page > email > phone > handle. Re-running the loader recomputes
the same key, so upserts are idempotent.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .normalize import (
    clean_phone,
    normalize_email,
    normalize_handle,
    normalize_page,
)

# Map a candidate signal to the channels.type it is stored under. Email and
# phone(whatsapp) are first-class channel types; a social handle is stored as a
# 'linkedin' channel (the only social channel_type in the frozen enum).
PHONE_CHANNEL = "whatsapp"   # phone contact -> WhatsApp channel (opt-in gated)
EMAIL_CHANNEL = "email"
HANDLE_CHANNEL = "linkedin"


class Candidate:
    """A normalized contact record awaiting resolution.

    All blocking keys are already normalized (or ``None``). ``attributes`` and
    the scalar lead fields are carried onto the lead when it is created or
    enriched. ``handles`` is the list of (channel_type, normalized_handle)
    social/contact channels to attach.
    """

    __slots__ = (
        "page", "email", "phone", "handle",
        "attributes", "lead_fields", "channels", "target_spec_id",
    )

    def __init__(
        self,
        page: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        handle: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
        lead_fields: Optional[Dict[str, Any]] = None,
        channels: Optional[List[Tuple[str, str]]] = None,
        target_spec_id: Optional[int] = None,
    ) -> None:
        self.page = page
        self.email = email
        self.phone = phone
        self.handle = handle
        self.attributes = attributes or {}
        self.lead_fields = lead_fields or {}
        # Extra channels beyond the email/phone/handle blocking keys (e.g. a
        # second email). Each is (channel_type, handle) with handle normalized.
        self.channels = channels or []
        # L1: the approved target_spec that surfaced this candidate (or None for
        # L0 callers like the Meta loader that don't pass one). Stamped onto the
        # lead row so Loop A can attribute outcomes back to an audience spec.
        self.target_spec_id = target_spec_id

    def identity_key(self) -> Optional[str]:
        """Deterministic key: page > email > phone > handle. ``None`` if the
        candidate has no usable signal at all (caller should skip it)."""
        if self.page:
            return "page:" + self.page
        if self.email:
            return "email:" + self.email
        if self.phone:
            return "phone:" + self.phone
        if self.handle:
            return "handle:" + self.handle
        return None

    def all_channels(self) -> List[Tuple[str, str]]:
        """Every (type, handle) channel this candidate contributes, de-duped,
        including the email/phone/handle blocking keys."""
        out: List[Tuple[str, str]] = []
        if self.email:
            out.append((EMAIL_CHANNEL, self.email))
        if self.phone:
            out.append((PHONE_CHANNEL, self.phone))
        if self.handle:
            out.append((HANDLE_CHANNEL, self.handle))
        for ch in self.channels:
            out.append(ch)
        # de-dup preserving order
        seen = set()
        deduped = []
        for ch in out:
            if ch not in seen:
                seen.add(ch)
                deduped.append(ch)
        return deduped


def build_candidate(raw: Dict[str, Any]) -> Candidate:
    """Normalize a loosely-typed dict of signals into a :class:`Candidate`.

    ``raw`` may carry ``page``/``email``/``phone``/``handle`` (raw strings),
    plus ``attributes`` / ``lead_fields`` / extra ``channels``. Normalization is
    applied here so callers (and tests) get the same keys the loader uses.
    """
    page = normalize_page(raw.get("page"))
    email = normalize_email(raw.get("email"))
    phone = clean_phone(raw.get("phone"))
    handle = normalize_handle(raw.get("handle"))
    extra_channels: List[Tuple[str, str]] = []
    for ctype, cval in raw.get("channels", []) or []:
        if ctype == EMAIL_CHANNEL:
            nv = normalize_email(cval)
        elif ctype == PHONE_CHANNEL:
            nv = clean_phone(cval)
        else:
            nv = normalize_handle(cval)
        if nv:
            extra_channels.append((ctype, nv))
    return Candidate(
        page=page,
        email=email,
        phone=phone,
        handle=handle,
        attributes=raw.get("attributes") or {},
        lead_fields=raw.get("lead_fields") or {},
        channels=extra_channels,
        target_spec_id=raw.get("target_spec_id"),
    )


# ---------------------------------------------------------------------------
# DB-backed resolution
# ---------------------------------------------------------------------------

def _find_lead_by_page(cur, page: str) -> Optional[int]:
    """Indexed equality lookup on leads.source_ref (the normalized page)."""
    cur.execute(
        "SELECT id FROM leads WHERE source_ref = %s LIMIT 1",
        (page,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _find_lead_by_identity_key(cur, key: str) -> Optional[int]:
    """UNIQUE-index lookup on leads.identity_key."""
    cur.execute(
        "SELECT id FROM leads WHERE identity_key = %s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _find_lead_by_channel(cur, ctype: str, handle: str) -> Optional[int]:
    """UNIQUE-index lookup on channels(type, handle) -> owning lead."""
    cur.execute(
        "SELECT lead_id FROM channels WHERE type = %s AND handle = %s LIMIT 1",
        (ctype, handle),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _lead_page(cur, lead_id: int) -> Optional[str]:
    cur.execute("SELECT source_ref FROM leads WHERE id = %s", (lead_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _merge_dict(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge incoming over base, but never overwrite an existing
    non-empty value with an empty one (enrichment is additive)."""
    out = dict(base or {})
    for k, v in (incoming or {}).items():
        if v in (None, "", [], {}):
            continue
        out[k] = v
    return out


def _attach_channel(cur, lead_id: int, ctype: str, handle: str) -> bool:
    """Attach (ctype, handle) to ``lead_id`` if not already present anywhere.

    Idempotent and false-merge-safe: the channels(type, handle) UNIQUE
    constraint means a handle lives on exactly one lead. If it already exists
    (on this lead or another), we leave it where it is and return False.
    Returns True if a new channel row was inserted.
    """
    cur.execute(
        "INSERT INTO channels (lead_id, type, handle) VALUES (%s, %s, %s) "
        "ON CONFLICT (type, handle) DO NOTHING RETURNING id",
        (lead_id, ctype, handle),
    )
    return cur.fetchone() is not None


def _update_lead(cur, lead_id: int, candidate: "Candidate") -> None:
    """Merge candidate signals into an existing lead: union attributes, fill in
    page/scalar lead fields only where currently NULL/empty (never clobber)."""
    cur.execute(
        "SELECT attributes, source_ref, segment, niche, platform, follower_band, "
        "follower_count, source FROM leads WHERE id = %s",
        (lead_id,),
    )
    row = cur.fetchone()
    if row is None:
        return
    (attrs, source_ref, segment, niche, platform,
     follower_band, follower_count, source) = row
    if isinstance(attrs, str):  # psycopg may hand back JSON as text in some setups
        attrs = json.loads(attrs)
    new_attrs = _merge_dict(attrs or {}, candidate.attributes)

    lf = candidate.lead_fields
    # Backfill the page if the lead had none and this candidate brings one.
    new_page = source_ref or candidate.page

    cur.execute(
        "UPDATE leads SET "
        "  attributes = %s, "
        "  source_ref = COALESCE(source_ref, %s), "
        "  segment = COALESCE(segment, %s), "
        "  niche = COALESCE(niche, %s), "
        "  platform = COALESCE(platform, %s), "
        "  follower_band = COALESCE(follower_band, %s), "
        "  follower_count = COALESCE(follower_count, %s), "
        "  source = COALESCE(source, %s) "
        "WHERE id = %s",
        (
            json.dumps(new_attrs),
            new_page,
            lf.get("segment"),
            lf.get("niche"),
            lf.get("platform"),
            lf.get("follower_band"),
            lf.get("follower_count"),
            lf.get("source"),
            lead_id,
        ),
    )

    # L1 (additive): backfill target_spec_id only if the lead has none yet and
    # this candidate brings one. Guarded so it never runs against a pre-0003
    # schema (L0 callers never set target_spec_id, so this branch is skipped).
    if candidate.target_spec_id is not None:
        cur.execute(
            "UPDATE leads SET target_spec_id = COALESCE(target_spec_id, %s) "
            "WHERE id = %s",
            (candidate.target_spec_id, lead_id),
        )


def _create_lead(cur, candidate: "Candidate", identity_key: str) -> int:
    lf = candidate.lead_fields
    cols = [
        "identity_key", "segment", "niche", "platform", "follower_band",
        "follower_count", "source", "source_ref", "attributes",
    ]
    vals: List[Any] = [
        identity_key,
        lf.get("segment"),
        lf.get("niche"),
        lf.get("platform"),
        lf.get("follower_band"),
        lf.get("follower_count"),
        lf.get("source"),
        candidate.page,
        json.dumps(candidate.attributes or {}),
    ]
    # L1 (additive): only reference the target_spec_id column when the candidate
    # actually carries one. L0 callers pass None, keeping this INSERT valid even
    # against a pre-0003 schema (the L0 loader tests build 0001 only).
    if candidate.target_spec_id is not None:
        cols.append("target_spec_id")
        vals.append(candidate.target_spec_id)
    placeholders = ", ".join(["%s"] * len(vals))
    cur.execute(
        "INSERT INTO leads ({cols}) VALUES ({ph}) RETURNING id".format(
            cols=", ".join(cols), ph=placeholders
        ),
        tuple(vals),
    )
    return cur.fetchone()[0]


def resolve(cur, candidate: "Candidate") -> Tuple[int, bool]:
    """Resolve a candidate to a lead id, creating or merging as needed.

    Returns ``(lead_id, created)`` where ``created`` is True iff a new lead row
    was inserted. Uses only indexed lookups. Enforces the false-merge guard:
    two leads with different non-null pages are never merged via a shared
    email/phone/handle.

    The caller owns the transaction (commit/rollback).
    """
    key = candidate.identity_key()
    if key is None:
        raise ValueError("candidate has no usable identity signal (page/email/phone/handle all empty)")

    lead_id: Optional[int] = None

    # 1) Strongest signal first: exact page match.
    if candidate.page:
        lead_id = _find_lead_by_page(cur, candidate.page)

    # 2) Same deterministic identity_key already present (e.g. an email-only
    #    creator seen again before any page was attached).
    if lead_id is None:
        lead_id = _find_lead_by_identity_key(cur, key)

    # 3) Weak signals: a shared channel points at an existing lead. Apply the
    #    false-merge guard before adopting it.
    if lead_id is None:
        for ctype, handle in candidate.all_channels():
            hit = _find_lead_by_channel(cur, ctype, handle)
            if hit is None:
                continue
            other_page = _lead_page(cur, hit)
            if candidate.page and other_page and other_page != candidate.page:
                # CONFLICT: both sides have a real, *different* page. The shared
                # contact point is ambiguous — do NOT merge. Keep looking for a
                # safe match; if none, we create a fresh lead below.
                continue
            lead_id = hit
            break

    if lead_id is not None:
        _update_lead(cur, lead_id, candidate)
        created = False
    else:
        lead_id = _create_lead(cur, candidate, key)
        created = True

    # Attach all contact channels (idempotent; respects the UNIQUE constraint so
    # a handle already owned by a *different* lead stays put — no false merge).
    for ctype, handle in candidate.all_channels():
        _attach_channel(cur, lead_id, ctype, handle)

    return lead_id, created
