#!/usr/bin/env python3
"""Fetch open positions from Finnish university job pages into docs/jobs.json.

    python scrape.py                 # normal run, writes docs/jobs.json
    python scrape.py --debug         # print what each site returned
    python scrape.py --only oulu     # one site, for fixing a broken parser
    python scrape.py --dry-run       # don't write the file
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, date
from pathlib import Path

import requests
import yaml

from scrapers import PARSERS, aalto_next_page

ROOT = Path(__file__).parent
CONFIG = ROOT / "universities.yml"
OUTPUT = ROOT / "docs" / "jobs.json"

HEADERS = {
    "User-Agent": (
        "finland-academic-jobs/1.0 (personal job-tracking tool; "
        "contact via GitHub repository issues)"
    ),
    "Accept-Language": "en,fi;q=0.8",
}

REQUEST_DELAY = 2.0   # seconds between requests, to stay polite
TIMEOUT = 30


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def categorise(title, categories):
    for label, pattern in categories.items():
        if re.search(pattern, title):
            return label
    return "Other"


def scrape_one(uni, categories, debug=False):
    parser_name = uni.get("parser", "generic")
    parser = PARSERS.get(parser_name)
    if parser is None:
        raise ValueError(f"unknown parser '{parser_name}'")

    url = uni["url"]
    html = fetch(url)
    jobs = parser(html, url, uni)

    # Aalto is the only paginated source so far.
    if parser_name == "aalto":
        for page in range(1, uni.get("max_pages", 5)):
            next_url = aalto_next_page(html, url, page)
            if not next_url:
                break
            time.sleep(REQUEST_DELAY)
            html = fetch(next_url)
            more = parser(html, next_url, uni)
            if not more:
                break
            jobs.extend(more)

    today = date.today().isoformat()
    cleaned, seen = [], set()
    for job in jobs:
        if not job.get("title") or job["url"] in seen:
            continue
        seen.add(job["url"])
        # Drop anything already closed. Keep undated entries: some genuinely
        # have rolling deadlines, and dropping them would hide real positions.
        if job.get("deadline") and job["deadline"] < today:
            continue
        job["university"] = uni["name"]
        job["university_short"] = uni.get("short", uni["name"])
        job["category"] = categorise(job["title"], categories)
        cleaned.append(job)

    if debug:
        print(f"\n--- {uni['name']} ({parser_name}) -> {len(cleaned)} open")
        for job in cleaned[:8]:
            print(f"    {job.get('deadline') or '(no date)':12} {job['title'][:70]}")
        if len(cleaned) > 8:
            print(f"    ... and {len(cleaned) - 8} more")

    return cleaned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--only", help="substring of a university name")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    categories = config.get("categories", {})
    universities = config["universities"]

    if args.only:
        needle = args.only.lower()
        universities = [
            u for u in universities
            if needle in u["name"].lower() or needle in u.get("short", "").lower()
        ]
        if not universities:
            sys.exit(f"No university matching '{args.only}'")

    all_jobs, sources = [], []

    for i, uni in enumerate(universities):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            jobs = scrape_one(uni, categories, debug=args.debug)
            all_jobs.extend(jobs)
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                "url": uni["url"],
                "status": "ok",
                "count": len(jobs),
                "verified": uni.get("verified", False),
            })
            print(f"OK    {uni['name']}: {len(jobs)} open positions")
        except Exception as exc:
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                "url": uni["url"],
                "status": "error",
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "verified": uni.get("verified", False),
            })
            print(f"FAIL  {uni['name']}: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Undated positions sort last.
    all_jobs.sort(key=lambda j: (j.get("deadline") is None, j.get("deadline") or "", j["title"]))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_count": len(all_jobs),
        "sources": sources,
        "jobs": all_jobs,
    }

    if args.dry_run:
        print(f"\n[dry run] {len(all_jobs)} jobs, nothing written")
        return

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {OUTPUT} — {len(all_jobs)} positions from "
          f"{sum(1 for s in sources if s['status'] == 'ok')}/{len(sources)} sources")

    # Fail the CI run only if everything broke, so one bad site doesn't
    # block the other eight from updating.
    if sources and all(s["status"] == "error" for s in sources):
        sys.exit("All sources failed")


if __name__ == "__main__":
    main()
