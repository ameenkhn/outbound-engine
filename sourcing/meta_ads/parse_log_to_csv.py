#!/usr/bin/env python3
"""
Parse the scraper log file into a CSV of advertiser leads.

The original run died before writing JSON/CSV. This script salvages what was
printed to stdout: advertiser names, Facebook page URLs, follower counts, and
counts (not values) of emails / phones / websites discovered per advertiser.
"""

import csv
import os
import re
from collections import OrderedDict

LOG = "scraper_results/run_20260507_120045.log"
OUT = "scraper_results/multi_keyword_RECOVERED_from_log.csv"

QUERY_RE = re.compile(r"^\s*Query:\s*(.+?)\s*$")
NAME_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.+?)\.\.\.\s*$")
VISITING_RE = re.compile(r"^\s*→ Visiting:\s*(\S+?)(?:\.\.\.)?\s*$")
FOUND_RE = re.compile(
    r"^\s*✓ Found:"
    r"(?:\s*(\d+)\s+emails?,?)?"
    r"(?:\s*(\d+)\s+phones?,?)?"
    r"(?:\s*(\d+)\s+websites?,?)?"
    r"(?:\s*([\d,]+)\s+followers?)?"
)
NO_CONTACT_RE = re.compile(r"^\s*✗ No contact info found")


def normalize_url(url: str) -> str:
    """Strip trailing /about and similar sub-paths so duplicates collapse."""
    if not url:
        return ""
    url = url.rstrip("/")
    for tail in ("/about", "/about_details", "/about_profile_transparency"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    return url.rstrip("/")


def parse():
    rows = []
    current_query = None
    pending = None

    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")

            m = QUERY_RE.match(line)
            if m:
                current_query = m.group(1)
                pending = None
                continue

            m = NAME_RE.match(line)
            if m and current_query:
                if pending:
                    rows.append(pending)
                pending = {
                    "query": current_query,
                    "advertiser": m.group(3).strip(),
                    "facebook_page": "",
                    "emails": 0,
                    "phones": 0,
                    "websites": 0,
                    "followers": "",
                    "followers_n": 0,
                    "found": False,
                }
                continue

            m = VISITING_RE.match(line)
            if m and pending:
                pending["facebook_page"] = normalize_url(m.group(1))
                continue

            m = FOUND_RE.match(line)
            if m and pending:
                e, p, w, fol = m.groups()
                pending["emails"] = int(e) if e else 0
                pending["phones"] = int(p) if p else 0
                pending["websites"] = int(w) if w else 0
                if fol:
                    pending["followers"] = fol
                    pending["followers_n"] = int(fol.replace(",", ""))
                pending["found"] = True
                rows.append(pending)
                pending = None
                continue

            if NO_CONTACT_RE.match(line) and pending:
                pending["found"] = False
                rows.append(pending)
                pending = None
                continue

    if pending:
        rows.append(pending)

    return rows


def dedupe(rows):
    """Collapse exact duplicates (same query + advertiser + page) and
    cross-query duplicates (same page URL — keep first query that found it)."""
    by_pair = OrderedDict()
    for r in rows:
        key = (r["query"], r["advertiser"].lower(), r["facebook_page"].lower())
        if key not in by_pair:
            by_pair[key] = dict(r)
            by_pair[key]["matched_queries"] = [r["query"]]
        else:
            existing = by_pair[key]
            if r["followers_n"] > existing["followers_n"]:
                existing["followers"] = r["followers"]
                existing["followers_n"] = r["followers_n"]
            for fld in ("emails", "phones", "websites"):
                existing[fld] = max(existing[fld], r[fld])

    by_page = OrderedDict()
    for r in by_pair.values():
        page_key = r["facebook_page"].lower() or f"NAME::{r['advertiser'].lower()}"
        if page_key in by_page:
            existing = by_page[page_key]
            if r["query"] not in existing["matched_queries"]:
                existing["matched_queries"].append(r["query"])
            for fld in ("emails", "phones", "websites"):
                existing[fld] = max(existing[fld], r[fld])
            if r["followers_n"] > existing["followers_n"]:
                existing["followers"] = r["followers"]
                existing["followers_n"] = r["followers_n"]
        else:
            by_page[page_key] = r

    return list(by_page.values())


def main():
    rows = parse()
    unique = dedupe(rows)
    unique.sort(key=lambda r: (-r["followers_n"], r["advertiser"].lower()))

    os.makedirs("scraper_results", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "Advertiser",
            "Facebook_Page",
            "Followers",
            "Followers_Count",
            "Emails_Found",
            "Phones_Found",
            "Websites_Found",
            "Matched_Queries",
            "First_Query",
        ])
        for r in unique:
            w.writerow([
                r["advertiser"],
                r["facebook_page"],
                r["followers"],
                r["followers_n"] or "",
                r["emails"],
                r["phones"],
                r["websites"],
                "; ".join(r["matched_queries"]),
                r["matched_queries"][0],
            ])

    visited_total = len(rows)
    found_total = sum(1 for r in rows if r["found"])
    print(f"Parsed {visited_total} advertiser visits from log")
    print(f"  with contact info: {found_total}")
    print(f"  without contact info: {visited_total - found_total}")
    print(f"Unique advertisers (deduped by FB page URL): {len(unique)}")
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
