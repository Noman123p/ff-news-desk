# নিউজ ডেস্ক: USD ইকোনমিক ক্যালেন্ডার, সম্ভাবনা আর বাংলা ব্রিফ

A static site that shows the ForexFactory calendar in **Dhaka time (UTC+6)**, with Previous, Forecast and Actual for each event. For high and medium impact USD news it adds **prediction-market probabilities** from Polymarket and Kalshi, plus a CME FedWatch link for FOMC events. It also writes a rule-based **Bangla brief** covering Gold, BTC, crypto and forex for every red or orange USD release in the next 24 hours.

Everything here is probabilistic. It is not financial advice.

## Structure
```
index.html                 UI (no build step)
assets/css/style.css       dark glass theme, motion (honours prefers-reduced-motion)
assets/js/app.js           rendering, filters, Dhaka time, probability panels
scripts/update.py          data updater (Python stdlib only)
data/calendar.json         events (this + next week)
data/probabilities.json    Polymarket / Kalshi / FedWatch per USD event
data/briefs.json           Bangla briefs for the next 24h (or "আজ বড় কোনো USD নিউজ নেই")
data/status.json           ForexFactory page/feed status (ff_page_ok, ff_feed_ok, reason, fallback)
scripts/ff_alert.sh        opens/closes the "ff-blocked" GitHub issue (uses gh + GITHUB_TOKEN)
.github/workflows/update.yml  cron: 07:00 Dhaka daily + every 2h, commits data/ only on real changes
tests/test_update.py       unit tests (stdlib)
tests/smoke.py             headless browser test + screenshots (playwright)
```

## Data sources
| Source | Used for | Notes |
|---|---|---|
| forexfactory.com calendar page (embedded JSON) | events, **Actual**, Forecast, Previous (this and next week) | primary |
| nfs.faireconomy.media `ff_calendar_thisweek.json` | fallback when the page is blocked | has no Actual; `nextweek.json` currently returns 404. Actuals from earlier runs are kept |
| Polymarket Gamma API | Fed decision, CPI m/m and y/y, Core CPI, NFP, unemployment, GDP | Yes price ≈ probability |
| Kalshi public API | Fed decision, CPI family, payrolls, U3, GDP, jobless claims, PPI, core PCE, ISM | mid of bid/ask; thin markets are flagged |
| CME FedWatch | FOMC only | cmegroup.com returns HTTP 403 to scripts and the official API is paid, so the site **links** to FedWatch and shows the Kalshi/Polymarket Fed market as the closest comparison |

If no market matches, the site shows **"ডেটা নেই"**. Numbers are never invented.

## Block alerts & commit noise
- On every run, `update.py` records whether the ForexFactory page loaded. A failure can be an HTTP 403/429/503, a Cloudflare or captcha page, a timeout, or a layout change. The result goes to `data/status.json` and is also exposed as step outputs.
- If the page fails, the workflow opens the issue **"⚠️ ForexFactory ব্লক করেছে — Actual ডেটা আসছে না"**. It carries the label `ff-blocked`, is assigned to the repo owner (so GitHub emails him), and its Bangla body gives the Dhaka time, HTTP status, reason and the fallback in use. Only one such issue is open at a time.
- The next successful run comments **"✅ আবার ঠিক হয়েছে"** and closes the issue.
- To test the alert path, run the workflow manually with **simulate_block = true**. This opens a test issue (and GitHub emails the owner). The next normal run closes it.
- When the page is blocked, the site shows a small amber banner.
- A file is rewritten only if its content changed. Timestamps, volumes and probability moves under 1.5 points don't count as changes. Every file is still refreshed at least every 12h as a heartbeat. Because of this, most runs produce no commit.

## Run locally
```bash
python3 scripts/update.py           # writes data/*.json
python3 -m http.server 8000         # open http://localhost:8000
python3 -m unittest discover -s tests -p "test_*.py"
pip install playwright && python tests/smoke.py http://localhost:8000/   # optional UI check
```

## Deploy (GitHub Pages)
1. Create a public repo, e.g. `Noman123p/ff-news-desk`, and push the `main` branch.
2. Go to **Settings → Pages → Source: Deploy from a branch**, then pick `main` / `/ (root)`.
3. Go to **Settings → Actions → General → Workflow permissions** and set it to **Read and write**.
4. Under **Actions**, enable workflows, then run **Update calendar data** once with *Run workflow*.
5. The site will be at `https://noman123p.github.io/ff-news-desk/`.

Scheduled runs can be delayed by GitHub by 5–20 minutes. If ForexFactory blocks GitHub's IPs, the updater falls back to the JSON feed. In that case Actual may stay blank until a later run succeeds, and next-week events may be missing.
