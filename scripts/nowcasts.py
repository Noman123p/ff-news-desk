"""Free official nowcasts (no API keys).

- Cleveland Fed Inflation Nowcasting: CPI / Core CPI / PCE / Core PCE (m/m and y/y)
- Atlanta Fed GDPNow: real GDP growth for the quarter being tracked

Functions take an `http_get(url, params=None, accept=..., timeout=...) -> (status, bytes)` callable
so they share the updater's retry/UA logic and can be mocked in tests.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone

CLE_PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
CLE_JSON = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_{kind}.json"
GDPNOW_PAGE = "https://www.atlantafed.org/cqer/research/gdpnow"
GDPNOW_XLSX = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/"
               "data/gdpnow/GDPTrackingModelDataAndForecasts.xlsx")

# category -> (chart kind, series name)
CLE_SERIES = {
    "cpi_mom": ("month", "CPI Inflation"), "core_cpi_mom": ("month", "Core CPI Inflation"),
    "cpi_yoy": ("year", "CPI Inflation"), "core_cpi_yoy": ("year", "Core CPI Inflation"),
    "pce_mom": ("month", "PCE Inflation"), "core_pce": ("month", "Core PCE Inflation"),
}
_cache: dict = {}


def ref_month(release_utc: datetime) -> str:
    """Inflation releases report the previous month: Oct 14 release -> '2026-9' (Cleveland's label format)."""
    d = release_utc.astimezone(timezone(timedelta(hours=-5))).date().replace(day=1) - timedelta(days=1)
    return f"{d.year}-{d.month}"


def cleveland(category: str, release_utc: datetime, http_get) -> tuple[dict | None, str]:
    if category not in CLE_SERIES:
        return None, "প্রযোজ্য নয়"
    kind, series = CLE_SERIES[category]
    if kind not in _cache:
        st, body = http_get(CLE_JSON.format(kind=kind), {"sc_lang": "en"})
        try:
            _cache[kind] = (json.loads(body.decode("utf-8")) if st == 200 else None, st)
        except ValueError:
            _cache[kind] = (None, st)
    charts, st = _cache[kind]
    if not isinstance(charts, list):
        return None, f"Cleveland Fed ডেটা পাওয়া যায়নি (HTTP {st})"
    want = ref_month(release_utc)
    chart = next((c for c in charts if (c.get("chart") or {}).get("subcaption") == want), None)
    if not chart:
        return None, f"{want} মাসের নাউকাস্ট নেই"
    ds = next((d for d in chart.get("dataset", []) if d.get("seriesname") == series), None)
    pts = [(i, v.get("value")) for i, v in enumerate((ds or {}).get("data", [])) if v.get("value") not in (None, "")]
    if not pts:
        return None, "নাউকাস্ট মান নেই"
    idx, raw = pts[-1]
    try:
        value = float(raw)
    except ValueError:
        return None, "নাউকাস্ট মান পড়া যায়নি"
    cats = (chart.get("categories") or [{}])[0].get("category", [])
    last_label = cats[idx].get("label") if idx < len(cats) else None
    comment = (chart.get("chart") or {}).get("_comment") or ""
    return {
        "source": "Cleveland Fed Inflation Nowcast",
        "kind": "nowcast",
        "value": round(value, 2),
        "unit": "%",
        "label": f"{series} ({'m/m' if kind == 'month' else 'y/y'}) · {want}",
        "as_of": (comment[:10] if comment else None),
        # the chart's own update stamp; per-point labels don't line up 1:1 with the data array
        "as_of_label": (comment[:10] if comment else last_label),
        "url": CLE_PAGE,
        "note": "দৈনিক আপডেট হওয়া মডেল-অনুমান (তেল ও পেট্রলের দাম + আগের ডেটা)। বাজারের সম্ভাবনা নয়।",
    }, "ok"


def _xlsx_rows(blob: bytes, sheet: str):
    z = zipfile.ZipFile(io.BytesIO(blob))
    wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
    sheets = dict(re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb))
    rels_xml = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
    rels = {}
    for tag in re.findall(r"<Relationship [^>]*/>", rels_xml):
        i = re.search(r'Id="([^"]+)"', tag)
        t = re.search(r'Target="([^"]+)"', tag)
        if i and t:
            rels[i.group(1)] = t.group(1)
    target = rels[sheets[sheet]].lstrip("/")
    path = target if target.startswith("xl/") else "xl/" + target
    try:
        ss = [re.sub(r"<[^>]+>", "", x) for x in
              re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8", "replace"), re.S)]
    except KeyError:
        ss = []
    xml = z.read(path).decode("utf-8", "replace")
    for row in re.findall(r"<row [^>]*>(.*?)</row>", xml, re.S):
        cells = {}
        for ref, attrs, v in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(?:<f[^>]*>.*?</f>|<f[^>]*/>)?<v>([^<]*)</v>', row, re.S):
            cells[ref] = ss[int(v)] if 't="s"' in attrs and v.isdigit() and int(v) < len(ss) else v
        yield cells


def gdpnow(http_get) -> tuple[dict | None, str]:
    if "gdpnow" in _cache:
        return _cache["gdpnow"]
    st, blob = http_get(GDPNOW_XLSX, accept="*/*", timeout=60)
    if st != 200 or not blob or blob[:2] != b"PK":
        _cache["gdpnow"] = (None, f"Atlanta Fed GDPNow ফাইল পাওয়া যায়নি (HTTP {st})")
        return _cache["gdpnow"]
    try:
        best, quarter = None, None
        for cells in _xlsx_rows(blob, "CurrentQtrEvolution"):
            cols = sorted(cells, key=lambda c: (len(c), c))   # A..Z, AA..AZ
            for i, c in enumerate(cols):
                txt = cells[c]
                m = re.search(r"Initial GDPNow (\d\d):Q(\d)", txt or "")
                if m:
                    quarter = f"20{m.group(1)}:Q{m.group(2)}"
            # triplets: Date | Major Releases | GDP*
            for i in range(0, len(cols) - 2):
                d, val = cells.get(cols[i]), cells.get(cols[i + 2])
                try:
                    serial, v = float(d), float(val)
                except (TypeError, ValueError):
                    continue
                if 40000 < serial < 60000 and -30 < v < 30 and (best is None or serial > best[0]):
                    best = (serial, v)
        if not best:
            raise ValueError("no numeric rows")
        as_of = (date(1899, 12, 30) + timedelta(days=int(best[0]))).isoformat()
        res = ({
            "source": "Atlanta Fed GDPNow",
            "kind": "nowcast",
            "value": round(best[1], 2),
            "unit": "%",
            "label": f"বাস্তব GDP প্রবৃদ্ধি (বার্ষিক হারে){' · ' + quarter if quarter else ''}",
            "as_of": as_of,
            "url": GDPNOW_PAGE,
            "note": "মডেল-অনুমান, অফিসিয়াল পূর্বাভাস নয়।",
        }, "ok")
    except Exception as e:  # layout changed
        res = (None, f"GDPNow ফাইল পড়া যায়নি ({type(e).__name__})")
    _cache["gdpnow"] = res
    return res
