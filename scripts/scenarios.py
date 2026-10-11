"""Rule-based Bangla market-reaction scenarios.

build_scenarios(...) returns three scenarios in the order the site shows them:
  1. primary   — the more likely *directional* outcome
  2. opposite  — the other direction
  3. neutral   — in line with forecast / hold / nothing new
Every number here is a rough historical first-hour range, clearly labelled — never a forecast.
"""
from __future__ import annotations

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bn(s) -> str:
    return str(s).translate(BN_DIGITS)


# Rough first-hour reaction ranges by (theme, impact). Only used for scheduled data/Fed events.
RANGES = {
    "fed": {"gold": "০.৫–২%", "btc": "২–৫%", "alts": "৩–৮%", "eurusd": "০.৩–১%", "dxy": "০.৩–০.৮%"},
    "High": {"gold": "০.৫–১.৫%", "btc": "১–৩%", "alts": "২–৫%", "eurusd": "০.৩–০.৮%", "dxy": "০.৩–০.৭%"},
    "Medium": {"gold": "০.২–০.৬%", "btc": "০.৫–১.৫%", "alts": "১–৩%", "eurusd": "০.১–০.৪%", "dxy": "০.১–০.৩%"},
}

WHY = {
    "inflation": {
        "up": "মূল্যস্ফীতি প্রত্যাশার চেয়ে বেশি (USD-এর জন্য Bullish ডেটা) → Fed-এর রেট কাট পিছিয়ে যাওয়ার (বা হাইকের) প্রত্যাশা বাড়ে → 2Y ইল্ড ও ডলার ওঠে।",
        "down": "মূল্যস্ফীতি প্রত্যাশার চেয়ে কম (USD-এর জন্য Bearish ডেটা) → রেট কাটের আশা বাড়ে → ইল্ড ও ডলার নামে, ঝুঁকিপূর্ণ অ্যাসেট সাপোর্ট পায়।",
    },
    "jobs": {
        "up": "শ্রমবাজারের ডেটা প্রত্যাশার চেয়ে ভালো (USD-এর জন্য Bullish) → Fed-এর তাড়াহুড়ো করে রেট কাটার দরকার কমে → ইল্ড ও ডলার ওঠে।",
        "down": "শ্রমবাজারের ডেটা প্রত্যাশার চেয়ে খারাপ (USD-এর জন্য Bearish) → রেট কাটের প্রত্যাশা বাড়ে → ডলার ও ইল্ড নামে। তবে খুব খারাপ ডেটা রিসেশন-ভীতি আনলে স্টক/ক্রিপ্টোও পড়তে পারে।",
    },
    "growth": {
        "up": "অর্থনীতির ডেটা প্রত্যাশার চেয়ে ভালো (USD-এর জন্য Bullish) → রেট বেশি দিন উঁচু রাখার সুযোগ → ইল্ড ও ডলার সাপোর্ট পায়।",
        "down": "অর্থনীতির ডেটা প্রত্যাশার চেয়ে খারাপ (USD-এর জন্য Bearish) → রেট কাটের আশা বাড়ে → ডলার নামে; ডেটা খুব খারাপ হলে ঝুঁকি-বিমুখতাও আসতে পারে।",
    },
    "housing": {
        "up": "আবাসন খাত প্রত্যাশার চেয়ে ভালো — উঁচু সুদেও চাহিদা টিকে আছে, USD-এর জন্য হালকা Bullish।",
        "down": "আবাসন খাতের ডেটা প্রত্যাশার চেয়ে খারাপ — উঁচু সুদের চাপ স্পষ্ট, USD-এর জন্য হালকা Bearish।",
    },
    "fed": {
        "up": "Fed-এর বার্তা USD-এর জন্য Bullish — রেট বেশি দিন উঁচু থাকবে বা আরও বাড়তে পারে → ইল্ড ও ডলার ওঠে, লিকুইডিটি টাইট হওয়ার ভয়।",
        "down": "Fed-এর বার্তা USD-এর জন্য Bearish — রেট কাটের পথ খোলা → ইল্ড ও ডলার নামে, লিকুইডিটি বাড়ার প্রত্যাশায় ঝুঁকিপূর্ণ অ্যাসেট ওঠে।",
    },
    "speech": {
        "up": "বক্তব্য USD-এর জন্য Bullish বা ঝুঁকি-বিমুখ (যেমন নতুন ট্যারিফ হুমকি, উঁচু ডলারের পক্ষে মন্তব্য) → সেফ-হেভেন হিসেবে USD ওঠে।",
        "down": "বক্তব্য USD-এর জন্য Bearish (যেমন Fed-কে রেট কমাতে চাপ, নিচু ডলারের পক্ষে মন্তব্য) → USD নামে।",
    },
    "other": {
        "up": "ডেটা প্রত্যাশার চেয়ে ভালো → USD-এর জন্য Bullish।",
        "down": "ডেটা প্রত্যাশার চেয়ে খারাপ → USD-এর জন্য Bearish।",
    },
}

WATCH = {
    "inflation": [
        "হেডলাইন বনাম কোর — দুটো উল্টো দিকে গেলে কোরকে প্রাধান্য দিন।",
        "প্রথম ১–৫ মিনিটের স্পাইক প্রায়ই রিভার্স হয় — 2Y ইল্ড ও DXY একই দিকে টিকে থাকছে কিনা দেখুন।",
        "BTC/অল্টে দুই দিকেই লিকুইডেশন উইক — ১৫ মিনিটের ক্যান্ডেল ক্লোজের আগে এন্ট্রি নয়।",
        "আগের মাসের রিভিশন বড় হলে হেডলাইনের অর্থ বদলে যায়।",
        "৩০–৬০ মিনিট পর CME FedWatch/Kalshi-তে পরবর্তী মিটিংয়ের সম্ভাবনা কতটা বদলাল।",
    ],
    "jobs": [
        "NFP, বেকারত্বের হার ও মজুরি (AHE) মিশ্র হলে প্রথম মুভ ভুয়া হতে পারে।",
        "আগের দুই মাসের রিভিশন হেডলাইনের চেয়েও গুরুত্বপূর্ণ হতে পারে।",
        "USD/JPY ও 2Y ইল্ড সাধারণত সবচেয়ে পরিষ্কার প্রতিক্রিয়া দেখায়।",
        "১৫ মিনিট পর ট্রেন্ড টিকলে তবেই কনফার্মেশন ধরুন।",
    ],
    "growth": [
        "হেডলাইনের সাথে উপাদানগুলো (prices paid, employment, new orders) দেখুন।",
        "ইল্ডের প্রতিক্রিয়া ছোট হলে মুভও দ্রুত মিলিয়ে যেতে পারে।",
        "একই সময়ে অন্য বড় নিউজ থাকলে সেটাই দিক ঠিক করবে।",
    ],
    "housing": [
        "সাধারণত ছোট মুভ — একই সময়ের বড় নিউজ থাকলে সেটাকে প্রাধান্য দিন।",
    ],
    "fed": [
        "স্টেটমেন্টের শব্দ বদল (অর্থনীতির মূল্যায়ন, ভবিষ্যৎ নির্দেশনা)।",
        "প্রেস কনফারেন্সে চেয়ারের টোন — প্রথম মুভ প্রায়ই উল্টে যায়, তাই প্রথম ৩০ মিনিট সাবধান।",
        "ডট-প্লট থাকলে (মার্চ/জুন/সেপ্টেম্বর/ডিসেম্বর) পরের বছরের রেট পথ।",
        "Kalshi/Polymarket ও CME FedWatch-এ পরবর্তী মিটিংয়ের সম্ভাবনা কতটা বদলাল।",
    ],
    "speech": [
        "হেডলাইন স্ক্যানারে (Tree News, Bloomberg ইত্যাদি) মূল উক্তি — গুজব নয়, সরাসরি উদ্ধৃতি।",
        "ট্যারিফ বা Fed উল্লেখে USD/JPY, গোল্ড ও BTC সবার আগে প্রতিক্রিয়া দেখায়।",
        "বক্তব্য শেষ হওয়ার আগে বড় পজিশন নয় — পরের বাক্যেই দিক বদলাতে পারে।",
    ],
    "other": [
        "প্রথম ১৫ মিনিটের দিক টিকে থাকে কিনা দেখুন; স্প্রেড স্বাভাবিক হওয়া পর্যন্ত অপেক্ষা করুন।",
    ],
}


def bias_word(direction: str) -> str:
    """'up' → Bullish, 'down' → Bearish, otherwise নিউট্রাল (for the asset named next to it)."""
    return {"up": "Bullish", "down": "Bearish"}.get(direction, "নিউট্রাল")


def _markets(usd: str, range_key: str, show_range: bool) -> dict:
    """usd: 'up' | 'down' | 'flat'. Every line names the asset and says Bullish / Bearish for *that* asset."""
    r = RANGES.get(range_key)
    rng = (lambda k: f" (আনুমানিক {r[k]})") if (show_range and r) else (lambda k: "")
    if usd == "flat":
        return {
            "gold": {"dir": "flat", "text": "Gold: নিউট্রাল — সীমিত মুভ; রিলিজের আগের পজিশন খুলে গেলে ছোট উইক, তারপর আগের ট্রেন্ডে ফেরা।"},
            "btc": {"dir": "flat", "text": "BTC: নিউট্রাল — দুই দিকে ছোট স্পাইক (স্টপ-হান্ট) তারপর রেঞ্জ; নিউজ-নির্ভর মুভ ফিকে হয়ে আগের ট্রেন্ড চলতে পারে।"},
            "crypto": {"dir": "flat", "text": "Altcoin: নিউট্রাল — অল্টকয়েন BTC-কে অনুসরণ করবে; ফান্ডিং/ওপেন ইন্টারেস্টই বেশি দিক ঠিক করবে।"},
            "forex": {"dir": "flat", "dxy": "→", "eurusd": "→", "usdjpy": "→", "gbpusd": "→",
                      "text": "DXY ও মেজর পেয়ার: নিউট্রাল — রেঞ্জে; প্রথম মিনিটের মুভ ফিরে আসার সম্ভাবনা বেশি।"},
            "indices": {"dir": "flat", "text": "S&P 500/Nasdaq: নিউট্রাল — ইল্ড স্থির থাকলে স্বাভাবিক সেশন-ট্রেন্ডে চলবে।"},
        }
    up = usd == "up"
    risk = "Bearish" if up else "Bullish"      # gold, crypto, stocks move against USD here
    usd_w = "Bullish" if up else "Bearish"
    return {
        "gold": {"dir": "down" if up else "up",
                 "text": f"Gold: {risk} — " + ("নিচে" if up else "উপরে") + rng("gold") + "; " + (
                     "ডলার ও রিয়েল ইল্ড বাড়লে সুদ-না-দেওয়া গোল্ডের আকর্ষণ কমে।" if up else
                     "ডলার ও ইল্ড নামলে গোল্ড সাধারণত সাপোর্ট পায়।")},
        "btc": {"dir": "down" if up else "up",
                "text": f"BTC: {risk} — " + ("নিচে" if up else "উপরে") + rng("btc") + "; " + (
                    "টাইট লিকুইডিটি ও রিস্ক-অফে লিভারেজড লং লিকুইডেট হতে পারে।" if up else
                    "রেট কাটের আশায় রিস্ক-অন; শর্ট স্কুইজে দ্রুত PUMP হতে পারে।")},
        "crypto": {"dir": "down" if up else "up",
                   "text": f"Altcoin: {risk} — " + ("BTC-এর চেয়ে বেশি % পড়তে পারে" if up else "BTC-এর চেয়ে বেশি % উঠতে পারে")
                   + rng("alts") + "; হাই-বিটা অ্যাসেট, লিকুইডেশন ক্যাসকেডের ঝুঁকি বেশি।"},
        "forex": {"dir": "up" if up else "down",
                  "dxy": "↑" if up else "↓", "eurusd": "↓" if up else "↑", "usdjpy": "↑" if up else "↓", "gbpusd": "↓" if up else "↑",
                  "text": f"DXY: {usd_w}" + rng("dxy") + f" · EUR/USD: {risk}" + rng("eurusd") + f" · GBP/USD: {risk} · USD/JPY: {usd_w}"
                  + "। ইল্ড-পার্থক্যের কারণে USD/JPY সাধারণত সবচেয়ে বেশি সাড়া দেয়।"},
        "indices": {"dir": "down" if up else "up",
                    "text": (f"S&P 500/Nasdaq: {risk} — US 2Y/10Y ইল্ড ↑; টেক বেশি সংবেদনশীল।" if up else
                             f"S&P 500/Nasdaq: {risk} — US 2Y/10Y ইল্ড ↓ (যদি রিসেশন-ভীতি না আসে)।")},
    }


TIE_PP = 0.5   # probabilities within this many points are treated as a tie


def _pct_bn(p):
    return f"{round(p, 1)}%".translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))


def data_labels(baseline):
    """Outcome labels for a numeric release, worded for the comparison baseline actually used."""
    if baseline == "previous":
        return {"above": "Previous-এর চেয়ে বেশি", "below": "Previous-এর চেয়ে কম",
                "inline": "নিউট্রাল — আগের মানের কাছাকাছি"}
    if baseline == "forecast":
        return {"above": "Forecast-এর চেয়ে বেশি", "below": "Forecast-এর চেয়ে কম",
                "inline": "নিউট্রাল — প্রত্যাশার (Forecast) কাছাকাছি"}
    return {"above": "প্রত্যাশার চেয়ে বেশি", "below": "প্রত্যাশার চেয়ে কম", "inline": "নিউট্রাল — প্রত্যাশার কাছাকাছি"}


def baseline_text(baseline):
    return {"forecast": "তুলনা: Forecast", "previous": "তুলনা: Previous — Forecast এখনো আসেনি"}.get(baseline)


def build_scenarios(*, theme: str, impact: str, title: str, kind: str, usd_dir: int,
                    forecast: str | None, ref_label: str, probs: dict | None,
                    lean: str | None, has_numbers: bool, is_decision: bool = False,
                    dir_hint: str | None = None, baseline: str | None = None,
                    lean_note: str | None = None) -> list:
    """
    Three scenarios, ordered so that no title contradicts the numbers.

    kind:  'data' (numeric release) · 'fed' (rate decision) · 'tone' (minutes / Fed speakers) · 'speech'
    probs: {'above','inline','below'} or {'hawk','neutral','dove'} in percent (≈100 in total), or None
    lean:  direction to treat as most likely when probs are missing ('above'|'below'|'inline'|'hawk'|'dove'|'neutral')
    dir_hint: when lean is neutral, which surprise direction is the likelier one

    With probs, scenario 1 is ALWAYS the highest-probability outcome:
      · directional top → [most likely, opposite, neutral]
      · neutral top     → [neutral (most likely), likelier direction, opposite direction]
    Without probs nothing is called "most likely" unless a lean exists (then it's labelled approximate).
    """
    show_range = (has_numbers and impact in ("High", "Medium")) or (kind == "fed" and impact == "High")
    range_key = "fed" if is_decision else impact
    th = theme if theme in WHY else "other"
    f = forecast or "—"

    if kind == "data":
        A, B, N = "above", "below", "inline"

        def usd_of(side):
            pos = (side == "above")
            return "up" if (pos if usd_dir >= 0 else not pos) else "down"
        if baseline is None and forecast:
            baseline = "forecast" if ref_label == "Forecast" else "previous"
        if baseline == "previous":
            ref, tail = f"Previous ({f})-এর", " (Forecast এখনো আসেনি)"
        elif baseline == "forecast":
            ref, tail = f"Forecast ({f})-এর", ""
        else:
            ref, tail = "প্রত্যাশার", " (Forecast এখনো প্রকাশ হয়নি)"
        cond = {
            "above": f"Actual {ref} চেয়ে স্পষ্টভাবে বেশি এলে{tail}",
            "below": f"Actual {ref} চেয়ে স্পষ্টভাবে কম এলে{tail}",
            "inline": f"Actual {ref} সমান বা খুব কাছাকাছি এলে{tail}",
        }
        usd = {A: usd_of(A), B: usd_of(B), N: "flat"}
        neutral_word = data_labels(baseline)["inline"]
    else:
        A, B, N = "hawk", "dove", "neutral"
        cond = {
            "fed": {"hawk": "Fed-এর সিদ্ধান্ত USD-এর জন্য Bullish হলে (হাইক, বা 'রেট বেশি দিন উঁচু' বার্তা)", "dove": "Fed-এর সিদ্ধান্ত USD-এর জন্য Bearish হলে (কাট, বা কাটের স্পষ্ট ইঙ্গিত)",
                    "neutral": "হোল্ড + প্রত্যাশিত/নিরপেক্ষ ভাষা হলে"},
            "tone": {"hawk": "টোন USD-এর জন্য Bullish হলে (মূল্যস্ফীতি নিয়ে উদ্বেগ, 'রেট বেশি দিন উঁচু' বা হাইকের ইঙ্গিত)",
                     "dove": "টোন USD-এর জন্য Bearish হলে (কাটের ইঙ্গিত, প্রবৃদ্ধি/চাকরি নিয়ে উদ্বেগ)",
                     "neutral": "টোন ভারসাম্যপূর্ণ হলে — বর্তমান নীতি বহাল, নতুন কোনো ইঙ্গিত নেই"},
            "speech": {"hawk": "USD-এর জন্য Bullish (বা ঝুঁকি-বিমুখ) মন্তব্য এলে", "dove": "USD-এর জন্য Bearish মন্তব্য এলে (যেমন Fed-কে রেট কমাতে চাপ)",
                       "neutral": "নতুন বা চমকপ্রদ কিছু না বললে"},
        }[kind if kind in ("fed", "tone", "speech") else "speech"]
        usd = {A: "up", B: "down", N: "flat"}
        neutral_word = {"fed": "নিউট্রাল — হোল্ড/প্রত্যাশিত বার্তা", "tone": "নিউট্রাল — ভারসাম্যপূর্ণ টোন",
                        "speech": "নিউট্রাল — নতুন কিছু নয়"}.get(kind, "নিউট্রাল")

    p = {k: (probs.get(k) if probs else None) for k in (A, B, N)}
    notes = {}
    if probs and all(isinstance(p[k], (int, float)) for k in (A, B, N)):
        ranked = sorted((A, B, N), key=lambda k: -p[k])
        top = ranked[0]
        if top == N:
            hi, lo = (A, B) if p[A] >= p[B] else (B, A)
            order = [N, hi, lo]
            tie_dir = abs(p[A] - p[B]) < TIE_PP
            titles = [f"{neutral_word} (সবচেয়ে সম্ভাব্য)",
                      "সারপ্রাইজ: দুই দিক প্রায় সমান — দিক ১" if tie_dir else "বেশি সম্ভাব্য দিক (সারপ্রাইজ হলে)",
                      "সারপ্রাইজ: দিক ২" if tie_dir else "উল্টো দিক"]
            roles = ["most_likely", "likelier_direction", "opposite_direction"]
        else:
            opp = B if top == A else A
            order = [top, opp, N]
            titles = ["সবচেয়ে সম্ভাব্য রেজাল্ট এলে", "উল্টো দিকে গেলে", neutral_word]
            roles = ["most_likely", "opposite", "neutral"]
        if p[ranked[0]] - p[ranked[1]] < TIE_PP:
            notes[order[0]] = f"প্রথম দুটি ফলাফলের সম্ভাবনা প্রায় সমান ({_pct_bn(p[ranked[0]])} বনাম {_pct_bn(p[ranked[1]])})।"
        prob_note = None
        approx = False
    elif lean in (A, B):
        opp = B if lean == A else A
        order = [lean, opp, N]
        titles = ["সম্ভাব্য দিক — ঝোঁক অনুযায়ী (% নেই)", "উল্টো দিকে গেলে", neutral_word]
        roles = ["most_likely", "opposite", "neutral"]
        prob_note = "বাজারের সরাসরি সম্ভাবনা নেই"
        approx = True
        notes[lean] = lean_note or "বাজারের % নেই — Forecast/নাউকাস্টের ঝোঁক অনুযায়ী সাজানো; নিশ্চয়তা কম।"
    elif lean == N:
        hi = dir_hint if dir_hint in (A, B) else A
        lo = B if hi == A else A
        order = [N, hi, lo]
        titles = [f"{neutral_word} — বেশি সম্ভাব্য (আনুমানিক)",
                  "বেশি সম্ভাব্য দিক (আনুমানিক)" if dir_hint in (A, B) else "সারপ্রাইজ: দিক ১",
                  "উল্টো দিক" if dir_hint in (A, B) else "সারপ্রাইজ: দিক ২"]
        roles = ["most_likely", "likelier_direction", "opposite_direction"]
        prob_note = "এই ইভেন্টের ফলাফলের সরাসরি বাজার নেই"
        approx = True
        notes[N] = lean_note or ("সরাসরি বাজার নেই — রেট-মার্কেটের প্রসঙ্গ থেকে আনুমানিক ক্রম; % দেওয়া হচ্ছে না।" if kind == "tone"
                    else "বাজারের % নেই — নাউকাস্ট " + ("আগের মানের" if baseline == "previous" else "প্রত্যাশার") + " কাছাকাছি; আনুমানিক ক্রম।")
    else:
        # nothing to rank by: show both directions neutrally, never call one "most likely"
        first = A
        order = [first, B, N]
        titles = ["দিক ১", "দিক ২", neutral_word]
        roles = ["direction_1", "direction_2", "neutral"]
        prob_note = "সম্ভাবনা-ডেটা নেই"
        approx = False
        notes[first] = "সম্ভাবনা-ডেটা নেই — কোনো দিককে 'সবচেয়ে সম্ভাব্য' বলা হচ্ছে না; ক্রমটি শুধু সাজানোর জন্য।"

    # every directional tab says plainly which way it is for USD: Bullish / Bearish
    noun = {"data": "ডেটা", "fed": "সিদ্ধান্ত", "tone": "টোন", "speech": "মন্তব্য"}.get(kind, "ফলাফল")
    titles = [t if usd[sd] == "flat" else f"{t} — USD-এর জন্য {'Bullish' if usd[sd] == 'up' else 'Bearish'} {noun}"
              for t, sd in zip(titles, order)]
    out = []
    for i, side in enumerate(order):
        u = usd[side]
        why = WHY[th][u] if u in ("up", "down") else (
            "ফলাফল প্রত্যাশার কাছাকাছি হলে বাজারে নতুন তথ্য কম — রিলিজের আগের পজিশন খুলে যাওয়াই মূল মুভ।")
        out.append({
            "key": side,
            "rank": i + 1,
            "role": roles[i],
            "title": titles[i],
            "condition": cond[side],
            "usd_bias": u,
            "prob": p[side],
            "prob_note": None if p[side] is not None else prob_note,
            "approx": approx and i == 0,
            "is_overall_top": bool(i == 0 and roles[0] == "most_likely"),
            "note": notes.get(side),
            "why": why,
            "markets": _markets(u, range_key, show_range),
            "watch": WATCH.get(th, WATCH["other"]),
        })
    return out
