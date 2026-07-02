# Exly Outbound — CRM front end (`web/`)

Next.js (App Router) + Supabase operating console for the Exly Autonomous Outbound Engine.
Deploys on Vercel from this `web/` subdirectory. It is now the **full operator app** — sourcing,
scoring, pipeline, lead-360, compose/send, outreach log, insights, and the knowledge base — not
just the original two screens.

## Architecture — Postgres is the contract
```
Next.js (Vercel) ──read──▶ Supabase Postgres ◀──read/write── Python engine (Celery / Railway)
       │                        ▲                                   ▲
       └── write app_jobs row ──┘   orchestration/app_jobs.py ──────┘
   (rescore / mode_b / mode_a / source_run / approve_spec / pipeline_cycle)
```
- **Display / send**: the front end reads Supabase directly (service-role server client) and sends
  WhatsApp/email via provider HTTP from server actions, logging every send to `outreach`.
- **Engine actions** (scrape, brain, re-score, pipeline loop): the front end writes an `app_jobs`
  row; the Python consumer `orchestration/app_jobs.py` claims and runs it. No engine logic is
  duplicated in TypeScript.
- **Inbound**: `app/api/webhooks/{resend,aisensy}` receive replies/bounces → log, mark replied,
  suppress, and optionally auto-respond (RAG).

## Setup
```sh
cd web
npm install
cp .env.example .env.local      # fill with your keys (never commit .env.local)
npm run dev                     # http://localhost:3000
```
Apply migrations `0001`–`0007` to the DB first (from repo root): `python -m data.migrate`.

Run the engine-side consumer so queued actions execute (from repo root, with `DATABASE_URL` set):
```sh
python -m orchestration.app_jobs --once     # or omit --once to loop
```

## Env (`.env.local`, never committed)
| Var | Scope | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | project URL + browser reads |
| `SUPABASE_SERVICE_ROLE_KEY` | **server only** | server components / actions; never `NEXT_PUBLIC_` |
| `ANTHROPIC_API_KEY` | server | AI copy + RAG replies |
| `AISENSY_API_KEY` / `AISENSY_CAMPAIGN` | server | WhatsApp send |
| `RESEND_API_KEY` / `EMAIL_FROM` | server | email send |
| `INBOUND_WEBHOOK_SECRET`, `AUTORESPOND`, `GOOGLE_*` | server | optional (webhooks, auto-responder, calendar) |

## Screens
- **`/` home**, **`/dashboard`** — funnel + overview.
- **`/sourcing` (L1)** — Mode A/B brain jobs, Quick Harvest, target-spec approve + Run (Meta/YouTube/Instagram/LinkedIn/all).
- **`/scoring` (L2)** — score distribution, priority queue, gate-failed list, editable weights panel + Re-score.
- **`/pipeline`** — kanban board + table across lifecycle stages.
- **`/leads` + `/leads/[id]`** — spreadsheet view; lead-360 with conversation thread, AI suggested reply, and demo booking (calendar).
- **`/compose`** — WhatsApp/email studio: template or AI copy, live preview, multi-select send to leads.
- **`/outreach`** — CRM send log with channel/status/niche filters, reply-rate, mark-replied.
- **`/insights`** — reply-rate + conversion by niche/channel/source + suggested actions.
- **`/kb`** — knowledge base entries powering RAG replies.

## Backend wiring — complete
- **Weights panel → live scorer:** `enrichment/run.py` loads the `scoring_config` row (weights,
  target niches, competitor tools) and passes it into `score_lead`, which merges it over the v1
  defaults. So editing the weights panel + Re-score changes real scores; if the row is absent the
  scorer falls back to the committed constants. (`source_run` is wired too.)

## Productization (deliberately not built — internal-team tool today)
Per-user **auth** (currently the service-role server client, no per-user RLS — protect the Vercel
deployment), multi-tenant isolation, and billing. These are the "turn it into a SaaS" layer.
