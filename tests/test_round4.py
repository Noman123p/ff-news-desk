"""Round 4: final prediction (bracket mode/EV), price-level math (ATR, reactions, S/R snapping,
move sizing), PUMP/DUMP direction consistency, and checks over the committed data."""
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import levels as L  # noqa: E402
import nowcasts  # noqa: E402
import predict as P  # noqa: E402
import update as u  # noqa: E402

CPI_POLY = [{"prob": 0.6, "interval": [None, 0.0]}, {"prob": 1.5, "interval": [0.2, 0.2]}, {"prob": 5.5, "interval": [0.4, 0.4]},
            {"prob": 34.5, "interval": [0.5, 0.5]}, {"prob": 37.0, "interval": [0.6, 0.6]}, {"prob": 17.0, "interval": [0.7, 0.7]},
            {"prob": 1.6, "interval": [0.8, None]}]
CPI_KALSHI = [{"prob": 6.5, "interval": [0.3, 0.4], "open": "lo"}, {"prob": 30.0, "interval": [0.4, 0.5], "open": "lo"},
              {"prob": 46.0, "interval": [0.5, 0.6], "open": "lo"}, {"prob": 15.0, "interval": [0.6, 0.7], "open": "lo"},
              {"prob": 2.5, "interval": [0.7, None], "open": "lo"}]


class Brackets(unittest.TestCase):
    def test_rep_value(self):
        self.assertEqual(P.rep_value([0.5, 0.6], "lo", 0.1), 0.6)        # Kalshi (0.5, 0.6] → 0.6
        self.assertEqual(P.rep_value([0.6, 0.6], None, 0.1), 0.6)        # Polymarket point
        self.assertAlmostEqual(P.rep_value([0.9, None], "lo", 0.1), 1.0)  # "> 0.9%" → 1.0
        self.assertEqual(P.rep_value([None, 0.0], None, 0.1), 0.0)       # "≤ 0.0%"
        self.assertEqual(P.rep_value([175000, 180000], "hi", 1000), 177500)  # wide bracket → midpoint

    def test_mode_and_expected_value(self):
        d = P.bracket_dist(CPI_POLY, 0.1)
        self.assertAlmostEqual(sum(d.values()), 100.0, places=6)
        st = P.bracket_stats(d)
        self.assertEqual(st["mode"], 0.6)
        self.assertAlmostEqual(st["mode_p"], 37.9, places=1)            # renormalised (37 / 97.7)
        exp = sum(o["prob"] * P.rep_value(o["interval"], None, 0.1) for o in CPI_POLY) / sum(o["prob"] for o in CPI_POLY)
        self.assertAlmostEqual(st["ev"], exp, places=9)

    def test_combine_and_mode_in_side(self):
        comb = P.combine_dists([P.bracket_dist(CPI_POLY, 0.1), P.bracket_dist(CPI_KALSHI, 0.1)])
        self.assertAlmostEqual(sum(comb.values()), 100.0, places=6)
        k, p = P.mode_in_side(comb, "below", 0.6, 0.1)
        self.assertEqual(k, 0.5)                                         # most likely value *below* forecast
        self.assertIsNone(P.mode_in_side({0.6: 100.0}, "above", 0.6, 0.1))

    def test_fed_buckets(self):
        self.assertEqual(P.fed_bucket("No change"), "hold")
        self.assertEqual(P.fed_bucket("Fed maintains rate"), "hold")
        self.assertEqual(P.fed_bucket("Cut 25bps"), "cut25")
        self.assertEqual(P.fed_bucket("Cut >25bps"), "cut50")
        self.assertEqual(P.fed_bucket("50+ bps decrease"), "cut50")
        self.assertEqual(P.fed_bucket("25 bps increase"), "hike25")


def verdict_for(probs, labels_kind="forecast"):
    labels = __import__("scenarios").data_labels(labels_kind)
    top = max(probs, key=probs.get)
    return {"probs": probs, "labels": labels, "confidence": "মাঝারি", "top": {"key": top, "label": labels[top], "pct": probs[top]}}


class Prediction(unittest.TestCase):
    def test_value_is_inside_verdict_side(self):
        # markets' modal value is 0.6 (= Forecast), but the summed "below" side wins → predict the best value below
        v = verdict_for({"above": 10.0, "inline": 40.0, "below": 50.0})
        p = P.predict_data(title="CPI m/m", forecast_raw="0.6%", previous_raw="0.4%", verdict=v,
                           market_srcs=[{"name": "Polymarket", "outcomes": CPI_POLY}], nowcast=None, history=[],
                           side_fn=u.bracket_side)
        self.assertEqual(p["side"], "below")
        self.assertEqual(p["value_text"], "0.5%")
        self.assertIn("Forecast (0.6%)-এর চেয়ে কম", p["compare"])
        u.assert_prediction(v, p)

    def test_inline_prediction_and_agreement(self):
        v = verdict_for({"above": 18.4, "inline": 42.7, "below": 38.9})
        nc = {"source": "Cleveland Fed", "value": 0.53}
        p = P.predict_data(title="CPI m/m", forecast_raw="0.6%", previous_raw="0.4%", verdict=v,
                           market_srcs=[{"name": "Polymarket", "outcomes": CPI_POLY}, {"name": "Kalshi", "outcomes": CPI_KALSHI}],
                           nowcast=nc, history=[], side_fn=u.bracket_side)
        self.assertEqual((p["value_text"], p["side"]), ("0.6%", "inline"))
        self.assertEqual(p["compare"], "Forecast-এর সমান (0.6%)")
        self.assertEqual(p["headline"], "CPI m/m সম্ভাব্য 0.6% — Forecast-এর সমান (0.6%)")
        nc_in = next(i for i in p["inputs"] if i["name"] == "Cleveland Fed")
        self.assertFalse(nc_in["agree"])                                   # 0.53 → 0.5 < 0.6
        self.assertTrue(all(i["agree"] for i in p["inputs"] if i["name"] in ("Polymarket", "Kalshi")))
        self.assertEqual(p["confidence"], "নিম্ন")                         # disagreement lowers মাঝারি → নিম্ন

    def test_only_forecast_is_low_confidence_near_forecast(self):
        v = {"probs": None, "labels": {}, "top": {"key": "inline", "label": "x", "pct": None}}
        p = P.predict_data(title="Retail Sales m/m", forecast_raw="0.3%", previous_raw="1.2%", verdict=v,
                           market_srcs=[], nowcast=None, history=[])
        self.assertEqual(p["compare"], "Forecast-এর কাছাকাছি")
        self.assertTrue(p["confidence"].startswith("নিম্ন"))
        self.assertIsNone(p["value_prob"])
        u.assert_prediction(v, p)

    def test_previous_only_gives_no_value(self):
        p = P.predict_data(title="X", forecast_raw="", previous_raw="0.4%", verdict={"top": None}, market_srcs=[],
                           nowcast=None, history=[])
        self.assertIsNone(p["value_text"])
        self.assertIn("Forecast এখনো আসেনি", p["method"])

    def test_wide_bracket_shows_range_in_k(self):
        outs = [{"prob": 20, "interval": [190000, 195000], "open": "hi"}, {"prob": 50, "interval": [200000, 205000], "open": "hi"},
                {"prob": 30, "interval": [205000, 210000], "open": "hi"}]
        v = verdict_for({"above": 80.0, "inline": 20.0, "below": 0.0})
        p = P.predict_data(title="Claims", forecast_raw="195K", previous_raw="190K", verdict=v,
                           market_srcs=[{"name": "Kalshi", "outcomes": outs}], nowcast=None, history=[], side_fn=u.bracket_side)
        self.assertEqual(p["value_text"], "200K–205K")
        self.assertEqual(p["side"], "above")

    def test_open_tail_label(self):
        outs = [{"prob": 30, "interval": [None, 0.3], "open": "lo"}, {"prob": 25, "interval": [0.8, 0.9], "open": "lo"},
                {"prob": 45, "interval": [0.9, None], "open": "lo"}]
        v = verdict_for({"above": 70.0, "inline": 0.0, "below": 30.0})
        p = P.predict_data(title="PPI", forecast_raw="0.5%", previous_raw="0.4%", verdict=v,
                           market_srcs=[{"name": "Kalshi", "outcomes": outs}], nowcast=None, history=[], side_fn=u.bracket_side)
        self.assertEqual(p["value_text"], "≥1.0%")

    def test_fed_pick_inside_top_side(self):
        labels = {"hawk": "রেট হাইক (হকিশ)", "neutral": "নিউট্রাল — রেট হোল্ড", "dove": "রেট কাট (ডোভিশ)"}
        v = {"probs": {"hawk": 2.0, "neutral": 38.0, "dove": 60.0}, "labels": labels, "confidence": "মাঝারি",
             "top": {"key": "dove", "label": labels["dove"], "pct": 60.0}}
        outs = [{"label": "No change", "prob": 38}, {"label": "Cut 25bps", "prob": 35}, {"label": "Cut >25bps", "prob": 25}, {"label": "Hike 25bps", "prob": 2}]
        p = P.predict_fed(title="FOMC", verdict=v, market_srcs=[{"name": "Kalshi", "outcomes": outs}])
        self.assertEqual(p["value"], "cut25")          # hold is the single biggest bucket, but cuts (60%) win as a side
        self.assertEqual(p["side"], "dove")
        u.assert_prediction(v, p)


# ------------------------------------------------------------------ levels
def bars_series(n, start=1_700_000_000, p0=100.0, step=0.1, width=3600):
    out, p = [], p0
    for i in range(n):
        o = p
        p = p + (step if (i // 7) % 2 == 0 else -step)
        out.append((start + i * width, o, max(o, p) + 0.05, min(o, p) - 0.05, p))
    return out


class LevelMath(unittest.TestCase):
    def test_atr_and_bar_index(self):
        b = bars_series(40)
        a = L.atr(b, None, 14)
        self.assertAlmostEqual(a, 0.2, places=6)       # range 0.1 + 2×0.05
        self.assertEqual(L.bar_index(b, b[5][0] + 1800, 3600), 5)
        self.assertIsNone(L.bar_index(b, b[-1][0] + 7200, 3600))

    def test_reaction_window(self):
        b = bars_series(40)
        mv, a = L.reaction(b, b[20][0] + 1800)          # release at :30 → open of bar 20 to close of bar 21
        self.assertAlmostEqual(mv, b[21][4] - b[20][1])
        self.assertGreater(a, 0)

    def test_snap_prefers_real_level_on_correct_side(self):
        lv = [(105.0, "PDH"), (104.0, "রাউন্ড নাম্বার"), (95.0, "PDL"), (103.2, "সুইং হাই")]
        self.assertEqual(L.snap(103.4, 100, +1, lv, 0.5), (103.2, "সুইং হাই"))
        self.assertEqual(L.snap(96.0, 100, -1, lv, 0.5), (96.0, "ATR-মাপ"))      # PDL 95 is 1.0 away (> tol)
        self.assertEqual(L.snap(104.9, 100, -1, lv, 0.5)[1], "ATR-মাপ")           # never snaps to the wrong side
        self.assertEqual(L.snap(101.5, 100, +1, lv, 0.6, between=(100, 101.6))[1], "ATR-মাপ")

    def test_size_moves_event_vs_pooled(self):
        ev = [(1.0, 1), (2.0, 1), (-0.5, -1), (1.5, 1), (0.4, 0), (0.6, 0), (0.2, 0)]
        sz = L.size_moves(ev, [])
        self.assertEqual(sz["src"], "event")
        self.assertAlmostEqual(sz["m"], 1.25)            # median |dir| of [0.5,1,1.5,2]
        self.assertGreaterEqual(sz["ext"], 1.4 * sz["m"] - 1e-9)
        self.assertAlmostEqual(sz["inline"], 0.4)
        self.assertEqual((sz["hits"], sz["n_dir"]), (3, 4))
        sz2 = L.size_moves(ev[:2], [(1.0, 1)] * 5)
        self.assertEqual(sz2["src"], "pooled")
        self.assertIsNone(L.size_moves(ev[:2], []))

    def test_level_ordering(self):
        sz = {"m": 1.0, "ext": 1.8, "inline": 0.5}
        sr = [(100.0 + k * 0.5, "রাউন্ড নাম্বার") for k in range(-8, 9)]
        up = L.scenario_levels(100.2, +1, sz, 1.0, sr, 2)
        self.assertTrue(up["invalid"] < up["from"] < up["pullback"] < up["t1"] < up["t2"], up)
        dn = L.scenario_levels(100.2, -1, sz, 1.0, sr, 2)
        self.assertTrue(dn["invalid"] > dn["from"] > dn["pullback"] > dn["t1"] > dn["t2"], dn)
        rg = L.scenario_levels(100.2, 0, sz, 1.0, sr, 2)
        self.assertTrue(rg["break"][0] < rg["range"][0] < rg["from"] < rg["range"][1] < rg["break"][1], rg)
        es = L.scenario_levels(7859.75, +1, sz, 12.3, [], 2, 0.25)
        self.assertTrue(all(abs(es[k] / 0.25 - round(es[k] / 0.25)) < 1e-9 for k in ("t1", "t2", "pullback", "invalid")))

    def test_hot_cpi_directions(self):
        # USD up (hot CPI): DXY PUMP, USD/JPY PUMP, EUR/USD & GBP/USD DUMP, Gold DUMP, BTC/ETH DUMP, stocks DUMP
        want = {"dxy": 1, "usdjpy": 1, "eurusd": -1, "gbpusd": -1, "gold": -1, "btc": -1, "eth": -1, "es": -1, "nq": -1}
        self.assertEqual({k: L.md_for(k, "up") for k in want}, want)
        self.assertEqual({k: L.md_for(k, "down") for k in want}, {k: -v for k, v in want.items()})
        self.assertTrue(all(L.md_for(k, "flat") == 0 for k in want))

    def test_first_move_text(self):
        self.assertEqual(L.first_move(1, 0.7, 10), "আগে হালকা DUMP (লিকুইডিটি/ফেক মুভ) → তারপর PUMP")
        self.assertEqual(L.first_move(-1, 0.2, 10), "সরাসরি DUMP")
        self.assertEqual(L.first_move(1, 0.9, 2), "সরাসরি PUMP")       # too few samples → no claim
        self.assertIn("whipsaw", L.first_move(0, None, 0))

    def test_fake_first(self):
        t0 = 1_700_000_000
        b = [(t0, 100, 100.2, 99.0, 99.4), (t0 + 900, 99.4, 101, 99.3, 100.8), (t0 + 1800, 100.8, 101.2, 100.7, 101.0),
             (t0 + 2700, 101, 101.5, 100.9, 101.4), (t0 + 3600, 101.4, 101.6, 101.3, 101.5)]
        self.assertTrue(L.fake_first(b, t0))           # dipped 1.0 first, then +1.5 → fake move

    def test_levels_changed_threshold(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = {"generated_at": now, "markets": {"gold": {"ok": True, "atr": 10.0}},
                "events": {"e": {"status": "pre", "scenarios": {"above": {"gold": {"dir": "DUMP", "t1": 4200, "t2": 4190, "pullback": 4208, "invalid": 4222}}}}}}
        small = json.loads(json.dumps(base)); small["events"]["e"]["scenarios"]["above"]["gold"]["t1"] = 4202   # 0.2 ATR
        big = json.loads(json.dumps(base)); big["events"]["e"]["scenarios"]["above"]["gold"]["t1"] = 4203.5    # 0.35 ATR
        self.assertFalse(L.levels_changed(base, small))
        self.assertTrue(L.levels_changed(base, big))
        old = json.loads(json.dumps(base)); old["generated_at"] = "2020-01-01T00:00:00Z"
        self.assertTrue(L.levels_changed(old, small))  # heartbeat


class BuildLevelsOffline(unittest.TestCase):
    """End-to-end build_levels with a fake price feed (no network)."""
    def fake_http(self, start):
        bars = bars_series(24 * 60, start=start, p0=100.0)
        def chart(url, params=None, **kw):
            ts = [x[0] for x in bars]
            q = {"open": [x[1] for x in bars], "high": [x[2] for x in bars], "low": [x[3] for x in bars], "close": [x[4] for x in bars]}
            j = {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [q]}, "meta": {"regularMarketPrice": bars[-1][4], "regularMarketTime": ts[-1]}}]}}
            return 200, json.dumps(j).encode()
        return chart, bars

    def test_pipeline_and_released_carry_over(self):
        start = 1_780_000_000 - 1_780_000_000 % 3600
        http, bars = self.fake_http(start)
        now = datetime.fromtimestamp(bars[-1][0] + 3600, timezone.utc)
        rel_t = lambda i: datetime.fromtimestamp(bars[i][0] + 1800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        archive = [{"currency": "USD", "impact": "High", "title": "CPI m/m", "time_utc": rel_t(100 + 120 * k),
                    "actual": "0.4%" if k % 2 else "0.2%", "forecast": "0.3%", "dir": 1, "time_special": None} for k in range(8)]
        fut = (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        an = {"order": ["e1", "e2"], "events": {
            "e1": {"title": "CPI m/m", "impact": "High", "time_utc": fut, "dir": 1, "data": {"actual": ""}, "verdict": {"kind": "data"},
                   "scenarios": [{"key": "above", "usd_bias": "up"}, {"key": "below", "usd_bias": "down"}, {"key": "inline", "usd_bias": "flat"}]},
            "e2": {"title": "CPI m/m", "impact": "High", "time_utc": rel_t(len(bars) - 30), "dir": 1, "data": {"actual": "0.4%"}, "verdict": {"kind": "data"},
                   "scenarios": [{"key": "above", "usd_bias": "up", "happened": True}]}}}
        prev = {"events": {"e2": {"status": "pre", "scenarios": {"above": {"gold": {"dir": "DUMP"}}}}}}
        doc = L.build_levels(an, archive, http, prev, now, log=lambda *a: None)
        self.assertTrue(all(m["ok"] for m in doc["markets"].values()))
        e1 = doc["events"]["e1"]
        self.assertEqual(e1["status"], "pre")
        for sk, s in (("above", "up"), ("below", "down"), ("inline", "flat")):
            for mk, lv in e1["scenarios"][sk].items():
                self.assertEqual(lv["md"], L.md_for(mk, s))
                self.assertEqual(lv["dir"], {1: "PUMP", -1: "DUMP", 0: "RANGE"}[lv["md"]])
        self.assertIn("গত ৮টা CPI m/m-এ", e1["basis"]["gold"])
        e2 = doc["events"]["e2"]
        self.assertEqual((e2["status"], e2["happened"]), ("released", "above"))
        self.assertEqual(e2["scenarios"]["above"]["gold"]["dir"], "DUMP")     # pre-release levels kept, not recomputed
        json.dumps(doc, allow_nan=False)


class AnalysisPrediction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(u, "DATA_DIR", self.tmp.name); self.p.start()

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def test_prediction_matches_verdict_and_tabs(self):
        t = (u.NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cal = {"generated_at": u.iso_utc(u.NOW), "events": [{"id": "e1", "title": "CPI m/m", "currency": "USD", "impact": "High",
               "time_utc": t, "time_special": None, "actual": "", "forecast": "0.6%", "previous": "0.4%", "revision": "", "abw": 0}]}
        lab = lambda outs: [{**o, "label": f"b{i}"} for i, o in enumerate(outs)]
        pm = {"source": "Polymarket", "url": "x", "title": "t", "outcomes": lab(CPI_POLY)}
        ks = {"source": "Kalshi", "url": "x", "title": "t", "outcomes": lab(CPI_KALSHI)}
        inf = lambda src: {**src, "outcomes": [{**o, "interval": [-math.inf if o["interval"][0] is None else o["interval"][0],
                                                                   math.inf if o["interval"][1] is None else o["interval"][1]]} for o in src["outcomes"]]}
        pe = {"polymarket": pm, "kalshi": ks, "vs_forecast": {"polymarket": u.vs_forecast(inf(pm), 0.6), "kalshi": u.vs_forecast(inf(ks), 0.6)}}
        with mock.patch.object(nowcasts, "cleveland", return_value=(None, "x")):
            a = u.build_analysis(cal, {"generated_at": u.iso_utc(u.NOW), "by_event": {"e1": pe}}, {}, [])["events"]["e1"]
        v, p, sc = a["verdict"], a["prediction"], a["scenarios"]
        self.assertEqual(p["side"], v["top"]["key"])
        self.assertEqual(sc[0]["key"], v["top"]["key"])
        self.assertIsNotNone(p["value_text"])


class GeneratedData(unittest.TestCase):
    def load(self, name):
        path = os.path.join(ROOT, "data", name)
        if not os.path.exists(path):
            self.skipTest(f"no {name}")
        return json.load(open(path, encoding="utf-8"))

    def test_predictions_consistent(self):
        an = self.load("analysis.json")
        for eid in an["order"]:
            a = an["events"][eid]
            if "prediction" not in a:
                self.skipTest("analysis.json predates round 4")
            with self.subTest(title=a["title"], time=a["time_utc"]):
                u.assert_prediction(a["verdict"], a["prediction"])
                if a["prediction"].get("side") and a["verdict"].get("top") and not a["prediction"].get("approx_only"):
                    self.assertEqual(a["prediction"]["side"], a["scenarios"][0]["key"])

    def test_levels_consistent(self):
        lv, an = self.load("levels.json"), self.load("analysis.json")
        for eid, e in lv["events"].items():
            if e.get("status") != "pre" or eid not in an["events"]:
                continue
            bias = {s["key"]: s["usd_bias"] for s in an["events"][eid]["scenarios"]}
            for sk, mks in e["scenarios"].items():
                for mk, x in mks.items():
                    with self.subTest(event=an["events"][eid]["title"], scenario=sk, market=mk):
                        self.assertEqual(x["md"], L.md_for(mk, bias[sk]))
                        if x["md"] > 0:
                            self.assertTrue(x["invalid"] < x["from"] < x["pullback"] < x["t1"] < x["t2"], x)
                        elif x["md"] < 0:
                            self.assertTrue(x["invalid"] > x["from"] > x["pullback"] > x["t1"] > x["t2"], x)
                        else:
                            self.assertTrue(x["break"][0] < x["range"][0] < x["from"] < x["range"][1] < x["break"][1], x)


if __name__ == "__main__":
    unittest.main()
