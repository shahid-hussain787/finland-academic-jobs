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

## What to expect on the first run

Six of the nine sites have parsers written against their actual page structure:
Oulu, Aalto, Vaasa, Åbo Akademi, Tampere and UEF.

Three do not: **Turku, Helsinki and Jyväskylä**. I couldn't inspect those pages
while building this, so they use a rough catch-all parser that grabs links whose
text looks like a job title. They're marked `verified: false` in
`universities.yml`, and the page shows a notice about them. They will probably
either return junk or return nothing on the first run.

To fix one, run it alone on your own machine:

```bash
pip install -r requirements.txt
python scrape.py --debug --only helsinki
```

Look at what comes back. If it's wrong, the usual cause is one of two things:

- **Zero results and the page looks fine in a browser.** The listings are loaded
  by JavaScript, so there's nothing in the HTML to parse. Open the page in
  Chrome, press F12, go to the **Network** tab, filter to **Fetch/XHR**, reload,
  and look for a request returning JSON full of job titles. That URL is what the
  scraper should fetch instead. Helsinki is the likeliest candidate for this.
- **Results, but messy.** Narrow `item_selector` in `universities.yml` to the
  container the listings actually sit in, and tighten `must_match`.

Once a site works reliably, set `verified: true` so the warning notice stops
showing for it.

---

## Everyday use

**Add a university.** Copy a block in `universities.yml`, change the name and
URL, pick the closest `parser`. If it's another `*.rekrytointi.com` or Varbi
site, it will very likely just work. Worth adding: LUT, Hanken, Uniarts, the
University of Lapland, and the research institutes (VTT, Luke, SYKE) if you want
non-university research jobs too.

**A site breaks.** Universities redesign pages, usually once or twice a year.
When that happens the daily run logs `FAIL` for that one site and keeps going
with the rest, and the page shows a notice with a direct link so nobody misses
postings while you fix it. Only a total failure of all nine stops the run.

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
