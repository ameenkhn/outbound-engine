# Exly Autonomous Outbound Engine

> Creator & Affiliate Acquisition Engine — "sales in reverse, pointed at creators."
> Full vision and 10-layer build plan: **[PRD.md](PRD.md)**.

An always-on engine that will source ICP creators and affiliates across India, reach out over email + WhatsApp (+ LinkedIn later), follow up, answer their questions, and book them into a demo — getting sharper over time on *who* to target and *what* to say.

This repo is being built **layer by layer** (L0 → L10). Each layer is a folder with a clean interface so the next one attaches without rework.

> 🧭 **Resuming work / new session?** Read **[HANDOFF.md](HANDOFF.md)** first — it has the current state, what's done, what's pending, and exactly where to pick up.

---

## ⭐ Where this repo is today (2026-07-02)

**A working backend (L0–L8, partial) + a full CRM front end (`web/`) that now sources, scores, personalizes, and *sends* — WhatsApp + email — end to end.** Verified against live Supabase Postgres in **Mumbai (ap-south-1)**; the front end builds clean and deploys green on **Vercel**, with the scraper worker on **Railway**. See [HANDOFF.md](HANDOFF.md) to resume.

| Layer | Module | Status | What's there |
|---|---|---|---|
| **L0** Data foundation | M2 | ✅ built | Schema (migrations `0001`–`0005`) live on Supabase Mumbai; loader + composite identity resolver; durable `send_jobs` queue; `outreach` send-log (`0005`) |
| **L1** Targeting + Sourcing | M1 | ✅ built | Targeting brain (Mode A persona + Mode B keyword); **five live sources** — Meta Ad Library (hardened) + YouTube + **Instagram + LinkedIn** (compliant public web-search discovery, no login/API key needed) + web-search enrichment fallback — all via `SourceAdapter`; scrape-time dedup (skips known `identity_key`s) |
| **L2** Enrichment + scoring | M3 | ✅ built | Rules-based ICP score (0–100) + deterministic `priority_rank`; weights editable via `scoring_config` |
| **L3** Personalization | M4 | ✅ built | Value-prop library + P4 anti-mail-merge guardrail; **Claude Haiku** niche-aware copy for WhatsApp + email (channel-aware `default_generator()`) |
| **L4** Dispatch | M5 | ✅ live | **WhatsApp via AiSensy** + **email via Resend** (HTTP adapters), plus Smartlead campaign push. Email warmup ramp built. Sends fire from the Compose studio and log to `outreach` |
| **L5** Follow-ups | M6 | ✅ built | D0/D3/D7 cadence + stop rules |
| **L6** Reply handling | M7 | 🟢 mostly | **Inbound webhooks** (`/api/webhooks/{resend,aisensy}`) auto-log replies, flip the lead to `replied`, and suppress on bounce/complaint/STOP. **AI suggested reply** (Claude Haiku, grounded in an Exly KB) drafts a response on the lead page. Full RAG over a large KB still lightweight |
| **L7** Conversion / booking | M8 | 🟢 built | **Book-demo** action + form on lead-360 writes a `conversions` row, emits a `book` event, advances the lead to `demo_booked`. External calendar sync not wired |
| **L8** Orchestration | M9 | 🟢 built | Celery+Redis + Postgres durable queue **plus** the always-on loop: `orchestration.pipeline.run_cycle` chains discover→score→personalize→(gated) send as a `pipeline_cycle` job; schedule it via Railway Cron (`python -m orchestration.enqueue_cycle …`) or Celery beat. Autopilot send is double-gated (`send:true` + `AUTOPILOT_SEND=1`) with per-channel consent/suppression/dedupe |
| **L9** Feedback loops | M10 | 🟢 built | **`/insights`** — reply-rate + conversion by niche/channel/source, with heuristic "suggested actions" (which niches to prioritise, which channel out-replies, which ICP weight to bump) |
| **L10** Analytics & ops | M11 | 🟢 via CRM | `web/` dashboard + Outreach log + Insights give funnel / send / reply-rate / conversion views |
| **Front end (CRM)** | — | ✅ built | **`web/`** Next.js app (Vercel + Supabase): sourcing, scoring, dashboard, pipeline, lead-360 (with two-way thread, AI reply, booking), **Compose studio**, **Outreach log**, **Insights** |

**Still needs work:** deep RAG auto-answer over a full KB, external calendar sync for L7, richer IG/LinkedIn profiles (optional paid provider via `INSTAGRAM_API_BASE` / `LINKEDIN_API_BASE`), and assembling the always-on orchestration loop (L8) on their own infra. **Go-live is gated** on: DPDP legal sign-off · provider accounts + keys configured (`ANTHROPIC_API_KEY`, `AISENSY_API_KEY`, `AISENSY_CAMPAIGN`, `RESEND_API_KEY`, `EMAIL_FROM`, optional `INBOUND_WEBHOOK_SECRET`) in Vercel · webhook URLs registered in AiSensy + Resend · Railway on a paid plan (RAM for the Meta worker).

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
│   ├── instagram/          IG discovery (free public web-search)        ✅
│   ├── linkedin/           LinkedIn discovery (sourcing-only)           ✅
│   ├── websearch/          general web-search discovery + enrichment    ✅
├── targeting/              M1 — AI targeting brain (Mode A + B)         ✅
├── enrichment/             L2 — ICP score + priority_rank               ✅
├── personalization/        L3 — generation + P4 guardrail               ✅
├── dispatch/
│   ├── email/              L4 — email adapter + warmup + Resend         ✅
│   ├── whatsapp/           L4 — WhatsApp send via AiSensy               ✅
│   └── smartlead/          L4 — Smartlead campaign push                 ✅
├── followups/              L5 — D0/D3/D7 cadence                        ✅
├── replies/ + inbound      L6 — inbound webhooks + AI suggested reply   🟢
├── conversion/             L7 — demo booking (form + conversions row)   🟢
├── orchestration/          L8 — Celery+Redis durable queue + app_jobs   🟡
├── feedback/               L9 — insights + suggested actions (in web/)  🟢
├── analytics/              L10 — (covered by web/ dashboard)            🟡
├── kb/                     RAG knowledge base (for L6)                  🔧
│
└── web/                    CRM front end — Next.js + Supabase           ✅
    ├── app/{sourcing,scoring,dashboard,pipeline,leads,compose,outreach,insights}
    │   L1·L2·dashboard·pipeline·lead-360·compose·outreach-log·insights
    └── app/api/webhooks/{resend,aisensy}   L6 inbound reply/bounce handlers
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

## ✉️ Compose → Send → track (the CRM send loop)

The `web/` app now closes the loop from lead to message to reply, all in the browser:

- **Compose studio** (`/compose`) — pick **WhatsApp** or **email**, start from a built-in template *or* generate niche-aware copy with **Claude Haiku**, and see it render in a live phone/inbox preview. Placeholders `{{first_name}}` and `{{niche}}` are filled per lead.
- **Send to many** — the **Recipients** panel loads your real pipeline leads (filtered by niche, only those with a valid handle for the channel), multi-select with select-all, and **Send to N leads** (capped at 60/batch for provider rate limits). Or flip to **Test to me** to send a one-off to your own number/email first.
- **Providers** — WhatsApp goes out via **AiSensy**, email via **Resend**. Every send is personalized per lead and written to the `outreach` table; sent leads auto-advance `new → contacted`.
- **Outreach log** (`/outreach`) — the CRM activity feed: Sent / Replied / **Reply-rate** / Failed cards, plus Channel · Status · Niche filters. Each row has **Mark replied** (advances the lead `contacted → replied`).
- **Lead 360** (`/leads/[id]`) — shows an *Outreach sent* history for that lead and a **Message →** button that deep-links to `/compose?lead=<id>` with that lead pre-selected.

## 💬 Replies, booking & the feedback loop (L6 · L7 · L9)

The loop keeps going *after* the send:

- **Inbound reply webhooks** — point AiSensy and Resend at `/api/webhooks/aisensy` and `/api/webhooks/resend`. Incoming replies are logged (as `direction = 'in'`), flip the last send to **replied**, and advance the lead `contacted → replied`. Hard bounces / spam complaints / `STOP` auto-suppress (channel-specific, or identity-wide for opt-out). Optional `?secret=...` gate via `INBOUND_WEBHOOK_SECRET`.
- **Two-way thread** — the lead-360 page shows sent + received messages inline.
- **AI suggested reply** (L6) — a **Draft AI reply** button drafts a response with Claude Haiku, grounded in a curated Exly knowledge base (no invented pricing). Copy it or open it pre-filled in Compose.
- **Book a demo** (L7) — a form on the lead page records a `conversions` row, emits a `book` event, and moves the lead to `demo_booked`.
- **Insights** (L9) at `/insights` — reply-rate + conversion broken down by niche / channel / source, plus heuristic **suggested actions** (which niche to source more of, which channel out-replies, which ICP weight to bump).

## ✉️ Env vars for sending & replies (Vercel → Settings → Environment Variables)

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Haiku copy + suggested replies |
| `AISENSY_API_KEY` + `AISENSY_CAMPAIGN` | WhatsApp send (AiSensy campaign API) |
| `RESEND_API_KEY` + `EMAIL_FROM` | Transactional email send (Resend) |
| `INBOUND_WEBHOOK_SECRET` | _(optional)_ shared secret for the inbound webhook URLs |

Apply migrations [`0005_outreach.sql`](data/migrations/0005_outreach.sql) (send-log) and [`0006_inbound_booking.sql`](data/migrations/0006_inbound_booking.sql) (inbound direction + booking columns) on Supabase before using Compose / replies / booking.

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
- **v1 — "Send machine" (L0–L5):** scrape → score → personalize → send (email + WhatsApp) → follow up. ✅ built.
- **v2 — "Autonomous to demo" (L6–L8):** reply handling ✅, AI suggested reply ✅, demo booking ✅ — orchestration loop (L8) still to assemble.
- **v3 — "Self-improving" (L9–L10 + LinkedIn):** feedback insights ✅, analytics ✅, Instagram + LinkedIn sourcing ✅ — deeper RAG auto-answer remains.

**Suggested next build:** assemble the always-on **orchestration loop (L8)** — a scheduled `source → score → personalize → send → follow-up` cycle on the Railway worker — and register the inbound webhook URLs in AiSensy + Resend so replies flow in automatically.
