# নিউজ ডেস্ক: ফরেক্স ক্যালেন্ডার, সম্ভাবনা আর বাংলা বিশ্লেষণ

A static site with three views, all in **Dhaka time (UTC+6)**. Use the ⋮ menu (or keys 1/2/3) to switch between them:

- **ড্যাশবোর্ড** `#/dashboard`: today's red and orange USD events, each with time, live countdown, Previous/Forecast/Actual and the combined verdict. Also the short Bangla briefs for the next 24h.
- **ক্যালেন্ডার** `#/calendar?d=YYYY-MM-DD&m=day|week`: a ForexFactory-style table (time, currency, impact folder, event, Actual/Forecast/Previous) for any day or week.
  - Actual is coloured better or worse the way FF does it, and the next event is highlighted.
  - Buttons for Today, This week and Next week, plus prev/next arrows and a date picker. Currency and impact filters are saved in localStorage.
  - Click a row to expand it; the expanded row links to the analysis page. A date that isn't in the archive gets an empty state linking to that day on forexfactory.com.
- **বিশ্লেষণ** `#/analysis/<eventId>`: for every red or orange USD event from today to the end of next week, five sections:
  - ক) what it is: a Bangla explainer plus FF specs
  - খ) when, with a live countdown
  - গ) the data, plus the last 6 releases with a chart
  - ঘ) probabilities: every source with its number, time and link, then a combined verdict with the most likely and second most likely outcome and an honest confidence level
  - ঙ) three scenarios (most likely / opposite / neutral), each with per-market reactions (Gold, BTC, alts, DXY/EURUSD/USDJPY/GBPUSD, US indices and yields) and what to watch in the first 15–60 minutes

  Any other event, such as a non-USD one from the calendar, gets a simpler page with time, data and history.

Everything here is probabilistic. It is not financial advice.

## Structure
```
index.html                 UI (no build step)
assets/css/style.css       dark glass theme, motion (honours prefers-reduced-motion)
assets/js/app.js           shell: hash router, ⋮ drawer, clock, status pill, banner
assets/js/util.js          Dhaka-time helpers, data loading, localStorage
assets/js/views/*.js       dashboard / calendar / analysis
scripts/update.py          data updater (Python stdlib only)
scripts/explainers.py      Bangla explainer dictionary (+ generic fallback)
scripts/scenarios.py       rule-based three-scenario market analysis
scripts/nowcasts.py        Cleveland Fed inflation nowcast + Atlanta Fed GDPNow
data/calendar.json         events (this + next week)
data/weeks/YYYY-MM-DD.json one file per FF week (key = Sunday week start); index.json lists them
data/ff_specs.json         FF event specs + release history, cached per event type
data/analysis.json         per-event analysis (explainer, data, history, sources, verdict, scenarios)
data/probabilities.json    Polymarket / Kalshi / FedWatch per USD event
data/briefs.json           Bangla briefs for the next 24h (or "আজ বড় কোনো USD নিউজ নেই")
data/status.json           ForexFactory page/feed status (ff_page_ok, ff_feed_ok, reason, fallback)
scripts/ff_alert.sh        opens/closes the "ff-blocked" GitHub issue (uses gh + GITHUB_TOKEN)
.github/workflows/update.yml  cron: 07:00 Dhaka daily + every 2h, commits data/ only on real changes
tests/test_update.py       unit tests (stdlib)
tests/test_round3.py       unit tests: week archive/backfill, specs, nowcasts, explainers, scenarios, verdict
tests/smoke.py             headless browser test + screenshots (playwright)
```

## Data sources
| Source | Used for | Notes |
|---|---|---|
| forexfactory.com calendar page (embedded JSON) | events, **Actual**, Forecast, Previous (this and next week) | primary |
| nfs.faireconomy.media `ff_calendar_thisweek.json` | fallback when the page is blocked | has no Actual; `nextweek.json` currently returns 404. Actuals from earlier runs are kept |
| Polymarket Gamma API | Fed decision, CPI m/m and y/y, Core CPI, NFP, unemployment, GDP | Yes price ≈ probability |
| Kalshi public API | Fed decision, CPI family, payrolls, U3, GDP, jobless claims, PPI, core PCE, ISM | mid of bid/ask; thin markets are flagged |
| forexfactory.com `calendar/details/1-<id>` | Analysis: FF specs (Measures, Usual Effect, …) + release history | fetched once per event type, refreshed every 30 days |
| Cleveland Fed Inflation Nowcasting (JSON) | CPI, Core CPI (m/m and y/y), PCE, Core PCE | daily model estimate; compared with Forecast |
| Atlanta Fed GDPNow (xlsx) | GDP | only downloaded when a GDP release is in the analysis window |
| CME FedWatch | FOMC only | cmegroup.com returns HTTP 403 to scripts and the official API is paid, so the site **links** to FedWatch and shows the Kalshi/Polymarket Fed market as the closest comparison |

If no market matches, the site shows **"ডেটা নেই"**. Numbers are never invented.

**The combined verdict** averages the markets' "above / near / below Forecast" split (for Fed events: hike / hold / cut). Confidence is **নিম্ন** when only one market is available, when liquidity is thin, or when the top outcome is under 50%. A nowcast on its own gives a direction but **no percentage**. With no source at all, the verdict reads "ডেটা নেই".

## Week archive & backfill
- Every run refreshes this week and next week. It also re-fetches last week until that week is complete.
- Each run then fills at most `FF_BACKFILL_PER_RUN` (4) missing weeks from the last `FF_BACKFILL_WEEKS` (52), waiting `FF_DELAY_S` (4s) between requests. It stops at the first refusal and keeps whatever it already has. Weeks that are already stored are never fetched again, so the archive keeps growing.
- None of this runs if the main FF fetch failed, and it never affects `ff_page_ok` or the alert.
- A week file is written only when its events change. The one-time 12-month backfill was done locally with `FF_BACKFILL_PER_RUN=60 FF_DELAY_S=3 python3 scripts/update.py`.
- To check that every nowcast source can be reached from GitHub's runner: **Run workflow** with **probe_all = true**, then read the log, or `status.json → nowcasts`.

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
pip install playwright && SHOTS=screenshots/v2 python tests/smoke.py http://localhost:8000/   # optional UI check
```

## Deploy (GitHub Pages)
1. Create a public repo, e.g. `Noman123p/ff-news-desk`, and push the `main` branch.
2. Go to **Settings → Pages → Source: Deploy from a branch**, then pick `main` / `/ (root)`.
3. Go to **Settings → Actions → General → Workflow permissions** and set it to **Read and write**.
4. Under **Actions**, enable workflows, then run **Update calendar data** once with *Run workflow*.
5. The site will be at `https://noman123p.github.io/ff-news-desk/`.

Scheduled runs can be delayed by GitHub by 5–20 minutes. If ForexFactory blocks GitHub's IPs, the updater falls back to the JSON feed. In that case Actual may stay blank until a later run succeeds, and next-week events may be missing.
