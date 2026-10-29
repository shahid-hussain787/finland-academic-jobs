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

from scrapers import (PARSERS, find_next_page, language_links,
                      increment_trailing_number)

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


# A single session for the whole run. Some recruitment systems hand out a
# session cookie and quietly ignore ?lang= and ?list= without it, which is
# why a plain request for a Finnish listing comes back in English.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url, extra_headers=None):
    """Fetch a URL. extra_headers covers sites whose endpoints expect the
    headers a browser sends when its own JavaScript makes the request —
    without them some Drupal AJAX routes answer 403."""
    resp = SESSION.get(url, timeout=TIMEOUT, headers=extra_headers or None)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def categorise(title, categories):
    for label, pattern in categories.items():
        if re.search(pattern, title):
            return label
    return "Other"


def scrape_url(url, uni, parser, parser_name, debug=False):
    """Fetch one URL and follow its pagination to the end.

    Three separate stops, because a runaway crawler on someone else's site
    is the one failure mode worth being paranoid about:
      - max_pages, a hard ceiling
      - no next-page link found
      - a page that adds no job we haven't already seen
    """
    headers = uni.get("headers")
    html = fetch(url, headers)
    jobs = parser(html, url, uni)
    seen_pages = {url}
    seen_jobs = {j["url"] for j in jobs}

    # Some sites print their other-language listings as links carrying a
    # session token. Following those is the only way to reach them.
    if uni.get("follow_languages"):
        for lang_url in language_links(html, url):
            if lang_url in seen_pages:
                continue
            seen_pages.add(lang_url)
            time.sleep(REQUEST_DELAY)
            try:
                lang_html = fetch(lang_url, headers)
                more = parser(lang_html, lang_url, uni)
            except Exception as exc:
                if debug:
                    print(f"      language page failed: {type(exc).__name__}: {exc}")
                continue
            fresh = [j for j in more if j["url"] not in seen_jobs]
            seen_jobs.update(j["url"] for j in fresh)
            jobs.extend(fresh)
            if debug and fresh:
                print(f"      +{len(fresh)} from another language listing")

    max_pages = uni.get("max_pages", 10)
    page_url = url

    for page in range(1, max_pages):
        # Endpoints that page by a number in the path have no "next" link
        # to find; the number itself is the pagination.
        if uni.get("page_by_number"):
            next_url = increment_trailing_number(page_url)
        else:
            next_url = find_next_page(html, page_url, page)
        if not next_url or next_url in seen_pages:
            break
        seen_pages.add(next_url)

        time.sleep(REQUEST_DELAY)
        try:
            html = fetch(next_url, headers)
            more = parser(html, next_url, uni)
        except Exception as exc:
            # A broken page 3 shouldn't throw away pages 1 and 2.
            if debug:
                print(f"      page {page + 1} failed: {type(exc).__name__}: {exc}")
            break

        fresh = [j for j in more if j["url"] not in seen_jobs]
        if not fresh:
            break
        seen_jobs.update(j["url"] for j in fresh)
        jobs.extend(fresh)
        page_url = next_url
        if debug:
            print(f"      page {page + 1}: +{len(fresh)}")

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
            found = scrape_url(url, uni, parser, parser_name, debug=debug)
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
        if i and not uni.get("link_only"):
            time.sleep(REQUEST_DELAY)
        if uni.get("link_only"):
            # Listed for browsing, never fetched. Used where a site forbids
            # automated access or hasn't been worked out yet; either way,
            # pretending we scraped it and found nothing would be a lie.
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                "url": uni.get("link") or (uni.get("urls") or [uni.get("url", "")])[0],
                "status": "link_only",
                "count": 0,
                "verified": True,
            })
            print(f"SKIP  {uni['name']}: listed for browsing only")
            continue

        if args.debug:
            print(f"\n--- {uni['name']} ({uni.get('parser', 'generic')})")
        try:
            jobs, errors, link = scrape_one(uni, categories, debug=args.debug)
            all_jobs.extend(jobs)
            sources.append({
                "name": uni["name"],
                "short": uni.get("short", uni["name"]),
                # What a visitor should open, which is not necessarily what
                # the scraper fetched: some sites are read through an API
                # endpoint that would be useless to a human.
                "url": uni.get("link") or link,
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
                "url": uni.get("link") or urls[0],
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
    scraped = [s for s in sources if s["status"] != "link_only"]
    if scraped and all(s["status"] == "error" for s in scraped):
        sys.exit("All sources failed")


if __name__ == "__main__":
    main()
