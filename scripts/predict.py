"""Final outcome prediction — one most-likely released value (or Fed decision) per event.

Pure functions (no network). Inputs are whatever the updater already has:
FF Forecast/Previous, past releases, Polymarket/Kalshi bracket distributions, nowcasts.

Consistency rule: the predicted value is always picked *inside* the outcome side that the
verdict calls most likely (above / inline / below Forecast, or hike / hold / cut), so the
prediction box can never contradict the verdict, the scenario tabs or the Dashboard.
"""
from __future__ import annotations

import re
from statistics import mean

BN = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bn(s) -> str:
    return str(s).translate(BN)


def decimals_of(s) -> int:
    m = re.search(r"\d+\.(\d+)", s or "")
    return len(m.group(1)) if m else 0


def unit_of(raw: str) -> str:
    """'0.3%' → '%', '225K' → 'K', '-1.2' → ''."""
    m = re.match(r"^\s*[<>≤≥]?\s*-?[\d.,]+\s*(.*)$", raw or "")
    return (m.group(1).strip() if m else "")


SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def fmt_val(v: float, dec: int, unit: str = "") -> str:
    """Values are stored in base units (225K → 225000, as the updater parses them)."""
    return f"{v / SCALE.get(unit.upper(), 1):.{dec}f}{unit}"


# --------------------------------------------------------------------------- brackets
def rep_value(interval, open_side, step):
    """The released value a bracket stands for.
    Releases are rounded to `step`, so Kalshi's (0.5, 0.6] means 0.6 and Polymarket's [0.6, 0.6] means 0.6.
    Open-ended buckets (≤x / ≥x) use their edge; wide buckets use the midpoint."""
    lo, hi = (list(interval) + [None, None])[:2]
    if lo is None and hi is None:
        return None
    if lo is None:      # "≤ x" (closed) or "< x" (open at hi)
        return float(hi) - (step if open_side == "hi" and step else 0)
    if hi is None:      # "≥ x" (closed) or "> x" (open at lo)
        return float(lo) + (step if open_side == "lo" and step else 0)
    lo, hi = float(lo), float(hi)
    if abs(hi - lo) < 1e-12:
        return lo
    if step and abs((hi - lo) - step) < step * 1e-3 + 1e-12:
        return hi if open_side != "hi" else lo
    return (lo + hi) / 2


def bracket_dist(outcomes, step, side_fn=None, sides=None, ranges=None):
    """outcomes [{prob, interval, open?}] → {value: prob%} normalised to 100 (equal values merged).
    If side_fn(interval, open) is given, each value's side vs the baseline is recorded in `sides`
    using exactly the rule the verdict used, so the picked value can't land on another side."""
    d = {}
    for o in outcomes or []:
        p, iv = o.get("prob"), o.get("interval")
        if p is None or not iv:
            continue
        v = rep_value(iv, o.get("open"), step)
        if v is None:
            continue
        v = round(v, 6)
        d[v] = d.get(v, 0.0) + float(p)
        if side_fn is not None and sides is not None:
            sides.setdefault(v, side_fn(iv, o.get("open")))
        lo, hi = iv[0], iv[1]
        if ranges is not None:
            if lo is None or hi is None:                   # open-ended tail: show "≤ x" / "≥ x"
                ranges.setdefault(v, ("le" if lo is None else "ge", v))
            elif step and (hi - lo) > step * 1.001:
                ranges.setdefault(v, (lo, hi))
    tot = sum(d.values())
    if tot <= 0:
        return {}
    return {k: v * 100.0 / tot for k, v in d.items()}


def bracket_stats(dist):
    """Modal value, its probability and the probability-weighted expected value."""
    if not dist:
        return None
    mode = max(sorted(dist), key=lambda k: dist[k])
    ev = sum(k * p for k, p in dist.items()) / 100.0
    return {"mode": mode, "mode_p": round(dist[mode], 1), "ev": ev}


def combine_dists(dists):
    """Equal-weight average of several {value: %} distributions (missing values count as 0)."""
    dists = [d for d in dists if d]
    if not dists:
        return {}
    keys = set().union(*dists)
    return {k: sum(d.get(k, 0.0) for d in dists) / len(dists) for k in keys}


def side_of(v, ref, step):
    if ref is None or v is None:
        return None
    if abs(v - ref) < (step or 1e-9) / 2:
        return "inline"
    return "above" if v > ref else "below"


def mode_in_side(dist, side, ref, step, sides=None):
    """Most likely value whose side (vs ref) is `side`."""
    sd = lambda k: (sides or {}).get(k) or side_of(k, ref, step)
    cand = {k: p for k, p in dist.items() if sd(k) == side}
    if not cand:
        return None
    k = max(sorted(cand), key=lambda x: cand[x])
    return k, cand[k]


# --------------------------------------------------------------------------- fed buckets
FED_BUCKETS = [("hold", "রেট অপরিবর্তিত (hold)"), ("cut25", "২৫ bps কাট"), ("cut50", "৫০ bps (বা বেশি) কাট"),
               ("hike25", "২৫ bps হাইক"), ("hike50", "৫০ bps (বা বেশি) হাইক")]
FED_LABEL = dict(FED_BUCKETS)
FED_SIDE = {"hold": "neutral", "cut25": "dove", "cut50": "dove", "hike25": "hawk", "hike50": "hawk"}


def fed_bucket(label: str):
    s = (label or "").lower()
    if re.search(r"no change|maintain|unchanged|\bhold\b|pause", s):
        return "hold"
    big = bool(re.search(r"50\+|>\s*25|50 ?bps|75|100", s))
    if re.search(r"cut|decrease|lower", s):
        return "cut50" if big else "cut25"
    if re.search(r"hike|increase|raise", s):
        return "hike50" if big else "hike25"
    return None


def fed_dist(outcomes):
    d = {}
    for o in outcomes or []:
        b = fed_bucket(o.get("label"))
        if b and o.get("prob") is not None:
            d[b] = d.get(b, 0.0) + float(o["prob"])
    tot = sum(d.values())
    return {k: v * 100.0 / tot for k, v in d.items()} if tot > 0 else {}


# --------------------------------------------------------------------------- trend
def trend_input(history, ref_kind):
    """Surprise record and direction of the last releases (archive + FF history)."""
    rows = [h for h in (history or []) if h.get("actual")]
    if len(rows) < 3:
        return None
    from_num = lambda s: _num(s)
    beats = misses = same = 0
    for h in rows:
        a, f = from_num(h["actual"]), from_num(h.get("forecast"))
        if a is None or f is None:
            continue
        if abs(a - f) < 1e-9:
            same += 1
        elif a > f:
            beats += 1
        else:
            misses += 1
    n = beats + misses + same
    acts = [from_num(h["actual"]) for h in rows[:3]]
    slope = None
    if None not in acts:
        slope = "up" if acts[0] > acts[-1] else ("down" if acts[0] < acts[-1] else "flat")
    lean = None
    if n >= 4:
        if beats >= 0.6 * n:
            lean = "above"
        elif misses >= 0.6 * n:
            lean = "below"
    return {"n": n, "beats": beats, "misses": misses, "same": same, "slope": slope, "lean": lean}


def _num(s):
    """Same semantics as update.parse_value: '225K' → 225000."""
    if s is None:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([KMBT]?)", str(s).replace(",", ""), re.I)
    return float(m.group(1)) * SCALE.get(m.group(2).upper(), 1) if m else None


# --------------------------------------------------------------------------- main entry
def _conf_down(c):
    order = ["উচ্চ", "মাঝারি–উচ্চ", "মাঝারি", "নিম্ন", "খুব নিম্ন"]
    base = (c or "নিম্ন").split(" — ")[0]
    return order[min(order.index(base) + 1, len(order) - 1)] if base in order else "নিম্ন"


def predict_data(*, title, forecast_raw, previous_raw, verdict, market_srcs, nowcast, history, side_fn=None):
    """Numeric release. market_srcs: [{name, outcomes}] (Polymarket/Kalshi as stored)."""
    ref_kind = "forecast" if _num(forecast_raw) is not None else ("previous" if _num(previous_raw) is not None else None)
    ref_raw = forecast_raw if ref_kind == "forecast" else (previous_raw if ref_kind == "previous" else "")
    ref = _num(ref_raw)
    dec = max(decimals_of(forecast_raw), decimals_of(previous_raw))
    unit = unit_of(ref_raw or previous_raw or "")
    step = (10 ** -dec if dec else 1.0) * SCALE.get(unit.upper(), 1)
    ref_word = "Forecast" if ref_kind == "forecast" else "Previous"
    inputs = []
    out = {"kind": "data", "title": title, "value": None, "value_text": None, "compare": None,
                       "value_prob": None, "side": None, "ev_text": None, "inputs": inputs, "confidence": "ডেটা নেই",
                       "method": None, "baseline": ref_kind}

    def cmp_text(v, s=None):
        s = s or side_of(v, ref, step)
        if s == "inline":
            return f"{ref_word}-এর সমান ({fmt_val(ref, dec, unit)})" if ref_kind == "forecast" else f"আগের মানের সমান ({fmt_val(ref, dec, unit)})"
        word = "বেশি" if s == "above" else "কম"
        return f"{ref_word} ({fmt_val(ref, dec, unit)})-এর চেয়ে {word}" if ref_kind == "forecast" else f"আগের মানের ({fmt_val(ref, dec, unit)}) চেয়ে {word}"

    dists, sides, ranges = [], {}, {}
    bsf = (lambda iv, op: side_fn(iv, op, ref)) if (side_fn and ref is not None) else None
    for s in market_srcs or []:
        d = bracket_dist(s.get("outcomes"), step, bsf, sides, ranges)
        st = bracket_stats(d)
        if not st:
            continue
        dists.append(d)
        inputs.append({"name": s["name"], "text": f"সবচেয়ে সম্ভাব্য {fmt_val(st['mode'], dec, unit)} ({bn(st['mode_p'])}%), "
                                                  f"গড় প্রত্যাশা {fmt_val(st['ev'], dec + 1, unit)}",
                       "value": st["mode"], "ev": round(st["ev"], dec + 2),
                       "side": sides.get(st["mode"]) or side_of(st["mode"], ref, step)})
    nc_v = None
    if nowcast and nowcast.get("value") is not None and ref is not None:
        nc_v = round(float(nowcast["value"]), dec)
        inputs.append({"name": nowcast.get("source") or "নাউকাস্ট",
                       "text": f"{nowcast['value']}{'%' if unit == '%' else unit} → {cmp_text(nc_v)}",
                       "value": nc_v, "side": side_of(nc_v, ref, step)})
    tr = trend_input(history, ref_kind)
    if tr:
        t = f"গত {bn(tr['n'])} রিলিজে Forecast-এর চেয়ে বেশি {bn(tr['beats'])} বার, কম {bn(tr['misses'])}, সমান {bn(tr['same'])}"
        if tr["slope"]:
            t += "; সাম্প্রতিক ধারা " + {"up": "উপরের দিকে", "down": "নিচের দিকে", "flat": "স্থির"}[tr["slope"]]
        inputs.append({"name": "আগের রিলিজের ধারা", "text": t, "side": tr["lean"]})
    if ref_kind == "forecast":
        pv = _num(previous_raw)
        inputs.append({"name": "ForexFactory Forecast/Previous",
                       "text": f"Forecast {forecast_raw}" + (f", Previous {previous_raw}" if previous_raw else ""),
                       "value": ref, "side": "inline"})
    elif ref_kind == "previous":
        inputs.append({"name": "ForexFactory", "text": f"Forecast এখনো আসেনি · Previous {previous_raw}", "side": None})

    top = (verdict or {}).get("top") or {}
    comb = combine_dists(dists)
    if comb and ref is not None and verdict.get("probs"):
        side = top.get("key")
        pick = mode_in_side(comb, side, ref, step, sides) or (max(comb, key=comb.get), comb[max(comb, key=comb.get)])
        v, p = pick
        st = bracket_stats(comb)
        out.update(value=v, side=sides.get(v) or side_of(v, ref, step), value_prob=round(p, 1),
                   ev_text=f"সব মার্কেট মিলিয়ে গড় প্রত্যাশা {fmt_val(st['ev'], dec + 1, unit)}",
                   method=f"{' + '.join(s['name'] for s in market_srcs if s.get('outcomes'))}-এর ব্র্যাকেট-সম্ভাবনা মিলিয়ে; "
                          f"‘{verdict['labels'][side]}’ দিকের ({bn(top['pct'])}%) মধ্যে সবচেয়ে সম্ভাব্য মান")
        conf = verdict.get("confidence") or "নিম্ন"
    elif verdict.get("probs") and top.get("key"):
        # the side is priced (vs_forecast) but no usable brackets → direction only, no fake number
        out.update(side=top["key"], value_prob=top.get("pct"), word=True,
                   value_text=verdict["labels"][top["key"]].replace("নিউট্রাল — ", ""), compare=None,
                   method="বাজার দিকের সম্ভাবনা দিচ্ছে, কিন্তু ব্র্যাকেট-ডেটা নেই — তাই নির্দিষ্ট মান নয়, শুধু দিক")
        conf = verdict.get("confidence") or "নিম্ন"
        for i in inputs:
            i["agree"] = None if i.get("side") is None else (i["side"] == out["side"])
        out["confidence"] = conf
        out["headline"] = f"{title}: {out['value_text']} ({bn(top['pct'])}%)"
        return out
    elif nc_v is not None and top.get("key"):
        out.update(value=nc_v, side=side_of(nc_v, ref, step), method="প্রেডিকশন মার্কেট নেই — নাউকাস্ট মডেলের মান (রাউন্ড করা)")
        conf = "নিম্ন — বাজারের % নেই"
    elif ref_kind == "forecast":
        out.update(value=ref, side="inline", approx_only=True,
                   method="শুধু ForexFactory Forecast/Previous আছে — বাজার বা নাউকাস্ট নেই, তাই Forecast-এর কাছাকাছি ধরা হলো")
        conf = "নিম্ন — শুধু Forecast"
    else:
        out["method"] = ("Forecast এখনো আসেনি এবং বাজার/নাউকাস্ট নেই — নির্দিষ্ট মান অনুমান করা হচ্ছে না"
                         if ref_kind == "previous" else "পূর্বাভাসের মতো ডেটা নেই")
        return out
    v = out["value"]
    out["value_text"] = fmt_val(v, dec, unit)
    if v in ranges and out.get("value_prob") is not None:      # wide bracket (e.g. claims 175K–180K): show the range
        lo, hi = ranges[v]
        out["value_text"] = (f"{'≤' if lo == 'le' else '≥'}{fmt_val(hi, dec, unit)}" if isinstance(lo, str)
                             else f"{fmt_val(lo, dec, unit)}–{fmt_val(hi, dec, unit)}")
    out["compare"] = ("Forecast-এর কাছাকাছি" if out.get("approx_only") else cmp_text(v, out["side"]))
    if ref_kind == "forecast" and _num(previous_raw) is not None and not out.get("approx_only"):
        pv = _num(previous_raw)
        out["vs_previous"] = ("আগের মানের সমান" if abs(v - pv) < step / 2 else
                              f"আগের মানের ({previous_raw}) চেয়ে {'বেশি' if v > pv else 'কম'}")
    # agreement
    for i in inputs:
        i["agree"] = None if i.get("side") is None else (i["side"] == out["side"])
    dis = [i for i in inputs if i["agree"] is False and i["name"] not in ("ForexFactory Forecast/Previous",)]
    if dis and not conf.startswith("নিম্ন"):
        conf = _conf_down(conf)
    if out["value_prob"] is not None and out["value_prob"] < 30 and not conf.startswith("নিম্ন"):
        conf = _conf_down(conf)
    out["confidence"] = conf
    out["headline"] = f"{title} সম্ভাব্য {out['value_text']} — {out['compare']}"
    return out


def predict_fed(*, title, verdict, market_srcs):
    dists = [fed_dist(s.get("outcomes")) for s in market_srcs or []]
    dists = [d for d in dists if d]
    out = {"kind": "fed", "title": title, "inputs": [], "confidence": verdict.get("confidence") or "ডেটা নেই"}
    for s, d in zip([s for s in market_srcs if fed_dist(s.get("outcomes"))], dists):
        b = max(d, key=d.get)
        out["inputs"].append({"name": s["name"], "text": f"সবচেয়ে সম্ভাব্য: {FED_LABEL[b]} ({bn(round(d[b], 1))}%)",
                              "side": FED_SIDE[b]})
    top = (verdict or {}).get("top")
    if not dists or not top or not verdict.get("probs"):
        out["method"] = "এই মিটিংয়ের সিদ্ধান্ত-মার্কেট পাওয়া যায়নি — CME FedWatch লিংকে দেখুন"
        out["value_text"] = None
        return out
    comb = combine_dists(dists)
    side = top["key"]
    cand = {k: p for k, p in comb.items() if FED_SIDE[k] == side} or comb
    b = max(cand, key=cand.get)
    out.update(value=b, value_text=FED_LABEL[b], value_prob=round(comb[b], 1), side=side,
               compare=f"{verdict['labels'][side]} দিকের মোট সম্ভাবনা {bn(top['pct'])}%",
               method="Kalshi/Polymarket-এর সিদ্ধান্ত-মার্কেট মিলিয়ে; CME FedWatch শুধু লিংক (সরাসরি ডেটা ব্লকড)")
    for i in out["inputs"]:
        i["agree"] = i["side"] == side
    out["headline"] = f"সম্ভাব্য: {FED_LABEL[b]} — {bn(out['value_prob'])}%"
    return out


def predict_tone(*, title, verdict):
    top = (verdict or {}).get("top")
    out = {"kind": "tone", "title": title, "inputs": [], "value_prob": None,
           "confidence": verdict.get("confidence") or "ডেটা নেই"}
    if not top:
        out.update(value_text=None, method="টোনের সরাসরি বাজার নেই এবং রেট-মার্কেট প্রসঙ্গও পাওয়া যায়নি")
        return out
    out.update(value=top["key"], side=top["key"], value_text=top["label"].replace("নিউট্রাল — ", ""),
               compare="আনুমানিক — % নেই", method="টোনের সরাসরি বাজার নেই; পরবর্তী FOMC-র রেট-মার্কেট শুধু প্রেক্ষাপট হিসেবে")
    if verdict.get("context"):
        out["inputs"].append({"name": "রেট-মার্কেট (প্রেক্ষাপট)", "text": verdict["context"]["text"], "agree": None})
    out["headline"] = f"সম্ভাব্য টোন: {out['value_text']} (আনুমানিক)"
    return out


def predict_speech(*, title):
    return {"kind": "speech", "title": title, "value_text": None, "inputs": [], "confidence": "ডেটা নেই",
            "method": "বক্তব্যের ফলাফলের আগাম বাজার বা মডেল নেই — নির্দিষ্ট প্রেডিকশন দেওয়া হচ্ছে না"}
