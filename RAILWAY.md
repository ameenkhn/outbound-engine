# Deploy the worker to Railway (make the website self-sufficient)

Your Vercel site can't run the scrapers itself — it just writes "jobs" to the
database. This deploys the **Python worker** that drains those jobs, runs the
scrapers (headless Chromium + the API/web sources), de-dupes, and writes leads
back to Supabase. Once it's running 24/7 on Railway, your site becomes a
standalone lead machine: click **Quick Harvest** on the site → worker scrapes →
leads appear in **/leads**. No laptop required.

```
Website (Vercel) ──writes app_jobs──▶ Supabase (Mumbai) ◀──drains & writes leads── Worker (Railway)
```

The repo already contains everything Railway needs: a `Dockerfile` (based on the
official Playwright image, so Chromium "just works") and a `.dockerignore`.

---

## 1. Push the code (if you haven't)

Commit + push in GitHub Desktop. The new files are `Dockerfile`, `.dockerignore`,
the inline-keyword `source_run`, and the website's **Quick Harvest** card. Vercel
redeploys the site; Railway will build from the same repo.

---

## 2. Get your Mumbai database connection string

Railway containers use IPv4, and Supabase's *direct* connection is IPv6-only — so
use the **pooler** string:

1. Open the **`outbound-mumbai`** project in Supabase → **Connect** (top bar).
2. Choose **Session pooler** (or the "ORM/URI" that contains `pooler.supabase.com`).
3. Copy the URI. It looks like:
   ```
   postgresql://postgres.kcfcibmbpnofpxsysagn:[YOUR-DB-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```
4. Replace `[YOUR-DB-PASSWORD]` with your DB password (Settings → Database →
   reset it if you don't know it).

---

## 3. Create the Railway service

1. Go to [railway.app](https://railway.app) → sign in with GitHub → **New Project**.
2. **Deploy from GitHub repo** → pick **`outbound-engine`** → authorize if asked.
3. Railway detects the **Dockerfile** and starts building the worker image
   (Playwright + Chromium — first build takes a few minutes).
4. (Optional) In Settings → pick a region near Mumbai/Singapore for lower DB latency.

---

## 4. Set the worker's environment variables

In the Railway service → **Variables** → add:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the Mumbai **pooler** URI from step 2 (required) |
| `YOUTUBE_API_KEY` | optional — enables the YouTube source |
| `WEBSEARCH_API_BASE` / `WEBSEARCH_API_KEY` | optional — paid search; otherwise free DuckDuckGo is used |
| `INSTAGRAM_API_BASE` / `INSTAGRAM_API_KEY` | optional — Instagram provider |
| `LINKEDIN_API_BASE` / `LINKEDIN_API_KEY` | optional — LinkedIn provider |
| `SCRAPER_PROXIES` | optional — rotating proxies for Meta/IG/LinkedIn |

Only `DATABASE_URL` is required. **Meta Ad Library + free Web Search work with no
keys at all.** Railway redeploys automatically when you add variables.

---

## 5. Confirm it's running

- Railway → your service → **Deployments** → **Logs**. You should see it boot and
  poll quietly (it logs when it processes a job). No errors = it's draining the queue.
- It runs the loop `python -m orchestration.app_jobs` forever.

---

## 6. Scrape from the website 🎉

1. Open your site → **L1 · Sourcing**.
2. In **⚡ Quick Harvest**, type a niche (e.g. `fitness coach`), pick **All sources**
   (or just Meta), click **Harvest now**.
3. That writes a job. The Railway worker picks it up within seconds, scrapes,
   de-dupes, and writes leads to Supabase.
4. Open **/leads** — your leads appear, scored and filterable. Re-runs never
   duplicate (the two-layer dedup handles it).

That's the full cloud loop: **site → queue → Railway worker → database → site**,
with nothing running on your laptop.

---

### Notes
- **Cost:** Railway's free trial covers light use; a small always-on worker is a
  few dollars/month after. The scrape itself is bursty (idle most of the time).
- **Meta scraping is heavy** (headless browser). Keep the worker's memory ≥ 1 GB.
- **Logs/feed:** the website shows job status under "Recent runs" on the Sourcing
  page; deep per-advertiser progress lives in the Railway logs (and the local
  control panel's live feed).
