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
.github/workflows/update.yml  cron: 07:00 Dhaka daily + every 2h, commits data/
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
