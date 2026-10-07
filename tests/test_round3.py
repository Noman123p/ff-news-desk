"""Tests for the v2 pieces: week archive/backfill, FF specs, nowcasts, explainers, scenarios, analysis."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import update as u  # noqa: E402
import explainers  # noqa: E402
import nowcasts  # noqa: E402
import scenarios  # noqa: E402

UTC = timezone.utc


def ff_page(days):
    js = json.dumps(days)
    return ("<html><script>window.calendarComponentStates = {};\n"
            f"window.calendarComponentStates[1] = {{ days: {js}, time: 1 }};</script></html>").encode("cp1252")


def ff_event(eid, name, ts, cur="USD", impact="High", actual="", forecast="0.3%", previous="0.2%", abw=0, ebase=100):
    return {"id": eid, "ebaseId": ebase, "name": name, "currency": cur, "impactName": impact.lower(), "dateline": ts,
            "timeLabel": "8:30am", "timeMasked": False, "actual": actual, "forecast": forecast, "previous": previous,
            "revision": "", "actualBetterWorse": abw, "soloUrl": f"/calendar/{eid}-x"}


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(u, "DATA_DIR", self.tmp.name)
        self.p.start()
        u.FF_STATUS.clear()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()
        u.FF_STATUS.clear()


class WeekKeys(unittest.TestCase):
    def test_week_start_and_param(self):
        from datetime import date
        self.assertEqual(u.week_start(date(2026, 10, 7)).isoformat(), "2026-10-04")   # Wed → Sun
        self.assertEqual(u.week_start(date(2026, 10, 4)).isoformat(), "2026-10-04")   # Sun stays
        self.assertEqual(u.week_start(date(2026, 10, 10)).isoformat(), "2026-10-04")  # Sat
        self.assertEqual(u.week_start(date(2026, 1, 1)).isoformat(), "2025-12-28")    # year boundary
        self.assertEqual(u.week_param("2026-09-06"), "sep6.2026")
        self.assertEqual(u.week_param("2025-12-28"), "dec28.2025")

    def test_fetch_page_week_key_and_ids(self):
        # FF week starts Sunday 00:00 at UTC-5 → dateline 05:00Z
        start = int(datetime(2026, 9, 6, 5, tzinfo=UTC).timestamp())
        days = [{"dateline": start, "events": []},
                {"dateline": start + 86400, "events": [ff_event(77, "CPI m/m", start + 86400 + 12 * 3600, actual="0.4%", abw=2)]}]
        with mock.patch.object(u, "http_get", return_value=(200, ff_page(days))):
            rows, note, st = u.fetch_ff_html("sep6.2026")
        self.assertEqual(note, "ok")
        r = rows[0]
        self.assertEqual(r["ff_week"], "2026-09-06")
        self.assertEqual((r["ff_id"], r["ebase"], r["abw"]), (77, 100, 2))
        self.assertEqual(r["actual"], "0.4%")


class Archive(TempData):
    def rows(self, actual=""):
        return [{"title": "CPI m/m", "currency": "USD", "impact": "High", "time_utc": "2026-09-08T12:30:00Z",
                 "time_special": None, "actual": actual, "forecast": "0.3%", "previous": "0.2%", "revision": "",
                 "abw": 1 if actual else 0, "ff_id": 5, "ebase": 9}]

    def test_save_week_only_on_change_and_keeps_actual(self):
        self.assertTrue(u.save_week("2026-09-06", self.rows("0.4%"), "html"))
        self.assertFalse(u.save_week("2026-09-06", self.rows("0.4%"), "html"))      # unchanged → no write
        # fallback feed without Actual must not erase the stored one
        self.assertFalse(u.save_week("2026-09-06", self.rows(""), "feed"))
        doc = u.load_json("weeks/2026-09-06.json", {})
        self.assertEqual(doc["events"][0]["actual"], "0.4%")
        self.assertEqual(doc["events"][0]["category"], "cpi_mom")
        self.assertTrue(doc["complete"])

    def test_backfill_stops_quietly_and_never_touches_status(self):
        u.FF_STATUS["this"] = {"page_ok": True}
        u.FF_STATUS["next"] = {"page_ok": True}
        calls = []

        def fake(week):
            calls.append(week)
            if len(calls) >= 3:
                return None, "HTTP 429 — ব্লক করেছে", 429
            key = datetime.strptime(week, "%b%d.%Y").date().isoformat()
            r = self.rows("1.0%")
            r[0]["ff_week"] = key
            return r, "ok", 200
        with mock.patch.object(u, "fetch_ff_html", side_effect=fake), mock.patch.object(u.time, "sleep"), \
                mock.patch.object(u, "BACKFILL_PER_RUN", 10):
            rep = u.archive_weeks({})
        self.assertEqual(len(rep["backfilled"]), 2)
        self.assertIn("429", rep["stopped"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(u.FF_STATUS, {"this": {"page_ok": True}, "next": {"page_ok": True}})
        idx = u.load_json("weeks/index.json", {})
        self.assertEqual(len(idx["weeks"]), 2)

    def test_no_backfill_when_main_page_failed(self):
        u.FF_STATUS["this"] = {"page_ok": False}
        with mock.patch.object(u, "fetch_ff_html") as f:
            u.archive_weeks({})
        f.assert_not_called()


class Specs(unittest.TestCase):
    def test_fetch_specs_parse(self):
        body = json.dumps({"data": {"specs": [
            {"title": "Source", "html": '<a href="https://www.bls.gov/cpi/" target="_blank">Bureau of Labor Statistics</a>'},
            {"title": "Usual Effect", "html": "'Actual' greater than 'Forecast' is good for currency;"},
            {"title": "Why Traders<br>Care", "html": "Consumer prices account for a majority of overall inflation"}],
            "history": {"events": [{"date": "Sep 11, 2026", "actual": "0.4%", "forecast": "0.4%", "previous": "0.1%", "actualBetterWorse": 0}]}}}).encode()
        with mock.patch.object(u, "http_get", return_value=(200, body)):
            sp = u.fetch_specs({"ff_id": 1, "title": "CPI m/m"})
        self.assertEqual(sp["source_url"], "https://www.bls.gov/cpi/")
        self.assertIn("Why Traders Care", sp["specs"])
        self.assertEqual(u.usual_dir(sp, -1), +1)
        self.assertEqual(sp["history"][0]["actual"], "0.4%")
        with mock.patch.object(u, "http_get", return_value=(403, b"")):
            self.assertIsNone(u.fetch_specs({"ff_id": 1, "title": "x"}))

    def test_history_merge(self):
        ev = {"title": "CPI m/m", "currency": "USD", "ebase": 9, "time_utc": "2026-10-14T12:30:00Z"}
        archive = [{"title": "CPI m/m", "currency": "USD", "ebase": 9, "time_utc": "2026-09-11T12:30:00Z", "actual": "0.4%", "forecast": "0.4%", "previous": "0.1%", "abw": 0},
                   {"title": "CPI m/m", "currency": "USD", "ebase": 9, "time_utc": "2026-10-14T12:30:00Z", "actual": "", "forecast": "", "previous": "0.4%"}]
        specs = {"history": [{"date": "Sep 11, 2026", "actual": "0.4%"}, {"date": "Aug 12, 2026", "actual": "0.1%", "forecast": "0.2%", "previous": "-0.4%", "abw": 2}]}
        h = u.history_for(ev, archive, specs)
        self.assertEqual([x["date"] for x in h], ["2026-09-11", "2026-08-12"])


class Nowcasts(unittest.TestCase):
    def setUp(self):
        nowcasts._cache.clear()

    def test_ref_month(self):
        self.assertEqual(nowcasts.ref_month(datetime(2026, 10, 14, 12, 30, tzinfo=UTC)), "2026-9")
        self.assertEqual(nowcasts.ref_month(datetime(2026, 1, 13, 13, 30, tzinfo=UTC)), "2025-12")

    def test_cleveland(self):
        charts = [{"chart": {"subcaption": "2026-9", "_comment": "2026-10-07 00:00"},
                   "categories": [{"category": [{"label": "10/06"}, {"label": "10/07"}]}],
                   "dataset": [{"seriesname": "CPI Inflation", "data": [{"value": "0.51"}, {"value": "0.5306"}]},
                               {"seriesname": "Core CPI Inflation", "data": [{"value": ""}, {"value": ""}]}]}]
        get = mock.Mock(return_value=(200, json.dumps(charts).encode()))
        r, why = nowcasts.cleveland("cpi_mom", datetime(2026, 10, 14, 12, 30, tzinfo=UTC), get)
        self.assertEqual(why, "ok")
        self.assertAlmostEqual(r["value"], 0.53, places=2)
        self.assertEqual(r["as_of"], "2026-10-07")
        r2, why2 = nowcasts.cleveland("core_cpi_mom", datetime(2026, 10, 14, 12, 30, tzinfo=UTC), get)
        self.assertIsNone(r2)
        self.assertEqual(get.call_count, 1)                 # cached per chart kind
        self.assertEqual(nowcasts.cleveland("nfp", datetime.now(UTC), get), (None, "প্রযোজ্য নয়"))
        nowcasts._cache.clear()
        r3, why3 = nowcasts.cleveland("cpi_mom", datetime(2026, 12, 10, tzinfo=UTC), get)
        self.assertIsNone(r3)
        self.assertIn("2026-11", why3)

    def test_gdpnow_xlsx(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="CurrentQtrEvolution" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/sharedStrings.xml", "<sst><si><t>Initial GDPNow 26:Q3 forecast</t></si><si><t>ISM</t></si></sst>")
            z.writestr("xl/worksheets/sheet1.xml",
                       '<worksheet><sheetData>'
                       '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                       '<row r="2"><c r="A2"><v>46200</v></c><c r="B2" t="s"><v>1</v></c><c r="C2"><v>2.1</v></c></row>'
                       '<row r="3"><c r="A3"><v>46301</v></c><c r="B3" t="s"><v>1</v></c><c r="C3"><v>3.68</v></c></row>'
                       '</sheetData></worksheet>')
        get = mock.Mock(return_value=(200, buf.getvalue()))
        r, why = nowcasts.gdpnow(get)
        self.assertEqual(why, "ok")
        self.assertEqual(r["value"], 3.68)
        self.assertIn("2026:Q3", r["label"])
        self.assertEqual(r["as_of"], "2026-10-06")
        nowcasts._cache.clear()
        r, why = nowcasts.gdpnow(mock.Mock(return_value=(404, b"")))
        self.assertIsNone(r)
        self.assertIn("404", why)


class ExplainScen(unittest.TestCase):
    def test_explainers(self):
        self.assertEqual(explainers.explain("Core CPI m/m")["key"], "core_cpi")
        self.assertEqual(explainers.explain("Non-Farm Employment Change")["key"], "nfp")
        self.assertEqual(explainers.explain("Federal Funds Rate")["theme"], "fed")
        g = explainers.explain("Some Unknown Index")
        self.assertTrue(g["what"])
        for k in ("name_bn", "what", "why", "effect", "theme"):
            self.assertIn(k, g)

    def test_scenarios_data(self):
        sc = scenarios.build_scenarios(theme="inflation", impact="High", title="CPI m/m", kind="data", usd_dir=1,
                                       forecast="0.3%", ref_label="Forecast", probs={"above": 50, "inline": 30, "below": 20},
                                       lean=None, has_numbers=True)
        self.assertEqual([s["key"] for s in sc], ["above", "below", "inline"])
        self.assertEqual(sc[0]["usd_bias"], "up")
        self.assertEqual(sc[0]["markets"]["gold"]["dir"], "down")
        self.assertTrue(sc[0]["is_overall_top"])
        for s in sc:
            self.assertEqual(set(s["markets"]), {"gold", "btc", "crypto", "forex", "indices"})
            self.assertTrue(s["watch"])
        # unemployment-style (higher = USD negative)
        sc2 = scenarios.build_scenarios(theme="jobs", impact="High", title="Unemployment Rate", kind="data", usd_dir=-1,
                                        forecast="4.3%", ref_label="Forecast", probs=None, lean="below", has_numbers=True)
        self.assertEqual(sc2[0]["key"], "below")
        self.assertEqual(sc2[0]["usd_bias"], "up")
        self.assertIsNone(sc2[0]["prob"])
        self.assertIn("নেই", sc2[0]["note"])

    def test_scenarios_fed_neutral_top_comes_first(self):
        sc = scenarios.build_scenarios(theme="fed", impact="High", title="Federal Funds Rate", kind="fed", usd_dir=1,
                                       forecast=None, ref_label="", probs={"hawk": 10, "neutral": 85, "dove": 5},
                                       lean=None, has_numbers=False, is_decision=True)
        self.assertEqual([s["key"] for s in sc], ["neutral", "hawk", "dove"])
        self.assertTrue(sc[0]["is_overall_top"])
        self.assertIn("সবচেয়ে সম্ভাব্য", sc[0]["title"])
        self.assertEqual(sc[1]["title"], "বেশি সম্ভাব্য দিক (সারপ্রাইজ হলে)")
        self.assertEqual(sc[2]["title"], "উল্টো দিক")
        self.assertFalse(any(s["is_overall_top"] for s in sc[1:]))

    def test_scenarios_data_inline_top_orders_directions(self):
        # the old bug: 'most likely' tab showed a 30% side while in-line was 36%
        sc = scenarios.build_scenarios(theme="jobs", impact="High", title="Unemployment Claims", kind="data", usd_dir=-1,
                                       forecast="200K", ref_label="Forecast", probs={"above": 30, "inline": 36, "below": 34},
                                       lean=None, has_numbers=True)
        self.assertEqual([s["key"] for s in sc], ["inline", "below", "above"])
        self.assertEqual([s["prob"] for s in sc], [36, 34, 30])
        self.assertEqual(sc[1]["usd_bias"], "up")      # fewer claims = USD up

    def test_scenarios_never_claim_most_likely_without_data(self):
        sc = scenarios.build_scenarios(theme="speech", impact="Medium", title="President Trump Speaks", kind="speech",
                                       usd_dir=0, forecast=None, ref_label="", probs=None, lean=None, has_numbers=False)
        self.assertFalse(any(s["is_overall_top"] for s in sc))
        self.assertFalse(any("সবচেয়ে সম্ভাব্য" in s["title"] for s in sc))
        self.assertTrue(all(s["prob"] is None and s["prob_note"] for s in sc))

    def test_tie_is_flagged(self):
        sc = scenarios.build_scenarios(theme="inflation", impact="High", title="CPI m/m", kind="data", usd_dir=1,
                                       forecast="0.3%", ref_label="Forecast", probs={"above": 40, "inline": 40.2, "below": 19.8},
                                       lean=None, has_numbers=True)
        self.assertEqual(sc[0]["key"], "inline")
        self.assertIn("প্রায় সমান", sc[0]["note"])


class Analysis(TempData):
    def cal(self, title="CPI m/m", forecast="0.3%"):
        t = (u.NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"generated_at": u.iso_utc(u.NOW), "events": [{"id": "e1", "title": title, "currency": "USD", "impact": "High",
                "time_utc": t, "time_special": None, "actual": "", "forecast": forecast, "previous": "0.2%", "revision": "", "abw": 0}]}

    def market(self, name, dist):
        return {"source": name, "url": "https://x", "title": "t", "outcomes": [{"label": "a", "prob": 50}, {"label": "b", "prob": 50}]}

    def run_an(self, cal, pe, nc=(None, "x")):
        prob = {"generated_at": u.iso_utc(u.NOW), "by_event": {"e1": pe}}
        with mock.patch.object(nowcasts, "cleveland", return_value=nc), mock.patch.object(nowcasts, "gdpnow", return_value=(None, "x")):
            return u.build_analysis(cal, prob, {}, [])["events"]["e1"]

    def test_verdict_from_two_markets(self):
        pe = {"polymarket": self.market("Polymarket", None), "kalshi": self.market("Kalshi", None),
              "vs_forecast": {"polymarket": {"above": 60, "inline": 30, "below": 10}, "kalshi": {"above": 70, "inline": 20, "below": 10}}}
        a = self.run_an(self.cal(), pe)
        v = a["verdict"]
        self.assertEqual(v["top"]["key"], "above")
        self.assertEqual(v["top"]["pct"], 65.0)
        self.assertEqual(v["second"]["key"], "inline")
        self.assertEqual(v["confidence"], "মাঝারি–উচ্চ")
        self.assertEqual(a["scenarios"][0]["key"], "above")
        self.assertEqual(len(a["scenarios"]), 3)
        self.assertTrue(a["disclaimer"])

    def test_nowcast_only_has_no_percent(self):
        nc = ({"source": "Cleveland Fed Nowcast", "kind": "nowcast", "value": 0.46, "unit": "%"}, "ok")
        a = self.run_an(self.cal(), {}, nc)
        v = a["verdict"]
        self.assertEqual(v["top"]["key"], "above")      # 0.46 → 0.5 > 0.3
        self.assertIsNone(v["top"]["pct"])
        self.assertTrue(v["confidence"].startswith("নিম্ন"))
        self.assertIsNone(v["probs"])
        self.assertEqual(a["scenarios"][0]["key"], "above")
        self.assertTrue(a["scenarios"][0]["approx"])

    def test_nothing_means_no_numbers(self):
        a = self.run_an(self.cal(title="President Trump Speaks", forecast=""), {})
        self.assertIsNone(a["verdict"]["top"])
        self.assertEqual(a["verdict"]["confidence"], "ডেটা নেই")
        self.assertTrue(all(s["prob"] is None for s in a["scenarios"]))
        self.assertTrue(any(not s["ok"] for s in a["sources"]))


class Consistency(TempData):
    def fed_cal(self, title):
        t = (u.NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"generated_at": u.iso_utc(u.NOW), "events": [{"id": "e1", "title": title, "currency": "USD", "impact": "High",
                "time_utc": t, "time_special": None, "actual": "", "forecast": "", "previous": "", "revision": "", "abw": 0}]}

    def fed_pe(self):
        m = {"source": "Kalshi", "url": "https://k", "title": "Fed", "outcomes": [{"label": "Hike 25bps", "prob": 17.3}]}
        return {"kalshi": m, "polymarket": dict(m, source="Polymarket"),
                "fed_view": {"kalshi": {"hike": 16.9, "hold": 82.1, "cut": 1.0}, "polymarket": {"hike": 17.7, "hold": 81.7, "cut": 0.6}}}

    def build(self, title, pe):
        prob = {"generated_at": u.iso_utc(u.NOW), "by_event": {"e1": pe}}
        with mock.patch.object(nowcasts, "cleveland", return_value=(None, "x")):
            return u.build_analysis(self.fed_cal(title), prob, {}, [])["events"]["e1"]

    def test_minutes_are_tone_not_rate_odds(self):
        a = self.build("FOMC Meeting Minutes", self.fed_pe())
        v = a["verdict"]
        self.assertEqual(v["kind"], "tone")
        self.assertIsNone(v["probs"])                       # no fake event-outcome %
        self.assertEqual(v["top"]["key"], "neutral")
        self.assertIsNone(v["top"]["pct"])
        self.assertEqual(v["second"]["key"], "hawk")
        self.assertTrue(v["confidence"].startswith("নিম্ন"))
        self.assertIn("প্রেক্ষাপট", v["context"]["text"])
        self.assertAlmostEqual(v["context"]["hike"] + v["context"]["hold"] + v["context"]["cut"], 100.0, places=6)
        self.assertEqual([s["key"] for s in a["scenarios"]], ["neutral", "hawk", "dove"])
        self.assertTrue(all(s["prob"] is None for s in a["scenarios"]))
        self.assertIn("টোন", a["scenarios"][1]["condition"])
        self.assertTrue(all(s["context_only"] for s in a["sources"] if s["kind"] == "market" and s["ok"]))
        self.assertIn("% দেওয়া হচ্ছে না", v["summary"])

    def test_rate_decision_uses_odds_and_neutral_first(self):
        a = self.build("Federal Funds Rate", self.fed_pe())
        v = a["verdict"]
        self.assertEqual(v["kind"], "fed")
        self.assertEqual(v["top"]["key"], "neutral")
        self.assertEqual(v["total"], 100.0)
        self.assertEqual([s["key"] for s in a["scenarios"]], ["neutral", "hawk", "dove"])
        self.assertEqual(a["scenarios"][0]["prob"], v["top"]["pct"])
        self.assertTrue(v["summary"].startswith("নিউট্রাল — রেট হোল্ড সবচেয়ে সম্ভাব্য"))

    def test_normalize_and_brief_alignment(self):
        n = u.normalize_probs({"a": 33.33, "b": 33.33, "c": 33.33})
        self.assertEqual(round(sum(n.values()), 6), 100.0)
        a = self.build("Federal Funds Rate", self.fed_pe())
        b = {"id": "e1", "likely": {"text": "old", "usd_bias": True, "impact": {}}, "confidence_bn": "x", "reasons": []}
        b = u.align_brief(b, a)
        self.assertEqual(b["likely"]["text"], a["verdict"]["summary"])
        self.assertEqual(b["likely"]["usd_bias"], "flat")
        self.assertEqual(b["confidence_bn"], a["verdict"]["confidence"])

    def test_baseline_wording_matches_comparison(self):
        P = {"above": 84.9, "inline": 11.7, "below": 3.4}
        kw = dict(theme="inflation", impact="High", title="CPI m/m", kind="data", usd_dir=1, probs=P, lean=None, has_numbers=True)
        prev = scenarios.build_scenarios(forecast="0.4%", ref_label="Previous", baseline="previous", **kw)
        neu = next(x for x in prev if x["key"] == "inline")
        self.assertIn("আগের মানের কাছাকাছি", neu["title"])
        blob = " ".join(x["title"] + x["condition"] for x in prev)
        self.assertNotIn("প্রত্যাশার", blob)
        self.assertIn("Previous (0.4%)", prev[0]["condition"])
        self.assertIn("Forecast এখনো আসেনি", prev[0]["condition"])
        fc = scenarios.build_scenarios(forecast="0.3%", ref_label="Forecast", baseline="forecast", **kw)
        self.assertIn("প্রত্যাশার (Forecast) কাছাকাছি", next(x for x in fc if x["key"] == "inline")["title"])
        self.assertNotIn("আগের মান", " ".join(x["title"] + x["condition"] for x in fc))
        self.assertEqual(scenarios.data_labels("previous")["above"], "Previous-এর চেয়ে বেশি")
        self.assertEqual(scenarios.baseline_text("previous"), "তুলনা: Previous — Forecast এখনো আসেনি")
        self.assertEqual(scenarios.baseline_text("forecast"), "তুলনা: Forecast")

    def test_guard_rejects_contradiction(self):
        v = {"top": {"key": "neutral", "pct": 81.9}, "probs": {"hawk": 17.3, "neutral": 81.9, "dove": 0.8}}
        bad = [{"key": "hawk", "prob": 17.3, "is_overall_top": True}, {"key": "dove", "prob": 0.8}, {"key": "neutral", "prob": 81.9}]
        with self.assertRaises(AssertionError):
            u.assert_consistent(v, bad)


class GeneratedData(unittest.TestCase):
    """Every event in the committed data/analysis.json must be internally consistent."""
    def test_all_generated_events(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "analysis.json")
        if not os.path.exists(path):
            self.skipTest("no generated analysis.json")
        an = json.load(open(path, encoding="utf-8"))
        briefs_p = os.path.join(os.path.dirname(path), "briefs.json")
        briefs = {b["id"]: b for b in json.load(open(briefs_p, encoding="utf-8")).get("briefs", [])} if os.path.exists(briefs_p) else {}
        for eid in an["order"]:
            a = an["events"][eid]
            v, sc = a["verdict"], a["scenarios"]
            with self.subTest(title=a["title"]):
                u.assert_consistent(v, sc)
                if v.get("probs"):
                    self.assertAlmostEqual(sum(v["probs"].values()), 100.0, delta=0.15)
                    self.assertEqual(max(v["probs"], key=v["probs"].get), sc[0]["key"])
                if a["category"] == "fed_talk":
                    self.assertIsNone(v.get("probs"))
                tops = [s for s in sc if s["is_overall_top"]]
                self.assertLessEqual(len(tops), 1)
                if tops:
                    self.assertIs(tops[0], sc[0])
                if not v.get("top"):
                    self.assertFalse(any("সবচেয়ে সম্ভাব্য" in s["title"] for s in sc))
                if eid in briefs:
                    self.assertEqual(briefs[eid]["likely"]["text"], v["summary"])
                if v.get("kind") == "data" and v.get("baseline"):
                    bl = v["baseline"]
                    self.assertEqual(v["labels"], scenarios.data_labels(bl))
                    self.assertEqual(v["baseline_text"], scenarios.baseline_text(bl))
                    wrong = "প্রত্যাশার" if bl == "previous" else "আগের মান"
                    texts = [x["title"] + x["condition"] for x in sc] + list(v["labels"].values()) + [v.get("summary") or ""]
                    if eid in briefs:
                        texts.append(briefs[eid]["likely"]["text"])
                    self.assertFalse(any(wrong in t for t in texts), (bl, [t for t in texts if wrong in t]))


if __name__ == "__main__":
    unittest.main()
