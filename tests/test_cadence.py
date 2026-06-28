"""Pure-logic tests for the L5 cadence (no database).

Covers the M6 spec for the default sequence:
  * step offsets are D0 / D3 / D7,
  * next_step advances the index and schedules run_after by the cadence gap,
  * the MAX_TOUCHES cap halts progression past the last touch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from followups import cadence


def test_default_offsets_are_d0_d3_d7():
    assert cadence.DEFAULT_OFFSETS_DAYS == (0, 3, 7)
    assert cadence.steps_due_offsets() == [0, 3, 7]
    # the cadence rows carry the same step/offset mapping
    assert [s.offset_days for s in cadence.DEFAULT_CADENCE] == [0, 3, 7]
    assert [s.step for s in cadence.DEFAULT_CADENCE] == [0, 1, 2]


def test_max_touches_matches_sequence_length():
    assert cadence.MAX_TOUCHES == 3
    assert cadence.cadence_length() == 3


def test_offset_for_step():
    assert cadence.offset_for_step(0) == 0
    assert cadence.offset_for_step(1) == 3
    assert cadence.offset_for_step(2) == 7
    assert cadence.offset_for_step(3) is None  # out of range
    assert cadence.offset_for_step(-1) is None


def test_next_step_from_d0_schedules_d3():
    last = datetime(2026, 6, 1, tzinfo=timezone.utc)
    result = cadence.next_step(current_step=0, last_sent_at=last)
    assert result is not None
    step, run_after = result
    assert step == 1
    # gap D0->D3 is 3 days, anchored on the last send
    assert run_after == last + timedelta(days=3)


def test_next_step_from_d3_schedules_d7():
    last = datetime(2026, 6, 4, tzinfo=timezone.utc)
    step, run_after = cadence.next_step(current_step=1, last_sent_at=last)
    assert step == 2
    # gap D3->D7 is 4 days
    assert run_after == last + timedelta(days=4)


def test_next_step_none_when_last_sent_at_missing_runs_immediately():
    step, run_after = cadence.next_step(current_step=0, last_sent_at=None)
    assert step == 1
    assert run_after is None  # enqueue treats None as "now"


def test_max_touches_cap_stops_progression():
    last = datetime(2026, 6, 8, tzinfo=timezone.utc)
    # After step 2 (the D7 touch, the 3rd of 3) there is no further step.
    assert cadence.next_step(current_step=2, last_sent_at=last) is None
    # And going beyond is still None.
    assert cadence.next_step(current_step=3, last_sent_at=last) is None


def test_max_touches_override_caps_earlier():
    last = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # With max_touches=1 (only the D0 send allowed) no follow-up is produced.
    assert cadence.next_step(current_step=0, last_sent_at=last, max_touches=1) is None
    # With max_touches=2 the D3 follow-up is allowed but not the D7.
    assert cadence.next_step(current_step=0, last_sent_at=last, max_touches=2) is not None
    assert cadence.next_step(current_step=1, last_sent_at=last, max_touches=2) is None


def test_is_due_respects_the_window():
    last = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # 2 days after the D0 send: the D3 touch (gap 3 days) is NOT yet due.
    assert cadence.is_due(0, last, now=last + timedelta(days=2)) is False
    # exactly at the 3-day gap: due.
    assert cadence.is_due(0, last, now=last + timedelta(days=3)) is True
    # well past: due.
    assert cadence.is_due(0, last, now=last + timedelta(days=10)) is True


def test_is_due_false_when_capped():
    last = datetime(2026, 6, 8, tzinfo=timezone.utc)
    # No step after the final touch -> never due.
    assert cadence.is_due(2, last, now=last + timedelta(days=365)) is False
