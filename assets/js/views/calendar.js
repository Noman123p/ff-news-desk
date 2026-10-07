// Calendar — ForexFactory-style table in Dhaka time, backed by data/weeks/*.json.
import {
  bn, esc, icon, timeDhaka, todayKey, dhakaKey, keyDate, addDays, weekStart, validKey, dayLong, dayShort, dayMini,
  dhakaDayRange, ffDayUrl, ffWeekUrl, ffKeyOf, impactIcon, IMPACT_BN, loadJSON, store,
} from '../util.js';

const STORE = 'ffdesk.cal.v2';
const CURS = ['USD', 'EUR', 'GBP', 'JPY', 'AUD', 'NZD', 'CAD', 'CHF', 'CNY'];
const IMPACTS = ['High', 'Medium', 'Low', 'Holiday'];
const DEFAULTS = { curs: [], impacts: [] };       // empty = all

const weekCache = new Map();
const loadWeek = (k) => {
  if (!weekCache.has(k)) weekCache.set(k, loadJSON(`data/weeks/${k}.json`, { optional: true }).then((d) => d || null));
  return weekCache.get(k);
};
export const getCachedWeeks = () => weekCache;

function readState(route) {
  const p = route.params;
  const d = validKey(p.get('d')) ? p.get('d') : todayKey();
  const m = p.get('m') === 'day' ? 'day' : 'week';
  return { d, m };
}
const hashFor = (d, m) => `#/calendar?d=${d}&m=${m}`;
function rangeOf({ d, m }) {
  if (m === 'day') return [d, d];
  const s = weekStart(d);
  return [s, addDays(s, 6)];
}
function daysBetween(a, b) { const out = []; for (let k = a; k <= b; k = addDays(k, 1)) out.push(k); return out; }
/** FF week files a Dhaka-day range needs: Dhaka day D = FF day D-1 (13:00→) + FF day D (→13:00). */
function neededWeeks(a, b) {
  const out = [];
  for (let k = weekStart(addDays(a, -1)); k <= weekStart(b); k = addDays(k, 7)) out.push(k);
  return out;
}

export function mount(el, route, ctx) {
  let state = readState(route);
  let filters = { ...DEFAULTS, ...store.get(STORE, {}) };
  let index = null;
  let openId = null;
  let token = 0;
  let core = ctx.core || {};

  el.innerHTML = `
    <div class="cal-head reveal">
      <div>
        <h1 class="page-title">${icon('calendar')}ইকোনমিক ক্যালেন্ডার</h1>
        <p class="hint">ForexFactory-র ডেটা, সব সময় <b>ঢাকা সময়ে</b>। সারিতে ক্লিক করলে সংক্ষিপ্ত বিবরণ ও বিশ্লেষণের লিংক।</p>
      </div>
      <div class="archive-note" id="archNote"></div>
    </div>
    <div class="cal-bar glass reveal" style="--i:1">
      <div class="quick" role="group" aria-label="দ্রুত যান">
        <button data-q="today">আজ</button><button data-q="this">এই সপ্তাহ</button><button data-q="next">আগামী সপ্তাহ</button>
      </div>
      <div class="stepper">
        <button class="icon-btn" data-step="-1" aria-label="আগের">${icon('left')}</button>
        <div class="range-label" id="rangeLabel" aria-live="polite">—</div>
        <button class="icon-btn" data-step="1" aria-label="পরের">${icon('right')}</button>
      </div>
      <div class="bar-right">
        <label class="date-pick" title="যেকোনো তারিখে যান">${icon('calendar')}<span class="sr">তারিখ বাছাই</span><input type="date" id="datePick"></label>
        <div class="seg" role="group" aria-label="ভিউ"><button data-m="day">দিন</button><button data-m="week">সপ্তাহ</button></div>
        <button class="ghost filter-toggle" id="filterToggle" aria-expanded="false" aria-controls="filterPanel">${icon('filter')}ফিল্টার<span class="fcount" id="fcount"></span></button>
      </div>
    </div>
    <div class="filter-panel" id="filterPanel"><div>
      <div class="glass fp-in">
        <div class="f-row"><span class="f-label">ইমপ্যাক্ট</span><div class="chips" id="impChips">
          ${IMPACTS.map((i) => `<button class="chip" data-imp="${i}">${impactIcon(i)}${IMPACT_BN[i]}</button>`).join('')}</div></div>
        <div class="f-row"><span class="f-label">কারেন্সি</span><div class="chips" id="curChips">
          ${CURS.map((c) => `<button class="chip cur" data-cur="${c}">${c}</button>`).join('')}</div></div>
        <div class="f-row f-foot"><span class="muted small">কিছু বাছাই না করলে সব দেখাবে। পছন্দ এই ব্রাউজারে সেভ থাকে।</span>
          <button class="ghost" id="fReset">সব দেখাও</button></div>
      </div>
    </div></div>
    <div class="cal-table glass reveal" style="--i:2" role="table" aria-label="ইকোনমিক ক্যালেন্ডার">
      <div class="c-head" role="row">
        <span role="columnheader">সময় <small>ঢাকা</small></span><span role="columnheader">কারেন্সি</span><span role="columnheader" aria-label="ইমপ্যাক্ট">ইমপ্যাক্ট</span>
        <span role="columnheader">ইভেন্ট</span><span role="columnheader" class="num">Actual</span><span role="columnheader" class="num">Forecast</span><span role="columnheader" class="num">Previous</span><span aria-hidden="true"></span>
      </div>
      <div class="c-body" id="cBody"><div class="loading-rows">${'<div class="sk-row"></div>'.repeat(8)}</div></div>
    </div>
    <p class="legend-line"><span class="v actual beat">সবুজ</span> = প্রত্যাশার চেয়ে ভালো (মুদ্রার জন্য), <span class="v actual miss">লাল</span> = খারাপ — ForexFactory-র রং অনুযায়ী।</p>`;

  const $ = (s) => el.querySelector(s);
  const body = $('#cBody');

  // ---------------------------------------------------------------- controls
  el.querySelector('.quick').addEventListener('click', (e) => {
    const q = e.target.closest('button')?.dataset.q; if (!q) return;
    const t = todayKey();
    ctx.go(q === 'today' ? hashFor(t, 'day') : q === 'this' ? hashFor(t, 'week') : hashFor(addDays(weekStart(t), 7), 'week'));
  });
  el.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
    const n = Number(b.dataset.step) * (state.m === 'day' ? 1 : 7);
    ctx.go(hashFor(addDays(state.m === 'week' ? weekStart(state.d) : state.d, n), state.m));
  }));
  el.querySelectorAll('[data-m]').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.m === state.m) return;
    const d = b.dataset.m === 'day' && weekStart(todayKey()) === weekStart(state.d) ? todayKey() : state.d;
    ctx.go(hashFor(d, b.dataset.m));
  }));
  $('#datePick').addEventListener('change', (e) => { if (validKey(e.target.value)) ctx.go(hashFor(e.target.value, 'day')); });
  const ft = $('#filterToggle'), fp = $('#filterPanel');
  ft.addEventListener('click', () => { const open = !fp.classList.contains('open'); fp.classList.toggle('open', open); ft.setAttribute('aria-expanded', String(open)); });
  const toggleIn = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  $('#impChips').addEventListener('click', (e) => { const b = e.target.closest('[data-imp]'); if (!b) return; filters.impacts = toggleIn(filters.impacts, b.dataset.imp); saveFilters(); });
  $('#curChips').addEventListener('click', (e) => { const b = e.target.closest('[data-cur]'); if (!b) return; filters.curs = toggleIn(filters.curs, b.dataset.cur); saveFilters(); });
  $('#fReset').addEventListener('click', () => { filters = { ...DEFAULTS }; saveFilters(); });
  function saveFilters() { store.set(STORE, filters); paintControls(); renderRows(true); }

  body.addEventListener('click', (e) => {
    const row = e.target.closest('.c-row'); if (!row || e.target.closest('a')) return;
    toggleRow(row);
  });
  body.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('c-main')) { e.preventDefault(); toggleRow(e.target.closest('.c-row')); }
  });
  function toggleRow(row) {
    const was = row.classList.contains('open');
    body.querySelectorAll('.c-row.open').forEach((r) => { r.classList.remove('open'); r.querySelector('.c-main').setAttribute('aria-expanded', 'false'); });
    if (!was) { row.classList.add('open'); row.querySelector('.c-main').setAttribute('aria-expanded', 'true'); openId = row.dataset.id; } else openId = null;
  }

  function paintControls() {
    const [a, b] = rangeOf(state);
    const t = todayKey();
    $('#rangeLabel').innerHTML = state.m === 'day'
      ? `<b>${dayLong(a)}</b>${a === t ? '<span class="tag">আজ</span>' : ''}`
      : `<b>${dayMini(a)} – ${dayMini(b)}</b> <span class="muted">${bn(keyDate(b).getUTCFullYear())}</span>${a <= t && t <= b ? '<span class="tag">এই সপ্তাহ</span>' : ''}`;
    $('#datePick').value = state.d;
    el.querySelectorAll('[data-m]').forEach((x) => x.classList.toggle('on', x.dataset.m === state.m));
    const ws = weekStart(t);
    const q = state.m === 'day' && state.d === t ? 'today' : state.m === 'week' && weekStart(state.d) === ws ? 'this'
      : state.m === 'week' && weekStart(state.d) === addDays(ws, 7) ? 'next' : '';
    el.querySelectorAll('[data-q]').forEach((x) => { x.classList.toggle('on', x.dataset.q === q); x.setAttribute('aria-pressed', String(x.dataset.q === q)); });
    el.querySelectorAll('[data-imp]').forEach((x) => { const on = filters.impacts.includes(x.dataset.imp); x.classList.toggle('on', on); x.setAttribute('aria-pressed', String(on)); });
    el.querySelectorAll('[data-cur]').forEach((x) => { const on = filters.curs.includes(x.dataset.cur); x.classList.toggle('on', on); x.setAttribute('aria-pressed', String(on)); });
    const n = filters.impacts.length + filters.curs.length;
    $('#fcount').textContent = n ? bn(n) : '';
    ft.classList.toggle('active', n > 0);
  }

  // ---------------------------------------------------------------- data
  let loaded = { events: [], covered: new Set(), weeks: [] };
  async function load(fade) {
    const my = ++token;
    paintControls();
    if (fade) body.classList.add('fading');
    try {
      index = index || await loadJSON('data/weeks/index.json', { optional: true }) || { weeks: [] };
    } catch { index = { weeks: [] }; }
    const avail = new Set(index.weeks || []);
    $('#archNote').innerHTML = index.first ? `${icon('db')}<span>আর্কাইভ: <b>${dayMini(index.first)} ${bn(index.first.slice(0, 4))}</b> থেকে <b>${dayMini(addDays(index.last, 6))} ${bn(index.last.slice(0, 4))}</b></span>` : '';
    const [a, b] = rangeOf(state);
    const need = neededWeeks(a, b);
    const docs = await Promise.all(need.map((k) => (avail.has(k) ? loadWeek(k) : Promise.resolve(null))));
    if (my !== token) return;
    const have = new Set(need.filter((k, i) => docs[i]));
    const covered = new Set(daysBetween(a, b).filter((d) => have.has(weekStart(addDays(d, -1))) && have.has(weekStart(d))));
    const [s] = dhakaDayRange(a), [, e] = dhakaDayRange(b);
    const seen = new Set(), events = [];
    docs.forEach((doc, i) => {
      (doc?.events || []).forEach((ev) => {
        const t = Date.parse(ev.time_utc);
        if (t < s || t >= e || seen.has(ev.id)) return;
        seen.add(ev.id);
        events.push({ ...ev, _t: t, _day: dhakaKey(new Date(t)), _week: need[i] });
      });
    });
    events.sort((x, y) => x._t - y._t || (x.currency > y.currency ? 1 : -1));
    loaded = { events, covered, weeks: need };
    renderRows(false);
    body.classList.remove('fading');
  }

  const vcell = (v, cls, label) => `<span class="v num ${v ? cls : 'na'}"><i class="v-label">${label}</i>${v ? esc(v) : (cls.includes('actual') ? '' : '')}</span>`;
  function renderRows(soft) {
    const [a, b] = rangeOf(state);
    const now = Date.now();
    const fi = filters.impacts, fc = filters.curs;
    const shown = loaded.events.filter((e) => (!fi.length || fi.includes(e.impact)) && (!fc.length || fc.includes(e.currency)));
    const next = shown.find((e) => e._t > now);
    const an = core.analysis?.events || {};
    const t = todayKey();
    let html = '', i = 0;
    for (const d of daysBetween(a, b)) {
      const evs = shown.filter((e) => e._day === d);
      const ffUrl = ffDayUrl(d);
      html += `<div class="c-day ${d === t ? 'today' : ''}" role="row"><span role="cell">${dayShort(d)}${d === t ? '<em>আজ</em>' : ''}</span><a href="${ffUrl}" target="_blank" rel="noopener" class="ff-link" title="ForexFactory-তে এই দিন">FF ${icon('ext')}</a></div>`;
      if (!loaded.covered.has(d) && !evs.length) {
        html += `<div class="c-empty" role="row"><span role="cell">${icon('info')}এই দিনের ডেটা আর্কাইভে নেই। <a href="${ffUrl}" target="_blank" rel="noopener">ForexFactory-তে ${dayShort(d)} দেখুন ${icon('ext')}</a></span></div>`;
        continue;
      }
      if (!evs.length) {
        const any = loaded.events.some((e) => e._day === d);
        html += `<div class="c-empty quiet" role="row"><span role="cell">${any ? 'ফিল্টারে এই দিনের কোনো নিউজ মেলেনি।' : 'এই দিনে কোনো নিউজ নেই।'}</span></div>`;
        continue;
      }
      if (!loaded.covered.has(d)) {
        html += `<div class="c-empty partial" role="row"><span role="cell">${icon('info')}এই দিনের একাংশের ডেটা আর্কাইভে নেই — তালিকা অসম্পূর্ণ হতে পারে। <a href="${ffUrl}" target="_blank" rel="noopener">ForexFactory-তে দেখুন ${icon('ext')}</a></span></div>`;
      }
      let lastTime = null;
      for (const e of evs) {
        const tl = e.time_special || timeDhaka(new Date(e._t));
        const showT = tl !== lastTime; lastTime = tl;
        const past = e._t <= now;
        const abw = e.abw === 1 ? 'beat' : e.abw === 2 ? 'miss' : '';
        const a2 = an[e.id];
        const href = `#/analysis/${encodeURIComponent(e.id)}?w=${e._week}`;
        const ffEv = ffDayUrl(ffKeyOf(e._t)) + (e.ff_id ? `#detail=${e.ff_id}` : '');
        html += `
        <div class="c-row ${e.actual || e.forecast || e.previous ? '' : 'novals'} ${past ? 'past' : ''} ${e === next ? 'next' : ''} imp-${e.impact} ${e.id === openId ? 'open' : ''} ${soft ? '' : 'rin'}" data-id="${esc(e.id)}" role="row" style="--i:${Math.min(i++, 30)}">
          <div class="c-main" tabindex="0" role="button" aria-expanded="${e.id === openId}" aria-label="${esc(e.currency)} ${esc(e.title)}, ${esc(tl)}">
            <span class="c-time" role="cell">${showT ? esc(tl) : ''}${e === next ? '<em class="next-tag">পরবর্তী</em>' : ''}</span>
            <span class="c-cur" role="cell">${esc(e.currency)}</span>
            <span class="c-imp" role="cell">${impactIcon(e.impact)}</span>
            <span class="c-ev" role="cell"><span class="name">${esc(e.title)}</span>${a2 ? `<span class="has-an" title="বিস্তারিত বিশ্লেষণ আছে">${icon('scope')}</span>` : ''}</span>
            <span class="c-vals">${vcell(e.actual, `actual ${abw}`, 'Actual')}${vcell(e.forecast, '', 'Forecast')}${vcell(e.previous, '', 'Previous')}</span>
            <span class="c-chev">${icon('chevron')}</span>
          </div>
          <div class="c-detail"><div><div class="cd-in">
            <div class="cd-main">
              <p class="cd-when">${icon('clock')}${dayLong(e._day)} · ${e.time_special ? esc(e.time_special) + ' · ' : ''}${timeDhaka(new Date(e._t))} ঢাকা · ${IMPACT_BN[e.impact] || e.impact} ইমপ্যাক্ট</p>
              ${a2 ? `<p class="cd-what"><b>${esc(a2.explainer?.name_bn || '')}</b> — ${esc((a2.explainer?.what || '').split('।')[0])}।</p>` : ''}
              ${e.revision ? `<p class="cd-rev">আগের মান সংশোধিত হয়ে <b>${esc(e.revision)}</b></p>` : ''}
              ${a2?.verdict?.top ? `<p class="cd-verdict">${icon('gauge')}সম্ভাব্য: <b>${esc(a2.verdict.top.label)}</b>${a2.verdict.top.pct != null ? ` (${bn(Math.round(a2.verdict.top.pct))}%)` : ''}</p>` : ''}
            </div>
            <div class="cd-actions">
              <a class="btn" href="${href}">${icon('scope')}বিশ্লেষণ পেজ</a>
              <a class="ghost" href="${ffEv}" target="_blank" rel="noopener">ForexFactory ${icon('ext')}</a>
            </div>
          </div></div></div>
        </div>`;
      }
    }
    body.innerHTML = html;
  }

  load(false);
  ctx.onTick(() => {   // keep "next" highlight honest without re-rendering every second
    const nx = body.querySelector('.c-row.next');
    if (nx && Date.parse((loaded.events.find((e) => e.id === nx.dataset.id) || {}).time_utc) <= Date.now()) renderRows(true);
  });
  return {
    update(r) { const ns = readState(r); const changed = ns.d !== state.d || ns.m !== state.m; state = ns; if (changed) { openId = null; load(true); } else paintControls(); },
    refresh(c) { core = c; weekCache.clear(); index = null; load(false); },
  };
}
