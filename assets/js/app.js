/* নিউজ ডেস্ক — front-end (no build step). Reads data/*.json produced by scripts/update.py. */
(() => {
  "use strict";

  const TZ = "Asia/Dhaka";
  const STORE_KEY = "ffdesk.filters.v1";
  const DEFAULTS = { range: "this", impacts: ["High", "Medium"], curs: ["USD"], q: "" };
  const RANGES = ["today", "this", "next", "all"];
  const IMPACTS = ["High", "Medium", "Low", "Holiday"];
  const CUR_ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY", "All"];
  const REFRESH_MS = 10 * 60 * 1000;
  const STALE_H = 14;   // updater rewrites files on change + 12h heartbeat, so >14h means Actions stopped
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  // ---------------------------------------------------------------- formatting
  const fmtTime = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true });
  const fmtClock = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const fmtKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
  const fmtDay = new Intl.DateTimeFormat("bn-BD", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });
  const fmtShortDay = new Intl.DateTimeFormat("bn-BD", { timeZone: TZ, weekday: "short", day: "numeric", month: "short" });
  const bnNum = (n, opts) => Number(n).toLocaleString("bn-BD", opts);
  const dayKey = (d) => fmtKey.format(d);
  const SPECIAL = { "All Day": "সারাদিন", "Tentative": "অনির্ধারিত", "Day 1": "দিন ১", "Day 2": "দিন ২", "Day 3": "দিন ৩" };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (typeof u === "string" && /^https:\/\//i.test(u) ? u : null);
  const icon = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;

  function parseNum(s) {
    if (s == null) return null;
    const m = String(s).replace(/,/g, "").match(/(-?\d+(?:\.\d+)?)\s*([KMBT]?)/i);
    if (!m) return null;
    const mult = { "": 1, K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[m[2].toUpperCase()];
    return parseFloat(m[1]) * mult;
  }

  function relAgo(date) {
    const min = Math.round((Date.now() - date.getTime()) / 60000);
    if (min < 1) return "এইমাত্র";
    if (min < 60) return `${bnNum(min)} মিনিট আগে`;
    const h = Math.floor(min / 60);
    if (h < 48) return `${bnNum(h)} ঘণ্টা আগে`;
    return `${bnNum(Math.floor(h / 24))} দিন আগে`;
  }

  function untilText(ms) {
    if (ms <= 0) return "রিলিজ হয়েছে";
    const m = Math.round(ms / 60000);
    if (m < 60) return `${bnNum(m)} মিনিট পর`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${bnNum(h)} ঘ ${bnNum(r)} মি পর` : `${bnNum(h)} ঘণ্টা পর`;
  }

  // ---------------------------------------------------------------- state
  const state = loadFilters();
  let DATA = { cal: null, prob: { by_event: {} }, briefs: null, status: null };
  const openRows = new Set();

  function loadFilters() {
    try {
      const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (s && typeof s === "object") {
        return {
          range: RANGES.includes(s.range) ? s.range : DEFAULTS.range,
          impacts: new Set(Array.isArray(s.impacts) ? s.impacts.filter((x) => IMPACTS.includes(x)) : DEFAULTS.impacts),
          curs: new Set(Array.isArray(s.curs) ? s.curs.filter((x) => typeof x === "string" && x.length <= 4) : DEFAULTS.curs),
          q: typeof s.q === "string" ? s.q.slice(0, 60) : "",
        };
      }
    } catch (_) { /* private mode / bad JSON */ }
    return { range: DEFAULTS.range, impacts: new Set(DEFAULTS.impacts), curs: new Set(DEFAULTS.curs), q: "" };
  }
  function saveFilters() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({ range: state.range, impacts: [...state.impacts], curs: [...state.curs], q: state.q }));
    } catch (_) { /* ignore */ }
  }

  // ---------------------------------------------------------------- data
  async function getJSON(path) {
    const r = await fetch(`${path}?t=${Math.floor(Date.now() / 60000)}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  async function load() {
    const [cal, prob, briefs, status] = await Promise.allSettled([
      getJSON("data/calendar.json"), getJSON("data/probabilities.json"), getJSON("data/briefs.json"),
      getJSON("data/status.json"),
    ]);
    if (cal.status !== "fulfilled" || !cal.value || !Array.isArray(cal.value.events)) {
      setStatus("err", "ডেটা লোড হয়নি");
      $("#rows").innerHTML = `<div class="no-rows">${icon("alert")} ক্যালেন্ডার ডেটা লোড করা যায়নি। একটু পরে আবার চেষ্টা করুন।</div>`;
      $("#briefs").innerHTML = "";
      if (cal.reason) console.warn(cal.reason);
      return;
    }
    const events = cal.value.events
      .filter((e) => e && typeof e.time_utc === "string" && !isNaN(Date.parse(e.time_utc)))
      .map((e) => ({ ...e, t: new Date(e.time_utc) }))
      .sort((a, b) => a.t - b.t);
    DATA = {
      cal: { ...cal.value, events },
      prob: prob.status === "fulfilled" && prob.value && prob.value.by_event ? prob.value : { by_event: {}, sources: {} },
      briefs: briefs.status === "fulfilled" ? briefs.value : null,
      status: status.status === "fulfilled" && status.value && typeof status.value === "object" ? status.value : null,
    };
    renderBanner();
    refreshStatus();

    renderCurrencyChips();
    syncFilterUI();
    renderHero();
    renderBriefs();
    renderRows(false);
    renderSources();
  }

  function lastUpdate() {
    // files are only rewritten on real changes, so take the newest timestamp of all of them
    const ts = [DATA.cal && DATA.cal.generated_at, DATA.prob && DATA.prob.generated_at,
                DATA.briefs && DATA.briefs.generated_at, DATA.status && DATA.status.checked_at]
      .map((x) => Date.parse(x)).filter((x) => !isNaN(x));
    return ts.length ? new Date(Math.max(...ts)) : null;
  }

  function refreshStatus() {
    if (!DATA.cal) return;
    const gen = lastUpdate();
    if (!gen) { setStatus("stale", "আপডেটের সময় অজানা"); return; }
    const ageH = (Date.now() - gen.getTime()) / 3.6e6;
    const blocked = DATA.status && DATA.status.ff_page_ok === false;
    if (DATA.cal.stale || ageH > STALE_H) setStatus("stale", `আপডেট ${relAgo(gen)} · পুরোনো হতে পারে`);
    else if (blocked) setStatus("stale", `আপডেট ${relAgo(gen)} · ব্যাকআপ ফিড`);
    else setStatus("ok", `আপডেট ${relAgo(gen)}`);
    $("#status").title = `শেষ ডেটা পরিবর্তন: ${fmtDay.format(gen)}, ${fmtTime.format(gen)} (ঢাকা) · ডেটা প্রতি ~২ ঘণ্টায় যাচাই হয়`;
  }

  function renderBanner() {
    const el = $("#ffBanner");
    const st = DATA.status;
    if (!st || st.ff_page_ok !== false) { el.hidden = true; el.innerHTML = ""; return; }
    const since = Date.parse(st.checked_at);
    el.innerHTML = `${icon("alert")}<span><b>ForexFactory এখন আমাদের আপডেটার ব্লক করছে</b> — `
      + `${esc(st.fallback_in_use || "ব্যাকআপ ফিড চলছে")}। নতুন Actual দেরিতে আসতে পারে।`
      + `${st.ff_reason ? ` <span class="muted">(${esc(st.ff_reason)}${isNaN(since) ? "" : ` · ${fmtTime.format(new Date(since))}`})</span>` : ""}</span>`;
    el.hidden = false;
  }

  function setStatus(kind, text) {
    const el = $("#status");
    el.className = `status ${kind}`;
    $("#statusText").textContent = text;
  }

  // ---------------------------------------------------------------- hero
  let nextHigh = null;
  function renderHero() {
    const now = Date.now();
    const usd = DATA.cal.events.filter((e) => e.currency === "USD");
    nextHigh = usd.find((e) => e.impact === "High" && e.t.getTime() > now) || null;
    $("#nextTitle").textContent = nextHigh ? nextHigh.title : "এই দুই সপ্তাহে আর কোনো হাই-ইমপ্যাক্ট USD নিউজ নেই";
    $("#nextTime").textContent = nextHigh
      ? `${fmtDay.format(nextHigh.t)} · ${fmtTime.format(nextHigh.t)} (ঢাকা)${nextHigh.forecast ? ` · Forecast ${nextHigh.forecast}` : ""}`
      : "";
    const today = dayKey(new Date());
    const nToday = usd.filter((e) => (e.impact === "High" || e.impact === "Medium") && dayKey(e.t) === today).length;
    const nWeek = usd.filter((e) => e.impact === "High" && e.week === "this").length;
    const nProb = Object.values(DATA.prob.by_event || {}).filter((p) => p && (p.polymarket || p.kalshi)).length;
    countTo($("#statToday"), nToday);
    countTo($("#statWeek"), nWeek);
    countTo($("#statProb"), nProb);
    tickCountdown();
  }

  function tickCountdown() {
    const set = (k, v) => { const el = $(`[data-cd="${k}"]`); if (el.textContent !== v) el.textContent = v; };
    if (!nextHigh) { set("h", "--"); set("m", "--"); set("s", "--"); return; }
    let ms = nextHigh.t.getTime() - Date.now();
    if (ms <= 0) { renderHero(); return; }
    const h = Math.floor(ms / 3.6e6); ms -= h * 3.6e6;
    const m = Math.floor(ms / 6e4); ms -= m * 6e4;
    const s = Math.floor(ms / 1000);
    set("h", String(h).padStart(2, "0")); set("m", String(m).padStart(2, "0")); set("s", String(s).padStart(2, "0"));
  }

  function countTo(el, target, opts = {}) {
    const dec = opts.decimals || 0;
    const suffix = opts.suffix || "";
    const fmt = (v) => bnNum(v, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suffix;
    if (reduceMotion || !isFinite(target)) { el.textContent = fmt(target); return; }
    const t0 = performance.now(), dur = 900;
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(target * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- briefs
  const ASSETS = [["gold", "গোল্ড (XAUUSD)", "gold"], ["btc", "বিটকয়েন", "btc"], ["crypto", "ক্রিপ্টো মার্কেট", "layers"], ["forex", "ফরেক্স (DXY)", "fx"]];

  function biasChip(b) {
    if (b === "up") return `<span class="bias up">${icon("up")}USD শক্তিশালী</span>`;
    if (b === "down") return `<span class="bias down">${icon("down")}USD দুর্বল</span>`;
    return `<span class="bias flat">${icon("flat")}দিক অনিশ্চিত</span>`;
  }
  function assetGrid(imp) {
    if (!imp || typeof imp !== "object") return `<div class="nodata">${icon("info")}ডেটা নেই</div>`;
    return `<div class="assets">${ASSETS.map(([k, label, ic]) => imp[k]
      ? `<div class="asset">${icon(ic)}<div><b>${label}</b>${k === "forex"
          ? `<span class="fx">${String(imp[k]).split(" · ").map(esc).join("<br>")}</span>`
          : `<span>${esc(imp[k])}</span>`}</div></div>` : "").join("")}</div>`;
  }

  function renderBriefs() {
    const box = $("#briefs");
    const B = DATA.briefs;
    if (!B || !Array.isArray(B.briefs)) {
      box.innerHTML = `<div class="card empty reveal">${icon("info")}<div class="big">ব্রিফ ডেটা নেই</div><div>briefs.json লোড হয়নি।</div></div>`;
      return;
    }
    const now = Date.now();
    const list = B.briefs.filter((b) => b && !isNaN(Date.parse(b.time_utc)) && Date.parse(b.time_utc) > now - 30 * 60000);
    if (!list.length) {
      const nh = B.next_high && !isNaN(Date.parse(B.next_high.time_utc)) ? B.next_high : null;
      box.innerHTML = `<div class="card empty reveal">${icon("calendar")}
        <div class="big">${esc(B.empty_message || "আজ বড় কোনো USD নিউজ নেই")}</div>
        <div>${nh ? `পরবর্তী হাই-ইমপ্যাক্ট: <b>${esc(nh.title)}</b> — ${fmtDay.format(new Date(nh.time_utc))}, ${fmtTime.format(new Date(nh.time_utc))}` : "পরবর্তী সময়ের জন্য ক্যালেন্ডার দেখুন।"}</div></div>`;
      return;
    }
    box.innerHTML = list.map((b, i) => {
      const t = new Date(b.time_utc);
      const ms = t.getTime() - now;
      const alert = ms > 0 && ms <= 3 * 3.6e6;
      const subs = Array.isArray(b.events) && b.events.length > 1
        ? `<div class="subevents">${b.events.map((e) => `<span>${esc(e.title)} · F <b>${esc(e.forecast || "—")}</b> · P <b>${esc(e.previous || "—")}</b></span>`).join("")}</div>`
        : (b.events && b.events[0] && (b.events[0].forecast || b.events[0].previous || b.events[0].actual) ? `<div class="subevents"><span>Forecast <b>${esc(b.events[0].forecast || "—")}</b></span><span>Previous <b>${esc(b.events[0].previous || "—")}</b></span>${b.events[0].actual ? `<span>Actual <b>${esc(b.events[0].actual)}</b></span>` : ""}</div>` : "");
      const alt = b.alternative || {};
      const altBody = alt.impact && alt.impact.usd_up
        ? `<h4>${icon("up")}USD শক্তিশালী হলে</h4>${assetGrid(alt.impact.usd_up)}<h4>${icon("down")}USD দুর্বল হলে</h4>${assetGrid(alt.impact.usd_down)}`
        : `<h4>${biasChip(alt.usd_bias)}</h4>${assetGrid(alt.impact)}`;
      const reasons = Array.isArray(b.reasons) && b.reasons.length
        ? `<ul class="reasons">${b.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "";
      const tr = b.typical_range || {};
      return `<article class="card is-${esc(b.impact)} reveal" style="--i:${i}">
        <div class="card-top">
          <span class="when">${icon("clock")}${fmtShortDay.format(t)} · <b class="tnum">${fmtTime.format(t)}</b></span>
          <span class="badge ${alert ? "alert" : ""}" data-until="${esc(b.time_utc)}">${alert ? icon("alert") : ""}${untilText(ms)}</span>
        </div>
        <h3><i class="dot dot-${esc(b.impact)} live"></i> ${esc(b.title)}</h3>
        <div class="cat">${esc(b.category_bn || "")}</div>
        ${subs}
        <div class="likely">
          <div class="likely-label"><span>সবচেয়ে সম্ভাব্য</span>${biasChip(b.likely && b.likely.usd_bias)}</div>
          <p>${esc(b.likely && b.likely.text)}</p>
        </div>
        ${assetGrid(b.likely && b.likely.impact)}
        <details class="alt"><summary><span>${esc(alt.text || "বিপরীত ফলাফল এলে")}</span>${icon("chevron")}</summary><div class="alt-body">${altBody}</div></details>
        ${reasons}
        <div class="card-foot">
          <span>আত্মবিশ্বাস: <b>${esc(b.confidence_bn || "—")}</b></span>
          ${tr.gold ? `<span title="${esc(tr.note || "")}">সাধারণ মুভ: গোল্ড <b>${esc(tr.gold)}</b> · BTC <b>${esc(tr.btc)}</b></span>` : ""}
        </div>
        <p class="disclaimer">${icon("info")}<span>${esc(b.disclaimer || "সম্ভাবনাভিত্তিক বিশ্লেষণ, গ্যারান্টি নয়।")}${tr.note ? ` (${esc(tr.note)})` : ""}</span></p>
      </article>`;
    }).join("");
  }

  function tickBadges() {
    refreshStatus();
    const now = Date.now();
    $$("[data-until]").forEach((el) => {
      const ms = Date.parse(el.dataset.until) - now;
      const alert = ms > 0 && ms <= 3 * 3.6e6;
      el.classList.toggle("alert", alert);
      el.innerHTML = (alert ? icon("alert") : "") + untilText(ms);
    });
  }

  // ---------------------------------------------------------------- filters
  function renderCurrencyChips() {
    const present = [...new Set(DATA.cal.events.map((e) => e.currency).filter(Boolean))];
    present.sort((a, b) => (CUR_ORDER.indexOf(a) + 1 || 99) - (CUR_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b));
    const allOn = present.every((c) => state.curs.has(c));
    $("#fCur").innerHTML =
      `<button class="chip cur" data-v="*" aria-pressed="${allOn}">সব</button>` +
      present.map((c) => `<button class="chip cur" data-v="${esc(c)}" aria-pressed="${state.curs.has(c)}">${c === "All" ? "গ্লোবাল" : esc(c)}</button>`).join("");
    $("#fCur").dataset.present = present.join(",");
  }

  function syncFilterUI() {
    $$("#fRange button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === state.range)));
    $$("#fImpact .chip").forEach((b) => b.setAttribute("aria-pressed", String(state.impacts.has(b.dataset.v))));
    const present = ($("#fCur").dataset.present || "").split(",").filter(Boolean);
    $$("#fCur .chip").forEach((b) => {
      const v = b.dataset.v;
      b.setAttribute("aria-pressed", String(v === "*" ? present.length > 0 && present.every((c) => state.curs.has(c)) : state.curs.has(v)));
    });
    if ($("#fSearch").value !== state.q) $("#fSearch").value = state.q;
    moveSegPill();
  }

  function moveSegPill() {
    const active = $(`#fRange button[data-v="${state.range}"]`);
    const pill = $("#fRange .seg-pill");
    if (!active || !pill) return;
    pill.style.width = `${active.offsetWidth}px`;
    pill.style.transform = `translateX(${active.offsetLeft}px)`;
  }

  function bindFilters() {
    $("#fRange").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      state.range = b.dataset.v; onFilter();
    });
    $("#fImpact").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      const v = b.dataset.v;
      state.impacts.has(v) ? state.impacts.delete(v) : state.impacts.add(v);
      onFilter();
    });
    $("#fCur").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]"); if (!b) return;
      const v = b.dataset.v;
      const present = ($("#fCur").dataset.present || "").split(",").filter(Boolean);
      if (v === "*") {
        const allOn = present.every((c) => state.curs.has(c));
        state.curs = new Set(allOn ? [] : present);
      } else {
        state.curs.has(v) ? state.curs.delete(v) : state.curs.add(v);
      }
      onFilter();
    });
    let tmr;
    $("#fSearch").addEventListener("input", (e) => {
      clearTimeout(tmr);
      tmr = setTimeout(() => { state.q = e.target.value.trim().slice(0, 60); onFilter(); }, 160);
    });
    $("#fReset").addEventListener("click", () => {
      state.range = DEFAULTS.range; state.impacts = new Set(DEFAULTS.impacts); state.curs = new Set(DEFAULTS.curs); state.q = "";
      onFilter();
    });
    window.addEventListener("resize", moveSegPill, { passive: true });
  }

  function onFilter() {
    saveFilters();
    syncFilterUI();
    renderRows(true);
  }

  function filtered() {
    const today = dayKey(new Date());
    const q = state.q.toLowerCase();
    return DATA.cal.events.filter((e) => {
      if (!state.impacts.has(e.impact)) return false;
      if (!state.curs.has(e.currency)) return false;
      if (state.range === "today" && dayKey(e.t) !== today) return false;
      if ((state.range === "this" || state.range === "next") && e.week !== state.range) return false;
      if (q && !(`${e.title} ${e.currency}`.toLowerCase().includes(q))) return false;
      return true;
    });
  }

  // ---------------------------------------------------------------- table
  function actualClass(e) {
    const a = parseNum(e.actual), f = parseNum(e.forecast);
    if (a == null || f == null || a === f || !e.dir) return "";
    return (a - f) * e.dir > 0 ? "beat" : "miss";
  }

  function hasMarket(p) { return p && (p.polymarket || p.kalshi); }

  function rowHTML(e, i, nextId) {
    const p = DATA.prob.by_event[e.id];
    const past = e.t.getTime() < Date.now();
    const special = e.time_special ? (SPECIAL[e.time_special] || e.time_special) : null;
    const cls = ["row", !(e.actual || e.forecast || e.previous) ? "novals" : "", p ? "has-prob" : "", past ? "past" : "", e.id === nextId ? "next-up" : "", openRows.has(e.id) ? "open" : ""].filter(Boolean).join(" ");
    const val = (v, label, extra = "") => `<span class="v num ${v ? extra : "na"}"><span class="v-label">${label}</span>${v ? esc(v) : "—"}</span>`;
    return `<div class="${cls} reveal" style="--i:${Math.min(i, 18)}" data-id="${esc(e.id)}">
      <div class="row-main" ${p ? `tabindex="0" role="button" aria-expanded="${openRows.has(e.id)}"` : ""}>
        <span class="t-time">${special ? `<span>${esc(special)}</span>` : fmtTime.format(e.t)}${special ? "" : ""}</span>
        <span class="t-cur">${esc(e.currency === "All" ? "GLB" : e.currency)}</span>
        <i class="dot dot-${esc(e.impact)} ${!past && e.impact === "High" ? "live" : ""}" title="${esc(e.impact)}"></i>
        <span class="t-title"><span class="name">${esc(e.title)}</span>${hasMarket(p) ? `<span class="pill-prob">সম্ভাবনা</span>` : ""}</span>
        <span class="vals">${val(e.actual, "Actual", `actual ${actualClass(e)}`)}${val(e.forecast, "Forecast")}${val(e.previous, "Previous")}</span>
        <span class="chev">${p ? icon("chevron") : ""}</span>
      </div>
      ${p ? `<div class="row-detail"><div>${openRows.has(e.id) ? probHTML(e, p) : ""}</div></div>` : ""}
    </div>`;
  }

  function renderRows(animate) {
    const host = $("#rows");
    const draw = () => {
      const list = filtered();
      const now = Date.now();
      const next = list.find((e) => e.t.getTime() > now);
      const today = dayKey(new Date());
      $("#resultCount").textContent = `${bnNum(list.length)}টি নিউজ`;
      if (!list.length) {
        host.innerHTML = `<div class="no-rows">এই ফিল্টারে কোনো নিউজ নেই। ফিল্টার বদলান বা <button class="ghost" data-act="reset" style="display:inline-flex">রিসেট</button> করুন।</div>`;
      } else {
        let html = "", last = "", i = 0;
        for (const e of list) {
          const k = dayKey(e.t);
          if (k !== last) {
            html += `<div class="day ${k === today ? "today" : ""}">${fmtDay.format(e.t)}${k === today ? `<span class="tag">আজ</span>` : ""}</div>`;
            last = k;
          }
          html += rowHTML(e, i++, next && next.id);
        }
        host.innerHTML = html;
        $$(".row.open", host).forEach((r) => animateBars(r));
      }
      host.classList.remove("fading");
    };
    if (animate && !reduceMotion) {
      host.classList.add("fading");
      setTimeout(draw, 170);
    } else draw();
  }

  function bindRows() {
    const host = $("#rows");
    const toggle = (rowEl) => {
      const id = rowEl.dataset.id;
      const e = DATA.cal.events.find((x) => x.id === id);
      const p = DATA.prob.by_event[id];
      if (!e || !p) return;
      const open = !rowEl.classList.contains("open");
      const inner = $(".row-detail > div", rowEl);
      if (open) {
        openRows.add(id);
        if (!inner.innerHTML) inner.innerHTML = probHTML(e, p);
        rowEl.classList.add("open");
        requestAnimationFrame(() => animateBars(rowEl));
      } else {
        openRows.delete(id);
        rowEl.classList.remove("open");
      }
      $(".row-main", rowEl).setAttribute("aria-expanded", String(open));
    };
    host.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-act=reset]")) { $("#fReset").click(); return; }
      if (ev.target.closest("a")) return;
      const main = ev.target.closest(".row-main");
      if (main) toggle(main.parentElement);
    });
    host.addEventListener("keydown", (ev) => {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.classList.contains("row-main")) {
        ev.preventDefault(); toggle(ev.target.parentElement);
      }
    });
  }

  // ---------------------------------------------------------------- probability panel
  function barsHTML(outcomes, keepOrder) {
    let list = (outcomes || []).filter((o) => o && typeof o.prob === "number" && isFinite(o.prob));
    if (!list.length) return "";
    const max = Math.max(...list.map((o) => o.prob));
    if (list.length > 8) list = list.filter((o) => o.prob >= 1);
    if (!keepOrder) list = [...list].sort((a, b) => b.prob - a.prob);
    return `<div class="bars">${list.slice(0, 12).map((o) => `
      <div class="bar ${o.prob === max ? "top" : ""}">
        <span class="bar-label" title="${esc(o.label)}">${esc(o.label)}</span>
        <span class="bar-track"><span class="bar-fill" data-w="${o.prob}"></span></span>
        <span class="bar-val" data-n="${o.prob}">0%</span>
      </div>`).join("")}</div>`;
  }

  function triHTML(vf, refKind, refVal) {
    if (!vf) return "";
    const refBn = refKind === "forecast" ? "Forecast" : "আগের মান";
    return `<div class="tri" title="${refBn} ${esc(refVal || "")} এর তুলনায়">
      <div class="tri-track"><span class="b" data-w="${vf.below}"></span><span class="i" data-w="${vf.inline}"></span><span class="a" data-w="${vf.above}"></span></div>
      <div class="tri-legend">
        <span><i style="background:#5aa9ff"></i>কম ${bnNum(vf.below)}%</span>
        <span><i style="background:#8a93a8"></i>${refBn} (${esc(refVal || "—")}) ${bnNum(vf.inline)}%</span>
        <span><i style="background:#ff8a5b"></i>বেশি ${bnNum(vf.above)}%</span>
      </div></div>`;
  }

  function sourceBox(name, src, note, vf, ref) {
    if (!src) {
      return `<div class="prob"><div class="prob-head"><b>${name}</b></div>
        <div class="nodata">${icon("info")}<span><b>ডেটা নেই</b> — ${esc(note || "মার্কেট পাওয়া যায়নি")}</span></div></div>`;
    }
    const url = safeUrl(src.url);
    const keep = src.kind === "thresholds" || (src.outcomes || []).some((o) => o.interval);
    const closes = src.closes_utc && !isNaN(Date.parse(src.closes_utc)) ? `মার্কেট বন্ধ: ${fmtShortDay.format(new Date(src.closes_utc))}` : "";
    return `<div class="prob">
      <div class="prob-head"><b>${name}</b>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">মার্কেট ${icon("ext")}</a>` : ""}</div>
      <p class="prob-sub">${esc(src.title || "")}${closes ? ` · ${closes}` : ""}${src.volume_usd ? ` · ভলিউম $${Number(src.volume_usd).toLocaleString("en-US")}` : ""}</p>
      ${barsHTML(src.outcomes, keep)}
      ${triHTML(vf, ref && ref.ref, ref && ref.ref_value)}
      ${src.low_liquidity ? `<div class="warn-thin">${icon("alert")}কম লিকুইডিটি — সংখ্যা কম নির্ভরযোগ্য</div>` : ""}
      ${src.note ? `<p class="prob-foot">${esc(src.note)}</p>` : ""}
    </div>`;
  }

  function probHTML(e, p) {
    const vf = p.vs_forecast || {};
    let fw = "";
    if (p.fedwatch) {
      const url = safeUrl(p.fedwatch.url);
      fw = `<div class="prob"><div class="prob-head"><b>CME FedWatch</b>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">অফিসিয়াল পেজ ${icon("ext")}</a>` : ""}</div>
        <div class="nodata">${icon("info")}<span><b>সরাসরি ডেটা নেই</b> — ${esc(p.fedwatch.reason || "")}</span></div>
        ${p.fedwatch.proxy_outcomes ? `<p class="prob-sub" style="margin-top:10px">${esc(p.fedwatch.proxy_source)} থেকে নিকটতম তুলনা:</p>${barsHTML(p.fedwatch.proxy_outcomes, false)}` : ""}
      </div>`;
    }
    return `<div class="prob-wrap"><div class="prob-grid">
      ${sourceBox("Polymarket", p.polymarket, p.polymarket_note, vf.polymarket, vf)}
      ${sourceBox("Kalshi", p.kalshi, p.kalshi_note, vf.kalshi, vf)}
      ${fw}
    </div></div>`;
  }

  function animateBars(scope) {
    $$("[data-w]", scope).forEach((el) => {
      const w = Math.max(0, Math.min(100, parseFloat(el.dataset.w) || 0));
      if (reduceMotion) el.style.width = `${w}%`;
      else requestAnimationFrame(() => requestAnimationFrame(() => { el.style.width = `${w}%`; }));
    });
    $$("[data-n]", scope).forEach((el) => {
      if (el.dataset.done) return;
      el.dataset.done = "1";
      countTo(el, parseFloat(el.dataset.n) || 0, { decimals: 1, suffix: "%" });
    });
  }

  // ---------------------------------------------------------------- footer
  function renderSources() {
    const cs = DATA.cal.sources || {}, ps = (DATA.prob && DATA.prob.sources) || {};
    const ok = (v) => (v === "ok" ? "ঠিক আছে" : v ? "সমস্যা" : "—");
    const ff = cs.ff_html_this === "ok" ? "ঠিক আছে" : (cs.ff_json_this === "ok" ? "ব্যাকআপ ফিড (Actual নেই)" : "সমস্যা");
    const items = [["ForexFactory", ff], ["Polymarket", ok(ps.polymarket)], ["Kalshi", ok(ps.kalshi)],
                   ["CME FedWatch", ps.cme_fedwatch ? "শুধু লিংক (সাইট ব্লক/API পেইড)" : "—"]];
    $("#sourceStatus").textContent = "সোর্স স্ট্যাটাস: " + items.map(([k, v]) => `${k}: ${v}`).join(" · ");
  }

  // ---------------------------------------------------------------- boot
  function tick() {
    $("#dhakaClock").textContent = fmtClock.format(new Date());
    if (DATA.cal) tickCountdown();
  }

  function init() {
    bindFilters();
    bindRows();
    syncFilterUI();
    tick();
    setInterval(tick, 1000);
    setInterval(tickBadges, 30000);
    const bar = $(".topbar");
    window.addEventListener("scroll", () => bar.classList.toggle("scrolled", window.scrollY > 8), { passive: true });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveSegPill);
    load().catch((err) => { console.error(err); setStatus("err", "ডেটা লোড হয়নি"); });
    setInterval(() => {
      document.body.classList.add("no-anim");
      load().catch(console.error).finally(() => setTimeout(() => document.body.classList.remove("no-anim"), 400));
    }, REFRESH_MS);
  }

  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
