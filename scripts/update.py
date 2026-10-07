#!/usr/bin/env python3
"""
FF News Desk — data updater.

Fetches the ForexFactory economic calendar, matches high/medium-impact USD
events to prediction-market probabilities (Polymarket, Kalshi; CME FedWatch
link for FOMC), and writes rule-based Bangla briefs.

Outputs (read by the static site):
  data/calendar.json       all events (this week + next week)
  data/probabilities.json  market probabilities per USD event
  data/briefs.json         Bangla briefs for USD red/orange events in next 24h

Standard library only, so it runs in GitHub Actions without `pip install`.
Never invents numbers: missing data stays null and the UI shows "ডেটা নেই".
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import explainers  # noqa: E402
import nowcasts  # noqa: E402
import scenarios  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

UTC = timezone.utc
DHAKA = timezone(timedelta(hours=6), "Asia/Dhaka")  # Bangladesh has no DST

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FF_JSON = "https://nfs.faireconomy.media/ff_calendar_{week}.json"   # thisweek / nextweek
FF_HTML = "https://www.forexfactory.com/calendar?week={week}"          # this / next / oct4.2026
FF_DETAILS = "https://www.forexfactory.com/calendar/details/1-{id}"    # specs + history JSON
POLY_SEARCH = "https://gamma-api.polymarket.com/public-search"
POLY_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
FEDWATCH_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"

BRIEF_WINDOW_H = 24
HEARTBEAT_H = 12      # rewrite unchanged files at least this often (proves the updater is alive)
PROB_TOL = 1.5        # percentage points: smaller probability moves don't count as a change
PROB_TOL_THIN = 5.0   # same, for markets flagged low_liquidity (their quotes jump around)
# Week archive (data/weeks/YYYY-MM-DD.json keyed by FF week start, a Sunday)
BACKFILL_WEEKS = int(os.environ.get("FF_BACKFILL_WEEKS", "52"))     # how far back to fill
BACKFILL_PER_RUN = int(os.environ.get("FF_BACKFILL_PER_RUN", "4"))  # polite: few old weeks per run
FF_DELAY_S = float(os.environ.get("FF_DELAY_S", "4"))               # pause between extra FF requests
SPECS_PER_RUN = int(os.environ.get("FF_SPECS_PER_RUN", "25"))
SPECS_MAX_AGE_D = 30
FF_TZ = timezone(timedelta(hours=-5))   # FF's anonymous calendar day/week boundaries (~US Eastern/Central)

NOW = datetime.now(UTC)
LAST_ERR = {}         # url -> short error text of the last failed request
FF_STATUS = {}        # week -> fetch status, filled by build_calendar()
WEEK_ROWS = {}        # week key -> (rows, source) fetched this run


def log(*a):
    print("[update]", *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get(url, params=None, accept="application/json", timeout=25, retries=2):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                                       "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = e
            LAST_ERR[url] = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 451):  # not retryable
                return e.code, b""
        except Exception as e:  # network / timeout
            last = e
            txt = repr(e)
            LAST_ERR[url] = "timeout" if "timed out" in txt.lower() or isinstance(e, TimeoutError) else type(e).__name__
        time.sleep(1.5 * (attempt + 1))
    log("GET failed:", url, repr(last))
    return None, b""


def get_json(url, params=None):
    status, body = http_get(url, params)
    if status != 200 or not body:
        return None, status
    try:
        return json.loads(body.decode("utf-8")), status
    except ValueError:
        return None, status


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([KMBT]?)", re.I)
_MULT = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_value(s):
    """'0.3%' -> 0.3, '200K' -> 200000, '-1.2M' -> -1200000, '' -> None."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    return float(m.group(1)) * _MULT[m.group(2).upper()]


def parse_bucket(label):
    """Polymarket bucket label -> (lo, hi) inclusive interval, or None.
    Examples: '0.4%', '≤0.0%', '≥0.8%', '0.6%+', '25k to 50k', '<-25k', '100k+'."""
    if not label:
        return None
    t = label.replace(",", "").replace("−", "-").strip()
    nums = [float(a) * _MULT[b.upper()] for a, b in _NUM_RE.findall(t)]
    if not nums:
        return None
    low = t.lower()
    if " to " in low or ("–" in t and len(nums) == 2):
        return (min(nums), max(nums)) if len(nums) == 2 else None
    v = nums[0]
    if t.startswith(("≤", "<=", "<")) or "or less" in low or "or lower" in low:
        return (-math.inf, v)
    if t.startswith(("≥", ">=", ">")) or t.endswith("+") or "or more" in low or "or higher" in low:
        return (v, math.inf)
    return (v, v)


def pct(p):
    return None if p is None else round(max(0.0, min(1.0, p)) * 100, 1)


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def iso_utc(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def dhaka_str(dt, fmt="%d %b, %I:%M %p"):
    return dt.astimezone(DHAKA).strftime(fmt)


def event_id(currency, title, ts):
    return hashlib.sha1(f"{currency}|{title}|{ts}".encode()).hexdigest()[:12]


def load_json(name, default):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, obj, compact=False):
    path = os.path.join(DATA_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")
    os.replace(tmp, path)


VOLATILE_KEYS = {"generated_at", "checked_at", "fetched_at", "volume_usd", "volume", "low_liquidity"}
PROB_KEYS = {"prob", "prob_above", "above", "inline", "below", "hike", "hold", "cut"}


def same_content(a, b, key=None, tol=PROB_TOL):
    """Deep-compare ignoring timestamps/volumes; probabilities equal within tol points
    (PROB_TOL_THIN inside a market flagged low_liquidity)."""
    if isinstance(a, dict) and isinstance(b, dict):
        ka = set(a) - VOLATILE_KEYS
        kb = set(b) - VOLATILE_KEYS
        if a.get("low_liquidity") or b.get("low_liquidity"):
            tol = max(tol, PROB_TOL_THIN)
        return ka == kb and all(same_content(a[k], b[k], k, tol) for k in ka)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_content(x, y, key, tol) for x, y in zip(a, b))
    if key in PROB_KEYS and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < tol
    return a == b


def too_old(obj, field="generated_at"):
    t = parse_iso((obj or {}).get(field)) if isinstance(obj, dict) else None
    return t is None or (NOW - t) > timedelta(hours=HEARTBEAT_H)


def write_if_changed(name, obj, field="generated_at", heartbeat=True, compact=False, quiet=False):
    """Write only on real content change (or heartbeat). Returns the object now on disk."""
    old = load_json(name, None)
    if old is not None and same_content(old, obj) and not (heartbeat and too_old(old, field)):
        if not quiet:
            log(f"{name}: unchanged — kept")
        return old
    write_json(name, obj, compact=compact)
    log(f"{name}: written")
    return obj


# --------------------------------------------------------------------------- #
# ForexFactory calendar
# --------------------------------------------------------------------------- #
IMPACT_NORM = {"high": "High", "medium": "Medium", "low": "Low", "holiday": "Holiday",
               "non-economic": "Holiday", "none": "Low"}


_BOT_RX = re.compile(r"Just a moment|cf-chl|challenge-platform|captcha|Attention Required|Access denied", re.I)


def fetch_ff_html(week):
    """Scrape the calendar page; it embeds a JSON 'days' array incl. Actual values.
    Returns (rows | None, reason, http_status)."""
    url = FF_HTML.format(week=week)
    status, body = http_get(url, accept="text/html")
    if status is None:
        return None, f"নেটওয়ার্ক সমস্যা/টাইমআউট ({LAST_ERR.get(url, 'unknown')})", None
    if status != 200 or not body:
        kind = "ব্লক করেছে" if status in (403, 429, 451, 503) else "সার্ভার এরর"
        return None, f"HTTP {status} — {kind}", status
    html = body.decode("cp1252", errors="replace")
    anchor = html.find("calendarComponentStates[1]")
    i = html.find("days:", anchor if anchor >= 0 else 0)
    if anchor < 0 or i < 0:
        if _BOT_RX.search(html[:20000]):
            return None, "Cloudflare/ক্যাপচা বট-চেক পেজ এসেছে (HTTP 200)", status
        return None, "ক্যালেন্ডার ডেটা পাওয়া যায়নি — পেজের গঠন বদলেছে বা বট-চেক", status
    try:
        days, _ = json.JSONDecoder().raw_decode(html[html.find("[", i):])
    except ValueError as e:
        return None, f"পার্স এরর: {e}", status
    wk = None
    if days and isinstance(days[0].get("dateline"), (int, float)):
        wk = week_start(datetime.fromtimestamp(days[0]["dateline"] + 12 * 3600, UTC).date()).isoformat()
    out = []
    for d in days:
        for e in d.get("events", []):
            ts = e.get("dateline")
            if not isinstance(ts, (int, float)):
                continue
            dt = datetime.fromtimestamp(ts, UTC)
            label = (e.get("timeLabel") or "").strip()
            out.append({
                "title": e.get("name") or "",
                "currency": e.get("currency") or "",
                "impact": IMPACT_NORM.get((e.get("impactName") or "").lower(), "Low"),
                "time_utc": iso_utc(dt),
                "time_special": label if (e.get("timeMasked") or not re.search(r"\d", label)) else None,
                "actual": (e.get("actual") or "").strip(),
                "forecast": (e.get("forecast") or "").strip(),
                "previous": (e.get("previous") or "").strip(),
                "revision": (e.get("revision") or "").strip(),
                "abw": e.get("actualBetterWorse") if e.get("actualBetterWorse") in (0, 1, 2) else 0,
                "ff_id": e.get("id"),
                "ebase": e.get("ebaseId"),
                "ff_week": wk,
                # soloUrl is stable; "url" embeds a day that depends on FF's server-side timezone
                "ff_url": "https://www.forexfactory.com" + e["soloUrl"] if e.get("soloUrl") else None,
            })
    if not out:
        return None, "পেজে কোনো ইভেন্ট নেই", status
    return out, "ok", status


def fetch_ff_json(week):
    data, status = get_json(FF_JSON.format(week=week))
    if not isinstance(data, list):
        return None, f"HTTP {status}"
    out = []
    for e in data:
        dt = parse_iso(e.get("date"))
        if not dt:
            continue
        out.append({
            "title": e.get("title") or "",
            "currency": e.get("country") or "",
            "impact": IMPACT_NORM.get((e.get("impact") or "").lower(), "Low"),
            "time_utc": iso_utc(dt),
            "time_special": None,
            "actual": (e.get("actual") or "").strip(),
            "forecast": (e.get("forecast") or "").strip(),
            "previous": (e.get("previous") or "").strip(),
            "revision": "",
            "ff_url": None,
        })
    return out, "ok"


# --------------------------------------------------------------------------- #
# Week archive
# --------------------------------------------------------------------------- #
WEEK_FIELDS = ("id", "title", "currency", "impact", "time_utc", "time_special", "actual", "forecast",
               "previous", "revision", "abw", "ff_id", "ebase", "category", "dir")


def week_start(d: date) -> date:
    """Sunday on/before d (FF weeks run Sunday–Saturday)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def week_param(key: str) -> str:
    d = date.fromisoformat(key)
    return f"{d.strftime('%b').lower()}{d.day}.{d.year}"


def ff_today() -> date:
    return NOW.astimezone(FF_TZ).date()


def week_complete(key: str) -> bool:
    end = datetime.combine(date.fromisoformat(key) + timedelta(days=7), datetime.min.time(), FF_TZ)
    return NOW > end + timedelta(hours=12)


def slim(e: dict) -> dict:
    cat, d = classify(e["title"])
    e = {**e, "category": e.get("category") or cat, "dir": e.get("dir") or d}
    return {k: e.get(k) for k in WEEK_FIELDS}


def save_week(key: str, rows: list, source: str) -> bool:
    """Write data/weeks/<key>.json if content changed. Keeps known Actuals when a fallback lacks them."""
    name = f"weeks/{key}.json"
    old = load_json(name, None)
    known = {e["id"]: e for e in (old or {}).get("events", [])}
    events = []
    for r in rows:
        r = dict(r)
        r.setdefault("id", event_id(r["currency"], r["title"], r["time_utc"]))
        k = known.get(r["id"])
        if k:
            for f in ("actual", "abw", "ff_id", "ebase", "revision"):
                if not r.get(f) and k.get(f):
                    r[f] = k[f]
        events.append(slim(r))
    events.sort(key=lambda x: (x["time_utc"], x["currency"], x["title"]))
    doc = {"week_start": key, "fetched_at": iso_utc(NOW), "complete": week_complete(key),
           "source": source, "events": events}
    # source/fetched_at alone never cause a rewrite (fallback runs would otherwise flap the file)
    if old is not None and same_content(old.get("events"), events) and old.get("complete") == doc["complete"]:
        return False
    write_json(name, doc, compact=True)
    log(f"{name}: written ({len(events)} events)")
    return True


def stored_weeks() -> list:
    d = os.path.join(DATA_DIR, "weeks")
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f))


def write_week_index():
    keys = stored_weeks()
    counts = {}
    for k in keys:
        doc = load_json(f"weeks/{k}.json", {})
        counts[k] = len(doc.get("events", []))
    idx = {"generated_at": iso_utc(NOW), "weeks": keys, "first": keys[0] if keys else None,
           "last": keys[-1] if keys else None, "event_counts": counts}
    write_if_changed("weeks/index.json", idx, heartbeat=False)


def archive_weeks(fetched: dict):
    """fetched: key -> (rows, source) from this run's this/next fetch. Then refresh the previous
    week until complete and backfill a few missing old weeks. Extra FF requests only when the
    main page fetch worked; failures here stop quietly and never touch FF_STATUS / the alert."""
    for key, (rows, src) in fetched.items():
        if key and rows:
            save_week(key, rows, src)
    page_ok = bool(FF_STATUS) and all(w["page_ok"] for w in FF_STATUS.values())
    report = {"backfilled": [], "stopped": None}
    if page_ok:
        this_key = week_start(ff_today())
        prev_key = (this_key - timedelta(days=7)).isoformat()
        have = set(stored_weeks())
        prev_doc = load_json(f"weeks/{prev_key}.json", None)
        todo = []
        if prev_doc is None or not prev_doc.get("complete"):
            todo.append(prev_key)
        for i in range(2, BACKFILL_WEEKS + 1):
            k = (this_key - timedelta(days=7 * i)).isoformat()
            if k not in have:
                todo.append(k)
        for n, key in enumerate(todo[:BACKFILL_PER_RUN + 1]):
            time.sleep(FF_DELAY_S if n else 1.0)
            rows, note, http = fetch_ff_html(week_param(key))
            if not rows:
                report["stopped"] = f"{key}: {note}"
                log(f"backfill stopped at {key}: {note}")
                break
            got = rows[0].get("ff_week") or key
            save_week(got, rows, "forexfactory.com (HTML)")
            report["backfilled"].append(got)
        if report["backfilled"]:
            log(f"backfill: {len(report['backfilled'])} week(s): {report['backfilled']}")
    write_week_index()
    return report


def build_calendar():
    prev = load_json("calendar.json", {})
    prev_actuals = {e["id"]: e.get("actual") for e in prev.get("events", []) if e.get("actual")}
    sources, events = {}, []
    WEEK_ROWS.clear()
    for week, jweek in (("this", "thisweek"), ("next", "nextweek")):
        rows, note, http = fetch_ff_html(week)
        src = "forexfactory.com (HTML)"
        sources[f"ff_html_{week}"] = note
        st = FF_STATUS[week] = {"page_ok": bool(rows), "page_reason": note, "page_http": http,
                                "feed_ok": None, "feed_reason": None}
        if not rows:
            rows, note2 = fetch_ff_json(jweek)
            src = "faireconomy JSON feed"
            sources[f"ff_json_{week}"] = note2
            st["feed_ok"], st["feed_reason"] = bool(rows), note2
            if not rows:
                continue
            for r in rows:   # the feed carries no week id: derive it (feed only exists for "this")
                r["ff_week"] = week_start(ff_today()).isoformat()
        for r in rows:
            r["id"] = event_id(r["currency"], r["title"], r["time_utc"])
            r["week"] = week
            r["source"] = src
            if not r["actual"] and prev_actuals.get(r["id"]):
                r["actual"] = prev_actuals[r["id"]]   # keep actuals from an earlier HTML run
            events.append(r)
        k = rows[0].get("ff_week")
        WEEK_ROWS[k] = (rows, src)
    # de-duplicate (same event can appear in both feeds at week edges)
    seen, uniq = set(), []
    for e in sorted(events, key=lambda x: (x["time_utc"], x["currency"], x["title"])):
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        uniq.append(e)
    if not uniq and prev.get("events"):
        log("calendar fetch failed everywhere; keeping previous data")
        prev["stale"] = True
        prev["sources"] = sources
        return prev
    return {"generated_at": iso_utc(NOW), "timezone_display": "Asia/Dhaka (UTC+6)",
            "stale": False, "sources": sources, "events": uniq}


# --------------------------------------------------------------------------- #
# Event classification
# --------------------------------------------------------------------------- #
# usd_dir: +1 => actual ABOVE forecast is USD-bullish (hawkish/strong), -1 => USD-bearish.
CATEGORIES = [
    ("fed_decision", re.compile(r"^(Federal Funds Rate|FOMC Statement|FOMC Press Conference)", re.I), +1),
    ("fed_talk", re.compile(r"^(?!.*\b(Philly|Richmond|Kansas City|Empire|Chicago|Dallas|NY) Fed\b)"
                            r".*(FOMC|Fed Chair|Fed Governor|Fed Vice Chair|Powell|Beige Book)", re.I), +1),
    ("core_cpi_mom", re.compile(r"^Core CPI m/m", re.I), +1),
    ("core_cpi_yoy", re.compile(r"^Core CPI y/y", re.I), +1),
    ("cpi_mom", re.compile(r"^CPI m/m", re.I), +1),
    ("cpi_yoy", re.compile(r"^CPI y/y", re.I), +1),
    ("nfp", re.compile(r"Non-Farm Employment Change", re.I), +1),
    ("unemployment", re.compile(r"^Unemployment Rate", re.I), -1),
    ("claims", re.compile(r"Unemployment Claims", re.I), -1),
    ("gdp", re.compile(r"GDP q/q", re.I), +1),
    ("core_pce", re.compile(r"Core PCE Price Index m/m", re.I), +1),
    ("pce_mom", re.compile(r"^PCE Price Index m/m", re.I), +1),
    ("ppi", re.compile(r"^PPI m/m", re.I), +1),
    ("core_ppi", re.compile(r"^Core PPI m/m", re.I), +1),
    ("core_retail", re.compile(r"^Core Retail Sales", re.I), +1),
    ("retail", re.compile(r"Retail Sales m/m", re.I), +1),
    ("ism_mfg", re.compile(r"ISM Manufacturing PMI", re.I), +1),
    ("speech", re.compile(r"Speaks|Testifies|Press Conference", re.I), 0),
]

CAT_BN = {
    "fed_decision": "FOMC রেট সিদ্ধান্ত", "fed_talk": "Fed বক্তব্য/মিনিটস",
    "core_cpi_mom": "কোর মূল্যস্ফীতি (মাসিক)", "core_cpi_yoy": "কোর মূল্যস্ফীতি (বার্ষিক)",
    "cpi_mom": "মূল্যস্ফীতি (মাসিক)", "cpi_yoy": "মূল্যস্ফীতি (বার্ষিক)",
    "nfp": "নন-ফার্ম পেরোল (চাকরি)", "unemployment": "বেকারত্বের হার",
    "claims": "সাপ্তাহিক বেকার ভাতা আবেদন", "gdp": "GDP প্রবৃদ্ধি", "core_pce": "কোর PCE মূল্যস্ফীতি",
    "ppi": "উৎপাদক মূল্যসূচক (PPI)", "core_ppi": "কোর PPI", "pce_mom": "PCE মূল্যস্ফীতি", "core_retail": "কোর খুচরা বিক্রি", "retail": "খুচরা বিক্রি", "ism_mfg": "ISM ম্যানুফ্যাকচারিং",
    "speech": "বক্তব্য", "other": "অর্থনৈতিক ডেটা",
}

# Rough historical first-hour reaction ranges (heuristic, NOT a forecast).
TYPICAL_RANGE = {
    "fed_decision": ("0.5–2%", "2–5%"), "cpi": ("0.5–1.5%", "1–3%"), "nfp": ("0.5–1.5%", "1–3%"),
    "fed_talk": ("0.2–0.8%", "0.5–2%"), "mid": ("0.2–0.6%", "0.5–1.5%"), "low": ("0.1–0.4%", "0.3–1%"),
}


def classify(title):
    for cat, rx, d in CATEGORIES:
        if rx.search(title):
            return cat, d
    low = title.lower()
    d = -1 if ("unemploy" in low or "claims" in low) else +1
    return "other", d


def range_key(cat, impact):
    if cat == "fed_decision":
        return "fed_decision"
    if cat in ("cpi_mom", "cpi_yoy", "core_cpi_mom", "core_cpi_yoy", "core_pce"):
        return "cpi"
    if cat in ("nfp", "unemployment"):
        return "nfp"
    if cat == "fed_talk":
        return "fed_talk"
    return "mid" if impact == "High" else "low"


# --------------------------------------------------------------------------- #
# Polymarket (Gamma API, public)
# --------------------------------------------------------------------------- #
POLY_RULES = {
    # category: (search query, title regex)
    "fed": ("Fed decision", re.compile(r"^Fed Decision in \w+\??$", re.I)),
    "cpi_mom": ("Inflation US Monthly", re.compile(r"Inflation US - Monthly", re.I)),
    "cpi_yoy": ("Inflation US Annual", re.compile(r"Inflation US - Annual", re.I)),
    "core_cpi_mom": ("Core CPI MoM", re.compile(r"^Core CPI MoM", re.I)),
    "core_cpi_yoy": ("Core CPI YoY", re.compile(r"^Core CPI YoY", re.I)),
    "nfp": ("jobs added", re.compile(r"How many jobs added in", re.I)),
    "unemployment": ("Unemployment Rate", re.compile(r"^\w+ Unemployment Rate$", re.I)),
    "gdp": ("US GDP growth", re.compile(r"^US GDP growth in Q\d", re.I)),
}
_poly_cache = {}


def poly_candidates(key):
    if key in _poly_cache:
        return _poly_cache[key]
    q, rx = POLY_RULES[key]
    data, status = get_json(POLY_SEARCH, {"q": q, "limit_per_type": 20, "events_status": "active"})
    evs = []
    for e in (data or {}).get("events", []) or []:
        if rx.search(e.get("title") or "") and not e.get("closed"):
            evs.append(e)
    _poly_cache[key] = (evs, status)
    return _poly_cache[key]


def poly_event_detail(slug):
    data, _ = get_json(POLY_EVENTS, {"slug": slug})
    return data[0] if isinstance(data, list) and data else None


def pick_by_time(cands, get_time, target, lo_h, hi_h):
    """Pick candidate whose time is within [target+lo_h, target+hi_h], nearest to target."""
    best, best_d = None, None
    for c in cands:
        t = get_time(c)
        if not t:
            continue
        dh = (t - target).total_seconds() / 3600
        if lo_h <= dh <= hi_h and (best_d is None or abs(dh) < best_d):
            best, best_d = c, abs(dh)
    return best


def polymarket_for(cat, ev_time):
    key = "fed" if cat in ("fed_decision", "fed_talk") else cat
    if key not in POLY_RULES:
        return None, "এই ধরনের নিউজের জন্য Polymarket মার্কেট নেই"
    cands, status = poly_candidates(key)
    if status != 200:
        return None, f"Polymarket API error (HTTP {status})"
    end = lambda e: parse_iso(e.get("endDate"))
    if cat == "fed_talk":   # minutes / speeches -> next upcoming meeting
        future = [c for c in cands if end(c) and end(c) > ev_time]
        ev = min(future, key=end) if future else None
    elif cat == "fed_decision":
        ev = pick_by_time(cands, end, ev_time, -24, 72)
    else:
        ev = pick_by_time(cands, end, ev_time, -12, 96)
    if not ev:
        return None, "মিলে যায় এমন সক্রিয় মার্কেট পাওয়া যায়নি"
    det = poly_event_detail(ev["slug"]) or ev
    outcomes = []
    for m in det.get("markets", []) or []:
        if m.get("closed") or not m.get("active", True):
            continue
        try:
            names = json.loads(m.get("outcomes") or "[]")
            prices = json.loads(m.get("outcomePrices") or "[]")
        except ValueError:
            continue
        if not names or not prices or names[0].lower() != "yes":
            continue
        p = fnum(prices[0])
        if p is None:
            continue
        label = m.get("groupItemTitle") or m.get("question") or ""
        is_fed = cat in ("fed_decision", "fed_talk")
        outcomes.append({"label": label, "prob": pct(p), "interval": None if is_fed else parse_bucket(label),
                         "volume": round(fnum(m.get("volume")) or 0)})
    if not outcomes:
        return None, "মার্কেটে দাম পাওয়া যায়নি"
    keep_order = cat not in ("fed_decision", "fed_talk")
    return {
        "source": "Polymarket",
        "title": det.get("title"),
        "url": f"https://polymarket.com/event/{det.get('slug')}",
        "closes_utc": det.get("endDate"),
        "volume_usd": round(fnum(det.get("volume")) or 0),
        "outcomes": outcomes if keep_order else sorted(outcomes, key=lambda o: -o["prob"]),
        "note": "Yes-দাম = বাজারের আনুমানিক সম্ভাবনা" + ("" if cat != "fed_talk" else " (পরবর্তী FOMC মিটিং)"),
    }, "ok"


# --------------------------------------------------------------------------- #
# Kalshi (public market data)
# --------------------------------------------------------------------------- #
KALSHI_SERIES = {
    "fed": "KXFEDDECISION", "cpi_mom": "KXCPI", "cpi_yoy": "KXCPIYOY",
    "core_cpi_mom": "KXCPICORE", "core_cpi_yoy": "KXCPICOREYOY", "nfp": "KXPAYROLLS",
    "unemployment": "KXU3", "gdp": "KXGDP", "claims": "KXJOBLESSCLAIMS",
    "core_pce": "KXPCECORE", "ppi": "KXUSPPI", "retail": "KXRETAIL", "ism_mfg": "KXISMPMI",
}
_kalshi_cache = {}


def kalshi_price(m):
    """Best estimate of P(yes) and whether the quote is thin.
    Tight book -> bid/ask mid. Wide book -> last trade (moves only on real trades, so thin
    markets don't flap between runs). Never traded -> mid if the spread is tolerable, else None."""
    bid, ask, last = (fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars")),
                      fnum(m.get("last_price_dollars")))
    traded = (fnum(m.get("volume_fp")) or 0) > 0 and last is not None and last > 0
    quoted = bid is not None and ask is not None and ask >= bid and (bid > 0 or ask > 0)
    spread = (ask - bid) if quoted else None
    if quoted and spread <= 0.10:
        return (bid + ask) / 2, False
    if traded:
        return last, True
    if quoted and spread <= 0.20:
        return (bid + ask) / 2, True
    return None, True


def kalshi_events(series):
    if series in _kalshi_cache:
        return _kalshi_cache[series]
    data, status = get_json(f"{KALSHI}/events", {"series_ticker": series, "status": "open",
                                                 "with_nested_markets": "true", "limit": 20})
    _kalshi_cache[series] = ((data or {}).get("events", []) or [], status)
    return _kalshi_cache[series]


def kalshi_close(e):
    ts = [parse_iso(m.get("close_time")) for m in e.get("markets", [])]
    ts = [t for t in ts if t]
    return min(ts) if ts else None


def fmt_strike(v, cat):
    if cat in ("nfp", "claims"):
        return f"{v/1000:g}K"
    return f"{v:g}%" if cat not in ("ism_mfg",) else f"{v:g}"


def kalshi_for(cat, ev_time):
    key = "fed" if cat in ("fed_decision", "fed_talk") else cat
    series = KALSHI_SERIES.get(key)
    if not series:
        return None, "এই ধরনের নিউজের জন্য Kalshi মার্কেট নেই"
    evs, status = kalshi_events(series)
    if status != 200:
        return None, f"Kalshi API error (HTTP {status})"
    if not evs:
        return None, "Kalshi-তে এখন খোলা মার্কেট নেই"
    if cat == "fed_talk":
        future = [e for e in evs if kalshi_close(e) and kalshi_close(e) > ev_time]
        ev = min(future, key=kalshi_close) if future else None
    elif cat == "fed_decision":
        ev = pick_by_time(evs, kalshi_close, ev_time, -24, 48)
    else:
        ev = pick_by_time(evs, kalshi_close, ev_time, -6, 24)
    if not ev:
        return None, "এই রিলিজের তারিখের সাথে মেলে এমন Kalshi মার্কেট নেই"
    mk = [m for m in ev.get("markets", []) if m.get("status") in ("active", "open", None)]
    outcomes, thin_n = [], 0
    if key == "fed":
        for m in mk:
            p, thin = kalshi_price(m)
            thin_n += thin
            if p is not None:
                outcomes.append({"label": m.get("yes_sub_title"), "prob": pct(p), "interval": None})
        outcomes.sort(key=lambda o: -o["prob"])
        kind = "buckets"
    else:
        th = []
        for m in mk:
            (p, thin), s = kalshi_price(m), fnum(m.get("floor_strike"))
            thin_n += thin
            if s is not None and p is not None and m.get("strike_type") in ("greater", "greater_or_equal"):
                th.append((s, p, m.get("strike_type")))
        th.sort()
        if len(th) < 2:
            return None, "Kalshi মার্কেটে দাম পাওয়া যায়নি"
        # enforce monotone non-increasing P(>s) to cancel stale-quote noise
        mono, run = [], 1.0
        for s, p, st in th:
            run = min(run, p)
            mono.append((s, run, st))
        ge = mono[0][2] == "greater_or_equal"
        # Bucket intervals. For 'greater' strikes, bucket (s_i, s_{i+1}]; for '>=', [s_i, s_{i+1}).
        outcomes.append({"label": f"{'<' if ge else '≤'} {fmt_strike(mono[0][0], cat)}",
                         "prob": pct(1 - mono[0][1]), "interval": [-math.inf, mono[0][0]], "open": "hi" if ge else "lo"})
        for (s1, p1, _), (s2, p2, _) in zip(mono, mono[1:]):
            outcomes.append({"label": (f"≥{fmt_strike(s1, cat)} ও <{fmt_strike(s2, cat)}" if ge
                                       else f">{fmt_strike(s1, cat)} ও ≤{fmt_strike(s2, cat)}"),
                             "prob": pct(p1 - p2), "interval": [s1, s2], "open": "hi" if ge else "lo"})
        outcomes.append({"label": f"{'≥' if ge else '>'} {fmt_strike(mono[-1][0], cat)}",
                         "prob": pct(mono[-1][1]), "interval": [mono[-1][0], math.inf], "open": "hi" if ge else "lo"})
        kind = "thresholds"
        ev["_thresholds"] = [{"strike": s, "prob_above": pct(p), "inclusive": st == "greater_or_equal"} for s, p, st in mono]
    if not outcomes:
        return None, "Kalshi মার্কেটে দাম পাওয়া যায়নি"
    return {
        "source": "Kalshi",
        "title": ev.get("title"),
        "url": f"https://kalshi.com/markets/{series.lower()}",
        "event_ticker": ev.get("event_ticker"),
        "closes_utc": iso_utc(kalshi_close(ev)) if kalshi_close(ev) else None,
        "kind": kind,
        "outcomes": outcomes,
        "thresholds": ev.get("_thresholds"),
        "low_liquidity": thin_n > len(mk) / 2,
        "note": "bid/ask মাঝামাঝি দাম (স্প্রেড বড় হলে শেষ ট্রেড)" + ("" if cat != "fed_talk" else " · পরবর্তী FOMC মিটিং"),
    }, "ok"


# --------------------------------------------------------------------------- #
# CME FedWatch
# --------------------------------------------------------------------------- #
_fedwatch_probe = None


def fedwatch_probe():
    global _fedwatch_probe
    if _fedwatch_probe is None:
        status, _ = http_get(FEDWATCH_URL, accept="text/html", retries=0, timeout=15)
        _fedwatch_probe = status
    return _fedwatch_probe


def fedwatch_for(cat, kalshi, poly):
    if cat not in ("fed_decision", "fed_talk"):
        return None
    st = fedwatch_probe()
    reason = ("CME সাইট অটোমেটেড অ্যাক্সেস ব্লক করে" + (f" (HTTP {st})" if st else "")
              + "; অফিসিয়াল FedWatch API পেইড। তাই সংখ্যা সরাসরি নেওয়া হয়নি — লিংকে গিয়ে মিলিয়ে নিন।")
    proxy = kalshi or poly
    return {
        "source": "CME FedWatch",
        "url": FEDWATCH_URL,
        "available": False,
        "reason": reason,
        "proxy_source": proxy["source"] if proxy else None,
        "proxy_outcomes": proxy["outcomes"][:3] if proxy else None,
    }


# --------------------------------------------------------------------------- #
# Probability summary vs forecast
# --------------------------------------------------------------------------- #
def side_of(interval, open_side, f):
    """Classify a bucket vs forecast f: 'above' | 'inline' | 'below'."""
    lo, hi = interval
    eps = 1e-9 * max(1.0, abs(f))
    if open_side == "lo":      # (lo, hi]
        if lo >= f - eps and lo != -math.inf:
            return "above"
        if hi < f - eps:
            return "below"
        return "inline"
    if open_side == "hi":      # [lo, hi)
        if lo > f + eps:
            return "above"
        if hi <= f + eps and hi != math.inf:
            return "below"
        return "inline"
    if lo > f + eps:           # closed [lo, hi]
        return "above"
    if hi < f - eps:
        return "below"
    return "inline"


def vs_forecast(src, forecast):
    if not src or forecast is None:
        return None
    acc = {"above": 0.0, "inline": 0.0, "below": 0.0}
    n = 0
    for o in src["outcomes"]:
        iv = o.get("interval")
        if not iv or o.get("prob") is None:
            continue
        acc[side_of(iv, o.get("open"), forecast)] += o["prob"]
        n += 1
    tot = sum(acc.values())
    if n < 2 or tot <= 0:
        return None
    return {k: round(v / tot * 100, 1) for k, v in acc.items()}  # normalised to 100


def fed_view(src):
    """Collapse Fed bucket outcomes into hike / hold / cut probabilities."""
    if not src:
        return None
    acc = {"hike": 0.0, "hold": 0.0, "cut": 0.0}
    for o in src["outcomes"]:
        lab = (o["label"] or "").lower()
        if "hike" in lab or "increase" in lab:
            acc["hike"] += o["prob"]
        elif "cut" in lab or "decrease" in lab:
            acc["cut"] += o["prob"]
        elif "maintain" in lab or "no change" in lab or "hold" in lab:
            acc["hold"] += o["prob"]
    tot = sum(acc.values())
    return {k: round(v / tot * 100, 1) for k, v in acc.items()} if tot > 0 else None


def json_safe(o):
    """Replace ±inf by None so output is strict JSON."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    return o


def build_probabilities(cal):
    by_event, sources = {}, {"polymarket": "ok", "kalshi": "ok", "cme_fedwatch": None}
    targets = [e for e in cal.get("events", [])
               if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
               and parse_iso(e["time_utc"]) and parse_iso(e["time_utc"]) > NOW - timedelta(days=2)]
    for e in targets:
        cat, d = classify(e["title"])
        t = parse_iso(e["time_utc"])
        poly, pnote = polymarket_for(cat, t)
        kal, knote = kalshi_for(cat, t)
        if "API error" in pnote:
            sources["polymarket"] = pnote
        if "API error" in knote:
            sources["kalshi"] = knote
        fw = fedwatch_for(cat, kal, poly)
        if fw:
            sources["cme_fedwatch"] = fw["reason"]
        ref_kind = "forecast" if parse_value(e["forecast"]) is not None else (
            "previous" if parse_value(e["previous"]) is not None else None)
        ref_raw = e[ref_kind] if ref_kind else ""
        f = parse_value(ref_raw)
        entry = {
            "event_id": e["id"], "title": e["title"], "category": cat, "category_bn": CAT_BN[cat],
            "usd_dir": d, "time_utc": e["time_utc"],
            "polymarket": poly, "polymarket_note": None if poly else pnote,
            "kalshi": kal, "kalshi_note": None if kal else knote,
            "fedwatch": fw,
            "vs_forecast": {"ref": ref_kind, "ref_value": ref_raw or None,
                            "polymarket": vs_forecast(poly, f), "kalshi": vs_forecast(kal, f)},
            "most_likely": {k: (max(v["outcomes"], key=lambda o: o["prob"]) if v else None)
                            for k, v in (("polymarket", poly), ("kalshi", kal))},
            "fed_view": ({"polymarket": fed_view(poly), "kalshi": fed_view(kal)}
                         if cat in ("fed_decision", "fed_talk") else None),
        }
        by_event[e["id"]] = json_safe(entry)
    return {"generated_at": iso_utc(NOW), "sources": sources, "by_event": by_event}


# --------------------------------------------------------------------------- #
# Bangla briefs (rule-based)
# --------------------------------------------------------------------------- #
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bn(s):
    return str(s).translate(BN_DIGITS)


def impact_lines(usd_up, strength="normal"):
    """Market impact if the USD-bullish (usd_up=True) or USD-bearish scenario plays out."""
    if usd_up is None:
        return {
            "gold": "দিক অনিশ্চিত — দুই দিকেই স্পাইক হতে পারে, রিলিজের পর প্রথম ক্যান্ডেল দেখে সিদ্ধান্ত নিন।",
            "btc": "ভোলাটিলিটি বাড়তে পারে; লিকুইডেশন উইক দুই দিকেই সম্ভব।",
            "crypto": "অল্টকয়েন BTC-কে ফলো করবে, বেশি দুলতে পারে।",
            "forex": "DXY ও মেজর পেয়ারে (EURUSD, GBPUSD, USDJPY) সাময়িক হুইপস।",
        }
    if usd_up:
        return {
            "gold": "নিম্নমুখী চাপ (ডলার ও বন্ড ইল্ড বাড়লে গোল্ড সাধারণত নামে)।",
            "btc": "বেয়ারিশ চাপ — রিস্ক-অফ, ডাম্প/উইক নিচে যাওয়ার সম্ভাবনা বেশি।",
            "crypto": "অল্টকয়েনে বড় পতনের ঝুঁকি (BTC-এর চেয়ে বেশি %)।",
            "forex": "DXY ↑ · EURUSD/GBPUSD ↓ · USDJPY ↑",
        }
    return {
        "gold": "ঊর্ধ্বমুখী সাপোর্ট (দুর্বল ডলার ও কম ইল্ডে গোল্ড সাধারণত বাড়ে)।",
        "btc": "বুলিশ — রিস্ক-অন, পাম্প হওয়ার সম্ভাবনা বেশি।",
        "crypto": "অল্টকয়েন BTC-এর চেয়ে বেশি % উঠতে পারে।",
        "forex": "DXY ↓ · EURUSD/GBPUSD ↑ · USDJPY ↓",
    }


def scenario_label(side, d):
    """side: 'above'|'below'|'inline' vs forecast; d: usd_dir."""
    if side == "inline":
        return None
    up = (side == "above")
    return up if d >= 0 else (not up)


def brief_for_group(group, probs):
    lead = max(group, key=lambda e: (e["impact"] == "High",
                                     probs.get(e["id"], {}).get("kalshi") is not None
                                     or probs.get(e["id"], {}).get("polymarket") is not None,
                                     e["forecast"] != ""))
    p = probs.get(lead["id"], {})
    cat, d = classify(lead["title"])
    f, pv = parse_value(lead["forecast"]), parse_value(lead["previous"])
    t = parse_iso(lead["time_utc"])
    reasons, likely_text, likely_up, alt_up, conf = [], "", None, None, "নিম্ন"

    if cat in ("fed_decision", "fed_talk"):
        fv = (p.get("fed_view") or {})
        view = fv.get("kalshi") or fv.get("polymarket")
        src = "Kalshi" if fv.get("kalshi") else "Polymarket"
        if view:
            top = max(view, key=view.get)
            name = {"hike": "রেট বাড়ানো (হাইক)", "hold": "রেট অপরিবর্তিত (হোল্ড)", "cut": "রেট কমানো (কাট)"}[top]
            reasons.append(f"{src}: হাইক {bn(view['hike'])}% · হোল্ড {bn(view['hold'])}% · কাট {bn(view['cut'])}%"
                           + (" (পরবর্তী মিটিং)" if cat == "fed_talk" else ""))
            hawk_skew = view["hike"] - view["cut"]
            if cat == "fed_decision":
                likely_text = f"{name} — বাজারের সম্ভাবনা {bn(view[top])}%"
                likely_up = True if top == "hike" else (False if top == "cut" else (hawk_skew > 5 or None))
                if top == "hold":
                    likely_up = None if abs(hawk_skew) <= 5 else hawk_skew > 0
                alt_up = (not likely_up) if likely_up is not None else (hawk_skew <= 0)
            else:
                tone = "হকিশ (কড়া)" if hawk_skew > 5 else ("ডোভিশ (নরম)" if hawk_skew < -5 else "নিরপেক্ষ")
                likely_text = (f"বাজারের ঝোঁক {tone} দিকে — পরবর্তী মিটিংয়ে {name} {bn(view[top])}%, "
                               f"হাইক {bn(view['hike'])}% বনাম কাট {bn(view['cut'])}%")
                likely_up = True if hawk_skew > 5 else (False if hawk_skew < -5 else None)
                alt_up = (not likely_up) if likely_up is not None else None
            conf = "মাঝারি" if view[top] >= 70 else "নিম্ন"
        else:
            likely_text = "বাজারের প্রোবাবিলিটি ডেটা নেই — দিক অনিশ্চিত"
    else:
        vf = (p.get("vs_forecast") or {})
        dist = vf.get("kalshi") or vf.get("polymarket")
        src = "Kalshi" if vf.get("kalshi") else "Polymarket"
        ref_bn = "Forecast" if vf.get("ref") == "forecast" else "আগের মান"
        ref_val = vf.get("ref_value") or ""
        if dist:
            reasons.append(f"{src}: {ref_bn}-এর চেয়ে বেশি {bn(dist['above'])}% · সমান/কাছাকাছি {bn(dist['inline'])}% · কম {bn(dist['below'])}%")
            ml = (p.get("most_likely") or {}).get("kalshi" if src == "Kalshi" else "polymarket")
            if ml:
                reasons.append(f"{src}-এ সবচেয়ে সম্ভাব্য রেঞ্জ: {ml['label']} ({bn(ml['prob'])}%)")
            skew = dist["above"] - dist["below"]
            if abs(skew) < 10:
                likely_text = f"{ref_bn} ({ref_val})-এর কাছাকাছি — সারপ্রাইজের সম্ভাবনা দুই দিকেই প্রায় সমান"
            else:
                side = "above" if skew > 0 else "below"
                likely_text = (f"{ref_bn} ({ref_val})-এর চেয়ে {'বেশি' if side == 'above' else 'কম'} আসার দিকে ঝোঁক "
                               f"({bn(max(dist['above'], dist['below']))}%)")
                likely_up = scenario_label(side, d)
                alt_up = None if likely_up is None else not likely_up
            conf = "মাঝারি" if max(dist.values()) >= 55 else "নিম্ন"
        elif f is not None:
            likely_text = f"Forecast ({lead['forecast']})-এর কাছাকাছি (বাজারের প্রোবাবিলিটি ডেটা নেই)"
            if pv is not None and f != pv:
                side = "above" if f > pv else "below"
                reasons.append(f"Forecast আগের মানের ({lead['previous']}) চেয়ে {'বেশি' if f > pv else 'কম'} — ট্রেন্ড {'উপরে' if f > pv else 'নিচে'}")
                likely_up = scenario_label(side, d)
                alt_up = None if likely_up is None else not likely_up
        elif cat == "speech":
            likely_text = "নির্ধারিত সংখ্যা নেই — বক্তব্যের টোনের ওপর হঠাৎ মুভ হতে পারে"
        else:
            likely_text = "Forecast নেই — দিক অনিশ্চিত, শুধু ভোলাটিলিটির জন্য সতর্ক থাকুন"
        if pv is not None and f is not None and not any("আগের মানের" in r for r in reasons):
            reasons.append(f"Forecast বনাম আগের মান: {lead['forecast']} বনাম {lead['previous']}")

    if cat == "speech":
        alt_text = "অপ্রত্যাশিত মন্তব্য (ট্যারিফ, Fed, ডলার নিয়ে) এলে — দিক অনুযায়ী"
    elif alt_up is None:
        alt_text = "প্রত্যাশা থেকে বড় বিচ্যুতি হলে — দিক অনুযায়ী"
    else:
        alt_text = "বিপরীত ফলাফল এলে"
    gr, br = TYPICAL_RANGE[range_key(cat, lead["impact"])]
    return {
        "id": lead["id"],
        "time_utc": lead["time_utc"],
        "time_dhaka": dhaka_str(t),
        "impact": lead["impact"],
        "title": lead["title"],
        "category_bn": CAT_BN[cat],
        "events": [{"id": e["id"], "title": e["title"], "impact": e["impact"], "forecast": e["forecast"],
                    "previous": e["previous"], "actual": e["actual"]} for e in group],
        "likely": {"text": likely_text, "usd_bias": None if likely_up is None else ("up" if likely_up else "down"),
                   "impact": impact_lines(likely_up)},
        "alternative": {"text": alt_text, "usd_bias": None if alt_up is None else ("up" if alt_up else "down"),
                        "impact": (impact_lines(alt_up) if alt_up is not None else
                                   {"usd_up": impact_lines(True), "usd_down": impact_lines(False)})},
        "reasons": reasons,
        "confidence_bn": conf,
        "typical_range": {"gold": gr, "btc": br,
                          "note": "আনুমানিক ঐতিহাসিক প্রথম-ঘণ্টার মুভ — পূর্বাভাস নয়"},
        "disclaimer": "এটি সম্ভাবনাভিত্তিক বিশ্লেষণ, কোনো গ্যারান্টি নয়। রিলিজের সময় স্প্রেড ও স্লিপেজ বাড়ে — SL ছাড়া ট্রেড নয়।",
    }


def align_brief(b, a):
    """Make a Dashboard brief say exactly what the analysis verdict / scenario 1 says."""
    v, sc = a.get("verdict") or {}, a.get("scenarios") or []
    b["likely"]["text"] = v.get("summary") or b["likely"]["text"]
    b["confidence_bn"] = v.get("confidence") or b.get("confidence_bn")
    b["reasons"] = list(v.get("basis") or [])
    if v.get("context"):
        b["reasons"].append(v["context"]["text"])
    if v.get("top") and sc:
        s0 = sc[0]
        b["likely"]["usd_bias"] = s0["usd_bias"]
        b["likely"]["impact"] = {k: s0["markets"][k]["text"] for k in ("gold", "btc", "crypto", "forex")}
        b["alternative"] = {"text": f"{sc[1]['title']}: {sc[1]['condition']}", "usd_bias": sc[1]["usd_bias"],
                            "prob": sc[1]["prob"],
                            "impact": {k: sc[1]["markets"][k]["text"] for k in ("gold", "btc", "crypto", "forex")}}
    else:
        b["likely"]["usd_bias"] = None
    b["verdict_top"] = v.get("top")
    return b


def build_briefs(cal, prob, analysis=None):
    probs = prob.get("by_event", {})
    end = NOW + timedelta(hours=BRIEF_WINDOW_H)
    upcoming = [e for e in cal.get("events", [])
                if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
                and NOW <= parse_iso(e["time_utc"]) <= end]
    groups = {}
    for e in upcoming:
        groups.setdefault(e["time_utc"], []).append(e)
    briefs = [brief_for_group(g, probs) for _, g in sorted(groups.items())]
    an = (analysis or {}).get("events", {})
    briefs = [align_brief(b, an[b["id"]]) if b["id"] in an else b for b in briefs]
    today = NOW.astimezone(DHAKA).date()
    today_major = [e for e in cal.get("events", [])
                   if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
                   and parse_iso(e["time_utc"]).astimezone(DHAKA).date() == today]
    nxt = next((e for e in cal.get("events", [])
                if e["currency"] == "USD" and e["impact"] == "High" and parse_iso(e["time_utc"]) > NOW), None)
    return {
        "generated_at": iso_utc(NOW),
        "window_hours": BRIEF_WINDOW_H,
        "has_major_today": bool(today_major),
        "has_briefs": bool(briefs),
        "empty_message": None if briefs else "আজ বড় কোনো USD নিউজ নেই",
        "next_high": ({"id": nxt["id"], "title": nxt["title"], "time_utc": nxt["time_utc"],
                       "time_dhaka": dhaka_str(parse_iso(nxt["time_utc"]))} if nxt else None),
        "briefs": briefs,
    }


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# ForexFactory event specs (cached by ebase id) + release history
# --------------------------------------------------------------------------- #
_TAG = re.compile(r"<[^>]+>")


def _text(html):
    t = _TAG.sub(" ", (html or "").replace("<br>", " "))
    t = re.sub(r"\s+", " ", t).strip().rstrip(";")
    return t.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")


def fetch_specs(ev):
    """FF details JSON for one event -> {specs:{title:text}, source_url, history:[...]} or None."""
    if not ev.get("ff_id"):
        return None
    data, status = None, None
    st, body = http_get(FF_DETAILS.format(id=ev["ff_id"]), accept="application/json")
    if st == 200 and body:
        try:
            data = json.loads(body.decode("utf-8", "replace")).get("data")
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return None
    specs, src_url = {}, None
    for sp in data.get("specs", []) or []:
        title = _text(sp.get("title"))
        if not title:
            continue
        specs[title] = _text(sp.get("html"))
        if title == "Source" and not src_url:
            m = re.search(r'href="(https?://[^"]+)"', sp.get("html") or "")
            src_url = m.group(1) if m else None
    hist = []
    for h in ((data.get("history") or {}).get("events") or [])[:12]:
        hist.append({"date": h.get("date"), "actual": h.get("actual") or "", "forecast": h.get("forecast") or "",
                     "previous": h.get("previous") or "", "abw": h.get("actualBetterWorse") or 0})
    return {"title": ev["title"], "fetched_at": iso_utc(NOW), "specs": specs, "source_url": src_url, "history": hist}


def update_specs(events, page_ok):
    cache = load_json("ff_specs.json", {"by_ebase": {}})
    by = cache.setdefault("by_ebase", {})
    todo = []
    for e in events:
        k = str(e.get("ebase") or "")
        if not k or not e.get("ff_id"):
            continue
        cur = by.get(k)
        age = NOW - (parse_iso((cur or {}).get("fetched_at")) or datetime(2000, 1, 1, tzinfo=UTC))
        if (cur is None or age > timedelta(days=SPECS_MAX_AGE_D)) and k not in {str(t["ebase"]) for t in todo}:
            todo.append(e)
    fetched = 0
    if page_ok:
        for e in todo[:SPECS_PER_RUN]:
            time.sleep(1.5)
            sp = fetch_specs(e)
            if sp is None:
                log(f"specs: stopped at {e['title']}")
                break
            by[str(e["ebase"])] = sp
            fetched += 1
    if fetched:
        cache["generated_at"] = iso_utc(NOW)
        write_json("ff_specs.json", cache)
        log(f"ff_specs.json: +{fetched} event types")
    return by


def load_archive():
    evs = []
    for k in stored_weeks():
        evs.extend(load_json(f"weeks/{k}.json", {}).get("events", []))
    return evs


def history_for(ev, archive, specs_entry, n=6):
    """Last n released values: stored weeks first (fresh), FF details history fills older gaps."""
    t0 = parse_iso(ev["time_utc"])
    same = [a for a in archive
            if a.get("actual") and parse_iso(a["time_utc"]) and parse_iso(a["time_utc"]) < t0
            and ((ev.get("ebase") and a.get("ebase") == ev.get("ebase"))
                 or (a["currency"] == ev["currency"] and a["title"] == ev["title"]))]
    seen, out = set(), []
    for a in sorted(same, key=lambda x: x["time_utc"], reverse=True):
        d = parse_iso(a["time_utc"]).astimezone(DHAKA).date().isoformat()
        if d in seen:
            continue
        seen.add(d)
        out.append({"date": d, "actual": a["actual"], "forecast": a.get("forecast") or "",
                    "previous": a.get("previous") or "", "abw": a.get("abw") or 0})
    for h in (specs_entry or {}).get("history", []):
        try:
            d = datetime.strptime(h["date"], "%b %d, %Y").date().isoformat()
        except (TypeError, ValueError):
            continue
        if d in seen or not h.get("actual") or d >= t0.date().isoformat():
            continue
        seen.add(d)
        out.append({**h, "date": d})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:n]


# --------------------------------------------------------------------------- #
# Per-event analysis
# --------------------------------------------------------------------------- #
FED_CATS = ("fed_decision", "fed_talk")
SIDE_BN = {"above": "বেশি", "inline": "কাছাকাছি/সমান", "below": "কম"}
SOURCE_HEALTH = {}    # name -> "ok" | reason; nowcast sources touched this run (reported in status.json)


def side_text(ref_bn, side):
    return f"{ref_bn} কাছাকাছি/সমান" if side == "inline" else f"{ref_bn} চেয়ে {SIDE_BN[side]}"


def decimals_of(s):
    m = re.search(r"\d+\.(\d+)", s or "")
    return len(m.group(1)) if m else 0


def usual_dir(specs_entry, fallback):
    eff = ((specs_entry or {}).get("specs") or {}).get("Usual Effect", "").lower()
    if "greater than" in eff and "good" in eff:
        return +1
    if "less than" in eff and "good" in eff:
        return -1
    return fallback


def avg_dists(dists):
    dists = [d for d in dists if d]
    if not dists:
        return None
    keys = dists[0].keys()
    return {k: round(sum(d[k] for d in dists) / len(dists), 1) for k in keys}


def normalize_probs(d):
    """Scale to exactly 100.0 (1 decimal); rounding remainder goes to the largest bucket."""
    tot = sum(v for v in d.values() if v is not None)
    if tot <= 0:
        return None
    out = {k: round(v / tot * 100, 1) for k, v in d.items()}
    big = max(out, key=out.get)
    out[big] = round(out[big] + (100.0 - sum(out.values())), 1)
    return out


def verdict_summary(v):
    """One Bangla line used by the Dashboard and briefs — always consistent with the scenario order."""
    top, sec, L = v.get("top"), v.get("second"), v.get("labels") or {}
    if not top:
        return "বাজারের সম্ভাবনা-ডেটা নেই — দুই দিকেই মুভ হতে পারে; রিলিজের পর প্রথম ক্যান্ডেল দেখে সিদ্ধান্ত নিন"
    neutral = top["key"] in ("inline", "neutral")
    if top.get("pct") is not None:
        if neutral:
            return (f"{L[top['key']]} সবচেয়ে সম্ভাব্য — {bn(top['pct'])}%; সারপ্রাইজ হলে বেশি সম্ভাব্য দিক: "
                    f"{sec['label']} {bn(sec['pct'])}%, উল্টো দিক {bn(v['third']['pct'])}%")
        return (f"সবচেয়ে সম্ভাব্য: {top['label']} — {bn(top['pct'])}%; দ্বিতীয়: {sec['label']} {bn(sec['pct'])}%")
    if v.get("kind") == "tone":
        return (f"আনুমানিক ঝোঁক: {top['label']}" + (f"; সারপ্রাইজ হলে {sec['label']}-এর ঝুঁকি বেশি" if sec else "")
                + " (টোনের সরাসরি বাজার নেই, % দেওয়া হচ্ছে না)")
    return f"ঝোঁক: {top['label']} (বাজারের % নেই — নিশ্চয়তা কম)"


def assert_consistent(verdict, sc):
    """Guard: scenario 1 must be the verdict's top outcome and %s must match; raise loudly in tests/CI."""
    top = verdict.get("top")
    if top:
        if sc[0]["key"] != top["key"]:
            raise AssertionError(f"scenario order {[x['key'] for x in sc]} vs verdict top {top['key']}")
        probs = verdict.get("probs") or {}
        for x in sc:
            if probs and x["prob"] != probs.get(x["key"]):
                raise AssertionError(f"scenario {x['key']} prob {x['prob']} != verdict {probs.get(x['key'])}")
        if probs:
            ps = [x["prob"] for x in sc]
            if ps[0] < max(ps) or (sc[0]["key"] in ("inline", "neutral") and ps[1] < ps[2]):
                raise AssertionError(f"scenarios not ordered by probability: {ps}")
    elif any(x["is_overall_top"] for x in sc):
        raise AssertionError("a scenario is called most likely without any basis")


def market_source(name, src, vf, fv, generated_at, context_only=False):
    ml = max(src["outcomes"], key=lambda o: o["prob"]) if src and src.get("outcomes") else None
    return {
        "name": name, "kind": "market", "ok": True, "url": src.get("url"), "title": src.get("title"),
        "updated_at": generated_at, "closes_utc": src.get("closes_utc"),
        "headline": (f"সবচেয়ে সম্ভাব্য: {ml['label']} — {bn(ml['prob'])}%" if ml else None),
        "outcomes": [o for o in src["outcomes"] if o.get("prob") is not None][:14],
        "kind_detail": src.get("kind"), "vs_forecast": vf, "fed_view": fv,
        "low_liquidity": bool(src.get("low_liquidity")),
        "context_only": context_only,
    }


def build_analysis(cal, prob, specs_by, archive):
    today = NOW.astimezone(DHAKA).date()
    pevents = prob.get("by_event", {})
    out, order = {}, []
    evs = [e for e in cal.get("events", [])
           if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
           and parse_iso(e["time_utc"]).astimezone(DHAKA).date() >= today]
    for e in evs:
        t = parse_iso(e["time_utc"])
        cat, cdir = classify(e["title"])
        sp = specs_by.get(str(e.get("ebase") or ""))
        d = usual_dir(sp, cdir)
        ex = explainers.explain(e["title"])
        p = pevents.get(e["id"], {})
        f_raw = e.get("forecast") or ""
        ref_kind = "forecast" if parse_value(f_raw) is not None else (
            "previous" if parse_value(e.get("previous")) is not None else None)
        ref_raw = e.get(ref_kind) if ref_kind else ""
        ref_val = parse_value(ref_raw)
        ref_bn = "Forecast" if ref_kind == "forecast" else "আগের মান"
        ref_of = "Forecast-এর" if ref_kind == "forecast" else "আগের মানের"

        # ---- sources
        sources = []
        vf = p.get("vs_forecast") or {}
        fv = p.get("fed_view") or {}
        for name, key in (("Polymarket", "polymarket"), ("Kalshi", "kalshi")):
            src = p.get(key)
            if src:
                sources.append(market_source(name, src, vf.get(key), fv.get(key), prob.get("generated_at"),
                                             context_only=cat == "fed_talk"))
            else:
                sources.append({"name": name, "kind": "market", "ok": False,
                                "note": p.get(f"{key}_note") or "মিলে যায় এমন মার্কেট নেই"})
        nc = None
        if cat in nowcasts.CLE_SERIES:
            nc, why = nowcasts.cleveland(cat, t, http_get)
            SOURCE_HEALTH["cleveland_fed"] = "ok" if nc else why
            sources.append({"name": "Cleveland Fed Nowcast", "kind": "nowcast", "ok": bool(nc), **(nc or {"note": why})})
        if cat == "gdp":
            nc, why = nowcasts.gdpnow(http_get)
            SOURCE_HEALTH["gdpnow"] = "ok" if nc else why
            sources.append({"name": "Atlanta Fed GDPNow", "kind": "nowcast", "ok": bool(nc), **(nc or {"note": why})})
        if cat in FED_CATS:
            fw = p.get("fedwatch") or {}
            sources.append({"name": "CME FedWatch", "kind": "link", "ok": False, "url": FEDWATCH_URL,
                            "note": fw.get("reason") or "CME সাইট স্ক্রিপ্ট ব্লক করে; অফিসিয়াল API পেইড — লিংকে দেখুন।"})
        if f_raw:
            sources.append({"name": "ForexFactory Forecast (কনসেনসাস)", "kind": "consensus", "ok": True,
                            "headline": f"বাজারের সাধারণ প্রত্যাশা: {f_raw}", "updated_at": cal.get("generated_at"),
                            "url": f"https://www.forexfactory.com/calendar?day={week_param(t.astimezone(FF_TZ).date().isoformat())}"})

        # ---- nowcast lean
        nc_side = None
        if nc and ref_val is not None:
            v = round(nc["value"], decimals_of(ref_raw))
            nc_side = "above" if v > ref_val else ("below" if v < ref_val else "inline")
            nc["vs_ref"] = nc_side
            nc["vs_ref_text"] = side_text(f"{ref_bn} ({ref_raw})-এর", nc_side)

        # ---- verdict (the scenario tabs, section ঘ and the Dashboard all read from this one object)
        if cat == "fed_decision":
            kind = "fed"
        elif cat == "fed_talk":
            kind = "tone"     # minutes / Fed speakers: the outcome is a tone, not a rate decision
        else:
            # a scheduled number (even before its Forecast is out) is still a data release
            hist = history_for(e, archive, sp)
            kind = "data" if (ref_val is not None or hist) and ex["theme"] != "speech" else "speech"
        thin = any(s.get("low_liquidity") for s in sources if s.get("ok"))
        markets_ok = [s for s in sources if s.get("kind") == "market" and s.get("ok")]
        verdict = {"kind": kind, "probs": None, "top": None, "second": None, "basis": [], "confidence": "ডেটা নেই",
                   "context": None, "labels": None}
        lean, dir_hint = None, None
        if kind == "fed":
            v = avg_dists([s["fed_view"] for s in markets_ok if s.get("fed_view")])
            verdict["labels"] = {"hawk": "রেট হাইক (হকিশ)", "neutral": "নিউট্রাল — রেট হোল্ড", "dove": "রেট কাট (ডোভিশ)"}
            if v:
                verdict["probs"] = normalize_probs({"hawk": v["hike"], "neutral": v["hold"], "dove": v["cut"]})
                verdict["basis"].append(f"{' + '.join(s['name'] for s in markets_ok if s.get('fed_view'))} — এই মিটিংয়ের সিদ্ধান্ত-মার্কেট")
        elif kind == "tone":
            verdict["labels"] = {"hawk": "হকিশ টোন", "neutral": "নিউট্রাল — ভারসাম্যপূর্ণ টোন", "dove": "ডোভিশ টোন"}
            v = avg_dists([s["fed_view"] for s in markets_ok if s.get("fed_view")])
            if v:
                ctx_p = normalize_probs({"hike": v["hike"], "hold": v["hold"], "cut": v["cut"]})
                verdict["context"] = {
                    **ctx_p,
                    "text": (f"রেট-মার্কেট প্রসঙ্গ (পরবর্তী FOMC মিটিং): হোল্ড {bn(ctx_p['hold'])}% · হাইক {bn(ctx_p['hike'])}% · "
                             f"কাট {bn(ctx_p['cut'])}% — এটি এই ইভেন্টের ফলাফলের সম্ভাবনা নয়, শুধু প্রেক্ষাপট।"),
                    "sources": [s["name"] for s in markets_ok if s.get("fed_view")],
                }
                dir_hint = "hawk" if ctx_p["hike"] >= ctx_p["cut"] else "dove"
                lean = "neutral" if ctx_p["hold"] >= 60 else dir_hint
                verdict["basis"].append("টোনের সরাসরি কোনো বাজার নেই — রেট-মার্কেট শুধু প্রেক্ষাপট")
                verdict["basis"].append("হোল্ড প্রত্যাশা প্রবল হলে ভারসাম্যপূর্ণ টোনই স্বাভাবিক; সারপ্রাইজ হলে "
                                        + ("হকিশ" if dir_hint == "hawk" else "ডোভিশ") + " দিকের ঝুঁকি বেশি দাম পাচ্ছে")
        elif kind == "data":
            v = avg_dists([s["vs_forecast"] for s in markets_ok if s.get("vs_forecast")])
            if v:
                verdict["probs"] = normalize_probs(v)
                verdict["basis"].append(f"{' + '.join(s['name'] for s in markets_ok if s.get('vs_forecast'))} — {ref_bn} {ref_raw}-এর তুলনায়")
            verdict["labels"] = {"above": side_text(ref_of, "above"), "below": side_text(ref_of, "below"),
                                 "inline": "নিউট্রাল — " + side_text(ref_of, "inline")}
            if nc_side:
                verdict["basis"].append(f"{nc['source']}: {nc['value']}% → {nc['vs_ref_text']}")
                if not v:
                    lean = nc_side
            if not v and not nc_side and ref_kind == "forecast" and parse_value(e.get("previous")) is not None:
                pv = parse_value(e.get("previous"))
                if ref_val != pv:
                    lean = "above" if ref_val > pv else "below"
                    verdict["basis"].append(f"Forecast আগের মানের চেয়ে {'বেশি' if ref_val > pv else 'কম'} — শুধু ট্রেন্ড-ঝোঁক")
        else:
            verdict["labels"] = {"hawk": "ডলার-সহায়ক মন্তব্য", "neutral": "নিউট্রাল — নতুন কিছু নয়", "dove": "ডলার-বিরোধী মন্তব্য"}
        probs = verdict["probs"]
        L = verdict["labels"] or {}
        if probs:
            ranked = sorted(probs.items(), key=lambda kv: -kv[1])
            verdict["top"] = {"key": ranked[0][0], "label": L[ranked[0][0]], "pct": ranked[0][1]}
            verdict["second"] = {"key": ranked[1][0], "label": L[ranked[1][0]], "pct": ranked[1][1]}
            verdict["third"] = {"key": ranked[2][0], "label": L[ranked[2][0]], "pct": ranked[2][1]}
            verdict["total"] = round(sum(probs.values()), 1)
            n_src = len([1 for s in markets_ok if s.get("vs_forecast") or s.get("fed_view")])
            if thin or n_src == 1 or ranked[0][1] < 50:
                verdict["confidence"] = "নিম্ন"
            elif n_src >= 2 and ranked[0][1] >= 65:
                verdict["confidence"] = "মাঝারি–উচ্চ"
            else:
                verdict["confidence"] = "মাঝারি"
            if thin:
                verdict["basis"].append("কিছু মার্কেটে লিকুইডিটি কম — সংখ্যা কম নির্ভরযোগ্য")
            if nc_side and kind == "data":
                agree = nc_side == ranked[0][0]
                verdict["basis"].append("নাউকাস্ট বাজারের সবচেয়ে সম্ভাব্য ফলাফলের সাথে " + ("একমত" if agree else "একমত নয় — সতর্ক থাকুন"))
        elif lean:
            verdict["top"] = {"key": lean, "label": L[lean], "pct": None}
            if lean in ("neutral", "inline") and dir_hint:
                verdict["second"] = {"key": dir_hint, "label": L[dir_hint], "pct": None}
            verdict["confidence"] = "নিম্ন" + (" — টোনের সরাসরি বাজার নেই" if kind == "tone" else " — বাজারের % নেই")
        verdict["summary"] = verdict_summary(verdict)

        # ---- outcome (after release)
        result = None
        a = parse_value(e.get("actual"))
        if a is not None and ref_val is not None and ref_kind == "forecast":
            side = "above" if a > ref_val else ("below" if a < ref_val else "inline")
            result = {"side": side, "text": f"Actual {e['actual']} — " + side_text(f"Forecast ({f_raw})-এর", side),
                      "abw": e.get("abw") or 0}

        sc = scenarios.build_scenarios(
            theme=ex["theme"], impact=e["impact"], title=e["title"],
            kind=kind, usd_dir=d, forecast=ref_raw, ref_label=ref_bn, probs=probs, lean=lean, dir_hint=dir_hint,
            has_numbers=kind == "data", is_decision=cat == "fed_decision")
        assert_consistent(verdict, sc)
        if result:
            for x in sc:
                x["happened"] = x["key"] == result["side"]

        out[e["id"]] = json_safe({
            "id": e["id"], "title": e["title"], "currency": e["currency"], "impact": e["impact"],
            "time_utc": e["time_utc"], "time_special": e.get("time_special"), "category": cat, "dir": d,
            "explainer": {**ex, "ff_specs": (sp or {}).get("specs"), "source_url": (sp or {}).get("source_url")},
            "data": {"actual": e.get("actual") or "", "forecast": f_raw, "previous": e.get("previous") or "",
                     "revision": e.get("revision") or "", "abw": e.get("abw") or 0, "result": result},
            "history": history_for(e, archive, sp),
            "sources": sources,
            "verdict": verdict,
            "scenarios": sc,
            "ff_url": f"https://www.forexfactory.com/calendar?day={week_param(t.astimezone(FF_TZ).date().isoformat())}"
                      + (f"#detail={e['ff_id']}" if e.get("ff_id") else ""),
            "disclaimer": "এটি নিয়মভিত্তিক বিশ্লেষণ ও বাজারের সম্ভাবনার সারাংশ — নিশ্চয়তা নয়। রিলিজের সময় স্প্রেড/স্লিপেজ বাড়ে; SL ছাড়া ট্রেড নয়।",
        })
        order.append(e["id"])
    return {"generated_at": iso_utc(NOW), "today_dhaka": today.isoformat(), "order": order, "events": out}



def build_status(cal, prob):
    weeks = FF_STATUS
    page_ok = bool(weeks) and all(w["page_ok"] for w in weeks.values())
    failed = {k: w for k, w in weeks.items() if not w["page_ok"]}
    feed_tried = [w for w in weeks.values() if w["feed_ok"] is not None]
    feed_ok = None if not feed_tried else any(w["feed_ok"] for w in feed_tried)
    wk_bn = {"this": "এই সপ্তাহ", "next": "আগামী সপ্তাহ"}
    reason = "; ".join(f"{wk_bn.get(k, k)}: {w['page_reason']}" for k, w in failed.items()) or None
    https = sorted({w["page_http"] for w in failed.values() if w["page_http"]})
    if page_ok:
        fallback = None
    elif feed_ok:
        fallback = "faireconomy JSON ফিড (এই সপ্তাহ) — Actual নেই; আগের রানে পাওয়া Actual রাখা হচ্ছে"
    elif cal.get("stale"):
        fallback = "কোনো সোর্স কাজ করেনি — আগের ডেটা দেখানো হচ্ছে"
    else:
        fallback = "ব্যাকআপ ফিডও কাজ করেনি"
    ps = prob.get("sources", {})
    return {
        "checked_at": iso_utc(NOW),
        "ff_page_ok": page_ok,
        "ff_feed_ok": feed_ok,
        "ff_http_status": https[0] if len(https) == 1 else (https or None),
        "ff_reason": reason,
        "fallback_in_use": fallback,
        "weeks": weeks,
        "polymarket_ok": ps.get("polymarket") == "ok",
        "kalshi_ok": ps.get("kalshi") == "ok",
    }


def gh_outputs(status):
    """Expose the FF status to later GitHub Actions steps (no-op locally)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path or not status:
        return
    one = lambda v: ("" if v is None else str(v)).replace("\n", " ").replace("\r", " ")[:500]
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"ff_page_ok={'true' if status['ff_page_ok'] else 'false'}\n")
        f.write(f"ff_feed_ok={one(status['ff_feed_ok']).lower()}\n")
        f.write(f"ff_http={one(status['ff_http_status'])}\n")
        f.write(f"ff_reason={one(status['ff_reason'])}\n")
        f.write(f"ff_fallback={one(status['fallback_in_use'])}\n")


def main():
    cal = build_calendar()
    for e in cal.get("events", []):
        e["category"], e["dir"] = classify(e["title"])
    cal = write_if_changed("calendar.json", cal)
    log(f"calendar: {len(cal.get('events', []))} events · sources {cal.get('sources')}")
    prob = write_if_changed("probabilities.json", build_probabilities(cal))
    log(f"probabilities: {len(prob['by_event'])} USD events · {prob['sources']}")
    status = build_status(cal, prob)
    page_ok = status["ff_page_ok"]
    backfill = archive_weeks(dict(WEEK_ROWS))
    analysis_targets = [e for e in cal.get("events", []) if e["currency"] == "USD" and e["impact"] in ("High", "Medium")]
    specs_by = update_specs(analysis_targets, page_ok)
    analysis = write_if_changed("analysis.json", build_analysis(cal, prob, specs_by, load_archive()))
    # briefs are built from the probabilities/analysis on disk, so tiny price jitter (kept back by
    # PROB_TOL) doesn't rewrite the Bangla text, and they always match the analysis verdict
    briefs = write_if_changed("briefs.json", build_briefs(cal, prob, analysis))
    log(f"briefs: {len(briefs['briefs'])} (next 24h)")
    if os.environ.get("PROBE_ALL") == "1":   # manual check of every free source from this machine
        for name, fn in (("cleveland_fed", lambda: nowcasts.cleveland("cpi_mom", NOW + timedelta(days=10), http_get)),
                         ("gdpnow", lambda: nowcasts.gdpnow(http_get))):
            if name not in SOURCE_HEALTH:
                r, why = fn()
                SOURCE_HEALTH[name] = "ok" if r else why
                log(f"probe {name}: {SOURCE_HEALTH[name]}" + (f" → {r.get('value')} {r.get('label')}" if r else ""))
        probe_ev = next((e for e in analysis_targets if e.get("ff_id")), None)
        if probe_ev and page_ok:
            sp = fetch_specs(probe_ev)
            SOURCE_HEALTH["ff_specs"] = "ok" if sp and sp.get("specs") else f"ব্যর্থ ({LAST_ERR.get(FF_DETAILS.format(id=probe_ev['ff_id']), 'no data')})"
            log(f"probe ff_specs ({probe_ev['title']}): {SOURCE_HEALTH['ff_specs']}")
    status["archive"] = {"weeks": len(stored_weeks()), "first": (stored_weeks() or [None])[0],
                         "backfill_stopped": backfill.get("stopped")}
    status["nowcasts"] = dict(SOURCE_HEALTH) or None
    write_if_changed("status.json", status, field="checked_at")
    gh_outputs(status)
    log(f"status: ff_page_ok={status['ff_page_ok']} feed_ok={status['ff_feed_ok']} reason={status['ff_reason']}")
    return 0 if cal.get("events") else 1


if __name__ == "__main__":
    sys.exit(main())
