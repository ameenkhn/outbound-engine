# Deploying the Outbound Engine (Supabase + Vercel)

This gets the whole stack live: the **Supabase** Postgres (the single source of
truth), the **Vercel**-hosted CRM (`web/`), and the Python engine that runs the
scrapers and drains the job queue.

```
Browser ──▶ Vercel (Next.js CRM)  ──writes app_jobs──▶  Supabase Postgres
                                                             ▲
Python engine (your box / a worker) ──drains app_jobs────────┘
   └─ sourcing adapters: meta_ads · instagram · linkedin · youtube (+ web-search enrich)
```

The CRM never runs scrapers itself — it writes an `app_jobs` row; the Python
`orchestration.app_jobs` consumer claims and runs it. So Vercel hosts the UI,
and the engine runs wherever you can keep a Python process alive (your machine, a
small VM, a Railway/Render worker, a cron box).

---

## 0. Security first (do this before anything ships)

Any Supabase / Vercel / AI-gateway keys pasted into a chat or committed earlier
are **compromised — rotate them** (Supabase dashboard → Project Settings → API →
roll keys; Vercel → Account → Tokens). Never commit `.env`, `.env.local`, or
service-role keys. The service-role key bypasses RLS — server-only, never
`NEXT_PUBLIC_`.

---

## 1. Apply the database schema to Supabase

The migrations in `data/migrations/` (`0001`–`0004`) are applied in order by the
forward-only runner. Point it at your Supabase Postgres via `DATABASE_URL`.

Get the connection string from Supabase → Project Settings → Database →
**Connection string → URI** (use the **Session/Direct** connection, port 5432):

```sh
cd Outbound
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

export DATABASE_URL="postgresql://postgres:<DB-PASSWORD>@db.<project-ref>.supabase.co:5432/postgres"

.venv/bin/python -m data.migrate --status     # shows pending: 0001..0004
.venv/bin/python -m data.migrate              # applies all four, idempotent
```

`--status` again should show all four under **Applied**. Re-running is safe — the
runner records each migration and refuses to re-apply or to run an edited one.

---

## 2. Configure the engine (provider keys)

Copy `.env.example` → `.env` and fill what you have. Everything is optional —
a source with no key is simply skipped, the others still run:

| Var | Source |
|---|---|
| `DATABASE_URL` | Supabase Postgres (same string as above) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 (official) |
| `INSTAGRAM_API_BASE` / `INSTAGRAM_API_KEY` | your Instagram provider (Apify/RapidAPI/…) |
| `LINKEDIN_API_BASE` / `LINKEDIN_API_KEY` | your LinkedIn provider |
| `WEBSEARCH_API_BASE` / `WEBSEARCH_API_KEY` | search provider (Serper/SerpAPI/Bing) — used for **enrichment only** |
| `SCRAPER_PROXIES` | optional rotating proxies (Meta + IG/LinkedIn/web clients) |

Meta Ad Library needs Playwright (no key, public data):
`.venv/bin/python -m playwright install chromium`.

Smoke-test locally with the control panel (no DB needed):
`.venv/bin/python -m sourcing.control_panel` → opens `http://127.0.0.1:8765`.

---

## 3. Deploy the CRM to Vercel

1. Push the repo to GitHub.
2. Vercel → **New Project** → import the repo. Set **Root Directory = `web`**
   (the Next.js app lives there; it builds with the default Next preset).
3. Add Environment Variables (Project → Settings → Environment Variables):

   | Name | Value | Scope |
   |---|---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<project-ref>.supabase.co` | all |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key (public-safe) | all |
   | `SUPABASE_SERVICE_ROLE_KEY` | Supabase **service-role** key (server-only) | all |

4. **Deploy.** Open the URL → `/dashboard`, `/leads`, `/sourcing` should render
   against your Supabase data. (`/leads` is paginated server-side and filterable.)

> v1 has no per-user auth — the service-role key reads/writes past RLS. **Protect
> the deployment**: Vercel → Settings → Deployment Protection → Password (or put
> it behind Vercel Authentication / your SSO) before sharing the URL.

---

## 4. Run the engine (drain the job queue)

The CRM's "Run Meta / Instagram / LinkedIn / YouTube / Run all" buttons enqueue
`source_run` jobs. A Python consumer executes them. Run it wherever Python lives,
with the same `.env` (so it can reach Supabase + the providers):

```sh
# one-shot (drain due jobs and exit — good for a cron entry every minute):
.venv/bin/python -m orchestration.app_jobs --once

# or loop forever (polls every 5s):
.venv/bin/python -m orchestration.app_jobs
```

Each `source_run` job runs the requested adapter(s) over the approved spec,
resolves leads through the loader (dedupe + false-merge guard), enriches missing
contacts via web search (budgeted), and writes results to Supabase — which the
CRM then shows. `--once` on a 1-minute cron is the simplest production setup.

---

## 5. Verify end-to-end

1. CRM → **L1 · Sourcing** → Mode B with a seed keyword → it auto-approves a spec.
2. Click **Run all** on that spec → an `app_jobs` row appears under "Recent runs".
3. Run `python -m orchestration.app_jobs --once` → the job flips to **done** with
   a per-source count.
4. CRM → **Leads** → the new leads are there, filterable and exportable to CSV.

That's the full loop: UI → queue → engine → Supabase → UI.
