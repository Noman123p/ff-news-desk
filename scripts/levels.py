"""Price levels per scenario per market (PUMP / DUMP, targets, pullback, invalidation).

Everything here is computed from fetched prices — nothing is typed in by hand:
  · current price + 1h ATR(14) from Yahoo Finance chart API (Coinbase as crypto fallback)
  · move size = median reaction of that market to past releases of the same event
    (ForexFactory archive × Yahoo 1h history), in ATR units, times today's ATR;
    if the event has < MIN_SAMPLES past releases, the pooled reaction to all past
    USD releases of the same impact is used instead (and labelled so)
  · targets snapped to nearby real support/resistance: previous-day high/low, 5-day
    high/low, 1h swing highs/lows, round numbers
  · "fake move first" only when the last ~60 days of 15m data show it often enough
Pure helpers are separated from fetching so they can be unit-tested.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from statistics import median

BN = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bn(s) -> str:
    return str(s).translate(BN)


# key, label, yahoo symbol, beta (direction when USD goes UP), round-number step, decimals, prefix, coinbase product
MARKETS = [
    ("gold", "Gold (XAU/USD)", "GC=F", -1, 10, 1, "$", None),
    ("dxy", "DXY (ডলার ইনডেক্স)", "DX-Y.NYB", +1, 0.25, 2, "", None),
    ("eurusd", "EUR/USD", "EURUSD=X", -1, 0.005, 4, "", None),
    ("usdjpy", "USD/JPY", "JPY=X", +1, 0.5, 2, "", None),
    ("gbpusd", "GBP/USD", "GBPUSD=X", -1, 0.005, 4, "", None),
    ("btc", "Bitcoin (BTC)", "BTC-USD", -1, 1000, 0, "$", "BTC-USD"),
    ("eth", "Ethereum (ETH)", "ETH-USD", -1, 50, 1, "$", "ETH-USD"),
    ("es", "S&P 500 (ES ফিউচার)", "ES=F", -1, 25, 2, "", None),
    ("nq", "Nasdaq 100 (NQ ফিউচার)", "NQ=F", -1, 100, 2, "", None),
]
MKEYS = [m[0] for m in MARKETS]
BETA = {m[0]: m[3] for m in MARKETS}
TICK = {"es": 0.25, "nq": 0.25, "gold": 0.1, "btc": 1, "eth": 0.1}
NOTE = {"gold": "COMEX গোল্ড ফিউচার (GC=F) — স্পট XAU/USD থেকে কয়েক ডলার আলাদা হতে পারে"}
YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}   # Yahoo answers 429 to some full browser UA strings
COINBASE = "https://api.exchange.coinbase.com/products/{p}/candles"
MIN_SAMPLES = 4
ATR_N = 120                # 1h ATR over ~5 trading days — steadier than ATR(14), which swings with the session
CHANGE_ATR = 0.25          # rewrite levels.json only if some level moved more than this × ATR
HEARTBEAT_H = 12


# --------------------------------------------------------------------------- fetching
def _bars_from_yahoo(j):
    r = (j or {}).get("chart", {}).get("result") or []
    if not r:
        return None
    r = r[0]
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        bars.append((int(t), float(o), float(h), float(l), float(c)))
    meta = r.get("meta", {})
    return {"bars": bars, "price": meta.get("regularMarketPrice"), "price_time": meta.get("regularMarketTime")}


def fetch_yahoo(sym, interval, rng, http_get):
    st, body = None, b""
    for host in YAHOO_HOSTS:
        try:
            st, body = http_get(f"{host}/v8/finance/chart/{sym}", {"interval": interval, "range": rng},
                                retries=1, headers=YAHOO_HEADERS)
        except TypeError:          # a plain http_get without the extra keywords (tests)
            st, body = http_get(f"{host}/v8/finance/chart/{sym}", {"interval": interval, "range": rng})
        if st == 200 and body:
            break
    if st != 200 or not body:
        return None, f"HTTP {st}" if st else "নেটওয়ার্ক ব্যর্থ"
    try:
        d = _bars_from_yahoo(json.loads(body.decode("utf-8")))
    except ValueError:
        return None, "JSON নয়"
    if not d or len(d["bars"]) < 50:
        return None, "যথেষ্ট ক্যান্ডেল নেই"
    return d, "ok"


def fetch_coinbase(product, http_get):
    st, body = http_get(COINBASE.format(p=product), {"granularity": 3600})
    if st != 200 or not body:
        return None, f"HTTP {st}" if st else "নেটওয়ার্ক ব্যর্থ"
    try:
        rows = json.loads(body.decode("utf-8"))
    except ValueError:
        return None, "JSON নয়"
    bars = sorted((int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4])) for r in rows if len(r) >= 5)
    if len(bars) < 50:
        return None, "যথেষ্ট ক্যান্ডেল নেই"
    return {"bars": bars, "price": bars[-1][4], "price_time": bars[-1][0] + 3600}, "ok"


# --------------------------------------------------------------------------- pure math
def atr(bars, end=None, n=14):
    """Average true range of the n bars ending before index `end` (exclusive)."""
    end = len(bars) if end is None else end
    if end < 2:
        return None
    lo = max(1, end - n)
    trs = []
    for i in range(lo, end):
        _, _, h, l, _ = bars[i]
        pc = bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else None


def bar_index(bars, t, width):
    """Index of the bar whose [start, start+width) contains t, or None."""
    lo, hi = 0, len(bars) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if bars[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid - 1
    i = hi
    if i < 0 or not (bars[i][0] <= t < bars[i][0] + width):
        return None
    return i


def reaction(bars, t_rel, width=3600, after=3600):
    """Move from the open of the release bar to the close of the bar holding release+after.
    Returns (move, atr_before) or None if the bars are missing (market closed / gap)."""
    i = bar_index(bars, t_rel, width)
    j = bar_index(bars, t_rel + after, width)
    if i is None or j is None or j < i:
        return None
    a = atr(bars, i, ATR_N)
    if not a:
        return None
    return bars[j][4] - bars[i][1], a


def fake_first(bars15, t_rel, width=900, after=3600, frac=0.5):
    """True if the first 15m bar after the release went the other way by > frac × the final 1h move."""
    i = bar_index(bars15, t_rel, width)
    j = bar_index(bars15, t_rel + after, width)
    if i is None or j is None:
        return None
    o = bars15[i][1]
    final = bars15[j][4] - o
    if final == 0:
        return None
    exc = (o - bars15[i][3]) if final > 0 else (bars15[i][2] - o)
    return exc > frac * abs(final)


def sr_levels(bars, step, now_idx=None):
    """Nearby real levels: previous UTC-day high/low, today's high/low, 5-day high/low, 1h swings, round numbers."""
    if not bars:
        return []
    end = len(bars) if now_idx is None else now_idx
    last_day = datetime.fromtimestamp(bars[end - 1][0], timezone.utc).date()
    days = {}
    for t, o, h, l, c in bars[max(0, end - 24 * 8):end]:
        d = datetime.fromtimestamp(t, timezone.utc).date()
        hh, ll = days.get(d, (-math.inf, math.inf))
        days[d] = (max(hh, h), min(ll, l))
    keys = sorted(days)
    out = []
    if last_day in days:
        out += [(days[last_day][0], "আজকের হাই"), (days[last_day][1], "আজকের লো")]
    prev = [k for k in keys if k < last_day]
    if prev:
        out += [(days[prev[-1]][0], "আগের দিনের হাই (PDH)"), (days[prev[-1]][1], "আগের দিনের লো (PDL)")]
    if len(keys) >= 3:
        k5 = keys[-6:]
        out += [(max(days[k][0] for k in k5), "৫-দিনের হাই"), (min(days[k][1] for k in k5), "৫-দিনের লো")]
    seg = bars[max(0, end - 120):end]
    k = 3
    for i in range(k, len(seg) - k):
        h, l = seg[i][2], seg[i][3]
        if all(h >= seg[x][2] for x in range(i - k, i + k + 1)):
            out.append((h, "সুইং হাই"))
        if all(l <= seg[x][3] for x in range(i - k, i + k + 1)):
            out.append((l, "সুইং লো"))
    p = bars[end - 1][4]
    base = math.floor(p / step) * step
    for n in range(-6, 8):
        out.append((round(base + n * step, 10), "রাউন্ড নাম্বার"))
    return out


def snap(target, price, md, levels, tol, min_dist=0.0, between=None):
    """Nearest real level to `target` within `tol` that lies on the md side of price (md=+1 above, -1 below)
    and at least min_dist away from price. `between`=(a,b) restricts to strictly inside (a,b).
    Returns (value, tag); falls back to the computed target itself."""
    best = None
    for v, tag in levels:
        if md and md * (v - price) < min_dist:
            continue
        if between and not (min(between) < v < max(between)):
            continue
        dd = abs(v - target)
        if dd <= tol and (best is None or dd < best[0] - 1e-12 or (abs(dd - best[0]) < 1e-12 and tag != "রাউন্ড নাম্বার")):
            best = (dd, v, tag)
    return (best[1], best[2]) if best else (target, "ATR-মাপ")


def size_moves(event_moves, pooled_moves):
    """event_moves / pooled_moves: lists of (signed_move_in_atr, surprise) where surprise is +1/-1/0
    (+1 = USD-positive, sign already flipped so + means the market's expected direction).
    Returns ATR multiples for the directional and in-line scenarios plus the evidence used."""
    def stats(ms):
        dirn = [m for m, s in ms if s != 0]
        inl = [abs(m) for m, s in ms if s == 0]
        allm = [abs(m) for m, _ in ms]
        return dirn, inl, allm
    ev_dir, ev_inl, ev_all = stats(event_moves)
    if len(ev_all) >= MIN_SAMPLES:
        src, use_dir, use_inl, use_all = "event", ev_dir, ev_inl, ev_all
    else:
        p_dir, p_inl, p_all = stats(pooled_moves)
        if len(p_all) < MIN_SAMPLES:
            return None
        src, use_dir, use_inl, use_all = "pooled", p_dir, p_inl, p_all
    absd = sorted(abs(m) for m in use_dir) if len(use_dir) >= MIN_SAMPLES else sorted(use_all)
    m = median(absd)
    ext = absd[min(len(absd) - 1, int(round(0.75 * (len(absd) - 1))))]
    ext = max(ext, 1.4 * m)
    inl = median(use_inl) if len(use_inl) >= 3 else 0.5 * m
    inl = min(inl, 0.8 * m)
    hits = sum(1 for x in use_dir if x > 0)
    return {"m": m, "ext": ext, "inline": inl, "src": src, "n": len(use_all), "n_dir": len(use_dir), "hits": hits}


def scenario_levels(price, md, size_atr, atr_now, levels, dec, tick=None):
    """Concrete levels for one market in one scenario. md: +1 PUMP, -1 DUMP, 0 range."""
    m = size_atr["m"] * atr_now
    ext = size_atr["ext"] * atr_now
    im = size_atr["inline"] * atr_now
    r = (lambda x: round(round(x / tick) * tick, dec)) if tick else (lambda x: round(x, dec))
    unit = tick or 10 ** -dec

    def strict(vals):
        """Rounded values must stay strictly increasing — nudge by one tick if rounding merged two levels."""
        out = []
        for v in vals:
            v = r(v)
            if out and v <= out[-1]:
                v = r(out[-1] + unit)
            out.append(v)
        return out

    if md == 0:
        hi, hi_t = snap(price + im, price, +1, levels, 0.35 * im, min_dist=0.3 * im)
        lo, lo_t = snap(price - im, price, -1, levels, 0.35 * im, min_dist=0.3 * im)
        bh_target = max(price + max(1.3 * im, 0.6 * m), hi + 0.3 * im)
        bl_target = min(price - max(1.3 * im, 0.6 * m), lo - 0.3 * im)
        bh, bh_t = snap(bh_target, price, +1, levels, 0.3 * m, min_dist=(hi - price) + 0.15 * im)
        bl, bl_t = snap(bl_target, price, -1, levels, 0.3 * m, min_dist=(price - lo) + 0.15 * im)
        seq_up = strict([price, hi, bh])          # from < range-high < break-high
        seq_dn = strict([-price, -lo, -bl])       # mirrored: from > range-low > break-low
        return {"md": 0, "from": r(price), "range": [-seq_dn[1], seq_up[1]], "range_tags": [lo_t, hi_t],
                "break": [-seq_dn[2], seq_up[2]], "break_tags": [bl_t, bh_t]}
    t1, t1_tag = snap(price + md * m, price, md, levels, 0.3 * m, min_dist=0.4 * m)
    t2, t2_tag = snap(price + md * ext, price, md, levels, 0.3 * m, min_dist=abs(t1 - price) + 0.3 * m)
    pb, pb_tag = snap(t1 - md * 0.5 * abs(t1 - price), price, md, levels, 0.2 * m, between=(price, t1))
    inv, inv_tag = snap(price - md * 0.6 * m, price, -md, levels, 0.25 * m, min_dist=0.3 * m)
    # order along the move: invalidation · from · pullback · T1 · T2 (mirrored for DUMP)
    fwd = strict([md * price, md * pb, md * t1, md * t2])     # outward from the price, so the price never moves
    back = strict([-md * price, -md * inv])
    pb, t1, t2 = (md * v for v in fwd[1:])
    inv = -md * back[1]
    return {"md": md, "from": r(price), "t1": r(t1), "t1_tag": t1_tag, "t2": r(t2), "t2_tag": t2_tag,
            "pullback": r(pb), "pb_tag": pb_tag, "invalid": r(inv), "inv_tag": inv_tag}


def first_move(md, fake_rate, n_fake):
    word = {1: "PUMP", -1: "DUMP"}
    if md == 0:
        return "দুই দিকে হালকা PUMP/DUMP (whipsaw) → তারপর রেঞ্জ"
    if fake_rate is not None and n_fake >= MIN_SAMPLES and fake_rate >= 0.5:
        return f"আগে হালকা {word[-md]} (লিকুইডিটি/ফেক মুভ) → তারপর {word[md]}"
    return f"সরাসরি {word[md]}"


MD_OF_USD = {"up": 1, "down": -1, "flat": 0}


def md_for(mkey, usd_bias):
    return BETA[mkey] * MD_OF_USD.get(usd_bias, 0)


# --------------------------------------------------------------------------- orchestration
def _parse(s):
    import re
    if s is None:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([KMBT]?)", str(s).replace(",", ""), re.I)
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2).upper()] if m else None


def _ts(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def releases_for(archive, title, ebase=None, before=None):
    out, seen = [], set()
    for a in archive:
        if a.get("currency") != "USD" or not a.get("time_utc") or a.get("time_special"):
            continue
        if not ((ebase and a.get("ebase") == ebase) or a.get("title") == title):
            continue
        t = _ts(a["time_utc"])
        if before and t >= before or t in seen:
            continue
        seen.add(t)
        out.append(a)
    return sorted(out, key=lambda x: x["time_utc"])


def surprise(a, usd_dir):
    av, fv = _parse(a.get("actual")), _parse(a.get("forecast"))
    if av is None or fv is None:
        return None
    if abs(av - fv) < 1e-12:
        return 0
    return (1 if av > fv else -1) * (1 if usd_dir >= 0 else -1)


def market_moves(bars, rels, mkey, usd_dir):
    """[(signed move in ATR units, surprise)] — sign flipped so + = the market's expected direction."""
    out = []
    for a in rels:
        s = surprise(a, a.get("dir") or usd_dir)
        if s is None:
            continue
        rr = reaction(bars, _ts(a["time_utc"]))
        if not rr:
            continue
        mv, at = rr
        exp = BETA[mkey] * s
        out.append(((mv / at) * (exp if exp else 1), s, abs(mv)))
    return out


def fmt_px(mkey, v):
    dec = {m[0]: m[5] for m in MARKETS}[mkey]
    pre = {m[0]: m[6] for m in MARKETS}[mkey]
    return f"{pre}{v:,.{dec}f}"


def build_levels(analysis, archive, http_get, prev=None, now=None, log=print):
    now = now or datetime.now(timezone.utc)
    prev = prev or {}
    health, mk = {}, {}
    for key, label, sym, beta, step, dec, pre, cb in MARKETS:
        d1, why = fetch_yahoo(sym, "60m", "730d", http_get)
        src = f"Yahoo Finance ({sym})"
        if not d1 and cb:
            d1, why2 = fetch_coinbase(cb, http_get)
            src = f"Coinbase ({cb})"
            why = why if not d1 else f"Yahoo ব্যর্থ ({why}) → Coinbase"
        if not d1:
            health[key] = f"ব্যর্থ ({why})"
            mk[key] = {"label": label, "ok": False, "note": f"দামের ডেটা পাওয়া যায়নি ({why})"}
            continue
        d15, _ = fetch_yahoo(sym, "15m", "60d", http_get) if src.startswith("Yahoo") else (None, "")
        bars = d1["bars"]
        price = float(d1["price"] or bars[-1][4])
        a_now = atr(bars, None, ATR_N)
        health[key] = "ok" if src.startswith("Yahoo") else why
        mk[key] = {"label": label, "ok": True, "symbol": sym, "source": src, "price": round(price, dec),
                   "price_time": datetime.fromtimestamp(int(d1["price_time"] or bars[-1][0]), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "atr": round(a_now, dec + 2), "dec": dec, "note": NOTE.get(key),
                   "_bars": bars, "_b15": (d15 or {}).get("bars") or [], "_sr": sr_levels(bars, step)}
    # pooled reactions by impact (all past USD releases of that impact with a numeric surprise)
    pool_rels = {}
    for imp in ("High", "Medium"):
        rels = [a for a in archive if a.get("currency") == "USD" and a.get("impact") == imp and a.get("actual")
                and a.get("time_utc") and not a.get("time_special")]
        uniq = {}
        for a in rels:
            uniq.setdefault(a["time_utc"], a)
        pool_rels[imp] = sorted(uniq.values(), key=lambda x: x["time_utc"])
    pooled = {}
    for imp, rels in pool_rels.items():
        for key, m in mk.items():
            if m["ok"]:
                pooled[(imp, key)] = [(x[0], x[1]) for x in market_moves(m["_bars"], rels, key, 1)]
    fake = {}
    for key, m in mk.items():
        if not m["ok"] or not m["_b15"]:
            continue
        res = [fake_first(m["_b15"], _ts(a["time_utc"])) for a in pool_rels["High"]]
        res = [x for x in res if x is not None]
        fake[key] = (sum(res) / len(res) if res else None, len(res))

    events = {}
    for eid in analysis.get("order", []):
        a = analysis["events"][eid]
        t_ev = _ts(a["time_utc"])
        released = bool((a.get("data") or {}).get("actual")) or t_ev <= now.timestamp()
        if released:
            old = (prev.get("events") or {}).get(eid)
            happened = next((s["key"] for s in a.get("scenarios", []) if s.get("happened")), None)
            if old:
                events[eid] = {**old, "status": "released", "happened": happened}
            else:
                events[eid] = {"status": "released", "happened": happened, "scenarios": {},
                               "note": "রিলিজের আগের লেভেল সংরক্ষিত নেই"}
            continue
        rels = [r for r in releases_for(archive, a["title"], None, t_ev) if r.get("actual")]
        usd_dir = a.get("dir") or 1
        ev = {"status": "pre", "scenarios": {}, "basis": {}, "first_move_basis": {}}
        for key, m in mk.items():
            if not m["ok"]:
                continue
            moves = market_moves(m["_bars"], rels, key, usd_dir)
            ev_m = [(x[0], x[1]) for x in moves]
            sz = size_moves(ev_m, pooled.get((a["impact"], key), []))
            if not sz:
                continue
            m_px = sz["m"] * m["atr"]
            if sz["src"] == "event":
                avg_px = sum(x[2] for x in moves) / len(moves)
                med_px = median(x[2] for x in moves)
                basis = (f"গত {bn(sz['n'])}টা {a['title']}-এ {m['label'].split(' (')[0]}-এর রিলিজ-ঘণ্টার গড় মুভ ±{fmt_px(key, avg_px)}, "
                         f"মধ্যমা ±{fmt_px(key, med_px)} (≈{bn(round(sz['m'], 1))}×ATR)")
                if sz["n_dir"]:
                    basis += f"; সারপ্রাইজের প্রত্যাশিত দিকে গেছে {bn(sz['hits'])}/{bn(sz['n_dir'])} বার"
            else:
                basis = (f"এই নিউজের ইতিহাস কম ({bn(len(moves))}টা) — গত {bn(sz['n'])}টা "
                         f"{'হাই' if a['impact'] == 'High' else 'মিডিয়াম'}-ইমপ্যাক্ট USD রিলিজের মধ্যমা ≈{bn(round(sz['m'], 1))}×ATR")
            basis += f"; এখনকার ১ঘ ATR (৫ দিনের গড়) {fmt_px(key, m['atr'])} → প্রথম টার্গেটের দূরত্ব ≈{fmt_px(key, m_px)}"
            ev["basis"][key] = basis
            fr, nf = fake.get(key, (None, 0))
            if fr is not None:
                ev["first_move_basis"][key] = f"গত ৬০ দিনের {bn(nf)}টা হাই-ইমপ্যাক্ট রিলিজে প্রথম ১৫ মিনিটে উল্টো ঝাঁকুনি {bn(round(fr * 100))}%"
            for s in a.get("scenarios", []):
                md = md_for(key, s.get("usd_bias"))
                lv = scenario_levels(m["price"], md, sz, m["atr"], m["_sr"], m["dec"], TICK.get(key))
                lv["dir"] = {1: "PUMP", -1: "DUMP", 0: "RANGE"}[md]
                lv["first"] = first_move(md, fr, nf)
                ev["scenarios"].setdefault(s["key"], {})[key] = lv
        events[eid] = ev
    markets = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in mk.items()}
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "markets": markets, "events": events,
            "health": health,
            "method": ("দাম: Yahoo Finance (ক্রিপ্টোতে দরকারে Coinbase)। মুভের মাপ: একই নিউজের আগের রিলিজে ঐ মার্কেটের "
                       "রিলিজ-ঘণ্টার মুভ (ATR-এককে) × আজকের ১ঘ ATR; টার্গেট কাছের আসল সাপোর্ট/রেজিস্ট্যান্সে (PDH/PDL, "
                       "৫-দিনের হাই/লো, সুইং, রাউন্ড নাম্বার) বসানো। সব লেভেল আনুমানিক।")}


LEVEL_FIELDS = ("t1", "t2", "pullback", "invalid")


def levels_changed(old, new):
    """True if any level moved more than CHANGE_ATR × ATR, events/markets changed, or the heartbeat is due."""
    if not old or set((old.get("events") or {})) != set(new.get("events") or {}):
        return True
    om, nm = old.get("markets") or {}, new.get("markets") or {}
    if {k for k, v in om.items() if v.get("ok")} != {k for k, v in nm.items() if v.get("ok")}:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.strptime(old["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if age > timedelta(hours=HEARTBEAT_H):
            return True
    except (KeyError, ValueError):
        return True
    for eid, ne in new["events"].items():
        oe = old["events"][eid]
        if oe.get("status") != ne.get("status") or oe.get("happened") != ne.get("happened"):
            return True
        for sk, mks in (ne.get("scenarios") or {}).items():
            for mkey, lv in mks.items():
                ol = ((oe.get("scenarios") or {}).get(sk) or {}).get(mkey)
                if not ol or ol.get("dir") != lv.get("dir"):
                    return True
                tol = CHANGE_ATR * (nm.get(mkey, {}).get("atr") or 0)
                vals = [(ol.get(f), lv.get(f)) for f in LEVEL_FIELDS] + list(zip(ol.get("range") or [], lv.get("range") or []))
                if any(a is None or b is None or abs(a - b) > tol for a, b in vals if (a, b) != (None, None)):
                    return True
    return False
