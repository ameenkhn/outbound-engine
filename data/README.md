# Lead DB / Data Foundation (L0)

**Status:** ✅ Schema frozen — this is the contract every other layer writes against.
**PRD:** L0 / M2 — see [`../PRD.md`](../PRD.md).
**Builds on:** nothing (foundation).

## The frozen contract
The single source of truth is [`migrations/0001_init_schema.sql`](migrations/0001_init_schema.sql).
Do not edit a committed migration in place — add a new `NNNN_*.sql`. Tables:
`target_specs, leads, channels, campaigns, messages, events, conversions,
suppression, kb_chunks`.

Eng-review decisions baked into the schema (plan-eng-review 2026-06-26):
- **3C — composite identity.** `leads.identity_key` is the resolved one-per-creator
  key (UNIQUE). Email/phone/socials are `channels` rows, not the identity. The
  resolver that fills `identity_key` is **T2** (next).
- **6A — suppression by reason.** `opt-out` => identity-wide (`channel_type` NULL,
  blocks the person everywhere); `hardbounce`/`complaint` => channel-specific.
  Enforced by the `suppression_scope_by_reason` CHECK + partial unique indexes.
  The dispatcher re-checks suppression at send time (T5), not at enqueue.
- **pgvector deferred to L6.** `kb_chunks` exists but the embedding column +
  extension are commented out, so L0 needs no extension.

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # set DATABASE_URL
.venv/bin/python -m data.migrate            # apply pending migrations
.venv/bin/python -m data.migrate --status   # show applied / pending / drift
```

## Interface (how the next layer attaches)
- **Reads:** writes from every layer.
- **Writes:** single source of truth for all layers.
- `data.db.connect()` — the one connection helper everything imports.
- `data.migrate` — forward-only runner; refuses to proceed if a frozen migration
  was edited on disk (protects the contract).

## Tests
- `tests/test_schema_structure.py` — no DB needed; asserts the contract (tables,
  enums, 3C uniqueness, 6A check) so CI catches drift.
- `tests/test_schema_db.py` — runs against a real Postgres when `DATABASE_URL`
  is set (builds the schema in a throwaway namespace, asserts constraints, drops it).
