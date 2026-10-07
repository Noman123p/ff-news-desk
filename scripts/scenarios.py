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
        "up": "মূল্যস্ফীতি প্রত্যাশার চেয়ে গরম → Fed-এর রেট কাট পিছিয়ে যাওয়ার (বা হাইকের) প্রত্যাশা বাড়ে → 2Y ইল্ড ও ডলার ওঠে।",
        "down": "মূল্যস্ফীতি প্রত্যাশার চেয়ে ঠান্ডা → রেট কাটের আশা বাড়ে → ইল্ড ও ডলার নামে, ঝুঁকিপূর্ণ অ্যাসেট সাপোর্ট পায়।",
    },
    "jobs": {
        "up": "শ্রমবাজার প্রত্যাশার চেয়ে শক্ত → Fed-এর তাড়াহুড়ো করে রেট কাটার দরকার কমে → ইল্ড ও ডলার ওঠে।",
        "down": "শ্রমবাজার দুর্বল হওয়ার সংকেত → রেট কাটের প্রত্যাশা বাড়ে → ডলার ও ইল্ড নামে। তবে খুব খারাপ ডেটা মন্দা-ভীতি আনলে স্টক/ক্রিপ্টোও পড়তে পারে।",
    },
    "growth": {
        "up": "অর্থনীতি প্রত্যাশার চেয়ে শক্তিশালী → রেট বেশি দিন উঁচু রাখার সুযোগ → ইল্ড ও ডলার সাপোর্ট পায়।",
        "down": "অর্থনৈতিক গতি দুর্বল → রেট কাটের আশা বাড়ে → ডলার নামে; দুর্বলতা বেশি হলে ঝুঁকি-বিমুখতাও আসতে পারে।",
    },
    "housing": {
        "up": "আবাসন খাত প্রত্যাশার চেয়ে ভালো — উঁচু সুদেও চাহিদা টিকে আছে, ডলারের জন্য হালকা ইতিবাচক।",
        "down": "আবাসন খাত দুর্বল — উঁচু সুদের চাপ স্পষ্ট, ডলারের জন্য হালকা নেতিবাচক।",
    },
    "fed": {
        "up": "Fed হকিশ — রেট বেশি দিন উঁচু থাকবে বা আরও বাড়তে পারে → ইল্ড ও ডলার ওঠে, লিকুইডিটি টাইট হওয়ার ভয়।",
        "down": "Fed ডোভিশ — রেট কাটের পথ খোলা → ইল্ড ও ডলার নামে, লিকুইডিটি বাড়ার প্রত্যাশায় ঝুঁকিপূর্ণ অ্যাসেট ওঠে।",
    },
    "speech": {
        "up": "বক্তব্য ডলার-সহায়ক বা ঝুঁকি-বিমুখ (যেমন কড়া ট্যারিফ হুমকি, শক্তিশালী ডলারের পক্ষে মন্তব্য) → সেফ-হেভেন হিসেবে USD ওঠে।",
        "down": "বক্তব্য ডলার-বিরোধী (যেমন Fed-কে রেট কমাতে চাপ, দুর্বল ডলারের পক্ষে মন্তব্য) → USD নামে।",
    },
    "other": {
        "up": "ডেটা প্রত্যাশার চেয়ে ভালো → USD-এর জন্য ইতিবাচক।",
        "down": "ডেটা প্রত্যাশার চেয়ে খারাপ → USD-এর জন্য নেতিবাচক।",
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


def _markets(usd: str, range_key: str, show_range: bool) -> dict:
    """usd: 'up' | 'down' | 'flat'."""
    r = RANGES.get(range_key)
    rng = (lambda k: f" (আনুমানিক {r[k]})") if (show_range and r) else (lambda k: "")
    if usd == "flat":
        return {
            "gold": {"dir": "flat", "text": "সীমিত মুভ; রিলিজের আগের পজিশন খুলে গেলে ছোট উইক, তারপর আগের ট্রেন্ডে ফেরা।"},
            "btc": {"dir": "flat", "text": "দুই দিকে ছোট স্পাইক (স্টপ-হান্ট) তারপর রেঞ্জ; নিউজ-নির্ভর মুভ ফিকে হয়ে আগের ট্রেন্ড চলতে পারে।"},
            "crypto": {"dir": "flat", "text": "অল্টকয়েন BTC-কে অনুসরণ করবে; ফান্ডিং/ওপেন ইন্টারেস্টই বেশি দিক ঠিক করবে।"},
            "forex": {"dir": "flat", "dxy": "→", "eurusd": "→", "usdjpy": "→", "gbpusd": "→",
                      "text": "DXY ও মেজর পেয়ার রেঞ্জে; প্রথম মিনিটের মুভ ফিরে আসার সম্ভাবনা বেশি।"},
            "indices": {"dir": "flat", "text": "ইল্ড স্থির থাকলে স্টক ইনডেক্স (S&P 500, Nasdaq) স্বাভাবিক সেশন-ট্রেন্ডে চলবে।"},
        }
    up = usd == "up"
    return {
        "gold": {"dir": "down" if up else "up",
                 "text": ("নিচে" if up else "উপরে") + rng("gold") + " — " + (
                     "ডলার ও রিয়েল ইল্ড বাড়লে সুদ-না-দেওয়া গোল্ডের আকর্ষণ কমে।" if up else
                     "দুর্বল ডলার ও কম ইল্ডে গোল্ড সাধারণত সাপোর্ট পায়।")},
        "btc": {"dir": "down" if up else "up",
                "text": ("নিচে" if up else "উপরে") + rng("btc") + " — " + (
                    "টাইট লিকুইডিটি ও রিস্ক-অফে লিভারেজড লং লিকুইডেট হতে পারে।" if up else
                    "রেট কাটের আশায় রিস্ক-অন; শর্ট স্কুইজে দ্রুত পাম্প হতে পারে।")},
        "crypto": {"dir": "down" if up else "up",
                   "text": ("অল্টকয়েন BTC-এর চেয়ে বেশি % পড়তে পারে" if up else "অল্টকয়েন BTC-এর চেয়ে বেশি % উঠতে পারে")
                   + rng("alts") + " — হাই-বিটা অ্যাসেট, লিকুইডেশন ক্যাসকেডের ঝুঁকি বেশি।"},
        "forex": {"dir": "up" if up else "down",
                  "dxy": "↑" if up else "↓", "eurusd": "↓" if up else "↑", "usdjpy": "↑" if up else "↓", "gbpusd": "↓" if up else "↑",
                  "text": ("DXY উপরে" if up else "DXY নিচে") + rng("dxy") + "; EUR/USD " + ("নিচে" if up else "উপরে") + rng("eurusd")
                  + "। ইল্ড-পার্থক্যের কারণে USD/JPY সাধারণত সবচেয়ে বেশি সাড়া দেয়।"},
        "indices": {"dir": "down" if up else "up",
                    "text": ("US 2Y/10Y ইল্ড ↑; S&P 500/Nasdaq চাপে (টেক বেশি সংবেদনশীল)।" if up else
                             "US 2Y/10Y ইল্ড ↓; S&P 500/Nasdaq সাপোর্ট পায় (যদি মন্দা-ভীতি না আসে)।")},
    }


def build_scenarios(*, theme: str, impact: str, title: str, kind: str, usd_dir: int,
                    forecast: str | None, ref_label: str, probs: dict | None,
                    lean: str | None, has_numbers: bool, is_decision: bool = False) -> list:
    """
    kind: 'data' (numeric release), 'fed' (decision/talk), 'speech' (no numbers)
    probs: {'above','inline','below'} or {'hawk','neutral','dove'} in percent (or None)
    lean:  'above'|'below'|'hawk'|'dove'|None — direction to treat as primary when probs are missing
    """
    show_range = (has_numbers and impact in ("High", "Medium")) or (kind == "fed" and impact == "High")
    range_key = "fed" if is_decision else impact
    th = theme if theme in WHY else "other"

    if kind == "data":
        p_up_side = probs.get("above") if probs else None
        p_dn_side = probs.get("below") if probs else None
        if probs:
            primary_side = "above" if (p_up_side or 0) >= (p_dn_side or 0) else "below"
        else:
            primary_side = lean if lean in ("above", "below") else "above"
        sides = [primary_side, "below" if primary_side == "above" else "above"]
        f = forecast or "—"

        def cond(side):
            word = "বেশি" if side == "above" else "কম"
            return f"Actual {ref_label} ({f})-এর চেয়ে স্পষ্টভাবে {word} এলে"

        def usd_of(side):
            pos = (side == "above")
            return "up" if (pos if usd_dir >= 0 else not pos) else "down"
        items = [(s, cond(s), usd_of(s), probs.get(s) if probs else None) for s in sides]
        neutral = ("inline", f"Actual {ref_label} ({f})-এর সমান বা খুব কাছাকাছি এলে", "flat",
                   probs.get("inline") if probs else None)
    else:  # fed / speech
        hawk = probs.get("hawk") if probs else None
        dove = probs.get("dove") if probs else None
        if probs:
            primary = "hawk" if (hawk or 0) >= (dove or 0) else "dove"
        else:
            primary = lean if lean in ("hawk", "dove") else "hawk"
        labels = {
            "fed": {"hawk": "Fed হকিশ হলে (হাইক, বা 'রেট বেশি দিন উঁচু' বার্তা)", "dove": "Fed ডোভিশ হলে (কাট, বা কাটের স্পষ্ট ইঙ্গিত)",
                    "neutral": "হোল্ড + প্রত্যাশিত/নিরপেক্ষ ভাষা হলে"},
            "speech": {"hawk": "ডলার-সহায়ক/ঝুঁকি-বিমুখ মন্তব্য এলে", "dove": "ডলার-বিরোধী মন্তব্য এলে (যেমন Fed-কে রেট কমাতে চাপ)",
                       "neutral": "নতুন বা চমকপ্রদ কিছু না বললে"},
        }[kind if kind in ("fed", "speech") else "speech"]
        sides = [primary, "dove" if primary == "hawk" else "hawk"]
        items = [(s, labels[s], "up" if s == "hawk" else "down", probs.get(s) if probs else None) for s in sides]
        neutral = ("neutral", labels["neutral"], "flat", probs.get("neutral") if probs else None)

    overall_top = None
    if probs:
        allp = {k: v for k, v in probs.items() if isinstance(v, (int, float))}
        overall_top = max(allp, key=allp.get) if allp else None

    out = []
    titles = ["সবচেয়ে সম্ভাব্য রেজাল্ট এলে", "উল্টো দিকে গেলে", "নিউট্রাল — প্রত্যাশার কাছাকাছি"]
    for i, (side, condition, usd, p) in enumerate(items + [neutral]):
        note = None
        if i == 0 and probs and overall_top and overall_top != side:
            note = "দুই দিকের সারপ্রাইজের মধ্যে এই দিকটা বেশি সম্ভাব্য; সামগ্রিকভাবে নিউট্রাল ফলাফলই সবচেয়ে সম্ভাব্য।"
        if i == 0 and not probs:
            note = "বাজারের সম্ভাবনা-ডেটা নেই — " + ("Forecast/নাউকাস্টের ঝোঁক অনুযায়ী সাজানো" if lean else "দিক অনিশ্চিত, প্রচলিত ক্রম অনুযায়ী দেখানো হয়েছে") + "।"
        why = WHY[th][usd] if usd in ("up", "down") else (
            "ফলাফল প্রত্যাশার কাছাকাছি হলে বাজারে নতুন তথ্য কম — রিলিজের আগের পজিশন খুলে যাওয়াই মূল মুভ।")
        out.append({
            "key": side,
            "title": titles[i],
            "condition": condition,
            "usd_bias": usd,
            "prob": p,
            "is_overall_top": bool(probs and overall_top == side),
            "note": note,
            "why": why,
            "markets": _markets(usd, range_key, show_range),
            "watch": WATCH.get(th, WATCH["other"]),
        })
    return out
