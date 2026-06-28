"""Unit tests for the value-prop library (no DB).

Pins: every segment x angle has complete content, the entries carry the three
required keys, and pick_angle returns a sane default per segment.
"""
from __future__ import annotations

import pytest

from personalization.value_props import (
    ANGLES,
    DEFAULT_ANGLE,
    SEGMENTS,
    VALUE_PROPS,
    get_value_prop,
    pick_angle,
)

REQUIRED_KEYS = {"usp", "differentiator", "wiifm"}


def test_segments_and_angles_are_the_expected_keys():
    assert set(SEGMENTS) == {"creator", "affiliate"}
    assert set(ANGLES) == {"cost_saving", "affiliate_fee", "competitive_switch", "ease"}


@pytest.mark.parametrize("segment", SEGMENTS)
@pytest.mark.parametrize("angle", ANGLES)
def test_every_segment_x_angle_has_complete_content(segment, angle):
    """No holes: every (segment, angle) pair resolves to a full entry."""
    entry = get_value_prop(segment, angle)
    assert set(entry.keys()) >= REQUIRED_KEYS
    for key in REQUIRED_KEYS:
        assert isinstance(entry[key], str)
        assert entry[key].strip(), "{0}/{1}/{2} is empty".format(segment, angle, key)
        # Each line should be a real sentence, not a placeholder.
        assert len(entry[key].split()) >= 5


def test_value_props_structure_is_full_grid():
    for segment in SEGMENTS:
        assert segment in VALUE_PROPS
        for angle in ANGLES:
            assert angle in VALUE_PROPS[segment], "missing {0}/{1}".format(segment, angle)


def test_mentions_exly_somewhere_per_segment_angle():
    """Each entry is an Exly pitch — 'Exly' should appear in at least one line."""
    for segment in SEGMENTS:
        for angle in ANGLES:
            entry = get_value_prop(segment, angle)
            blob = " ".join(entry.values()).lower()
            assert "exly" in blob, "no Exly mention in {0}/{1}".format(segment, angle)


def test_pick_angle_defaults():
    assert pick_angle("creator") == "cost_saving"
    assert pick_angle("affiliate") == "affiliate_fee"


def test_pick_angle_matches_default_table():
    for segment in SEGMENTS:
        assert pick_angle(segment) == DEFAULT_ANGLE[segment]
        # The default must itself be a valid, populated angle.
        assert pick_angle(segment) in ANGLES
        get_value_prop(segment)  # uses the default, must not raise


def test_pick_angle_unknown_segment_raises():
    with pytest.raises(ValueError):
        pick_angle("nonprofit")


def test_get_value_prop_unknown_segment_raises():
    with pytest.raises(ValueError):
        get_value_prop("nonprofit", "ease")


def test_get_value_prop_unknown_angle_raises():
    with pytest.raises(ValueError):
        get_value_prop("creator", "telepathy")


def test_get_value_prop_default_angle_when_none():
    entry = get_value_prop("affiliate", None)
    assert entry == get_value_prop("affiliate", "affiliate_fee")


def test_creator_cost_saving_is_about_cost():
    entry = get_value_prop("creator", "cost_saving")
    blob = " ".join(entry.values()).lower()
    assert any(w in blob for w in ("cost", "margin", "subscription", "tool"))


def test_affiliate_fee_is_about_earnings():
    entry = get_value_prop("affiliate", "affiliate_fee")
    blob = " ".join(entry.values()).lower()
    assert any(w in blob for w in ("commission", "earn", "payout"))
