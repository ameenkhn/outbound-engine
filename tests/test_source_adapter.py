"""Unit tests for the SourceAdapter contract + registry (no DB).

Covers the interface itself (run signature, approval gate) and the small
registry (register / get_adapter / enabled_adapters / enable-disable).
"""
from __future__ import annotations

import pytest

import sourcing.base as base
from sourcing.base import (
    SourceAdapter,
    register,
    get_adapter,
    enabled_adapters,
    set_enabled,
    is_registered,
)


class _DummyAdapter(SourceAdapter):
    name = "dummy"

    def run(self, target_spec):
        if not self.require_approved(target_spec):
            return []
        return [{"page": "facebook.com/x", "attributes": {}, "lead_fields": {}}]


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts from an empty registry and restores afterward."""
    base._reset_registry_for_tests()
    yield
    base._reset_registry_for_tests()


def test_register_and_get_adapter_roundtrip():
    register("dummy", _DummyAdapter)
    assert is_registered("dummy")
    adapter = get_adapter("dummy")
    assert isinstance(adapter, SourceAdapter)
    assert adapter.name == "dummy"


def test_get_unregistered_raises_keyerror_with_known_names():
    register("dummy", _DummyAdapter)
    with pytest.raises(KeyError) as exc:
        get_adapter("nope")
    # the error lists known names so a typo is debuggable
    assert "dummy" in str(exc.value)


def test_enabled_adapters_reflects_enable_disable():
    register("dummy", _DummyAdapter, enabled=True)
    names = [a.name for a in enabled_adapters()]
    assert names == ["dummy"]

    set_enabled("dummy", False)
    assert enabled_adapters() == []

    set_enabled("dummy", True)
    assert [a.name for a in enabled_adapters()] == ["dummy"]


def test_register_disabled_is_known_but_not_enabled():
    register("dummy", _DummyAdapter, enabled=False)
    assert is_registered("dummy")
    assert enabled_adapters() == []
    # still retrievable by name
    assert get_adapter("dummy").name == "dummy"


def test_run_contract_yields_candidates_for_approved_spec():
    register("dummy", _DummyAdapter)
    adapter = get_adapter("dummy")
    out = list(adapter.run({"approved": True}))
    assert out and out[0]["page"] == "facebook.com/x"


def test_run_contract_yields_nothing_for_unapproved_spec():
    register("dummy", _DummyAdapter)
    adapter = get_adapter("dummy")
    assert list(adapter.run({"approved": False})) == []


def test_require_approved_accepts_object_and_dict():
    class Spec:
        approved = True

    assert SourceAdapter.require_approved(Spec()) is True
    assert SourceAdapter.require_approved({"approved": True}) is True
    assert SourceAdapter.require_approved({"approved": False}) is False
    assert SourceAdapter.require_approved({}) is False


def test_register_empty_name_rejected():
    with pytest.raises(ValueError):
        register("", _DummyAdapter)
