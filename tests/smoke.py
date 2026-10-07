"""Headless smoke test (v2 — dashboard · calendar · analysis).

    python tests/smoke.py [base_url]        # needs `pip install playwright` + Chrome/Chromium
    SHOTS=screenshots/v2 python tests/smoke.py http://localhost:8765/

The browser runs in America/New_York on purpose: every time must still render in Dhaka time.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
OUT = os.environ.get("SHOTS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "screenshots", "v2")
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")
os.makedirs(OUT, exist_ok=True)
DHAKA = timezone(timedelta(hours=6))
problems, notes = [], []
ROUTES = ["#/dashboard", "#/calendar", "#/analysis"]


def attach(page, tag, allow_404=()):
    def on_resp(r):
        if r.status >= 400 and not any(a in r.url for a in allow_404):
            problems.append(f"[{tag}] HTTP {r.status}: {r.url}")
    page.on("console", lambda m: m.type == "error" and not (allow_404 and "404" in m.text) and problems.append(f"[{tag}] console.error: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"[{tag}] pageerror: {e}"))
    page.on("response", on_resp)


def check(cond, msg):
    if not cond:
        problems.append(msg)
    return cond


def overflow(pg):
    return pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def settle(pg, ms=900):
    pg.wait_for_timeout(ms)


def h(pg):
    return pg.evaluate("location.hash")


with sync_playwright() as p:
    br = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": 1366, "height": 900}, timezone_id="America/New_York", locale="en-US", device_scale_factor=1)
    pg = ctx.new_page(); attach(pg, "desktop")
    pg.goto(BASE, wait_until="networkidle"); settle(pg, 1500)
    check(h(pg) == "#/dashboard", f"root should redirect to #/dashboard, got {h(pg)}")
    an = json.loads(pg.evaluate("fetch('data/analysis.json').then(r=>r.text())"))
    cal = json.loads(pg.evaluate("fetch('data/calendar.json').then(r=>r.text())"))
    idx = json.loads(pg.evaluate("fetch('data/weeks/index.json').then(r=>r.text())"))
    now = datetime.now(timezone.utc)
    today = now.astimezone(DHAKA).date()

    # ---------------- dashboard
    usd_today = [e for e in cal["events"] if e["currency"] == "USD" and e["impact"] in ("High", "Medium")
                 and datetime.fromisoformat(e["time_utc"].replace("Z", "+00:00")).astimezone(DHAKA).date() == today]
    n_cards = pg.locator(".ev-card").count()
    check(n_cards == len(usd_today), f"dashboard today cards {n_cards} != expected {len(usd_today)}")
    check(pg.locator("#ffBanner").is_hidden(), "banner should be hidden when FF ok")
    for e in usd_today[:3]:
        t = datetime.fromisoformat(e["time_utc"].replace("Z", "+00:00")).astimezone(DHAKA)
        want = t.strftime("%I:%M %p").lstrip("0")
        card = pg.locator(f'.ev-card[href="#/analysis/{e["id"]}"]')
        if not e.get("time_special"):
            check(want in card.inner_text(), f"dashboard time for {e['title']}: want {want} (Dhaka), got {card.locator('.t').inner_text()}")
    pg.screenshot(path=f"{OUT}/desktop-dashboard.png", full_page=True)
    notes.append(f"dashboard: {n_cards} today cards, {pg.locator('.brief').count()} briefs")

    # countdown ticks
    if pg.locator(".hero [data-cd]").count():
        a1 = pg.inner_text(".hero [data-cd] [data-u=s]"); settle(pg, 1200); a2 = pg.inner_text(".hero [data-cd] [data-u=s]")
        check(a1 != a2, "hero countdown not ticking")

    # ---------------- drawer menu
    pg.click("#menuBtn"); settle(pg, 500)
    check(pg.evaluate("document.body.classList.contains('drawer-open')"), "drawer did not open")
    check(pg.get_attribute("#menuBtn", "aria-expanded") == "true", "menu aria-expanded not true")
    pg.screenshot(path=f"{OUT}/desktop-menu.png")
    pg.keyboard.press("Escape"); settle(pg, 450)
    check(not pg.evaluate("document.body.classList.contains('drawer-open')"), "Escape did not close drawer")
    pg.click("#menuBtn"); settle(pg, 400)
    pg.click(".drawer-nav a[data-nav=calendar]"); settle(pg, 1200)
    check(h(pg) == "#/calendar", f"drawer nav → calendar failed: {h(pg)}")
    check(not pg.evaluate("document.body.classList.contains('drawer-open')"), "drawer should close after navigating")

    # ---------------- calendar
    rows = pg.locator(".c-row").count()
    check(rows > 20, f"calendar week view has only {rows} rows")
    check(pg.locator(".c-row.next").count() <= 1, "more than one 'next' row")
    pg.screenshot(path=f"{OUT}/desktop-calendar.png")
    # timezone: FOMC-ish check — every visible row time equals its UTC time converted to Dhaka
    wk = json.loads(pg.evaluate(f"fetch('data/weeks/{idx['weeks'][-2]}.json').then(r=>r.text())"))
    sample = [e for e in wk["events"] if not e.get("time_special")][:200]
    shown = pg.evaluate("""() => { let last=''; return [...document.querySelectorAll('.c-row')].map(r => { const t = r.querySelector('.c-time').childNodes[0]?.textContent || ''; if (t) last = t; return [r.dataset.id, last]; }); }""")
    shown = dict(shown)
    mism = []
    for e in sample:
        if e["id"] in shown:
            want = datetime.fromisoformat(e["time_utc"].replace("Z", "+00:00")).astimezone(DHAKA).strftime("%I:%M %p").lstrip("0")
            if shown[e["id"]] != want:
                mism.append((e["title"], want, shown[e["id"]]))
    check(not mism, f"calendar Dhaka time mismatches: {mism[:3]}")
    # Today / This week / Next week / arrows / picker
    pg.click("[data-q=today]"); settle(pg)
    check(f"d={today.isoformat()}&m=day" in h(pg), f"Today button hash: {h(pg)}")
    check("আজ" in pg.inner_text("#rangeLabel"), "Today label missing 'আজ'")
    pg.click("[data-step='1']"); settle(pg)
    check(f"d={(today + timedelta(days=1)).isoformat()}&m=day" in h(pg), f"next-day arrow: {h(pg)}")
    pg.click("[data-q=next]"); settle(pg)
    sun = today - timedelta(days=(today.weekday() + 1) % 7)
    check(f"d={(sun + timedelta(days=7)).isoformat()}&m=week" in h(pg), f"Next week hash: {h(pg)}")
    pg.click("[data-step='-1']"); settle(pg)
    check(f"d={sun.isoformat()}&m=week" in h(pg), f"prev-week arrow: {h(pg)}")
    # back button walks the calendar history
    pg.go_back(); settle(pg)
    check(f"d={(sun + timedelta(days=7)).isoformat()}&m=week" in h(pg), f"back after prev-week: {h(pg)}")
    # date picker → any date (archive) ; week boundary: a Saturday and the following Sunday
    pg.fill("#datePick", "2026-03-14"); pg.dispatch_event("#datePick", "change"); settle(pg, 1200)
    check("d=2026-03-14&m=day" in h(pg), f"date picker hash: {h(pg)}")
    sat = pg.locator(".c-row").count()
    check(sat >= 0 and pg.locator(".c-day").count() == 1, "day view should show exactly one day")
    pg.click("[data-step='1']"); settle(pg, 1200)
    check("d=2026-03-15&m=day" in h(pg), f"Sat→Sun arrow across week boundary: {h(pg)}")
    sunday_rows = pg.locator(".c-row").count()
    notes.append(f"week boundary: Sat 2026-03-14 rows={sat}, Sun 2026-03-15 rows={sunday_rows}")
    # Dhaka-day correctness for an archived day: compare with week JSON computed in Python
    def expected_day(k):
        d = datetime.fromisoformat(k).date()
        start = datetime(d.year, d.month, d.day, tzinfo=DHAKA); end = start + timedelta(days=1)
        ids = set()
        for w in idx["weeks"]:
            wd = datetime.fromisoformat(w).date()
            if wd > d or wd < d - timedelta(days=8):
                continue
            doc = json.loads(pg.evaluate(f"fetch('data/weeks/{w}.json').then(r=>r.text())"))
            for e in doc["events"]:
                t = datetime.fromisoformat(e["time_utc"].replace("Z", "+00:00"))
                if start <= t < end:
                    ids.add(e["id"])
        return ids
    for k in ("2026-03-15", "2026-03-14", "2026-03-13", "2026-03-08", today.isoformat()):
        pg.goto(BASE + f"#/calendar?d={k}&m=day"); settle(pg, 1300)
        got = set(pg.eval_on_selector_all(".c-row", "els => els.map(e => e.dataset.id)"))
        exp = expected_day(k)
        check(got == exp, f"day {k}: shown {len(got)} vs expected {len(exp)} (missing {list(exp - got)[:3]}, extra {list(got - exp)[:3]})")
    # empty state: date outside archive → link to that FF day
    pg.goto(BASE + "#/calendar?d=2024-01-10&m=day"); settle(pg, 1000)
    link = pg.locator(".c-empty a").first
    check(link.count() == 1 and "day=jan10.2024" in (link.get_attribute("href") or ""), "empty-state FF link missing/wrong for 2024-01-10")
    pg.screenshot(path=f"{OUT}/desktop-calendar-empty.png")
    pg.goto(BASE + "#/calendar?d=2027-02-01&m=week"); settle(pg, 1000)
    check(pg.locator(".c-empty").count() == 7, "future week should show 7 empty days")
    # invalid date param falls back to today
    pg.goto(BASE + "#/calendar?d=2026-02-31&m=day"); settle(pg, 900)
    check("আজ" in pg.inner_text("#rangeLabel"), "invalid date should fall back to today")
    # filters persist
    pg.goto(BASE + "#/calendar"); settle(pg, 1100)
    allrows = pg.locator(".c-row").count()
    pg.click("#filterToggle"); settle(pg, 500)
    pg.click("[data-cur=USD]"); pg.click("[data-imp=High]"); settle(pg, 400)
    fr = pg.eval_on_selector_all(".c-row", "els => els.map(e => [e.querySelector('.c-cur').textContent, e.className])")
    check(all(c == "USD" and "imp-High" in cl for c, cl in fr), "filter leak (USD+High)")
    check(len(fr) < allrows, "filter didn't reduce rows")
    pg.screenshot(path=f"{OUT}/desktop-calendar-filters.png")
    pg.reload(); settle(pg, 1300)
    check(pg.locator(".c-row").count() == len(fr), "filters not persisted after reload")
    pg.evaluate("localStorage.removeItem('ffdesk.cal.v2')"); pg.reload(); settle(pg, 1200)
    check(pg.locator(".c-row").count() == allrows, "default (all) filters not restored")
    # expand row → analysis link
    row = pg.locator(".c-row:has(.has-an)").first
    if row.count():
        row.locator(".c-main").click(); settle(pg, 600)
        check("open" in row.get_attribute("class"), "row did not expand")
        pg.screenshot(path=f"{OUT}/desktop-calendar-expanded.png")
        rid = row.get_attribute("data-id")
        row.locator("a.btn").click(); settle(pg, 1400)
        check(h(pg).startswith(f"#/analysis/{rid}"), f"row → analysis link: {h(pg)}")
        pg.go_back(); settle(pg, 1200)
        check(h(pg).startswith("#/calendar"), f"back from analysis → calendar failed: {h(pg)}")
    # keyboard: Enter toggles row
    pg.locator(".c-main").first.focus(); pg.keyboard.press("Enter"); settle(pg, 300)
    check(pg.locator(".c-row.open").count() == 1, "keyboard Enter did not expand row")

    # ---------------- analysis
    pg.goto(BASE + "#/analysis"); settle(pg, 1500)
    check(re.match(r"#/analysis/[0-9a-f]{12}$", h(pg)) is not None, f"#/analysis should pick a default event: {h(pg)}")
    pg.screenshot(path=f"{OUT}/desktop-analysis.png", full_page=True)
    for sid in ("sec-what", "sec-when", "sec-data", "sec-prob", "sec-scen"):
        check(pg.locator(f"#{sid}").count() == 1, f"analysis missing section {sid}")
    # pick a data event with probabilities (CPI preferred)
    best = next((i for i in an["order"] if an["events"][i]["verdict"].get("top") and an["events"][i]["verdict"].get("probs")), None)
    if best:
        pg.click(f'.pick[href="#/analysis/{best}"]'); settle(pg, 1300)
        check(h(pg) == f"#/analysis/{best}", "picker navigation failed")
        check(pg.locator(".verdict .v-pct").count() == 1, "verdict % missing")
        pg.locator("#sec-prob").scroll_into_view_if_needed(); settle(pg, 1200)
        pg.screenshot(path=f"{OUT}/desktop-analysis-probability.png")
        tabs = pg.eval_on_selector_all(".sc-tabs .sc-p", "els => els.map(e => e.textContent)")
        bnd = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
        nums = [float(t.translate(bnd).rstrip('%')) for t in tabs if t.strip()]
        check(len(nums) == 3 and nums[0] == max(nums), f"tab 1 must be the highest probability: {tabs}")
        check(abs(sum(nums) - 100) < 0.3, f"tab %s don't add to ~100: {tabs}")
        vp = float(pg.inner_text(".verdict .v-pct").translate(bnd).rstrip('%'))
        check(vp == nums[0], f"verdict % {vp} != tab 1 {nums[0]}")
        pg.click("[data-sc='1']"); settle(pg, 500)
        check(pg.get_attribute("[data-sc='1']", "aria-selected") == "true", "scenario tab 2 not selected")
        pg.locator("#sec-scen").scroll_into_view_if_needed(); settle(pg, 400)
        pg.screenshot(path=f"{OUT}/desktop-analysis-scenarios.png")
        pg.go_back(); settle(pg, 1200)
        check(h(pg) != f"#/analysis/{best}", "back from picked event did nothing")
    # every generated event: tab order/labels never contradict the numbers (checked in the rendered page)
    for eid in an["order"]:
        ev = an["events"][eid]
        pg.goto(BASE + f"#/analysis/{eid}"); settle(pg, 700)
        titles = pg.eval_on_selector_all(".sc-tabs .sc-t", "els => els.map(e => e.textContent)")
        if not ev["verdict"].get("top"):
            check(not any("সবচেয়ে সম্ভাব্য" in t for t in titles), f"{ev['title']}: 'most likely' shown without data: {titles}")
        if ev["category"] == "fed_talk":
            check(pg.locator(".verdict .v-rows").count() == 0, f"{ev['title']}: rate odds shown as event outcome")
            check(pg.locator(".v-context").count() == 1, f"{ev['title']}: rate-market context box missing")
        dash_top = ev["verdict"].get("top")
        check(pg.locator(".sc-tabs button").count() == 3, f"{ev['title']}: expected 3 scenario tabs")
    # live countdown on analysis
    fut = next((i for i in an["order"] if datetime.fromisoformat(an["events"][i]["time_utc"].replace("Z", "+00:00")) > now + timedelta(minutes=2)), None)
    if fut:
        pg.goto(BASE + f"#/analysis/{fut}"); settle(pg, 1300)
        s1 = pg.inner_text("#sec-when [data-u=s]"); settle(pg, 1100); s2 = pg.inner_text("#sec-when [data-u=s]")
        check(s1 != s2, "analysis countdown not ticking")
    # unknown id + plain (non-analysis) event
    pg.goto(BASE + "#/analysis/ffffffffffff"); settle(pg, 1200)
    check("পাওয়া যায়নি" in pg.inner_text("#view"), "unknown analysis id should show not-found")
    eur = next((e for e in wk["events"] if e["currency"] == "EUR" and e.get("actual")), None)
    if eur:
        pg.goto(BASE + f"#/analysis/{eur['id']}?w={idx['weeks'][-2]}"); settle(pg, 2000)
        check(eur["title"] in pg.inner_text(".an-hero"), "plain analysis view for non-USD event failed")
        pg.screenshot(path=f"{OUT}/desktop-analysis-plain.png", full_page=True)
    # keyboard shortcuts + overflow on every route
    for r in ROUTES:
        pg.goto(BASE + r); settle(pg, 1200)
        check(overflow(pg) <= 0, f"desktop horizontal overflow on {r}: {overflow(pg)}px")
    pg.keyboard.press("2"); settle(pg, 900)
    check(h(pg).startswith("#/calendar"), "keyboard shortcut 2 → calendar failed")
    # unknown route → dashboard
    pg.goto(BASE + "#/nope/x"); settle(pg, 900)
    check(h(pg) == "#/dashboard", f"unknown route should go to dashboard: {h(pg)}")
    # perf sanity
    perf = pg.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0]; const r = performance.getEntriesByType('resource');
        return { dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd), requests: r.length,
                 kb: Math.round(r.reduce((a, x) => a + (x.transferSize || 0), 0) / 1024) }; }""")
    notes.append(f"perf (desktop, cold-ish): {perf}")
    ctx.close()

    # ---------------- mobile
    m = br.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=1.5, timezone_id="America/New_York", locale="en-US")
    mp = m.new_page(); attach(mp, "mobile")
    for r, name in (("#/dashboard", "dashboard"), ("#/calendar", "calendar"), (f"#/analysis/{best}" if best else "#/analysis", "analysis")):
        mp.goto(BASE + r, wait_until="networkidle"); settle(mp, 1500)
        check(overflow(mp) <= 0, f"mobile horizontal overflow on {r}: {overflow(mp)}px")
        mp.screenshot(path=f"{OUT}/mobile-{name}.png")
        mp.screenshot(path=f"{OUT}/mobile-{name}-full.png", full_page=True)
    mp.click("#menuBtn"); settle(mp, 600)
    mp.screenshot(path=f"{OUT}/mobile-menu.png")
    mp.click(".drawer-nav a[data-nav=dashboard]"); settle(mp, 1200)
    check(h(mp) == "#/dashboard", "mobile drawer navigation failed")
    mp.goto(BASE + "#/calendar"); settle(mp, 1300)
    mp.locator(".c-row .c-main").first.click(); settle(mp, 500)
    check(overflow(mp) <= 0, "mobile overflow with expanded row")
    mp.screenshot(path=f"{OUT}/mobile-calendar-expanded.png")
    m.close()

    # ---------------- failure modes
    bctx = br.new_context(viewport={"width": 1366, "height": 900})
    bp = bctx.new_page(); attach(bp, "blocked")
    st = json.loads(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "status.json"), encoding="utf-8").read())
    st.update(ff_page_ok=False, ff_http_status=403, ff_reason="HTTP 403 — ব্লক করেছে", fallback_in_use="faireconomy JSON ফিড (Actual নেই)")
    bp.route("**/data/status.json*", lambda r: r.fulfill(status=200, body=json.dumps(st), content_type="application/json"))
    bp.goto(BASE + "#/dashboard"); settle(bp, 1300)
    check(bp.locator("#ffBanner").is_visible(), "blocked banner not visible")
    bp.screenshot(path=f"{OUT}/desktop-blocked-banner.png")
    bctx.close()

    nctx = br.new_context(viewport={"width": 1366, "height": 900})
    np_ = nctx.new_page(); attach(np_, "no-data", allow_404=("analysis.json", "calendar.json", "index.json"))
    np_.route("**/data/analysis.json*", lambda r: r.fulfill(status=404, body="x"))
    np_.route("**/data/calendar.json*", lambda r: r.fulfill(status=404, body="x"))
    np_.route("**/data/weeks/index.json*", lambda r: r.fulfill(status=404, body="x"))
    np_.goto(BASE + "#/dashboard"); settle(np_, 1200)
    check("লোড করা যায়নি" in np_.inner_text("#view"), "dashboard without calendar should show error state")
    np_.goto(BASE + "#/analysis"); settle(np_, 1200)
    check("কোনো নিউজ নেই" in np_.inner_text("#view"), "analysis without data should show empty state")
    np_.goto(BASE + "#/calendar"); settle(np_, 1200)
    check(np_.locator(".c-empty").count() == 7, "calendar without index should show empty days with FF links")
    nctx.close()

    rctx = br.new_context(viewport={"width": 1366, "height": 900}, reduced_motion="reduce")
    rp = rctx.new_page(); attach(rp, "reduced-motion")
    for r in ROUTES:
        rp.goto(BASE + r); settle(rp, 800)
        check(rp.locator(".view").count() == 1, f"reduced-motion render failed on {r}")
    rctx.close()
    br.close()

print("\n".join("· " + n for n in notes))
if problems:
    print(f"\n{len(problems)} PROBLEM(S):")
    print("\n".join("✗ " + x for x in problems))
    sys.exit(1)
print("\nALL SMOKE CHECKS PASSED")
