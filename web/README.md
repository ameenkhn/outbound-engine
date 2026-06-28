# Exly Outbound — CRM front end (`web/`)

Next.js (App Router) + Supabase operating console for the Exly Autonomous Outbound Engine.
Deploys on Vercel from this `web/` subdirectory. v1 ships the two screens whose backend is
already built and was CLI-only: **L1 Sourcing/Targeting** and **L2 Scoring**.

## Architecture — Postgres is the contract
```
Next.js (Vercel) ──read──▶ Supabase Postgres ◀──read/write── Python engine (Celery)
       │                        ▲                                   ▲
       └── write app_jobs row ──┘   orchestration/app_jobs.py ──────┘
   (rescore / mode_b / mode_a / source_run / approve_spec)
```
- **Display** (scores, specs, queue): the front end reads Supabase directly (service-role server client).
- **Engine actions**: the front end writes a row to `app_jobs` (migration 0004); the Python
  consumer `orchestration/app_jobs.py` claims it and runs the brain / scorer / adapter. No
  logic is duplicated in TypeScript.
- `approve_spec` is pure DB, so the UI does it directly (no engine round-trip).

## Setup
```sh
cd web
npm install
cp .env.example .env.local      # fill with FRESH (rotated) keys — see below
npm run dev                     # http://localhost:3000
```
Apply migration 0004 to the DB first (from repo root): `python -m data.migrate`.

Run the engine-side consumer so queued actions execute:
```sh
# from repo root, with DATABASE_URL + ANTHROPIC_API_KEY set
python -m orchestration.app_jobs --once     # or omit --once to loop
```

## Env (`.env.local`, never committed)
| Var | Scope | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | public | project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | browser reads |
| `SUPABASE_SERVICE_ROLE_KEY` | **server only** | server components / actions; never `NEXT_PUBLIC_` |

> ⚠️ The Vercel token + AI-gateway key pasted in chat earlier are **compromised — rotate them**.
> The `SUPABASE_SECRET_KEY` rotation is also still open. The front end reads everything from env;
> it never contains a literal secret.

## Screens
- **`/scoring` (L2):** score distribution, pool summary, priority queue (what the dispatcher works
  next), gate-failed list, and an editable **weights panel** (writes `scoring_config`) + **Re-score**
  (enqueues a `rescore` job).
- **`/sourcing` (L1):** Mode B keyword expansion + Mode A persona (enqueue brain jobs), the target-spec
  library with **Approve** (sign-off) and **Run YouTube / Run Meta** (enqueue `source_run`), plus a
  recent-runs feed and YouTube quota (if the adapter wrote it into spec attributes).

## Backend wiring TODOs (not done in this front-end branch)
1. **`enrichment` must read `scoring_config`.** The weights panel writes `scoring_config` (id=1, seeded
   with the v1 defaults), but `enrichment/score.py` still reads its hardcoded `WEIGHTS`/`TARGET_ICP_NICHES`.
   Wire `enrichment/run.py` to load `scoring_config` and pass weights/niches into `score_lead`. Until then
   the panel is the source of truth on screen but the scorer uses its constants.
2. **`source_run` dispatch.** `orchestration/app_jobs.py::_do_source_run` is a documented stub — the
   `SourceAdapter` registry/entrypoint is owned by the sourcing session; complete it there.
3. **Auth.** v1 uses the service-role server client with no per-user RLS. Protect the Vercel deployment
   (Vercel password protection or Supabase Auth) before exposing it; ideally move to anon + RLS.

## Not yet built (next surfaces, per the design doc)
Dashboard, pipeline board, lead-360, conversation inbox (needs dispatch + WhatsApp live), conversion/no-show
ops. Migration 0004 already added `conversions.demo_scheduled_at`/`status` and `leads.notes` so those land cleanly.
