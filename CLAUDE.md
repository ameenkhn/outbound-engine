# Exly Autonomous Outbound Engine — agent instructions

## 🧭 Session start: resume from the handoff
**Before doing anything, read [HANDOFF.md](HANDOFF.md).** It is the source of truth for
current state, branch layout, what's built vs pending, the ordered pick-up list, and the
go-live gates. Then continue from its "▶ Pick up here" section. Cross-check `git log`/branches
against it before any git operation.

## What this repo is
A layered (L0–L10) outbound engine that sources ICP creators/affiliates, reaches out
(email + WhatsApp), follows up, and books demos. Full plan in [PRD.md](PRD.md); public
overview + run commands in [README.md](README.md); front end in [web/README.md](web/README.md).
Single source of truth = the Supabase Postgres schema (`data/migrations/`).

## Hard rules
- **Branch:** `main` is the single branch with everything (consolidated 2026-06-27). Work from
  `main`. A parallel session may merge its own task branch into `main` later — don't clobber it.
- **Secrets:** never commit `.env*`, tokens, or keys. The Vercel / AI-gateway / `SUPABASE_SECRET_KEY`
  values pasted earlier are compromised and must be rotated before go-live.
- **Migrations are frozen once committed** — add a new `NNNN_*.sql`, never edit an applied one.
- Don't commit `.mcp.json` (kept untracked by convention).

## Skill routing
When a request matches a skill, invoke it. Key: product/scope → `/office-hours`; architecture →
`/plan-eng-review`; bugs → `/investigate`; code review → `/review`; ship → `/ship`; save/restore
context → `/context-save` / `/context-restore`; learnings → `/learn`.

> gstack note: project artifacts/learnings live under slug **`NEWPro`** (run gstack skills from the
> parent folder to hit that bucket); from inside this repo the slug resolves to `affiliate-spec-Outbound`.
> HANDOFF.md is the reliable, slug-independent resume anchor.
