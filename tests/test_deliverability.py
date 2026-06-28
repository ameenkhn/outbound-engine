"""No-DB unit tests for the pure deliverability helpers (warmup ramp + scope key)."""
from __future__ import annotations

import datetime as dt

from dispatch.email.deliverability import (
    STEADY_STATE_CAP,
    WARMUP_SCHEDULE,
    daily_scope_key,
    warmup_cap,
)


def test_warmup_cap_starts_low():
    # A cold domain must not be allowed to blast on day one.
    assert warmup_cap(0) <= 50
    assert warmup_cap(0) > 0


def test_warmup_cap_is_monotonic_non_decreasing():
    caps = [warmup_cap(d) for d in range(0, len(WARMUP_SCHEDULE) + 10)]
    for earlier, later in zip(caps, caps[1:]):
        assert later >= earlier, f"warmup cap must never decrease: {earlier} -> {later}"


def test_warmup_cap_negative_day_treated_as_day_zero():
    assert warmup_cap(-5) == warmup_cap(0)


def test_warmup_cap_plateaus_at_steady_state():
    # Well past the ramp, the cap is the steady-state value.
    assert warmup_cap(len(WARMUP_SCHEDULE)) == STEADY_STATE_CAP
    assert warmup_cap(10_000) == STEADY_STATE_CAP
    # And steady state is >= the last ramp value (monotonicity at the boundary).
    assert STEADY_STATE_CAP >= WARMUP_SCHEDULE[-1]


def test_warmup_cap_returns_int():
    assert isinstance(warmup_cap(0), int)
    assert isinstance(warmup_cap(500), int)


def test_daily_scope_key_format_with_date():
    key = daily_scope_key("Mail.Example.com", dt.date(2026, 6, 26))
    assert key == "email:mail.example.com:2026-06-26"


def test_daily_scope_key_accepts_datetime():
    key = daily_scope_key("send.brand.io", dt.datetime(2026, 6, 26, 13, 45, 0))
    assert key == "email:send.brand.io:2026-06-26"


def test_daily_scope_key_accepts_iso_string():
    key = daily_scope_key("send.brand.io", "2026-06-26")
    assert key == "email:send.brand.io:2026-06-26"


def test_daily_scope_key_lowercases_and_strips_domain():
    assert daily_scope_key("  SEND.Brand.IO  ", "2026-01-01") == "email:send.brand.io:2026-01-01"
