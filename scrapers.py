"""One parser per site layout. Each returns a list of dicts:

    {"title": str, "url": str, "deadline": "YYYY-MM-DD" or None, "unit": str or None}

Parsers never raise for missing optional fields; a job with no deadline is
still a job. They raise only when the page shape is clearly wrong, so the
run log tells you a site changed instead of silently reporting zero jobs.
"""

import json
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


NEXT_TEXT = re.compile(
    r"^(next|next page|seuraava|seuraava sivu|n\u00e4sta|n\u00e4sta sidan|\u00bb|\u203a|>|>>)$",
    re.I,
)


def find_next_page(html, base_url, page_index):
    """Find the link to the next page of listings, or None if this is the last.

    Tried in order of reliability:
      1. rel="next", which is unambiguous when a site bothers to set it
      2. a link whose text or aria-label means "next"
      3. a ?page=N link where N is exactly the page we want

    The third is last because a page=N link could be anything; requiring the
    exact expected number keeps it from wandering off into unrelated links.
    """
    soup = BeautifulSoup(html, "html.parser")

    rel_next = soup.find(
        "a",
        href=True,
        rel=lambda v: v and "next" in ([v] if isinstance(v, str) else v),
    )
    if rel_next:
        return urljoin(base_url, rel_next["href"])

    for a in soup.find_all("a", href=True):
        label = _clean(a.get("aria-label") or "")
        if NEXT_TEXT.match(_clean(a.get_text())) or NEXT_TEXT.match(label):
            return urljoin(base_url, a["href"])

    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]page=(\d+)", a["href"])
        if m and int(m.group(1)) == page_index:
            return urljoin(base_url, a["href"])

    return None


# Kept so older configs importing this name still work.
aalto_next_page = find_next_page


# --------------------------------------------------------------------------
# Tampere  ("Application period ends: 2026-03-01 23:59")
# --------------------------------------------------------------------------
def tampere(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    jobs = []

    marker = re.compile(r"Application period ends|Hakuaika p\u00e4\u00e4ttyy", re.I)
    for node in main.find_all(string=marker):
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
        raise ValueError("no application-deadline text found \u2014 Tampere layout may have changed")
    return jobs


# --------------------------------------------------------------------------
# UEF  ("Apply by: 15.11.2026 - Place of employment: ...")
# --------------------------------------------------------------------------
def uef(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    jobs = []

    marker = re.compile(r"Apply by|Hae viimeist\u00e4\u00e4n", re.I)
    for node in main.find_all(string=marker):
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
        raise ValueError("no apply-by text found \u2014 UEF layout may have changed")
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


# --------------------------------------------------------------------------
# Jyväskylä (and anyone else on Talentadore). Server-rendered list where each
# entry links out to ats.talentadore.com and carries two dates: "Posted on"
# and "Application deadline". Anchoring on the link target rather than on CSS
# classes makes this survive the site's next redesign.
# --------------------------------------------------------------------------
def talentadore(html, base_url, cfg):
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()

    for link in soup.find_all("a", href=True):
        if "talentadore.com/apply" not in link["href"]:
            continue
        title = _clean(link.get_text())
        if not title or link["href"] in seen:
            continue
        seen.add(link["href"])

        # Walk up until the container also holds the deadline label, so we
        # don't accidentally read the "Posted on" date instead.
        deadline, unit = None, None
        holder = link.parent
        for _ in range(6):
            if holder is None:
                break
            text = _clean(holder.get_text())
            if "Application deadline" in text or "Hakuaika" in text:
                tail = re.split(r"Application deadline|Hakuaika p\u00e4\u00e4ttyy", text)[-1]
                deadline = _parse_date(tail)
                head = text.split(title)[0]
                head = re.sub(r"(?i)university of jyv\u00e4skyl\u00e4", "", head)
                unit = _clean(head) or None
                break
            holder = holder.parent

        jobs.append({
            "title": title,
            "url": urljoin(base_url, link["href"]),
            "unit": unit,
            "deadline": deadline,
        })

    if not jobs:
        raise ValueError("no Talentadore job links found \u2014 layout may have changed")
    return jobs


def language_links(html, base_url):
    """Other-language listings on a rekrytointi.com page.

    These sites hand out a session token and ignore ?lang= / ?list= unless the
    request carries it, so the only way in is to follow the links the page
    itself prints, token included. That matters: Vaasa advertises almost
    everything in Finnish and nothing in English.
    """
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "o=A_LOJ" in href and re.search(r"list=\d+", href) and "lang=" in href:
            url = urljoin(base_url, href)
            if url not in found:
                found.append(url)
    return found


# --------------------------------------------------------------------------
# Helsinki. Their listing page ships no jobs; a Drupal endpoint supplies them:
#   https://www.helsinki.fi/en/ajax_get_jobs/en/null/null/null/0
# The nulls are the filter dropdowns, the final digit is the page number.
# Permitted by robots.txt: the only ajax rule there is "Disallow: /ajax/",
# and this path does not fall under it.
#
# The response may be JSON or an HTML fragment, so this handles both rather
# than assuming. Field names are matched loosely for the same reason.
# --------------------------------------------------------------------------
TITLE_KEYS = ("title", "job_title", "name", "otsikko", "position", "heading")
URL_KEYS = ("url", "link", "href", "path", "apply_url", "job_url", "alias")
DATE_KEYS = ("deadline", "closing", "close", "end_date", "application_end",
             "expires", "valid_to", "hakuaika", "paattyy", "due")


def _pick(d, keys):
    for k in d:
        lowered = k.lower()
        if any(cand in lowered for cand in keys):
            value = d[k]
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value)
    return None


def _walk_json(node, out):
    """Collect every dict that looks like a job posting, at any depth."""
    if isinstance(node, dict):
        title = _pick(node, TITLE_KEYS)
        if title and len(title.strip()) > 3:
            out.append(node)
        else:
            for v in node.values():
                _walk_json(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, out)


def helsinki(payload, base_url, cfg):
    text = payload.strip()

    if text[:1] in "{[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"endpoint returned unparseable JSON: {exc}")

        candidates = []
        _walk_json(data, candidates)
        jobs, seen = [], set()
        for item in candidates:
            title = _clean(_pick(item, TITLE_KEYS))
            if not title:
                continue
            raw_url = _pick(item, URL_KEYS) or ""
            # Drupal often sends rendered markup rather than plain values,
            # and sometimes the only link lives inside the title itself.
            for source in (raw_url, title):
                if "<" in source:
                    a = BeautifulSoup(source, "html.parser").find("a", href=True)
                    if a:
                        raw_url = a["href"]
                        break
                    raw_url = "" if source is raw_url else raw_url
            url = urljoin(base_url, raw_url) if raw_url else base_url
            if url in seen:
                continue
            seen.add(url)
            if "<" in title:
                title = _clean(BeautifulSoup(title, "html.parser").get_text())
            jobs.append({
                "title": title,
                "url": url,
                "unit": None,
                "deadline": _parse_date(_pick(item, DATE_KEYS) or ""),
            })
        if not jobs:
            # An empty list is how the last page of results announces itself.
            # Only complain when there was content we failed to make sense of.
            if not data:
                return []
            raise ValueError("JSON parsed but no job-shaped records found")
        return jobs

    # Otherwise treat it as an HTML fragment.
    soup = BeautifulSoup(payload, "html.parser")
    jobs, seen = [], set()
    for link in soup.find_all("a", href=True):
        title = _clean(link.get_text())
        if len(title) < 8 or link["href"] in seen:
            continue
        seen.add(link["href"])
        deadline, holder = None, link.parent
        for _ in range(4):
            if holder is None:
                break
            deadline = _parse_date(holder.get_text())
            if deadline:
                break
            holder = holder.parent
        jobs.append({
            "title": title,
            "url": urljoin(base_url, link["href"]),
            "unit": None,
            "deadline": deadline,
        })
    if not jobs:
        raise ValueError("no jobs found in the endpoint response")
    return jobs


def increment_trailing_number(url):
    """Next page for endpoints that end in a page number, like .../null/0."""
    m = re.search(r"(\d+)(/?)$", url)
    if not m:
        return None
    return url[:m.start(1)] + str(int(m.group(1)) + 1) + m.group(2)


PARSERS = {
    "varbi": varbi,
    "helsinki": helsinki,
    "talentadore": talentadore,
    "rekrytointi": rekrytointi,
    "aalto": aalto,
    "tampere": tampere,
    "uef": uef,
    "generic": generic,
}
