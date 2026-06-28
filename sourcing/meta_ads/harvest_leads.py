#!/usr/bin/env python3
"""
Efficient lead harvester for Meta (Facebook) Ads Library.

Goal: collect AT LEAST `TARGET` unique leads that each have BOTH an email and a
phone number, with minimal wasted work.

Two phases:
  Phase A — Build a large pool of UNIQUE advertisers (listing only, no page
            visits). Seeds from any existing *_partial.json / *_full.json
            checkpoints, then expands with fast scrolls over every query.
  Phase B — Visit each unique advertiser's Facebook page exactly ONCE, using
            several concurrent workers. Keep leads that have both an email and a
            phone. Stop as soon as we reach TARGET, then write CSV + JSON.

Checkpoints are written continuously so nothing is lost if interrupted.
"""

import asyncio
import csv
import glob
import json
import os
import sys
from datetime import datetime

from playwright.async_api import async_playwright
from facebook_ads_scraper import (
    FacebookAdsLibraryScraper,
    get_concurrency,
    pick_proxy,
    pick_user_agent,
)

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
QUERIES = [
    "life coach India", "business coach India", "career coach India",
    "mentorship program India", "online mentor India",
    "online educator India", "online course India",
    "corporate trainer India", "fitness trainer India",
    "content creator course India", "digital creator India", "course creator India",
    "business consultant India", "online consultant India",
    "community building course India",
    "public speaking coach India", "keynote speaker India",
    "startup founder course India",
    "webinar host India",
    "online teacher India",
    "business strategist India", "personal branding coach India",
]

TARGET = 200            # leads with BOTH email and phone
# Concurrent advertiser-page visits — configurable via SCRAPER_CONCURRENCY
# (default 2, lowered from the old hardcoded 4 to reduce block rate).
CONCURRENCY = get_concurrency()
MAX_SCROLLS_A = 40      # scroll depth during pool building
OUT = "scraper_results"

os.makedirs(OUT, exist_ok=True)
RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
POOL_PATH = os.path.join(OUT, f"harvest_{RUN_TS}_pool.json")
PROGRESS_PATH = os.path.join(OUT, f"harvest_{RUN_TS}_progress.json")
LEADS_CSV = os.path.join(OUT, f"harvest_{RUN_TS}_leads_email_phone.csv")
ALL_CSV = os.path.join(OUT, f"harvest_{RUN_TS}_all_contacts.csv")
LEADS_JSON = os.path.join(OUT, f"harvest_{RUN_TS}_leads.json")


def norm(s):
    return (s or "").strip().lower()


# ----------------------------------------------------------------------------
# PHASE A — build unique advertiser pool
# ----------------------------------------------------------------------------
def seed_pool_from_checkpoints():
    pool = {}
    files = glob.glob(f"{OUT}/*_partial.json") + glob.glob(f"{OUT}/*_full.json")
    for f in files:
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for ad in data.get("ads", []):
            k = norm(ad.get("advertiser"))
            if not k:
                continue
            url = ad.get("advertiser_page_url", "") or ""
            if k not in pool:
                pool[k] = {"advertiser": ad.get("advertiser"), "url": url}
            elif not pool[k]["url"] and url:
                pool[k]["url"] = url
    print(f"🌱 Seeded pool from {len(files)} checkpoint file(s): "
          f"{len(pool)} unique advertisers", flush=True)
    return pool


async def build_pool(scraper):
    pool = seed_pool_from_checkpoints()
    for i, q in enumerate(QUERIES, 1):
        try:
            res = await scraper.scrape_ads(
                query=q, country="IN", active_status="active",
                ad_type="all", media_type="all",
                max_scrolls=MAX_SCROLLS_A,
                scrape_advertiser_details=False, max_ads_to_detail=0,
                filter_by_keywords=True, min_keyword_matches=1,
            )
        except Exception as e:
            print(f"[A {i}/{len(QUERIES)}] {q!r} ERROR: {e}", flush=True)
            continue

        added = 0
        for ad in res:
            if isinstance(ad, dict) and "error" in ad and "advertiser" not in ad:
                continue
            k = norm(ad.get("advertiser"))
            if not k:
                continue
            url = ad.get("advertiser_page_url", "") or ""
            if k not in pool:
                pool[k] = {"advertiser": ad.get("advertiser"), "url": url}
                added += 1
            elif not pool[k]["url"] and url:
                pool[k]["url"] = url
        print(f"[A {i}/{len(QUERIES)}] {q!r}: +{added} new (pool={len(pool)})",
              flush=True)
        # persist pool after every query
        with open(POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(list(pool.values()), f, ensure_ascii=False, indent=2)

    print(f"\n✅ Phase A done. Total unique advertisers in pool: {len(pool)}",
          flush=True)
    return list(pool.values())


# ----------------------------------------------------------------------------
# PHASE B — concurrent detailing until TARGET leads with email+phone
# ----------------------------------------------------------------------------
async def harvest(scraper, pool):
    leads = []          # advertisers with BOTH email and phone
    all_contacts = []   # advertisers with any email or phone
    visited = set()
    lock = asyncio.Lock()
    done = asyncio.Event()
    counters = {"visited": 0, "both": 0, "any": 0, "errors": 0}

    queue = asyncio.Queue()
    for entry in pool:
        queue.put_nowait(entry)

    def write_progress():
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "run_ts": RUN_TS,
                "target": TARGET,
                "counters": counters,
                "leads_email_phone": len(leads),
                "pool_size": len(pool),
            }, f, ensure_ascii=False, indent=2)

    def write_outputs():
        # leads with both email + phone
        with open(LEADS_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Advertiser", "Email", "Phone", "All_Emails", "All_Phones",
                        "WhatsApp", "Website", "Instagram", "Category", "Followers",
                        "City", "Facebook_Page"])
            for r in leads:
                d = r["details"]
                w.writerow([
                    r["advertiser"],
                    (d.get("emails") or [""])[0],
                    (d.get("phones") or [""])[0],
                    "; ".join(d.get("emails", [])),
                    "; ".join(d.get("phones", [])),
                    d.get("whatsapp", ""),
                    "; ".join(d.get("websites", [])),
                    d.get("instagram_username", ""),
                    d.get("category", ""),
                    d.get("followers", ""),
                    d.get("city", ""),
                    d.get("facebook_page", r.get("url", "")),
                ])
        # any contact
        with open(ALL_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Advertiser", "Emails", "Phones", "WhatsApp", "Websites",
                        "Instagram", "Category", "Followers", "City", "Facebook_Page"])
            for r in all_contacts:
                d = r["details"]
                w.writerow([
                    r["advertiser"],
                    "; ".join(d.get("emails", [])),
                    "; ".join(d.get("phones", [])),
                    d.get("whatsapp", ""),
                    "; ".join(d.get("websites", [])),
                    d.get("instagram_username", ""),
                    d.get("category", ""),
                    d.get("followers", ""),
                    d.get("city", ""),
                    d.get("facebook_page", r.get("url", "")),
                ])
        with open(LEADS_JSON, "w", encoding="utf-8") as f:
            json.dump({"run_ts": RUN_TS, "target": TARGET,
                       "counters": counters,
                       "leads_email_phone": leads,
                       "all_contacts": all_contacts}, f,
                      ensure_ascii=False, indent=2, default=str)

    async def worker(wid, context):
        page = await context.new_page()
        while not done.is_set():
            try:
                entry = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            name = entry["advertiser"]
            k = norm(name)
            async with lock:
                if k in visited:
                    queue.task_done()
                    continue
                visited.add(k)
            url = entry.get("url") or ""
            try:
                if not url:
                    url = await scraper.search_and_get_advertiser_page(page, name)
                details = None
                if url:
                    details = await scraper.scrape_advertiser_page(page, url, name)
            except Exception as e:
                async with lock:
                    counters["errors"] += 1
                queue.task_done()
                continue

            async with lock:
                counters["visited"] += 1
                rec = {"advertiser": name, "url": url, "details": details or {}}
                emails = (details or {}).get("emails") or []
                phones = (details or {}).get("phones") or []
                if emails or phones:
                    all_contacts.append(rec)
                    counters["any"] += 1
                if emails and phones:
                    leads.append(rec)
                    counters["both"] += 1
                    print(f"  ⭐ LEAD #{len(leads)}: {name} | {emails[0]} | {phones[0]}",
                          flush=True)
                    if len(leads) >= TARGET:
                        done.set()
                if counters["visited"] % 10 == 0:
                    write_progress()
                    write_outputs()
                    print(f"[B] visited={counters['visited']} "
                          f"email+phone={counters['both']} any={counters['any']} "
                          f"errors={counters['errors']}", flush=True)
            queue.task_done()
        await page.close()

    async with async_playwright() as pw:
        browser, _ = await scraper.init_browser(pw)
        # one context per worker for isolation
        contexts = []
        for _ in range(CONCURRENCY):
            ctx_kwargs = dict(
                user_agent=pick_user_agent(),  # rotate UA per context
                viewport={"width": 1920, "height": 1080},
                locale="en-US", timezone_id="Asia/Kolkata",
            )
            proxy = pick_proxy()  # rotating proxy if SCRAPER_PROXIES is set, else None
            if proxy:
                ctx_kwargs["proxy"] = proxy
            ctx = await browser.new_context(**ctx_kwargs)
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            contexts.append(ctx)
        try:
            await asyncio.gather(*[worker(i, contexts[i]) for i in range(CONCURRENCY)])
        finally:
            write_progress()
            write_outputs()
            for ctx in contexts:
                try:
                    await ctx.close()
                except Exception:
                    pass
            await browser.close()

    return leads, all_contacts, counters


async def main():
    print("=" * 80, flush=True)
    print("🎯 META ADS LIBRARY — LEAD HARVESTER (email + phone)", flush=True)
    print(f"   Target: {TARGET} leads with BOTH email and phone", flush=True)
    print(f"   Concurrency: {CONCURRENCY}", flush=True)
    print(f"   Queries: {len(QUERIES)}", flush=True)
    print("=" * 80, flush=True)

    scraper = FacebookAdsLibraryScraper()

    print("\n📥 PHASE A: building unique advertiser pool...\n", flush=True)
    pool = await build_pool(scraper)

    print(f"\n📞 PHASE B: detailing {len(pool)} advertisers "
          f"(stop at {TARGET} email+phone leads)...\n", flush=True)
    leads, all_contacts, counters = await harvest(scraper, pool)

    print("\n" + "=" * 80, flush=True)
    print("📊 FINAL", flush=True)
    print(f"   Advertisers visited: {counters['visited']}", flush=True)
    print(f"   Leads with email+phone: {len(leads)}", flush=True)
    print(f"   Advertisers with any contact: {counters['any']}", flush=True)
    print(f"   Errors: {counters['errors']}", flush=True)
    print(f"\n💾 Files:", flush=True)
    print(f"   • {LEADS_CSV}", flush=True)
    print(f"   • {ALL_CSV}", flush=True)
    print(f"   • {LEADS_JSON}", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
