# HANDOFF — Exly Autonomous Outbound Engine

> **New session: read this top-to-bottom, then continue from "▶ Pick up here."**
> Full vision: [PRD.md](PRD.md) · public overview: [README.md](README.md) · front end: [web/README.md](web/README.md)

_Last updated: 2026-07-02_ — **the engine is now feature-complete (L0–L10) for internal-team use.** README.md is the authoritative current status; sections 4 onward below are historical/roadmap notes retained for context.

---

## 1. What this is
A layered (L0–L10) engine that sources ICP creators/affiliates across India, reaches out (email + WhatsApp), follows up, and books demos. North-star = **demos booked / week**. Single source of truth = the Supabase Postgres schema; the front end is a Next.js CRM on Vercel that reads/writes it.

## 2. Branch state — FULLY CONSOLIDATED 2026-06-27 (ONE branch: `main`)
- **`main` is the single branch and holds the entire engine** — all backend layers + the CRM front end + docs. **Work from `main`.** Tip `df49805`.
- The combined branch `web-frontend-l1-l2` was **fast-forwarded into `main` (PR #2 MERGED) and deleted**. The earlier layer branches (`l0-integration`, `l0-data-foundation`, `l0-loader-resolver`, `scraper-hardening`) were each verified 0-unmerged and deleted too. **Only `main` remains on GitHub.**
- **Vercel:** the `outbound` project deploys `web/` as Next.js (Root Directory = `web` + `web/vercel.json` pin); the deploy is **green**. Env vars set in Vercel (rotate if not already).
- **Parallel session:** a second session runs a different task on its own branch and will merge into `main` separately. Don't clobber it.

## 3. What's built (verified — 2026-07-02)
**306 Python tests green + web build green; DB = Supabase Mumbai `kcfcibmbpnofpxsysagn`, migrations `0001`–`0007`, 113 leads.**

| Layer | Status | Notes |
|---|---|---|
| L0 data foundation | ✅ | schema migrations `0001`–`0007` live on Supabase Mumbai; loader + composite identity resolver; `send_jobs` + `outreach` + `kb_docs` tables. |
| L1 targeting + sourcing | ✅ | brain Mode A + Mode B → `target_specs`; **five live sources** — Meta Ad Library, YouTube, **Instagram + LinkedIn** (compliant public web-search), web-search enrichment — via `SourceAdapter`; scrape-time dedup. |
| L2 enrichment + scoring | ✅ | rules-based `icp_score` (0–100) + deterministic `priority_rank`; weights in `scoring_config`. |
| L3 personalization | ✅ | value-prop library + P4 guardrail; Claude Haiku channel-aware copy (email + WhatsApp). |
| L4 dispatch | ✅ | **email via Resend** + **WhatsApp via AiSensy** (live HTTP adapters) + Smartlead campaign push; email warmup. Sends fire from Compose and log to `outreach`. |
| L5 follow-ups | ✅ | D0/D3/D7 cadence + stop rules. |
| L6 reply handling | ✅ | inbound webhooks (`/api/webhooks/{resend,aisensy}`) auto-log replies, flip to `replied`, suppress on bounce/STOP; **RAG** (`kb_docs` + `kb_search`) powers suggested-reply + auto-responder; KB editable at `/kb`. |
| L7 conversion / booking | ✅ | book-demo form → `conversions` + `book` event → `demo_booked`, with **Google Calendar + Meet** sync. |
| L8 orchestration | ✅ | durable queue + `app_jobs` consumer **+ always-on `pipeline.py` loop** (discover→score→personalize→gated send), schedulable via `enqueue_cycle` / Celery beat. |
| L9 feedback loops | ✅ | `/insights` — reply-rate + conversion by niche/channel/source + heuristic suggested actions. |
| L10 analytics | ✅ | funnel / send / reply-rate / conversion via `web/` dashboard, Outreach log, Insights. |
| **Front end (CRM)** | ✅ | `web/` Next.js + Supabase on Vercel: sourcing, scoring, dashboard, pipeline, lead-360 (thread + AI reply + booking), compose, outreach, insights, `/kb`. `next build` clean (14 routes). |

> **Remaining is not layer work — it's productization:** multi-tenant auth + billing + self-serve onboarding (to sell beyond the internal team), and the optional deeper items in the roadmap below (import-at-scale, niche taxonomy, messaging-flow builder). Setup to go live (env vars, provider accounts, webhooks, Railway paid) is in the go-live checklist.

## 4. This-session deliverables (front end)
- `web/` Next.js app — reads Supabase directly; engine actions write `app_jobs` rows.
- Migration `data/migrations/0004_frontend_crm.sql` (additive): `app_jobs` queue, `scoring_config` (seeded), `conversions.demo_scheduled_at`+`status` (no-show), `leads.notes`.
- `orchestration/app_jobs.py` — consumer draining `app_jobs` (`rescore`/`mode_a`/`mode_b`/`approve_spec`; `source_run` stubbed).

## ▶ Roadmap to full automation
**PRD goal:** an always-on engine that autonomously sources → personalizes → sends → follows up → answers → **books a demo**, learning over time. Here's what's a brick, what needs wiring, and what's net-new. Work from `main`.

### A. ✅ BUILT — the bricks (code exists, tested)
L0 schema/loader/identity-resolver · L1 targeting brain (Mode A+B) + Meta & YouTube `SourceAdapter`s · L2 ICP scoring + `priority_rank` · L3 personalization + P4 guardrail · L4 **email** adapter + warmup · L5 follow-up cadence · L6 **dumb** inbound (reply/bounce → events + suppression + opt-out + human handoff) · L8 **durable queue substrate** (`send_jobs` claim→send→record) + `app_jobs` consumer · CRM front end (L1/L2/dashboard/pipeline/lead-360).

### B. 🔌 NEEDS INTEGRATION — wire the bricks together (this is where automation actually turns on; mostly glue, highest leverage)
1. **Assemble the L8 always-on loop** ← THE automation unlock. A scheduled controller (Celery beat) that chains end-to-end: approved `target_specs` → adapters (source) → `enrichment.run` (score) → `priority_rank` select → L3 (personalize) → `send_jobs` enqueue → L4 email (dispatch) → L5 (follow-up) → L6 (capture replies). Every piece exists; nothing runs them on a schedule yet. → `orchestration/`.
2. **Run the `app_jobs` consumer on a schedule** (beat/cron) so the CRM buttons (re-score, run sourcing, brain modes) actually execute. → `orchestration/app_jobs.py`.
3. **`scoring_config` → scorer** — `enrichment/run.py` must read `scoring_config` (the L2 panel writes it) instead of hardcoded `WEIGHTS`, so the weights panel is live.
4. **`_do_source_run`** — wire the `SourceAdapter` registry so the CRM "Run YouTube/Meta" buttons actually source (stub today).
5. **Lead-selection → dispatch** — the `priority_rank` selector that feeds personalize/send (flagged unbuilt in the v1 design).
6. **Inbound reply body** — store the reply text (events hold only intent/sentiment now) so the conversation thread + smart replies have content.

### C. 🆕 YET TO BUILD — new code (v2 → v3)
> **⚠️ Mostly DONE now (2026-07-02) — see §3 for the authoritative status.** Built since this list was written: **L4 WhatsApp (AiSensy)**, **L6 smart/RAG reply + auto-responder**, **L7 booking + Google Calendar** (no-show logic still TBD), **L9 insights** (heuristic; full bandit TBD), **L10 analytics**, **Instagram + LinkedIn sourcing**, **L8 always-on loop**. Genuinely still open below: CRM **import-at-scale**, **niche taxonomy**, **messaging-flow builder**, and **multi-tenant auth/billing**.
- **L4 WhatsApp** — opt-in-led Interakt BSP adapter into the send seam (templates + 24h window + opt-in; PRD §13). Plus a Click-to-WhatsApp / email opt-in funnel to *earn* the opt-in (cold WhatsApp is banned).
- **L6 smart reply** — intent classify (Haiku) → KB-RAG auto-answer (`kb/` embeddings + pgvector) → escalate. The "is it automated?" two-way core.
- **L7 conversion / demo booking** — ✅ built: book-demo form → `conversions` + Google Calendar/Meet sync. Still open: **No-show** detect + re-engage (`0004` fields ready; logic TBD), and external Calendly/Cal.com webhook if desired.
- **L9 feedback loops** — Loop A (targeting: bias sourcing + re-weight ICP toward converters) + Loop B (content: bandit on angle/subject/CTA per segment). Reads `events`+`conversions`.
- **L10 analytics/ops** — reputation alerts + weekly digest (the CRM dashboard already covers the funnel view).
- **Sourcing expansion** — Instagram + LinkedIn (sourcing-only) adapters on the `SourceAdapter` interface.
- **CRM remaining surfaces** — conversation inbox (needs WhatsApp + dispatch live) + conversion/no-show ops screen.
- **CRM · Data import AT SCALE** (`/import`) — bring an **existing HUGE lead database** in (user has one). Front-end: CSV upload → column-map (name/page→`identity_key`, email→email channel, phone→whatsapp channel, **main_niche / sub_niche**, `platform`, `follower_count`, `source`) → preview. Because the DB is large: upload the file to **Supabase Storage** (NOT inline in the `app_jobs` payload — payload carries the storage ref), and the NEW `import_leads` handler in `orchestration/app_jobs.py` **streams it in batches** (~1k rows), each row → **smart cleaning** (reuse the scraper validators: `clean_phone` Indian-mobile normalize, `is_valid_email`, host-boundary `is_valid_website`) → `data/loader` + the **composite identity resolver**, so it **dedups against existing leads AND within the file** (no duplicates; channels merge onto the existing identity — preserves `lead-identity-not-email`). Resumable + idempotent (safe to re-run). Optional ingest **filters** (require a reachable channel · geo=IN · niche in target list). Returns new / merged / cleaned / rejected counts. Engine session owns the loader contract.
- **CRM · Niche taxonomy + segmentation** — make niche a first-class, **editable** segment the engine targets from. Model: **`main_niche`** (one word — fitness, finance, astrology, …) + **`sub_niche`** (yoga, stock-trading, …). Add `leads.main_niche` + `leads.sub_niche` (small migration) or a `niches` lookup table. In the CRM: assign/update main+sub niche **per-lead and in bulk**, and map a niche column on import. **Segment/filter** the whole pool by main+sub niche across pipeline / scoring / sourcing, and let the **L1 targeting brain + L2 scorer pick targets by niche** (extends L2's `TARGET_ICP_NICHES` and the brain's keyword/persona inputs). This is the "update my niche and the engine picks from there" ask.
- **CRM · Messaging flows** (`/flows`) — define + pick outreach sequences instead of hardcoded cadence/value-props. NEW migration (next free number — coordinate with engine session): `sequences(id, name, segment, active)` + `sequence_steps(sequence_id, step_no, day_offset, channel, angle, subject, body)` with `{{name}}/{{niche}}` merge fields. Front-end: a flow builder (add/reorder steps) + a picker to attach a flow to a `campaign`/segment. Engine wiring (L3/L5 owners): L3 renders `body` per step; L5 schedules by `day_offset` — replaces the hardcoded D0/D3/D7 + the value-prop library. (Requested 2026-06-27; deferred to coordinate with the live engine session.)

### 🎯 Critical path to "autonomous to demo" (v2)
1. **B1 + B2 (+ B3/B4/B5)** → the engine runs itself email-only: source → score → personalize → send → follow-up. This is the "is it automated?" → *yes*. Biggest unlock; do first.
2. **C · L6 smart reply** → genuine two-way conversation.
3. **C · L7 booking + no-show** → closes the loop to a booked demo.
4. **v3:** add WhatsApp (L4) + IG/LinkedIn sourcing + L9 feedback loops (gets smarter on who + what).

## 5. 🚧 Go-live gates (block real sending / merge to main)
- [ ] **Rotate leaked secrets** — Vercel token, AI-gateway key, `SUPABASE_SECRET_KEY` (all were pasted in chat). New values → Vercel env / `.env.local`, never the repo.
- [ ] **DPDP legal sign-off** for outreach to scraped contacts (opt-out alone does not cure lawful basis). Has legal lead time.
- [ ] **Run migration `0004`** on the live DB: `python -m data.migrate`.
- [x] **Auth-gate** — ✅ Vercel Deployment Protection is ON (verified: anon visitors hit the Vercel login wall). It's the access gate for the internal CRM (the app has no login of its own) — keep it on; add teammates to the Vercel team rather than disabling it.
- [ ] Warmed sending domain (SPF/DKIM/DMARC) for email.

## 6. How to run
```sh
# backend (repo root) — DATABASE_URL etc. in gitignored .env (Supabase Session pooler)
set -a; . ./.env; set +a
.venv/bin/python -m pytest                      # 117 green on live Supabase
.venv/bin/python -m data.migrate                # apply migrations (incl. 0004)
.venv/bin/python -m orchestration.app_jobs --once   # drain front-end-queued engine jobs

# front end
cd web && npm install && cp .env.example .env.local   # fill ROTATED keys
npm run dev      # localhost:3000   (npm run build to verify)
```

## 7. Gotchas / learnings (see gstack `/learn`)
- **Supabase external conn:** use the **Session pooler** host + project-qualified user `postgres.<ref>` (direct `db.<ref>` host is IPv6-only + throttles).
- **DB tests false-green:** root `conftest.py` must `load_dotenv()` before collection (else `skipif` silently skips).
- **gstack slug split:** project artifacts/learnings live under slug **`NEWPro`** (set when cwd = parent folder); from inside the repo the slug resolves to `affiliate-spec-Outbound` (a different/empty bucket). Run `/learn`, `/context-restore` etc. from the parent dir, or read this file directly.
