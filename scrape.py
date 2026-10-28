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
FAILURES = ROOT / "failures.txt"   # read by the workflow to raise an alert

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


def scrape_url(url, uni, parser, parser_name):
    """Fetch one URL (following Aalto's pagination) and parse it."""
    html = fetch(url)
    jobs = parser(html, url, uni)

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

    return jobs


def scrape_one(uni, categories, debug=False):
    """Try every URL for this university and merge what comes back.

    A university counts as failed only when every one of its URLs fails.
    That way a site that has moved keeps working from its new address while
    the old one sits harmlessly in the list.
    """
    parser_name = uni.get("parser", "generic")
    parser = PARSERS.get(parser_name)
    if parser is None:
        raise ValueError(f"unknown parser '{parser_name}'")

    urls = uni.get("urls") or ([uni["url"]] if uni.get("url") else [])
    if not urls:
        raise ValueError("no urls configured")

    raw, errors, used = [], [], []
    for i, url in enumerate(urls):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            found = scrape_url(url, uni, parser, parser_name)
            raw.extend(found)
            used.append(url)
            if debug:
                print(f"    {len(found):3} from {url}")
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")
            if debug:
                print(f"    FAILED {url}: {type(exc).__name__}: {exc}")

    if errors and not used:
        raise RuntimeError(" | ".join(errors))

    today = date.today().isoformat()
    cleaned, seen = [], set()
    for job in raw:
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
        for job in cleaned[:8]:
            print(f"      {job.get('deadline') or '(no date)':12} {job['title'][:66]}")
        if len(cleaned) > 8:
            print(f"      ... and {len(cleaned) - 8} more")

    return cleaned, errors, used[0] if used else urls[0]


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

    all_jobs, sources, alerts = [], [], []

    for i, uni in enumerate(universities):
        if i:
            time.sleep(REQUEST_DELAY)
        if args.debug:
            print(f"\n--- {uni['name']} ({uni.get('parser', 'generic')})")
        try:
            jobs, errors, link = scrape_one(uni, categories, debug=args.debug)
            all_jobs.extend(jobs)
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                "url": link,
                "status": "ok",
                "count": len(jobs),
                "verified": uni.get("verified", False),
            })
            print(f"OK    {uni['name']}: {len(jobs)} open positions")
            # One URL of several failing is worth knowing about even though
            # the source as a whole still worked.
            for err in errors:
                alerts.append(f"{uni['name']} (partial): {err}")
                print(f"WARN  {uni['name']}: {err}", file=sys.stderr)
        except Exception as exc:
            urls = uni.get("urls") or [uni.get("url", "")]
            message = f"{type(exc).__name__}: {exc}"
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                "url": urls[0],
                "status": "error",
                "count": 0,
                "error": message[:300],
                "verified": uni.get("verified", False),
            })
            alerts.append(f"{uni['name']}: {message}")
            print(f"FAIL  {uni['name']}: {message}", file=sys.stderr)

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

    # The workflow reads this file and opens an issue if it has content,
    # so a broken site reaches your inbox instead of waiting to be noticed.
    if alerts and not args.only:
        FAILURES.write_text("\n".join(alerts), encoding="utf-8")
        print(f"{len(alerts)} problem(s) recorded for alerting")
    elif FAILURES.exists():
        FAILURES.unlink()

    # Fail the CI run only if everything broke, so one bad site doesn't
    # block the other eight from updating.
    if sources and all(s["status"] == "error" for s in sources):
        sys.exit("All sources failed")


if __name__ == "__main__":
    main()
