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
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

UTC = timezone.utc
DHAKA = timezone(timedelta(hours=6), "Asia/Dhaka")  # Bangladesh has no DST

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

FF_JSON = "https://nfs.faireconomy.media/ff_calendar_{week}.json"   # thisweek / nextweek
FF_HTML = "https://www.forexfactory.com/calendar?week={week}"          # this / next
POLY_SEARCH = "https://gamma-api.polymarket.com/public-search"
POLY_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
FEDWATCH_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"

BRIEF_WINDOW_H = 24
HEARTBEAT_H = 12      # rewrite unchanged files at least this often (proves the updater is alive)
PROB_TOL = 1.5        # percentage points: smaller probability moves don't count as a change
NOW = datetime.now(UTC)
LAST_ERR = {}         # url -> short error text of the last failed request
FF_STATUS = {}        # week -> fetch status, filled by build_calendar()


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


def write_json(name, obj):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")
    os.replace(tmp, path)


VOLATILE_KEYS = {"generated_at", "checked_at", "volume_usd", "volume"}
PROB_KEYS = {"prob", "prob_above", "above", "inline", "below", "hike", "hold", "cut"}


def same_content(a, b, key=None):
    """Deep-compare ignoring timestamps/volumes; probabilities equal within PROB_TOL points."""
    if isinstance(a, dict) and isinstance(b, dict):
        ka = set(a) - VOLATILE_KEYS
        kb = set(b) - VOLATILE_KEYS
        return ka == kb and all(same_content(a[k], b[k], k) for k in ka)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_content(x, y, key) for x, y in zip(a, b))
    if key in PROB_KEYS and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < PROB_TOL
    return a == b


def too_old(obj, field="generated_at"):
    t = parse_iso((obj or {}).get(field)) if isinstance(obj, dict) else None
    return t is None or (NOW - t) > timedelta(hours=HEARTBEAT_H)


def write_if_changed(name, obj, field="generated_at"):
    """Write only on real content change (or heartbeat). Returns the object now on disk."""
    old = load_json(name, None)
    if old is not None and same_content(old, obj) and not too_old(old, field):
        log(f"{name}: unchanged — kept")
        return old
    write_json(name, obj)
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


def build_calendar():
    prev = load_json("calendar.json", {})
    prev_actuals = {e["id"]: e.get("actual") for e in prev.get("events", []) if e.get("actual")}
    sources, events = {}, []
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
        for r in rows:
            r["id"] = event_id(r["currency"], r["title"], r["time_utc"])
            r["week"] = week
            r["source"] = src
            if not r["actual"] and prev_actuals.get(r["id"]):
                r["actual"] = prev_actuals[r["id"]]   # keep actuals from an earlier HTML run
            events.append(r)
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
    ("ppi", re.compile(r"^PPI m/m", re.I), +1),
    ("core_ppi", re.compile(r"^Core PPI m/m", re.I), +1),
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
    "ppi": "উৎপাদক মূল্যসূচক (PPI)", "core_ppi": "কোর PPI", "retail": "খুচরা বিক্রি", "ism_mfg": "ISM ম্যানুফ্যাকচারিং",
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
    Mid of bid/ask when the spread is reasonable; else last trade (if it traded); else None."""
    bid, ask, last = (fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars")),
                      fnum(m.get("last_price_dollars")))
    traded = (fnum(m.get("volume_fp")) or 0) > 0 and last is not None and last > 0
    if bid is not None and ask is not None and ask >= bid and (ask - bid) <= 0.20 and (bid > 0 or ask > 0):
        return (bid + ask) / 2, (ask - bid) > 0.10
    if traded:
        return last, True
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


def build_briefs(cal, prob):
    probs = prob.get("by_event", {})
    end = NOW + timedelta(hours=BRIEF_WINDOW_H)
    upcoming = [e for e in cal.get("events", [])
                if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
                and NOW <= parse_iso(e["time_utc"]) <= end]
    groups = {}
    for e in upcoming:
        groups.setdefault(e["time_utc"], []).append(e)
    briefs = [brief_for_group(g, probs) for _, g in sorted(groups.items())]
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
    # briefs are built from the probabilities that are on disk, so tiny price
    # jitter (kept back by PROB_TOL) doesn't rewrite the Bangla text either
    briefs = write_if_changed("briefs.json", build_briefs(cal, prob))
    log(f"briefs: {len(briefs['briefs'])} (next 24h)")
    status = build_status(cal, prob)
    write_if_changed("status.json", status, field="checked_at")
    gh_outputs(status)
    log(f"status: ff_page_ok={status['ff_page_ok']} feed_ok={status['ff_feed_ok']} reason={status['ff_reason']}")
    return 0 if cal.get("events") else 1


if __name__ == "__main__":
    sys.exit(main())
