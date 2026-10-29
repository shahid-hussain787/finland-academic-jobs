# Open academic positions in Finland

Checks nine Finnish university job pages once a day and publishes everything it
finds as one searchable page, sorted by application deadline. Anyone with the
link sees the same list. No server, no cost.

---

## Setting it up

**1. Create the repository.** On GitHub, make a new **public** repository called
`finland-academic-jobs`. Public matters: Pages and Actions are free on public
repos, and the whole point is a link you can share.

**2. Upload these files.** On the empty repo page use *uploading an existing
file*, then drag in everything from this folder. Keep the folder structure:
`docs/` and `.github/workflows/` must stay as they are. If the web uploader
drops the `.github` folder (it sometimes hides dot-folders), create it manually
with *Add file → Create new file* and type `.github/workflows/scrape.yml` as the
filename, then paste the contents in.

**3. Turn on Pages.** Repo *Settings → Pages*. Under Source pick **Deploy from a
branch**, branch `main`, folder **`/docs`**. Save. After a minute your page is
live at:

```
https://YOUR-USERNAME.github.io/finland-academic-jobs/
```

That is the link you share.

**4. Let the workflow write to the repo.** *Settings → Actions → General*, scroll
to **Workflow permissions**, choose **Read and write permissions**, save. Without
this the daily run can't commit the updated results.

**5. Run it once by hand.** Go to the **Actions** tab, pick *Update positions*,
click **Run workflow**. Takes about a minute. Open the run and read the log — it
prints each university and how many positions it found. Then reload your Pages
link.

From here it runs itself every morning.

---

## What to expect

Seven of the nine sites have parsers written against their actual page
structure: Oulu, Aalto, Vaasa, Åbo Akademi, Tampere, UEF and Jyväskylä.

**Turku and Helsinki return nothing.** Their listings are loaded by JavaScript,
so there is nothing in the page HTML to parse. They're marked
`verified: false`, and the site shows a notice about them. See below.

## Fixing a site that returns nothing

Run it alone on your own machine:

```bash
pip install -r requirements.txt
python scrape.py --debug --only helsinki
```

If it reports zero but the page looks fine in a browser, the data is being
fetched separately by the page's own code. Find that request:

1. Open the job page in Chrome and press **F12**
2. Click the **Network** tab, then filter to **Fetch/XHR**
3. Reload the page
4. Look through the requests for one whose response contains job titles

That request's URL is what the scraper should fetch instead of the human-facing
page. Usually it returns JSON, which is far easier and more stable to parse than
HTML. Add it to that university's `urls:` list and write a small parser for it.

If it returns results but they're messy, narrow `item_selector` to the container
the listings actually sit in and tighten `must_match`.

Once a site works reliably, set `verified: true` so its warning disappears.

## Everyday use

**Add a university.** Copy a block in `universities.yml`, change the name and
`urls:`, pick the closest `parser`. If it's another `*.rekrytointi.com` or Varbi
site, it will very likely just work. Worth adding: LUT, Hanken, Uniarts, the
University of Lapland, and the research institutes (VTT, Luke, SYKE) if you want
non-university research jobs too.

**A site breaks.** Universities redesign pages, usually once or twice a year.
Three things then happen. The daily run logs `FAIL` for that one site and keeps
going with the rest. The page shows a notice with a direct link, so nobody
misses postings while you fix it. And the workflow opens a GitHub issue, which
GitHub emails you about, so you find out within a day rather than whenever you
next happen to look. While that issue stays open no further alerts are filed,
so a long-running breakage won't mail you every morning — close it once fixed.

**A university moves to a new system.** This is what the `urls:` list is for.
Put the new address at the top and leave the old one below it. Every URL is
fetched and the results merged, so the source keeps working as long as any one
of them does, and it only counts as failed when they all fail.

The scraper deliberately does **not** try to find the new address by itself.
It could search the web for the university's careers page, but it has no way to
confirm the result is the right page, so it would sometimes parse the wrong
site and present the output as real data. A scraper that quietly shows wrong
jobs is worse than one that visibly fails.

**Multiple pages of positions.** Handled automatically for every site. After
parsing a page the scraper looks for a next-page link (`rel="next"`, a link
reading Next or Seuraava, or a `?page=N` link) and keeps going. It stops at
`max_pages` (10 by default, settable per university), when there is no next
link, or when a page adds nothing new.

Worth knowing: as of writing, only Aalto actually paginates. Oulu, UEF and
Tampere each show everything on one page.

**Positions advertised only in Finnish.** This is a much bigger gap than
pagination. Tampere states outright that it publishes all positions in Finnish
and only the ones not requiring Finnish in English, so its English page shows a
fraction of what is open; Vaasa often has nothing in English at all. Both now
fetch their Finnish listing as a second URL. The Tampere and UEF parsers read
Finnish deadline wording as well as English.

If you want to try this for another university, add its Finnish page as a
second URL and check the next run. Positions appearing twice means that
university uses different links per language, and dedupe by URL can't catch it
— remove the second URL in that case.

**Change how often it checks.** Edit the `cron` line in
`.github/workflows/scrape.yml`. `"10 5 * * *"` means 05:10 UTC daily. Once a day
is plenty — deadlines are weeks out, and hammering these sites hourly is rude and
gets you blocked.

---

## How it fits together

| File | What it does |
|---|---|
| `universities.yml` | The list of sites and how to read each one. Edit this most. |
| `scrapers.py` | One parser per page layout. |
| `scrape.py` | Fetches each site, drops closed positions, writes `docs/jobs.json`. |
| `.github/workflows/scrape.yml` | Runs it daily and commits the result. |
| `docs/index.html` | The public page. Reads `jobs.json`, groups by urgency. |
| `docs/jobs.json` | The data. Overwritten by every run — don't edit by hand. |

Positions with no visible deadline are kept rather than dropped, since some are
genuinely rolling. Positions past their deadline are removed.

---

## One thing to be aware of

This reads public job listings the same way a browser does, with a two-second
pause between requests and an identifying user agent. That's normal and
low-impact at one run a day. Still, these are other people's websites: if a
university asks you to stop, stop, and if you start sharing the link widely,
check each site's terms first. Keep the "check the original posting" line in the
footer — deadlines occasionally change after posting, and some positions are
advertised only in Finnish on a page the scraper reads in English.
