# Exly Autonomous Outbound Engine

> Creator & Affiliate Acquisition Engine — "sales in reverse, pointed at creators."
> Full vision and 10-layer build plan: **[PRD.md](PRD.md)**.

An always-on engine that will source ICP creators and affiliates across India, reach out over email + WhatsApp (+ LinkedIn later), follow up, answer their questions, and book them into a demo — getting sharper over time on *who* to target and *what* to say.

This repo is being built **layer by layer** (L0 → L10). Each layer is a folder with a clean interface so the next one attaches without rework.

> 🧭 **Resuming work / new session?** Read **[HANDOFF.md](HANDOFF.md)** first — it has the current state, what's done, what's pending, and exactly where to pick up.

---

## ⭐ Where this repo is today (2026-06-27)

**A working backend (L0–L8, partial) + a CRM front end (`web/`).** Built across parallel sessions and verified against live Supabase Postgres (117 backend tests green; the front end builds clean + deploys green on Vercel). **Consolidated onto `main`** (the single branch) on 2026-06-27 — it holds the entire engine. See [HANDOFF.md](HANDOFF.md) to resume.

| Layer | Module | Status | What's there |
|---|---|---|---|
| **L0** Data foundation | M2 | ✅ built | 9-table schema (migrations `0001`–`0004`) live on Supabase; loader + composite identity resolver; durable `send_jobs` queue |
| **L1** Targeting + Sourcing | M1 | 🟢 mostly | Targeting brain (Mode A persona + Mode B keyword, Sonnet-class); Meta Ad Library scraper (hardened) + YouTube Data API adapter via `SourceAdapter`. IG/LinkedIn pending |
| **L2** Enrichment + scoring | M3 | ✅ built | Rules-based ICP score (0–100) + deterministic `priority_rank`; weights editable via `scoring_config` |
| **L3** Personalization | M4 | ✅ built | Value-prop library + P4 anti-mail-merge guardrail; generate→queue |
| **L4** Dispatch | M5 | 🟡 email | Email adapter + warmup ramp built. WhatsApp = opt-in-led BSP (Interakt) adapter **pending** |
| **L5** Follow-ups | M6 | ✅ built | D0/D3/D7 cadence + stop rules (placeholder bug fixed, 12/12 on live PG) |
| **L6** Reply handling | M7 | 🟡 dumb | Inbound reply/bounce → events + suppression + opt-out + human handoff. Smart RAG auto-answer **not** built |
| **L7** Conversion / booking | M8 | ⬜ stub | handoff-payload stub only |
| **L8** Orchestration | M9 | 🟡 substrate | Celery+Redis + Postgres durable queue (idempotent claim→send→record). The always-on source→…→reply loop **not yet assembled** |
| **L9** Feedback loops | M10 | ⬜ missing | — |
| **L10** Analytics & ops | M11 | 🟡 via CRM | The `web/` dashboard gives funnel / reputation / awaiting-reply views; no separate analytics service |
| **Front end (CRM)** | — | ✅ built | **`web/`** Next.js app (Vercel + Supabase): L1 sourcing, L2 scoring, dashboard, pipeline, lead-360 |

**Not live yet:** WhatsApp send, smart replies (L6 RAG), demo booking (L7), the always-on loop (L8), feedback loops (L9). **Go-live is gated** on: DPDP legal sign-off · rotating the leaked tokens (Vercel / AI-gateway / `SUPABASE_SECRET_KEY`) · running migration `0004` · wiring `scoring_config` into the scorer + `_do_source_run`.

---

## 📁 Repo structure

```
Outbound/
├── HANDOFF.md              ← resume here (current state + next steps)
├── PRD.md                  ← the full plan
├── README.md               ← you are here
├── requirements.txt  .gitignore  pyrefly.toml
│
├── data/                   L0 — schema/migrations + loader + resolver   ✅
├── sourcing/
│   ├── meta_ads/           Meta Ad Library scraper (hardened)           ✅
│   ├── youtube/            YouTube Data API adapter                     ✅
│   ├── base.py             SourceAdapter interface                      ✅
│   ├── instagram/          🔧   └── linkedin/  🔧 (sourcing only)
├── targeting/              M1 — AI targeting brain (Mode A + B)         ✅
├── enrichment/             L2 — ICP score + priority_rank               ✅
├── personalization/        L3 — generation + P4 guardrail               ✅
├── dispatch/
│   ├── email/              L4 — email adapter + warmup                  ✅
│   └── whatsapp/           L4 — WhatsApp BSP (Interakt)                 🔧
├── followups/              L5 — D0/D3/D7 cadence                        ✅
├── replies/ + inbound      L6 — dumb inbound (events+suppression)       🟡
├── conversion/             L7 — demo booking                            ⬜ stub
├── orchestration/          L8 — Celery+Redis durable queue + app_jobs   🟡
├── feedback/               L9 — Loop A + Loop B                         🔧
├── analytics/              L10 — (covered by web/ dashboard)            🟡
├── kb/                     RAG knowledge base (for L6)                  🔧
│
└── web/                    CRM front end — Next.js + Supabase           ✅
    └── app/{sourcing,scoring,dashboard,pipeline,leads}   L1·L2·dashboard·pipeline·lead-360
```

✅ built · 🟡 partial · ⬜ stub · 🔧 scaffold. Each layer folder has a `README.md` with its PRD layer + read/write contract.

---

## 🚀 What works today: the Meta scraper

### Setup (one time)
```sh
cd "Outbound"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium      # ~150MB browser download
```

### Run it
```sh
# 1. Quick smoke test — 1 query, 2 scrolls, 3 advertiser pages (~1-2 min)
.venv/bin/python sourcing/meta_ads/run_debug.py

# 2. Full multi-keyword harvest — heavy: 22 queries, long run, writes to ./scraper_results/
.venv/bin/python sourcing/meta_ads/run_scraper.py

# 3. One-off custom search
.venv/bin/python sourcing/meta_ads/facebook_ads_scraper.py \
  '{"query": "fitness coach", "country": "IN", "max_scrolls": 3, "max_ads_to_detail": 5}'
```

Edit the `queries` / `params` near the top of [`sourcing/meta_ads/run_scraper.py`](sourcing/meta_ads/run_scraper.py) to change keywords and depth.

### The scripts in `sourcing/meta_ads/`
| File | Role |
|---|---|
| `facebook_ads_scraper.py` | Core engine — scrape, extract, filter, visit advertiser pages |
| `run_scraper.py` | Multi-keyword runner → 4 CSVs + JSON in `scraper_results/` |
| `run_debug.py` | Small single-query test runner |
| `harvest_leads.py` | Two-phase concurrent harvester (target: email+phone leads) |
| `harvest_niche.py` | Niche harvester with stricter Indian-phone validation |
| `resume_harvest.py` | Resume an interrupted harvest from a pool checkpoint |
| `parse_log_to_csv.py` | Salvage advertiser data from a crashed run's log |

### Output
Runs write to `scraper_results/` (gitignored — it holds scraped personal data). You get a full JSON plus CSVs: comprehensive, contacts-only, social-only, and high-value leads.

---

## 🛡️ Production hardening (env vars)
The scraper engine reads a few optional environment variables (see `.env.example`). All default to the historical behaviour, so nothing changes if they're unset:

| Var | Default | Purpose |
|---|---|---|
| `SCRAPER_PROXIES` | _(empty)_ | Comma-separated proxy URLs (inline `user:pass@host` supported). A rotating proxy is applied per browser context; empty = direct connection. |
| `SCRAPER_CONCURRENCY` | `2` | Concurrent advertiser-page visits (lowered from the old hardcoded 4). |
| `SCRAPER_MAX_RETRIES` | `3` | Navigate+detect retries per query, with exponential backoff + jitter on blocked/empty pages. |
| `SCRAPER_BACKOFF_BASE` / `SCRAPER_BACKOFF_MAX` | `1.0` / `60.0` | Backoff schedule bounds (seconds). |
| `SCRAPER_LOG_LEVEL` | `INFO` | Verbosity of the structured logger (failures are logged to stderr). |

A realistic **user-agent is rotated per context** from a small pool. Failures are no longer silently swallowed: per-item errors are logged but skipped (resilience preserved), while a **wholesale failure is loud** — if the `Library ID` selector is never detected across the run *and* no ads are extracted, `scrape_ads` raises `ScraperBlockedError` (non-zero exit) instead of emitting empty rows that downstream mistakes for success.

## ⚠️ Known limitations (don't mistake these for done)
- **Phone numbers are noisy** in `run_scraper.py`/`harvest_leads.py` page-level extraction paths, though WhatsApp and the per-advertiser phone/whatsapp fields now route through the strict `clean_phone` Indian-mobile validator.
- **Website filter over-rejects.** A substring blocklist drops legit domains like `box.com`/`fox.com`. (Fix flagged.)
- **Facebook HTML changes often.** A "No ads found" result usually means selectors need updating, not that setup is broken.
- **No phone/email validation against real deliverability** — these are raw extractions.

## ⚖️ Compliance & ethics (read before scaling)
This sources **personal contact data**. Before any outreach at volume:
- **WhatsApp:** cold messaging is restricted — use the official Business API via a BSP with pre-approved templates, opt-in-led. Raw blasting gets numbers banned. (PRD §13)
- **LinkedIn:** sourcing/enrichment only; automated DMs violate ToS.
- **India DPDP Act 2023:** needs a lawful basis, opt-out handling, and retention limits. Loop in legal before scale.
- Scraping the Meta Ad Library is subject to Facebook's Terms of Service.

---

## 🗺️ Roadmap
- **v1 — "Send machine" (L0–L5):** scrape → score → personalize → send (email + WhatsApp) → follow up. Humans handle replies.
- **v2 — "Autonomous to demo" (L6–L8):** reply handling, auto-answer, demo booking, orchestration loop.
- **v3 — "Self-improving" (L9–L10 + LinkedIn):** both feedback loops, analytics, LinkedIn.

**Suggested next build:** `data/` (L0) — the Lead DB schema. Every other layer reads and writes through it, so it unblocks the most work. See [PRD.md §12](PRD.md) for the starter schema.
