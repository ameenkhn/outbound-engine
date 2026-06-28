#!/usr/bin/env python3
"""
Niche lead harvester (business / money / trauma-healing / ho'oponopono /
inner-child-healing coaches). Builds a fresh unique-advertiser pool from these
queries only (no seeding from prior runs), then visits each page once with
concurrent workers, keeping only leads with a VALID email + VALID phone.
Stops at TARGET_VALID.
"""
import asyncio, csv, glob, json, os, re
from datetime import datetime
from playwright.async_api import async_playwright
from facebook_ads_scraper import (
    FacebookAdsLibraryScraper,
    get_concurrency,
    pick_proxy,
    pick_user_agent,
)

QUERIES = [
    # business coach
    "business coach India", "business coaching India", "business mentor India",
    # money coach
    "money coach India", "money mindset coach India", "financial coach India",
    "wealth coach India",
    # trauma healing coach
    "trauma healing coach India", "trauma healing India", "trauma recovery coach India",
    # ho'oponopono coach
    "hoponopono India", "hooponopono coach India", "ho'oponopono healing India",
    # inner child healing coach
    "inner child healing coach India", "inner child healing India",
    "inner child therapy India",
    # adjacent/broadening to ensure volume for sparse niches
    "healing coach India", "subconscious mind coach India",
]

TARGET_VALID = 110          # valid email+phone leads (buffer over 100)
CONCURRENCY = get_concurrency()  # default 2, override via SCRAPER_CONCURRENCY
MAX_SCROLLS_A = 40
OUT = "scraper_results"
RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
TAG = "niche"
POOL_PATH = os.path.join(OUT, f"{TAG}_{RUN_TS}_pool.json")
PROGRESS = os.path.join(OUT, f"{TAG}_{RUN_TS}_progress.json")
FINAL_CSV = os.path.join(OUT, f"{TAG}_{RUN_TS}_leads_FINAL.csv")
FINAL_JSON = os.path.join(OUT, f"{TAG}_{RUN_TS}_leads_FINAL.json")


def norm(s): return (s or "").strip().lower()


def clean_phone(p):
    raw = (p or "").strip()
    digits = re.sub(r"\D", "", raw)
    m = re.search(r"(?:\+?91|0)?([6-9]\d{9})", digits)
    if m:
        return "+91 " + m.group(1)
    if raw.startswith("0") and 10 <= len(digits) <= 11 and digits[1] in "1234589":
        return raw
    return None


def pick_phone(phones):
    for p in (phones or []):
        c = clean_phone(p)
        if c:
            return c
    return None


def valid_lead(details):
    emails = (details or {}).get("emails") or []
    ph = pick_phone((details or {}).get("phones"))
    return (emails[0] if emails else None), ph


async def build_pool(scraper):
    pool = {}
    for i, q in enumerate(QUERIES, 1):
        try:
            res = await scraper.scrape_ads(
                query=q, country="IN", active_status="active",
                ad_type="all", media_type="all", max_scrolls=MAX_SCROLLS_A,
                scrape_advertiser_details=False, max_ads_to_detail=0,
                filter_by_keywords=True, min_keyword_matches=1)
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
        print(f"[A {i}/{len(QUERIES)}] {q!r}: +{added} (pool={len(pool)})", flush=True)
        with open(POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(list(pool.values()), f, ensure_ascii=False, indent=2)
    print(f"\n✅ Phase A done. Pool: {len(pool)} unique advertisers", flush=True)
    return list(pool.values())


async def harvest(scraper, pool):
    leads = []
    visited = set()
    seen = set()
    lock = asyncio.Lock()
    done = asyncio.Event()
    counters = {"visited": 0, "valid": 0, "errors": 0}
    queue = asyncio.Queue()
    for e in pool:
        queue.put_nowait(e)

    def write_outputs():
        with open(FINAL_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Advertiser", "Email", "Phone", "All_Emails", "All_Phones",
                        "Website", "Category", "Followers", "City", "Facebook_Page"])
            for r in leads:
                d = r.get("details", {})
                email, ph = valid_lead(d)
                w.writerow([r["advertiser"], email, ph,
                            "; ".join(d.get("emails", []) or []),
                            "; ".join(d.get("phones", []) or []),
                            "; ".join(d.get("websites", []) or []),
                            d.get("category", ""), d.get("followers", ""),
                            d.get("city", ""), d.get("facebook_page", r.get("url", ""))])
        with open(FINAL_JSON, "w", encoding="utf-8") as f:
            json.dump({"run_ts": RUN_TS, "counters": counters, "leads": leads},
                      f, ensure_ascii=False, indent=2, default=str)
        with open(PROGRESS, "w", encoding="utf-8") as f:
            json.dump({"valid_total": len(leads), "counters": counters,
                       "target": TARGET_VALID, "pool": len(pool)}, f, indent=2)

    async def worker(ctx):
        page = await ctx.new_page()
        while not done.is_set():
            try:
                entry = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            name = entry["advertiser"]; k = norm(name)
            async with lock:
                if k in visited:
                    queue.task_done(); continue
                visited.add(k)
            url = entry.get("url") or ""
            try:
                if not url:
                    url = await scraper.search_and_get_advertiser_page(page, name)
                details = await scraper.scrape_advertiser_page(page, url, name) if url else None
            except Exception:
                async with lock:
                    counters["errors"] += 1
                queue.task_done(); continue
            async with lock:
                counters["visited"] += 1
                email, ph = valid_lead(details)
                if email and ph and k not in seen:
                    seen.add(k)
                    leads.append({"advertiser": name, "url": url, "details": details})
                    counters["valid"] += 1
                    print(f"  ⭐ LEAD #{len(leads)}: {name} | {email} | {ph}", flush=True)
                    if len(leads) >= TARGET_VALID:
                        done.set()
                if counters["visited"] % 10 == 0:
                    write_outputs()
                    print(f"[B] visited={counters['visited']} valid={counters['valid']} "
                          f"errors={counters['errors']}", flush=True)
            queue.task_done()
        await page.close()

    async with async_playwright() as pw:
        browser, _ = await scraper.init_browser(pw)
        ctxs = []
        for _ in range(CONCURRENCY):
            ctx_kwargs = dict(
                user_agent=pick_user_agent(),  # rotate UA per context
                viewport={"width": 1920, "height": 1080},
                locale="en-US", timezone_id="Asia/Kolkata")
            proxy = pick_proxy()  # rotating proxy if SCRAPER_PROXIES set, else None
            if proxy:
                ctx_kwargs["proxy"] = proxy
            c = await browser.new_context(**ctx_kwargs)
            await c.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            ctxs.append(c)
        try:
            await asyncio.gather(*[worker(c) for c in ctxs])
        finally:
            write_outputs()
            for c in ctxs:
                try: await c.close()
                except Exception: pass
            await browser.close()
    return leads, counters


async def main():
    print("=" * 70, flush=True)
    print(f"🎯 NICHE HARVEST — target {TARGET_VALID} valid email+phone leads", flush=True)
    print(f"   queries: {len(QUERIES)} | concurrency: {CONCURRENCY}", flush=True)
    print("=" * 70, flush=True)
    scraper = FacebookAdsLibraryScraper()
    print("\n📥 PHASE A: building pool...\n", flush=True)
    pool = await build_pool(scraper)
    print(f"\n📞 PHASE B: detailing (stop at {TARGET_VALID})...\n", flush=True)
    leads, counters = await harvest(scraper, pool)
    print("\n" + "=" * 70, flush=True)
    print(f"DONE. Valid email+phone leads: {len(leads)}", flush=True)
    print(f"  visited={counters['visited']} errors={counters['errors']}", flush=True)
    print(f"  CSV : {FINAL_CSV}", flush=True)
    print(f"  JSON: {FINAL_JSON}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
