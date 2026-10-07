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
            with mock.patch.object(u, "fetch_ff_html", return_value=(None, "HTTP 403")), \
                 mock.patch.object(u, "get_json", side_effect=lambda url, params=None: (feed, 200) if "thisweek" in url else (None, 404)):
                cal = u.build_calendar()
            self.assertEqual(len(cal["events"]), 1)
            self.assertEqual(cal["events"][0]["actual"], "0.5%")
            self.assertEqual(cal["events"][0]["time_utc"], "2026-10-14T12:30:00Z")

    def test_total_failure_keeps_previous(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(u, "DATA_DIR", d):
            json.dump({"events": [{"id": "x", "title": "old"}]}, open(os.path.join(d, "calendar.json"), "w"))
            with mock.patch.object(u, "fetch_ff_html", return_value=(None, "HTTP 403")), \
                 mock.patch.object(u, "get_json", return_value=(None, 503)):
                cal = u.build_calendar()
            self.assertTrue(cal["stale"])
            self.assertEqual(cal["events"][0]["title"], "old")


if __name__ == "__main__":
    unittest.main()
