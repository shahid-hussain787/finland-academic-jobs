"""One parser per site layout. Each returns a list of dicts:

    {"title": str, "url": str, "deadline": "YYYY-MM-DD" or None, "unit": str or None}

Parsers never raise for missing optional fields; a job with no deadline is
still a job. They raise only when the page shape is clearly wrong, so the
run log tells you a site changed instead of silently reporting zero jobs.
"""

import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
FI_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def _parse_date(text):
    """Accept 2026-11-26 or 26.11.2026 anywhere in the text."""
    if not text:
        return None
    m = ISO_DATE.search(text)
    if m:
        y, mo, d = m.groups()
    else:
        m = FI_DATE.search(text)
        if not m:
            return None
        d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


# --------------------------------------------------------------------------
# Varbi  (University of Oulu, and most Nordic universities on Varbi)
# Server-rendered table: Job opening | City | Department | Application deadline
# --------------------------------------------------------------------------
def varbi(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("no <table> found — Varbi layout may have changed")

    jobs = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        link = cells[0].find("a")
        if not link:
            continue
        jobs.append({
            "title": _clean(link.get_text()),
            "url": urljoin(base_url, link.get("href", "")),
            "unit": _clean(cells[2].get_text()) if len(cells) > 2 else None,
            "deadline": _parse_date(cells[-1].get_text()),
        })
    return jobs


# --------------------------------------------------------------------------
# rekrytointi.com  (Vaasa, Åbo Akademi, and several others)
# Table: Position | Application period starts | Application period ends
# --------------------------------------------------------------------------
def rekrytointi(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = cells[0].find("a")
            title = _clean((link or cells[0]).get_text())
            if not title:
                continue
            deadline = _parse_date(cells[-1].get_text())
            if deadline is None and len(cells) >= 3:
                deadline = _parse_date(cells[2].get_text())
            jobs.append({
                "title": title,
                "url": urljoin(base_url, link.get("href", "")) if link else base_url,
                "unit": None,
                "deadline": deadline,
            })
    if not jobs:
        # These sites say so in words when nothing is open. That's a real
        # answer, not a failure — don't cry wolf about a working site.
        if re.search(r"no job postings|ei avoimia|inga lediga", html, re.I):
            return []
        raise ValueError("no table rows parsed — rekrytointi layout may have changed")
    return jobs


# --------------------------------------------------------------------------
# Aalto  (Drupal listing, paginated via ?page=N, "Closes on: 28.10.2026")
# --------------------------------------------------------------------------
def aalto(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    jobs = []

    for heading in main.find_all(["h2", "h3"]):
        link = heading.find("a")
        if not link or "/open-positions/" not in link.get("href", ""):
            continue
        title = _clean(link.get_text())
        if not title:
            continue

        # The "Closes on:" line sits in a sibling after the heading.
        deadline = None
        unit = None
        for sib in list(heading.next_siblings)[:6]:
            text = _clean(getattr(sib, "get_text", lambda: str(sib))())
            if "Closes on" in text:
                deadline = _parse_date(text)
                break
        for sib in list(heading.previous_siblings)[:6]:
            text = _clean(getattr(sib, "get_text", lambda: str(sib))())
            if "School of" in text or "University Units" in text:
                unit = re.sub(r"\s*\d{1,2}\.\d{1,2}\.\d{4}\s*$", "", text)
                break

        jobs.append({
            "title": title,
            "url": urljoin(base_url, link["href"]),
            "unit": unit,
            "deadline": deadline,
        })

    if not jobs:
        raise ValueError("no position headings found — Aalto layout may have changed")
    return jobs


def aalto_next_page(html, base_url, page_index):
    """Aalto paginates with ?page=1, ?page=2 ... Return the next URL or None."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if f"page={page_index}" in a["href"]:
            return urljoin(base_url, a["href"])
    return None


# --------------------------------------------------------------------------
# Tampere  ("Application period ends: 2026-03-01 23:59")
# --------------------------------------------------------------------------
def tampere(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    jobs = []

    for node in main.find_all(string=re.compile(r"Application period ends")):
        deadline = _parse_date(str(node))
        # Walk backwards to the nearest preceding link, which is the title.
        holder = node.parent
        link = None
        for _ in range(6):
            if holder is None:
                break
            prev = holder.find_previous("a", href=True)
            if prev and _clean(prev.get_text()):
                link = prev
                break
            holder = holder.parent
        if link is None:
            continue
        jobs.append({
            "title": _clean(link.get_text()),
            "url": urljoin(base_url, link["href"]),
            "unit": None,
            "deadline": deadline,
        })

    if not jobs:
        raise ValueError("no 'Application period ends' text found — Tampere layout may have changed")
    return jobs


# --------------------------------------------------------------------------
# UEF  ("Apply by: 15.11.2026 - Place of employment: ...")
# --------------------------------------------------------------------------
def uef(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    jobs = []

    for node in main.find_all(string=re.compile(r"Apply by")):
        text = _clean(str(node))
        deadline = _parse_date(text)
        unit = None
        if "Place of employment:" in text:
            unit = _clean(text.split("Place of employment:", 1)[1])

        holder = node.parent
        link = None
        for _ in range(6):
            if holder is None:
                break
            prev = holder.find_previous("a", href=True)
            if prev and _clean(prev.get_text()):
                link = prev
                break
            holder = holder.parent
        if link is None:
            continue
        jobs.append({
            "title": _clean(link.get_text()),
            "url": urljoin(base_url, link["href"]),
            "unit": unit,
            "deadline": deadline,
        })

    if not jobs:
        raise ValueError("no 'Apply by' text found — UEF layout may have changed")
    return jobs


# --------------------------------------------------------------------------
# Generic fallback, driven by the `generic:` block in universities.yml.
# Collects links whose text looks like a job title, then hunts nearby text
# for a date. Imprecise by design — use it to get a site working, then
# write a proper parser once you can see what the page actually contains.
# --------------------------------------------------------------------------
def generic(html, base_url, cfg):
    g = (cfg or {}).get("generic") or {}
    soup = BeautifulSoup(html, "html.parser")
    scope = soup.select(g.get("item_selector", "main a")) or []
    must_match = g.get("must_match")
    pattern = re.compile(must_match) if must_match else None

    jobs, seen = [], set()
    for node in scope:
        link = node if node.name == "a" else node.find("a", href=True)
        if not link or not link.get("href"):
            continue
        title = _clean(link.get_text())
        if not title or len(title) < 8:
            continue
        if pattern and not pattern.search(title):
            continue
        url = urljoin(base_url, link["href"])
        if url in seen:
            continue
        seen.add(url)

        # Look for a date in the link's container, then its parent.
        deadline = None
        holder = link.parent
        for _ in range(3):
            if holder is None:
                break
            deadline = _parse_date(holder.get_text())
            if deadline:
                break
            holder = holder.parent

        jobs.append({"title": title, "url": url, "unit": None, "deadline": deadline})

    return jobs


PARSERS = {
    "varbi": varbi,
    "rekrytointi": rekrytointi,
    "aalto": aalto,
    "tampere": tampere,
    "uef": uef,
    "generic": generic,
}
