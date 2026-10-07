"""Headless smoke test: python tests/smoke.py [base_url]  (needs `pip install playwright` + Chrome/Chromium)."""
import json, sys, os
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots")
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")
problems = []


def attach(page, tag):
    page.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"[{tag}] console.{m.type}: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"[{tag}] pageerror: {e}"))
    page.on("requestfailed", lambda r: problems.append(f"[{tag}] requestfailed: {r.url} {r.failure}"))
    page.on("response", lambda r: r.status >= 400 and problems.append(f"[{tag}] HTTP {r.status}: {r.url}"))


with sync_playwright() as p:
    br = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
    # Desktop, browser in New York TZ to prove times are rendered in Dhaka regardless of viewer TZ
    ctx = br.new_context(viewport={"width": 1366, "height": 900}, timezone_id="America/New_York", locale="en-US", device_scale_factor=1)
    pg = ctx.new_page(); attach(pg, "desktop")
    pg.goto(BASE, wait_until="networkidle"); pg.wait_for_timeout(1500)
    res = {}
    res["count_default"] = pg.inner_text("#resultCount")
    res["rows_default"] = pg.locator(".row").count()
    res["briefs"] = pg.locator("#briefs article").count()
    res["next_title"] = pg.inner_text("#nextTitle")
    res["next_time"] = pg.inner_text("#nextTime")
    # timezone check: compare first row time with data
    cal = json.loads(pg.evaluate("fetch('data/calendar.json').then(r=>r.text())"))
    res["status"] = pg.inner_text("#statusText")
    first = pg.locator(".row").first
    fid = first.get_attribute("data-id")
    ev = next(e for e in cal["events"] if e["id"] == fid)
    res["tz_check"] = {"utc": ev["time_utc"], "shown": first.locator(".t-time").inner_text(), "title": ev["title"]}
    # every row must match filters (USD + High/Medium + this week)
    ids = pg.eval_on_selector_all(".row", "els => els.map(e => e.dataset.id)")
    byid = {e["id"]: e for e in cal["events"]}
    bad = [byid[i]["title"] for i in ids if byid[i]["currency"] != "USD" or byid[i]["impact"] not in ("High", "Medium") or byid[i]["week"] != "this"]
    if bad: problems.append(f"default filter leak: {bad}")
    exp = [e for e in cal["events"] if e["currency"] == "USD" and e["impact"] in ("High", "Medium") and e["week"] == "this"]
    if len(exp) != len(ids): problems.append(f"default filter count mismatch: shown {len(ids)} expected {len(exp)}")
    pg.screenshot(path=f"{OUT}/desktop-full.png", full_page=True)
    pg.screenshot(path=f"{OUT}/desktop.png")
    # open a probability row
    pg.click("#fRange button[data-v=next]"); pg.wait_for_timeout(700)
    res["rows_next"] = pg.locator(".row").count()
    cpi = pg.locator(".row.has-prob", has_text="CPI m/m").first
    cpi.locator(".row-main").click(); pg.wait_for_timeout(1400)
    res["cpi_open_bars"] = cpi.locator(".bar").count()
    res["cpi_bar_widths"] = cpi.locator(".bar-fill").evaluate_all("els => els.slice(0,4).map(e => e.style.width)")
    cpi.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/desktop-probability.png")
    # filters: all impacts + all currencies
    for v in ("Low", "Holiday"): pg.click(f"#fImpact .chip[data-v={v}]")
    pg.click("#fCur .chip[data-v='*']"); pg.click("#fRange button[data-v=all]"); pg.wait_for_timeout(700)
    res["rows_all"] = pg.locator(".row").count(); res["total_events"] = len(cal["events"])
    # search
    pg.fill("#fSearch", "zzzz"); pg.wait_for_timeout(600)
    res["empty_state"] = pg.locator(".no-rows").count()
    pg.click("[data-act=reset]"); pg.wait_for_timeout(700)
    res["after_reset"] = pg.inner_text("#resultCount")
    # today filter
    pg.click("#fRange button[data-v=today]"); pg.wait_for_timeout(600)
    res["rows_today"] = pg.locator(".row").count()
    # keyboard toggle
    pg.click("#fRange button[data-v=this]"); pg.wait_for_timeout(600)
    fomc = pg.locator(".row.has-prob", has_text="FOMC Meeting Minutes").first
    fomc.locator(".row-main").focus(); pg.keyboard.press("Enter"); pg.wait_for_timeout(900)
    res["fomc_open"] = "open" in (fomc.get_attribute("class") or "")
    res["fomc_text_has_fedwatch"] = "CME FedWatch" in fomc.inner_text()
    ctx.close()

    # Mobile (Dhaka TZ, reduced motion)
    m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                       timezone_id="Asia/Dhaka", reduced_motion="reduce")
    mp = m.new_page(); attach(mp, "mobile")
    mp.goto(BASE, wait_until="networkidle"); mp.wait_for_timeout(1200)
    res["mobile_overflow_px"] = mp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    mp.screenshot(path=f"{OUT}/mobile.png")
    mp.screenshot(path=f"{OUT}/mobile-full.png", full_page=True)
    m.close()

    # Missing-data resilience: block probabilities + briefs
    e = br.new_context(viewport={"width": 1200, "height": 800})
    ep = e.new_page(); attach(ep, "missing-data")
    ep.route("**/data/probabilities.json*", lambda r: r.fulfill(status=404, body="nope"))
    ep.route("**/data/briefs.json*", lambda r: r.fulfill(status=200, body='{"briefs":[],"empty_message":"আজ বড় কোনো USD নিউজ নেই","next_high":null}', content_type="application/json"))
    ep.goto(BASE, wait_until="networkidle"); ep.wait_for_timeout(1000)
    res["missing_brief_text"] = ep.inner_text("#briefs")[:80]
    res["missing_rows"] = ep.locator(".row").count()
    e.close()
    # Calendar missing entirely
    e2 = br.new_context(); p2 = e2.new_page(); attach(p2, "no-calendar")
    p2.route("**/data/calendar.json*", lambda r: r.fulfill(status=500, body="x"))
    p2.goto(BASE, wait_until="networkidle"); p2.wait_for_timeout(800)
    res["no_cal_status"] = p2.inner_text("#statusText"); res["no_cal_rows"] = p2.inner_text("#rows")[:60]
    e2.close()
    br.close()

# expected 404/500 from the deliberately broken contexts are not problems
problems = [x for x in problems if not (x.startswith("[missing-data]") and ("404" in x or "probabilities" in x)) and not x.startswith("[no-calendar]")]
print(json.dumps(res, ensure_ascii=False, indent=1))
print("PROBLEMS:", json.dumps(problems, ensure_ascii=False, indent=1))
