"""Structural tests for the L0 schema — run with NO database required.

These read the frozen migration SQL and assert the contract is intact: the
right tables, the full lifecycle enum, the eng-review decisions (3C identity
uniqueness, 6A suppression-by-reason), and that pgvector stays deferred. They
catch schema drift in CI without needing a Postgres instance.
"""
from __future__ import annotations

import re
from pathlib import Path

import data.migrate as migrate

SQL = (Path(__file__).resolve().parent.parent / "data" / "migrations" / "0001_init_schema.sql").read_text()
SQL_LC = SQL.lower()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


SQL_NORM = _norm(SQL)

# SQL with `-- ...` line comments stripped, for "no *active* statement" checks
# (so a comment documenting a deferred feature doesn't trip a substring match).
SQL_NO_COMMENTS = "\n".join(re.sub(r"--.*$", "", line) for line in SQL.splitlines()).lower()


def test_all_tables_present():
    for table in (
        "target_specs", "leads", "channels", "campaigns", "messages",
        "events", "conversions", "suppression", "kb_chunks",
    ):
        assert f"create table {table} (" in SQL_NORM, f"missing table: {table}"


def test_lead_lifecycle_enum_complete():
    # M2 AC: the lifecycle statuses must all exist.
    for status in (
        "new", "queued", "contacted", "replied", "in_conversation",
        "demo_booked", "converted", "dead", "opted_out",
    ):
        assert f"'{status}'" in SQL_LC, f"lead_status missing: {status}"


def test_event_types_include_bounce_and_complaint():
    # Added by eng review so bounce/complaint can drive suppression.
    m = re.search(r"create type event_type_t\s+as enum\s*\((.*?)\)", SQL_NORM, re.S)
    assert m, "event_type_t enum not found"
    body = m.group(1)
    for ev in ("open", "reply", "click", "bounce", "complaint", "book", "optout"):
        assert f"'{ev}'" in body, f"event_type missing: {ev}"


def test_channel_types():
    for ch in ("email", "whatsapp", "linkedin"):
        assert f"'{ch}'" in SQL_LC


def test_3c_identity_key_unique():
    # 3C: lead identity is one resolved key, unique per creator.
    assert "identity_key text not null" in SQL_NORM
    assert "unique (identity_key)" in SQL_NORM


def test_6a_suppression_scope_by_reason_check():
    # 6A: opt-out is identity-wide (channel_type NULL); bounce/complaint are
    # channel-specific (channel_type NOT NULL).
    assert "suppression_scope_by_reason" in SQL_LC
    m = re.search(r"constraint suppression_scope_by_reason check\s*\((.*?)\)\s*\)", SQL_NORM, re.S)
    assert m, "6A check constraint not found"
    body = m.group(1)
    assert "reason = 'optout' and channel_type is null" in body
    assert "reason in ('hardbounce', 'complaint') and channel_type is not null" in body


def test_6a_partial_unique_indexes():
    assert "suppression_identity_wide_uniq" in SQL_LC
    assert "suppression_per_channel_uniq" in SQL_LC


def test_pgvector_deferred_to_l6():
    # The embedding column must stay commented out so L0 needs no extension.
    assert "deferred to l6" in SQL_LC
    # No *active* (uncommented) vector column or extension in L0.
    assert "create extension" not in SQL_NO_COMMENTS, "no active CREATE EXTENSION allowed in L0"
    assert not re.search(r"^\s*embedding\s+vector", SQL_NO_COMMENTS, re.M), "embedding column must be commented out"


def test_updated_at_trigger_on_leads():
    assert "leads_set_updated_at" in SQL_LC
    assert "set_updated_at()" in SQL_LC


def test_runner_discovers_the_migration():
    names = [p.name for p in migrate.discover()]
    assert "0001_init_schema.sql" in names
    # filenames must be ordered so application order is deterministic.
    assert names == sorted(names)
