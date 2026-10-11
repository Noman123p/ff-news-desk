"""NOMAN's wording rule: the site never says নরম / গরম / শক্তিশালী / শক্ত / দুর্বল / কড়া (or hot / soft /
strong / weak / hawkish / dovish …). Sentiment is only ever "Bullish" or "Bearish", always next to the asset
it describes ("USD-এর জন্য Bullish ডেটা", "Gold: Bearish"). This scans every template, script, the README
and all generated data, plus freshly built scenarios/briefs."""
import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import scenarios  # noqa: E402

BANNED_BN = ["নরম", "গরম", "শক্তিশালী", "শক্ত", "দুর্বল", "কড়া", "কড়া", "ঠান্ডা", "ঠাণ্ডা", "উত্তপ্ত",
             "হকিশ", "ডোভিশ", "বুলিশ", "বেয়ারিশ", "বেয়ারিশ", "মন্দা"]
BANNED_EN = re.compile(r"\b(hot|hotter|soft|softer|softening|strong|stronger|strength|weak|weaker|weakness|"
                       r"hawkish|dovish|hawk-ish|cool|cooler|cooling)\b", re.I)


def files():
    pats = ["index.html", "README.md", "assets/**/*.js", "assets/**/*.css", "scripts/*.py",
            "data/*.json", "data/weeks/*.json", "*.webmanifest", "manifest.json"]
    seen = set()
    for p in pats:
        for f in glob.glob(os.path.join(ROOT, p), recursive=True):
            if f not in seen and os.path.isfile(f):
                seen.add(f)
                yield f


def hits(text):
    out = [w for w in BANNED_BN if w in text]
    out += sorted({m.group(0) for m in BANNED_EN.finditer(text)})
    return out


class BannedWords(unittest.TestCase):
    def test_no_banned_words_anywhere(self):
        bad = {}
        for f in files():
            with open(f, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    h = hits(line)
                    if h:
                        bad.setdefault(os.path.relpath(f, ROOT), []).append((n, h))
        self.assertEqual(bad, {}, f"banned sentiment words found: {bad}")

    def test_scanner_catches_them(self):
        for w in ["CPI গরম", "দুর্বল ডলার", "শক্ত শ্রমবাজার", "hot CPI", "Strong USD", "hawkish Fed"]:
            self.assertTrue(hits(w), w)
        self.assertFalse(hits("USD-এর জন্য Bullish ডেটা · Gold: Bearish · PUMP / DUMP"))

    def test_built_scenarios_name_the_asset(self):
        for theme, kind in [("inflation", "data"), ("jobs", "data"), ("growth", "data"), ("housing", "data"),
                            ("fed", "fed"), ("fed", "tone"), ("speech", "speech"), ("other", "data")]:
            for probs, lean in [(None, None), (None, "above" if kind == "data" else "hawk"),
                                ({"above": 60, "inline": 30, "below": 10} if kind == "data" else {"hawk": 10, "neutral": 80, "dove": 10}, None)]:
                sc = scenarios.build_scenarios(theme=theme, impact="High", title="X", kind=kind, usd_dir=1,
                                               forecast="0.3%" if kind == "data" else None, ref_label="Forecast",
                                               probs=probs, lean=lean, has_numbers=kind == "data")
                blob = json.dumps(sc, ensure_ascii=False)
                self.assertFalse(hits(blob), (theme, kind, hits(blob)))
                for s in sc:
                    if s["usd_bias"] in ("up", "down"):
                        w = "Bullish" if s["usd_bias"] == "up" else "Bearish"
                        self.assertIn(f"USD-এর জন্য {w}", s["title"], (theme, kind, s["title"]))
                        risk = "Bearish" if w == "Bullish" else "Bullish"
                        self.assertTrue(s["markets"]["gold"]["text"].startswith(f"Gold: {risk}"))
                        self.assertTrue(s["markets"]["btc"]["text"].startswith(f"BTC: {risk}"))
                        self.assertIn(f"DXY: {w}", s["markets"]["forex"]["text"])

    def test_generated_analysis_bias_tags(self):
        with open(os.path.join(ROOT, "data", "analysis.json"), encoding="utf-8") as fh:
            an = json.load(fh)
        for eid, a in an.get("events", {}).items():
            for s in a.get("scenarios") or []:
                if s["usd_bias"] in ("up", "down"):
                    self.assertRegex(s["title"], r"USD-এর জন্য (Bullish|Bearish)", (eid, s["title"]))
            p = a.get("prediction") or {}
            if p.get("usd_text"):
                self.assertRegex(p["usd_text"], r"^USD-এর জন্য (Bullish|Bearish|নিউট্রাল)")


if __name__ == "__main__":
    unittest.main()
