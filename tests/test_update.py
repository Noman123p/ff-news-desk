"""Unit tests for scripts/update.py helpers (stdlib only): python -m unittest discover tests"""
import math, os, sys, json, tempfile, unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import update as u  # noqa: E402


class Values(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(u.parse_value("0.3%"), 0.3)
        self.assertEqual(u.parse_value("200K"), 200000)
        self.assertEqual(u.parse_value("-1.2M"), -1200000)
        self.assertEqual(u.parse_value("1,234.5"), 1234.5)
        self.assertEqual(u.parse_value("<0.1%"), 0.1)
        self.assertIsNone(u.parse_value(""))
        self.assertIsNone(u.parse_value(None))
        self.assertIsNone(u.parse_value("n/a"))

    def test_parse_bucket(self):
        self.assertEqual(u.parse_bucket("0.4%"), (0.4, 0.4))
        self.assertEqual(u.parse_bucket("≤0.0%"), (-math.inf, 0.0))
        self.assertEqual(u.parse_bucket("≥0.8%"), (0.8, math.inf))
        self.assertEqual(u.parse_bucket("0.6%+"), (0.6, math.inf))
        self.assertEqual(u.parse_bucket("25k to 50k"), (25000, 50000))
        self.assertEqual(u.parse_bucket("<-25k"), (-math.inf, -25000))
        self.assertEqual(u.parse_bucket("100k+"), (100000, math.inf))
        self.assertEqual(u.parse_bucket("-25k to 0"), (-25000, 0))
        self.assertIsNone(u.parse_bucket("No change"))

    def test_side_of(self):
        # Kalshi 'greater' buckets (lo, hi]
        self.assertEqual(u.side_of((0.3, 0.4), "lo", 0.4), "inline")
        self.assertEqual(u.side_of((0.4, 0.5), "lo", 0.4), "above")
        self.assertEqual(u.side_of((0.2, 0.3), "lo", 0.4), "below")
        self.assertEqual(u.side_of((-math.inf, 0.0), "lo", 0.4), "below")
        # Kalshi '>=' buckets [lo, hi)
        self.assertEqual(u.side_of((200000, 205000), "hi", 200000), "inline")
        self.assertEqual(u.side_of((195000, 200000), "hi", 200000), "below")
        self.assertEqual(u.side_of((205000, math.inf), "hi", 200000), "above")
        # Polymarket closed buckets
        self.assertEqual(u.side_of((0.4, 0.4), None, 0.4), "inline")
        self.assertEqual(u.side_of((0.8, math.inf), None, 0.4), "above")

    def test_classify(self):
        self.assertEqual(u.classify("Philly Fed Manufacturing Index")[0], "other")
        self.assertEqual(u.classify("FOMC Meeting Minutes")[0], "fed_talk")
        self.assertEqual(u.classify("Fed Chair Powell Speaks")[0], "fed_talk")
        self.assertEqual(u.classify("Federal Funds Rate")[0], "fed_decision")
        self.assertEqual(u.classify("Core PPI m/m")[0], "core_ppi")
        self.assertEqual(u.classify("Unemployment Claims"), ("claims", -1))
        self.assertEqual(u.classify("Non-Farm Employment Change")[0], "nfp")

    def test_vs_forecast(self):
        src = {"outcomes": [{"label": "a", "prob": 20, "interval": (0.2, 0.3), "open": "lo"},
                            {"label": "b", "prob": 50, "interval": (0.3, 0.4), "open": "lo"},
                            {"label": "c", "prob": 30, "interval": (0.4, math.inf), "open": "lo"}]}
        self.assertEqual(u.vs_forecast(src, 0.4), {"above": 30.0, "inline": 50.0, "below": 20.0})
        self.assertIsNone(u.vs_forecast(src, None))
        self.assertIsNone(u.vs_forecast(None, 0.4))

    def test_kalshi_price(self):
        self.assertAlmostEqual(u.kalshi_price({"yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44", "last_price_dollars": "0.1"})[0], 0.42)
        # untraded, very wide spread -> no number rather than a fake 0
        self.assertIsNone(u.kalshi_price({"yes_bid_dollars": "0.00", "yes_ask_dollars": "1.00", "last_price_dollars": "0.0000", "volume_fp": "0"})[0])

    def test_json_safe(self):
        self.assertEqual(u.json_safe({"a": [math.inf, -math.inf, 1.0]}), {"a": [None, None, 1.0]})


class CalendarFallback(unittest.TestCase):
    def test_html_fail_uses_json_and_keeps_old_actuals(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "DATA_DIR", d):
            eid = u.event_id("USD", "CPI m/m", "2026-10-14T12:30:00Z")
            json.dump({"events": [{"id": eid, "actual": "0.5%"}]}, open(os.path.join(d, "calendar.json"), "w"))
            feed = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "0.4%", "previous": "0.4%"}]
            with mock.patch.object(u, "fetch_ff_html", return_value=(None, "HTTP 403 — ব্লক করেছে", 403)), \
                 mock.patch.object(u, "get_json", side_effect=lambda url, params=None: (feed, 200) if "thisweek" in url else (None, 404)):
                cal = u.build_calendar()
            st = u.build_status(cal, {"sources": {"polymarket": "ok", "kalshi": "ok"}})
            self.assertFalse(st["ff_page_ok"])
            self.assertTrue(st["ff_feed_ok"])
            self.assertEqual(st["ff_http_status"], 403)
            self.assertIn("HTTP 403", st["ff_reason"])
            self.assertIn("JSON", st["fallback_in_use"])
            out = os.path.join(d, "gh_out")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": out}):
                u.gh_outputs(st)
            lines = dict(l.split("=", 1) for l in open(out, encoding="utf-8").read().splitlines())
            self.assertEqual(lines["ff_page_ok"], "false")
            self.assertEqual(lines["ff_http"], "403")
            self.assertEqual(lines["ff_feed_ok"], "true")
            self.assertEqual(len(cal["events"]), 1)
            self.assertEqual(cal["events"][0]["actual"], "0.5%")
            self.assertEqual(cal["events"][0]["time_utc"], "2026-10-14T12:30:00Z")

    def test_total_failure_keeps_previous(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "DATA_DIR", d):
            json.dump({"events": [{"id": "x", "title": "old"}]}, open(os.path.join(d, "calendar.json"), "w"))
            with mock.patch.object(u, "fetch_ff_html", return_value=(None, "HTTP 403", 403)), \
                 mock.patch.object(u, "get_json", return_value=(None, 503)):
                cal = u.build_calendar()
            self.assertTrue(cal["stale"])
            self.assertEqual(cal["events"][0]["title"], "old")


class FFPageDetection(unittest.TestCase):
    def setUp(self):
        u.FF_STATUS.clear()

    def test_captcha_page(self):
        html = b"<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x.js'></script></html>"
        with mock.patch.object(u, "http_get", return_value=(200, html)):
            rows, reason, http = u.fetch_ff_html("this")
        self.assertIsNone(rows); self.assertIn("ক্যাপচা", reason); self.assertEqual(http, 200)

    def test_403_and_timeout(self):
        with mock.patch.object(u, "http_get", return_value=(403, b"")):
            self.assertEqual(u.fetch_ff_html("this")[1:], ("HTTP 403 — ব্লক করেছে", 403))
        with mock.patch.object(u, "http_get", return_value=(None, b"")):
            rows, reason, http = u.fetch_ff_html("this")
        self.assertIsNone(http); self.assertIn("টাইমআউট", reason)

    def test_ok_page(self):
        days = [{"events": [{"name": "CPI m/m", "currency": "USD", "impactName": "high", "dateline": 1791981000,
                             "timeLabel": "7:30am", "actual": "0.5%", "forecast": "0.4%", "previous": "0.4%",
                             "soloUrl": "/calendar/1-us-cpi-mm"}]}]
        html = ("x calendarComponentStates[1] = {\ndays: " + json.dumps(days) + ", more: 1};").encode("cp1252")
        with mock.patch.object(u, "http_get", return_value=(200, html)):
            rows, reason, http = u.fetch_ff_html("this")
        self.assertEqual(reason, "ok"); self.assertEqual(rows[0]["actual"], "0.5%")
        self.assertEqual(rows[0]["time_utc"], "2026-10-14T12:30:00Z")


class ChangeDetection(unittest.TestCase):
    def test_same_content(self):
        a = {"generated_at": "x", "by_event": {"e": {"prob": 50.0, "label": "A", "volume_usd": 1}}}
        b = {"generated_at": "y", "by_event": {"e": {"prob": 51.0, "label": "A", "volume_usd": 999}}}
        self.assertTrue(u.same_content(a, b))
        b["by_event"]["e"]["prob"] = 52.0
        self.assertFalse(u.same_content(a, b))
        self.assertFalse(u.same_content({"k": [1, 2]}, {"k": [1, 2, 3]}))

    def test_write_if_changed_and_heartbeat(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "DATA_DIR", d):
            obj = {"generated_at": u.iso_utc(u.NOW), "v": 1}
            u.write_if_changed("t.json", obj)
            kept = u.write_if_changed("t.json", {"generated_at": "2099-01-01T00:00:00Z", "v": 1})
            self.assertEqual(kept["generated_at"], obj["generated_at"])          # unchanged -> kept
            old = {"generated_at": "2000-01-01T00:00:00Z", "v": 1}
            u.write_json("t.json", old)
            new = u.write_if_changed("t.json", {"generated_at": u.iso_utc(u.NOW), "v": 1})
            self.assertNotEqual(new["generated_at"], old["generated_at"])        # heartbeat -> rewritten


if __name__ == "__main__":
    unittest.main()
