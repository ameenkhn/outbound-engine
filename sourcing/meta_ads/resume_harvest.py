#!/usr/bin/env python3
"""
Resume Phase B: continue visiting unvisited advertisers from the existing pool
until we have >= TARGET_VALID leads that each have a VALID email and a VALID
phone (Indian mobile 6-9 x10, or landline with leading 0 + STD). Junk numbers
(parsed page IDs starting 1-5) are rejected so they don't count toward target.

Combines newly found leads with the prior run's results and writes a single
FINAL csv/json.
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

TARGET_VALID = 205          # valid email+phone leads (buffer over 200)
CONCURRENCY = get_concurrency()  # default 2, override via SCRAPER_CONCURRENCY
OUT = "scraper_results"
RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
FINAL_CSV = os.path.join(OUT, f"harvest_{RUN_TS}_leads_FINAL.csv")
FINAL_JSON = os.path.join(OUT, f"harvest_{RUN_TS}_leads_FINAL.json")
PROGRESS = os.path.join(OUT, f"harvest_{RUN_TS}_resume_progress.json")


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


def load_pool():
    f = sorted(glob.glob(f"{OUT}/harvest_*_pool.json"))[-1]
    return json.load(open(f, encoding="utf-8"))


def load_prior():
    """Return (valid_leads[list of recs], visited_names[set])."""
    f = sorted(glob.glob(f"{OUT}/harvest_*_leads.json"))[-1]
    data = json.load(open(f, encoding="utf-8"))
    visited = set()
    valid = []
    seen = set()
    for r in data.get("all_contacts", []):
        visited.add(norm(r.get("advertiser")))
    for r in data.get("leads_email_phone", []):
        visited.add(norm(r.get("advertiser")))
        k = norm(r.get("advertiser"))
        email, ph = valid_lead(r.get("details", {}))
        if email and ph and k not in seen:
            seen.add(k)
            valid.append(r)
    return valid, visited, seen


async def main():
    scraper = FacebookAdsLibraryScraper()
    pool = load_pool()
    valid_leads, visited, seen = load_prior()
    print(f"Pool: {len(pool)} | already visited: {len(visited)} | "
          f"valid leads carried over: {len(valid_leads)}", flush=True)

    todo = [e for e in pool if norm(e["advertiser"]) not in visited]
    print(f"Remaining to visit: {len(todo)} | target valid: {TARGET_VALID}", flush=True)

    queue = asyncio.Queue()
    for e in todo:
        queue.put_nowait(e)

    lock = asyncio.Lock()
    done = asyncio.Event()
    counters = {"visited": 0, "new_valid": 0, "errors": 0}

    def write_outputs():
        with open(FINAL_CSV, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Advertiser", "Email", "Phone", "All_Emails", "All_Phones",
                        "Website", "Category", "Followers", "City", "Facebook_Page"])
            for r in valid_leads:
                d = r.get("details", {})
                email, ph = valid_lead(d)
                w.writerow([r["advertiser"], email, ph,
                            "; ".join(d.get("emails", []) or []),
                            "; ".join(d.get("phones", []) or []),
                            "; ".join(d.get("websites", []) or []),
                            d.get("category", ""), d.get("followers", ""),
                            d.get("city", ""), d.get("facebook_page", r.get("url", ""))])
        with open(FINAL_JSON, "w", encoding="utf-8") as f:
            json.dump({"run_ts": RUN_TS, "valid_leads": valid_leads,
                       "counters": counters}, f, ensure_ascii=False,
                      indent=2, default=str)
        with open(PROGRESS, "w", encoding="utf-8") as f:
            json.dump({"valid_total": len(valid_leads), "counters": counters,
                       "target": TARGET_VALID}, f, indent=2)

    async def worker(ctx):
        page = await ctx.new_page()
        while not done.is_set():
            try:
                entry = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            name = entry["advertiser"]
            k = norm(name)
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
                    valid_leads.append({"advertiser": name, "url": url, "details": details})
                    counters["new_valid"] += 1
                    print(f"  ⭐ VALID #{len(valid_leads)}: {name} | {email} | {ph}", flush=True)
                    if len(valid_leads) >= TARGET_VALID:
                        done.set()
                if counters["visited"] % 10 == 0:
                    write_outputs()
                    print(f"[R] visited+={counters['visited']} new_valid={counters['new_valid']} "
                          f"total_valid={len(valid_leads)} errors={counters['errors']}", flush=True)
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

    print("\n" + "=" * 70, flush=True)
    print(f"DONE. Total valid email+phone leads: {len(valid_leads)}", flush=True)
    print(f"  newly added this run: {counters['new_valid']} "
          f"(visited {counters['visited']}, errors {counters['errors']})", flush=True)
    print(f"  FINAL CSV : {FINAL_CSV}", flush=True)
    print(f"  FINAL JSON: {FINAL_JSON}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
