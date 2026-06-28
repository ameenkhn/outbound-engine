"""Cadence definitions for the L5 follow-up engine (M6).

Pure logic + data: NO database, NO I/O, NO clock of its own. Everything that
needs "now" or "last sent" is passed in, so this module is trivially unit-
testable and Python-3.9 safe.

A *cadence* is an ordered sequence of touches. Each touch has a 0-based ``step``
index and a ``offset_days`` measured from the FIRST contact (step 0 = the
initial outreach the dispatch layer already sent at D0). The default sequence is
the M6 spec: D0 / D3 / D7.

    step 0  -> D0  (the initial send; owned by L4 dispatch, not re-sent here)
    step 1  -> D3  (first follow-up)
    step 2  -> D7  (second / final follow-up)

``MAX_TOUCHES`` caps the total number of touches (including the D0 send). Once a
lead has sent ``MAX_TOUCHES`` messages it falls out of the cadence — there is no
step beyond the last defined offset.

The engine (``engine.py``) maps a lead's "how many sends so far" to the current
step, then asks :func:`next_step` for the next one.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, NamedTuple, Optional, Tuple


class CadenceStep(NamedTuple):
    """One touch in a cadence."""

    step: int          # 0-based index (0 = the D0 initial send)
    offset_days: int   # days after first contact this touch is due


# ---- Default cadence: D0 / D3 / D7 -----------------------------------------
# Tuple of offsets in days from first contact. Index == step.
DEFAULT_OFFSETS_DAYS: Tuple[int, ...] = (0, 3, 7)

# Total touches allowed, INCLUDING the D0 initial send. With the default
# sequence (D0, D3, D7) that is 3 touches -> at most 2 follow-ups after D0.
MAX_TOUCHES: int = len(DEFAULT_OFFSETS_DAYS)

# The default cadence as CadenceStep rows, one per offset.
DEFAULT_CADENCE: Tuple[CadenceStep, ...] = tuple(
    CadenceStep(step=i, offset_days=d) for i, d in enumerate(DEFAULT_OFFSETS_DAYS)
)


def get_cadence(segment: Optional[str] = None, campaign: Optional[str] = None) -> Tuple[CadenceStep, ...]:
    """Return the cadence (sequence of steps) for a segment/campaign.

    Today every segment/campaign uses :data:`DEFAULT_CADENCE`; the signature
    exists so a per-segment or per-campaign override can be added later without
    touching the engine. ``segment`` / ``campaign`` are accepted and ignored for
    now (single default sequence).
    """
    return DEFAULT_CADENCE


def cadence_length(segment: Optional[str] = None, campaign: Optional[str] = None) -> int:
    """Number of touches in the cadence (== MAX_TOUCHES for the default)."""
    return len(get_cadence(segment, campaign))


def offset_for_step(step: int, segment: Optional[str] = None, campaign: Optional[str] = None) -> Optional[int]:
    """Offset (days from first contact) for ``step``, or ``None`` if out of range."""
    cadence = get_cadence(segment, campaign)
    if 0 <= step < len(cadence):
        return cadence[step].offset_days
    return None


def next_step(
    current_step: int,
    last_sent_at: Optional[datetime],
    segment: Optional[str] = None,
    campaign: Optional[str] = None,
    max_touches: Optional[int] = None,
) -> Optional[Tuple[int, Optional[datetime]]]:
    """Compute the next touch after ``current_step``.

    Args:
        current_step: 0-based index of the LAST touch already sent. The D0
            initial send is step 0, so a lead that has only had the initial
            outreach is at ``current_step == 0``.
        last_sent_at: when that last touch was sent (tz-aware). Used to compute
            ``run_after`` for the next touch by advancing the *interval* between
            the two cadence offsets. ``None`` means "schedule immediately".
        segment / campaign: select the cadence (default sequence today).
        max_touches: cap override (defaults to the cadence length / MAX_TOUCHES).

    Returns:
        ``(next_step_index, run_after)`` where ``run_after`` is the tz-aware
        datetime the next touch becomes due, or ``None`` if the cap is reached
        / there is no further step (the lead falls out of the cadence).

        ``run_after`` is ``last_sent_at + (offset[next] - offset[current])`` —
        the gap between consecutive cadence offsets, anchored on the real last-
        send time so drift in the scheduler doesn't compound. If ``last_sent_at``
        is ``None`` the next touch is due immediately (``run_after`` is ``None``,
        which ``enqueue`` treats as "now").
    """
    cadence = get_cadence(segment, campaign)
    cap = max_touches if max_touches is not None else len(cadence)

    nxt = current_step + 1

    # Cap: total touches (including D0) may not exceed `cap`. Touch count for
    # step index `nxt` is `nxt + 1` (0-based). Stop once that would exceed cap.
    if nxt + 1 > cap:
        return None
    # No cadence definition for this step -> end of sequence.
    if nxt >= len(cadence):
        return None

    next_offset = cadence[nxt].offset_days

    if last_sent_at is None:
        return (nxt, None)

    # Anchor the next run on the gap between consecutive offsets so the spacing
    # is preserved even if the previous send was late.
    cur_offset = cadence[current_step].offset_days if 0 <= current_step < len(cadence) else 0
    gap_days = next_offset - cur_offset
    if gap_days < 0:
        gap_days = 0
    run_after = last_sent_at + timedelta(days=gap_days)
    return (nxt, run_after)


def is_due(
    current_step: int,
    last_sent_at: Optional[datetime],
    now: datetime,
    segment: Optional[str] = None,
    campaign: Optional[str] = None,
    max_touches: Optional[int] = None,
) -> bool:
    """True iff the next touch after ``current_step`` exists AND is due by ``now``.

    "Due" means the gap between the current and next cadence offset has elapsed
    since ``last_sent_at`` (i.e. the lead's last send is older than the next
    cadence interval). A lead with no further step (cap reached) is never due.
    """
    nxt = next_step(current_step, last_sent_at, segment, campaign, max_touches)
    if nxt is None:
        return False
    _, run_after = nxt
    if run_after is None:
        return True
    return run_after <= now


def steps_due_offsets() -> List[int]:
    """The raw offset list (days). Handy for tests/introspection."""
    return list(DEFAULT_OFFSETS_DAYS)
