// Analysis — per-event explainer, timing, data/history, probabilities and three scenarios.
import {
  bn, esc, icon, timeDhaka, todayKey, dhakaKey, addDays, weekStart, dayLong, dayShort, stampDhaka, agoBn,
  countdownParts, pad, parseNum, impactIcon, IMPACT_BN, ffDayUrl, ffKeyOf, loadJSON, validKey,
} from '../util.js';

const DIR = { up: ['up', '↑', 'উপরে'], down: ['down', '↓', 'নিচে'], flat: ['flat', '→', 'সীমিত'] };
const MARKETS = [
  ['gold', 'gold', 'Gold (XAU/USD)'], ['btc', 'btc', 'Bitcoin'], ['crypto', 'layers', 'ক্রিপ্টো / অল্টকয়েন'],
  ['forex', 'fx', 'Forex'], ['indices', 'index', 'US ইনডেক্স ও ইল্ড'],
];
const SPEC_KEYS = ['Source', 'Measures', 'Usual Effect', 'Frequency', 'Next Release', 'FF Notes', 'Why Traders Care', 'Derived Via', 'Also Called', 'Acro Expand'];
const SPEC_BN = { Source: 'সূত্র', Measures: 'কী মাপে', 'Usual Effect': 'সাধারণ প্রভাব', Frequency: 'কতদিন পরপর', 'Next Release': 'পরের রিলিজ', 'FF Notes': 'FF নোট', 'Why Traders Care': 'ট্রেডাররা কেন দেখে', 'Derived Via': 'কীভাবে তৈরি', 'Also Called': 'অন্য নাম', 'Acro Expand': 'পূর্ণরূপ' };
const KIND_BN = { market: 'প্রেডিকশন মার্কেট', nowcast: 'নাউকাস্ট মডেল', consensus: 'কনসেনসাস', link: 'শুধু লিংক' };
const pctTxt = (p) => (p == null ? '—' : `${bn(Math.round(p * 10) / 10)}%`);
const abwCls = (abw) => (abw === 1 ? 'beat' : abw === 2 ? 'miss' : '');

function dayLabel(k) {
  const t = todayKey();
  return k === t ? 'আজ' : k === addDays(t, 1) ? 'আগামীকাল' : dayShort(k);
}

export function mount(el, route, ctx) {
  let core = ctx.core || {};
  let id = route.parts[0] || null;
  let hint = route.params;
  let ev = null;        // the event currently shown
  let scenarioIdx = 0;

  const list = () => {
    const A = core.analysis;
    return (A?.order || []).map((k) => A.events[k]).filter(Boolean);
  };
  function defaultId() {
    const L = list(), now = Date.now(), t = todayKey();
    const today = L.filter((e) => dhakaKey(new Date(e.time_utc)) === t);
    return (today.find((e) => Date.parse(e.time_utc) > now) || today[0] || L.find((e) => Date.parse(e.time_utc) > now) || L[0])?.id || null;
  }

  function picker(activeId) {
    const L = list();
    if (!L.length) return '';
    const groups = new Map();
    L.forEach((e) => { const k = dhakaKey(new Date(e.time_utc)); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); });
    const now = Date.now();
    return `<nav class="an-pick glass" aria-label="ইভেন্ট বাছাই">
      <div class="pick-head"><b>আজ ও আসন্ন</b><span class="muted small">USD লাল/কমলা · ${bn(L.length)}টি</span></div>
      <div class="pick-rail">${[...groups].map(([k, evs]) => `
        <div class="pick-group"><span class="pick-day">${dayLabel(k)}</span>
          ${evs.map((e) => `<a class="pick ${e.id === activeId ? 'on' : ''} ${Date.parse(e.time_utc) <= now ? 'past' : ''}" href="#/analysis/${esc(e.id)}" ${e.id === activeId ? 'aria-current="true"' : ''}>
            ${impactIcon(e.impact)}<span class="pick-t">${e.time_special ? esc(e.time_special) : timeDhaka(new Date(e.time_utc))}</span><span class="pick-n">${esc(e.title)}</span></a>`).join('')}
        </div>`).join('')}</div></nav>`;
  }

  // ---------------------------------------------------------------- sections
  function secHead(n, title, sub, ic) {
    return `<header class="sec-head"><span class="sec-n">${n}</span><div><h2>${icon(ic)}${title}</h2>${sub ? `<p>${sub}</p>` : ''}</div></header>`;
  }
  function sWhat(a) {
    const x = a.explainer || {}, sp = x.ff_specs || {};
    const specRows = SPEC_KEYS.filter((k) => sp[k]).map((k) => `<div><dt>${SPEC_BN[k]} <small>${k}</small></dt><dd>${esc(sp[k])}</dd></div>`).join('');
    return `<section class="an-sec glass" id="sec-what">${secHead('ক', 'নিউজটা কী', esc(x.name_bn || ''), 'book')}
      <p class="lead">${esc(x.what || '')}</p>
      <div class="facts">
        ${x.publisher ? `<div><small>কে প্রকাশ করে</small><b>${esc(x.publisher)}</b></div>` : ''}
        ${x.frequency ? `<div><small>কত দিন পরপর</small><b>${esc(x.frequency)}</b></div>` : ''}
        <div><small>ইমপ্যাক্ট</small><b class="imp-inline">${impactIcon(a.impact)}${IMPACT_BN[a.impact] || a.impact}</b></div>
      </div>
      ${x.why ? `<div class="callout"><b>কেন গুরুত্বপূর্ণ</b><p>${esc(x.why)}</p></div>` : ''}
      ${x.effect ? `<div class="callout alt"><b>সাধারণ প্রভাব</b><p>${esc(x.effect)}</p></div>` : ''}
      ${specRows ? `<details class="specs"><summary>${icon('chevron')}ForexFactory স্পেক <span class="muted">(ইংরেজি, FF থেকে)</span></summary><dl>${specRows}</dl>
        ${x.source_url ? `<a href="${esc(x.source_url)}" target="_blank" rel="noopener" class="small">অফিসিয়াল সূত্র ${icon('ext')}</a>` : ''}</details>` : ''}
    </section>`;
  }
  function sWhen(a) {
    const t = new Date(a.time_utc), k = dhakaKey(t);
    return `<section class="an-sec glass" id="sec-when">${secHead('খ', 'কখন', 'ঢাকা সময় (UTC+6)', 'clock')}
      <div class="when-grid">
        <div class="when-big"><b>${a.time_special ? esc(a.time_special) : timeDhaka(t)}</b><span>${dayLong(k)}</span>
          ${a.time_special ? '<small class="muted">নির্দিষ্ট সময় ঘোষণা হয়নি — আনুমানিক</small>' : ''}</div>
        <div class="countdown big" data-cd="${esc(a.time_utc)}" aria-live="off">
          <div><b data-u="d">--</b><span>দিন</span></div><div><b data-u="h">--</b><span>ঘণ্টা</span></div><div><b data-u="m">--</b><span>মিনিট</span></div><div><b data-u="s">--</b><span>সেকেন্ড</span></div>
        </div>
        <p class="released" hidden>${icon('info')}<span></span></p>
      </div></section>`;
  }
  function historyChart(h) {
    const pts = h.slice().reverse().map((r) => ({ d: r.date, a: parseNum(r.actual), f: parseNum(r.forecast), abw: r.abw }));
    if (pts.filter((p) => p.a != null).length < 2) return '';
    const vals = pts.flatMap((p) => [p.a, p.f]).filter((v) => v != null);
    let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    if (hi === lo) hi = lo + 1;
    const W = 320, H = 110, pad2 = 14, bw = (W - pad2 * 2) / pts.length;
    const y = (v) => H - 18 - ((v - lo) / (hi - lo)) * (H - 30);
    const y0 = y(0);
    const bars = pts.map((p, i) => {
      if (p.a == null) return '';
      const x = pad2 + i * bw + bw * 0.22, w = bw * 0.56, top = Math.min(y(p.a), y0), hh = Math.max(2, Math.abs(y(p.a) - y0));
      const f = p.f != null ? `<line class="fc" x1="${x - 3}" x2="${x + w + 3}" y1="${y(p.f)}" y2="${y(p.f)}"/>` : '';
      return `<rect class="b ${abwCls(p.abw)}" x="${x}" y="${top}" width="${w}" height="${hh}" rx="3" style="--i:${i}"/>${f}<text x="${x + w / 2}" y="${H - 4}">${esc(p.d.slice(5).replace('-', '/'))}</text>`;
    }).join('');
    return `<svg class="hchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="আগের রিলিজের চার্ট"><line class="zero" x1="${pad2}" x2="${W - pad2}" y1="${y0}" y2="${y0}"/>${bars}</svg>
      <p class="chart-key"><i class="k-bar"></i>Actual <i class="k-fc"></i>Forecast</p>`;
  }
  function sData(a) {
    const d = a.data || {};
    const h = a.history || [];
    return `<section class="an-sec glass" id="sec-data">${secHead('গ', 'ডেটা', 'Previous / Forecast / Actual — রিলিজের আগে Actual ফাঁকা থাকে', 'db')}
      <div class="pfa big">
        <div><small>Previous</small><b>${d.previous ? esc(d.previous) : '—'}</b>${d.revision ? `<em>সংশোধিত: ${esc(d.revision)}</em>` : ''}</div>
        <div><small>Forecast</small><b>${d.forecast ? esc(d.forecast) : '—'}</b></div>
        <div class="act ${abwCls(d.abw)}"><small>Actual</small><b>${d.actual ? esc(d.actual) : '—'}</b>${d.actual ? '' : '<em>এখনো প্রকাশ হয়নি</em>'}</div>
      </div>
      ${d.result ? `<p class="result ${abwCls(d.abw)}">${icon('info')}${esc(d.result.text)}</p>` : ''}
      ${h.length ? `<div class="hist">
        <div class="hist-chart">${historyChart(h)}</div>
        <table class="htable"><thead><tr><th>তারিখ</th><th class="num">Actual</th><th class="num">Forecast</th><th class="num">Previous</th></tr></thead>
        <tbody>${h.map((r) => `<tr><td>${dayShort(r.date)} <span class="muted">${bn(r.date.slice(0, 4))}</span></td><td class="num ${abwCls(r.abw)}"><b>${esc(r.actual || '—')}</b></td><td class="num">${esc(r.forecast || '—')}</td><td class="num">${esc(r.previous || '—')}</td></tr>`).join('')}</tbody></table>
      </div>` : '<p class="nodata">' + icon('info') + 'আগের রিলিজের ডেটা নেই।</p>'}
    </section>`;
  }
  function sourceCard(s) {
    if (!s.ok) {
      return `<div class="src off"><div class="src-head"><b>${esc(s.name)}</b><span class="kind">${KIND_BN[s.kind] || ''}</span></div>
        <p class="src-note">${s.kind === 'link' ? '' : '<b>ডেটা নেই</b> — '}${esc(s.note || '')}</p>
        ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" class="src-link">${s.kind === 'link' ? 'FedWatch খুলুন' : 'খুলুন'} ${icon('ext')}</a>` : ''}</div>`;
    }
    let body = '';
    if (s.kind === 'market') {
      const outs = (s.outcomes || []).filter((o) => o.prob != null).sort((x, y) => y.prob - x.prob).slice(0, 6);
      const max = Math.max(...outs.map((o) => o.prob), 1);
      body = `<div class="bars">${outs.map((o, i) => `<div class="bar ${i === 0 ? 'top' : ''}"><span class="bar-label" title="${esc(o.label)}">${esc(o.label)}</span><span class="bar-track"><span class="bar-fill" style="--w:${(o.prob / max) * 100}%"></span></span><span class="bar-val">${pctTxt(o.prob)}</span></div>`).join('')}</div>`;
      const v = s.vs_forecast;
      if (v) body += `<div class="tri"><div class="tri-track"><span class="b" style="--w:${v.below}%"></span><span class="i" style="--w:${v.inline}%"></span><span class="a" style="--w:${v.above}%"></span></div>
        <div class="tri-legend"><span><i class="b"></i>কম ${pctTxt(v.below)}</span><span><i class="i"></i>কাছাকাছি ${pctTxt(v.inline)}</span><span><i class="a"></i>বেশি ${pctTxt(v.above)}</span></div></div>`;
      const fv = s.fed_view;
      if (fv) body += `<div class="tri"><div class="tri-track"><span class="b" style="--w:${fv.cut}%"></span><span class="i" style="--w:${fv.hold}%"></span><span class="a" style="--w:${fv.hike}%"></span></div>
        <div class="tri-legend"><span><i class="b"></i>কাট ${pctTxt(fv.cut)}</span><span><i class="i"></i>হোল্ড ${pctTxt(fv.hold)}</span><span><i class="a"></i>হাইক ${pctTxt(fv.hike)}</span></div></div>`;
      if (s.low_liquidity) body += `<p class="warn-thin">${icon('alert')}লিকুইডিটি কম — দাম দ্রুত বদলাতে পারে</p>`;
    } else if (s.kind === 'nowcast') {
      body = `<div class="nc"><b>${bn(s.value)}${s.unit === '%' || !s.unit ? '%' : ''}</b><span>${esc(s.label || '')}</span></div>
        ${s.vs_ref_text ? `<p class="nc-vs">${esc(s.vs_ref_text)}</p>` : ''}${s.note ? `<p class="src-note">${esc(s.note)}</p>` : ''}`;
    } else if (s.kind === 'consensus') {
      body = `<div class="nc"><b>${esc((s.headline || '').split(': ').pop())}</b><span>অর্থনীতিবিদদের গড় প্রত্যাশা</span></div>`;
    }
    const when = s.as_of || s.updated_at;
    return `<div class="src ${s.context_only ? 'ctx' : ''}"><div class="src-head"><b>${esc(s.name)}</b><span class="kind">${s.context_only ? 'শুধু প্রেক্ষাপট' : (KIND_BN[s.kind] || '')}</span></div>
      ${s.title ? `<p class="src-title">${esc(s.title)}</p>` : ''}${s.context_only ? '<p class="src-note">পরবর্তী FOMC সিদ্ধান্তের দাম — এই ইভেন্টের (টোনের) ফলাফলের সম্ভাবনা নয়।</p>' : ''}${body}
      <div class="src-foot">${when ? `<span>${icon('clock')}${s.as_of ? `ডেটা: ${esc(s.as_of_label || s.as_of)}` : `আপডেট ${agoBn(when)}`}</span>` : ''}
        ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">উৎস ${icon('ext')}</a>` : ''}</div></div>`;
  }
  const RANK_ROLE = { 0: 'সবচেয়ে সম্ভাব্য', 1: 'দ্বিতীয়', 2: 'তৃতীয়' };
  function sProb(a) {
    const v = a.verdict || {};
    const conf = v.confidence || 'ডেটা নেই';
    const confCls = conf.startsWith('নিম্ন') ? 'low' : conf.startsWith('মাঝারি–') ? 'high' : conf.startsWith('মাঝারি') ? 'mid' : 'none';
    const sc = a.scenarios || [];
    const neutralTop = v.top && (v.top.key === 'inline' || v.top.key === 'neutral');
    // ranked rows come straight from the scenario order, so section ঘ and the tabs can never disagree
    const rows = v.probs ? sc.map((s, i) => {
      const role = i === 0 ? 'সবচেয়ে সম্ভাব্য' : neutralTop ? (i === 1 ? 'বেশি সম্ভাব্য দিক' : 'উল্টো দিক') : (s.key === 'inline' || s.key === 'neutral' ? 'নিউট্রাল' : 'উল্টো দিক');
      return `<div class="vr ${i === 0 ? 'top' : ''}"><span class="vr-role">${role}</span><span class="vr-label">${esc(v.labels?.[s.key] || s.key)}</span>
        <span class="vr-track"><span class="vr-fill" style="--w:${s.prob}%"></span></span><b class="vr-pct">${pctTxt(s.prob)}</b></div>`;
    }).join('') : '';
    const ctxBox = v.context ? `<div class="v-context">${icon('info')}<div><b>রেট-মার্কেট — শুধু প্রেক্ষাপট</b><p>${esc(v.context.text)}</p></div></div>` : '';
    let verdict;
    if (v.top && v.probs) {
      verdict = `
      <div class="verdict">
        <div class="v-main"><small>সম্মিলিত রায়</small><b>${esc(v.top.label)}${neutralTop ? ' — সবচেয়ে সম্ভাব্য' : ''}</b><span class="v-pct">${pctTxt(v.top.pct)}</span></div>
        <div class="v-rows">${rows}</div>
        <p class="v-total">${icon('info')}তিনটি ফলাফল মিলে ${pctTxt(v.total ?? 100)} — একই সংখ্যা নিচের তিন সিনারিও ট্যাবেও।</p>
        <div class="v-foot"><span class="conf ${confCls}">আস্থা: ${esc(conf)}</span>${(v.basis || []).map((b) => `<span class="basis">${esc(b)}</span>`).join('')}</div>
      </div>`;
    } else if (v.top) {
      verdict = `
      <div class="verdict approx">
        <div class="v-main"><small>${v.kind === 'tone' ? 'আনুমানিক টোন-ঝোঁক' : 'আনুমানিক ঝোঁক'}</small><b>${esc(v.top.label)}</b><span class="v-pct na">% নেই</span></div>
        ${v.second ? `<div class="v-second"><small>সারপ্রাইজ হলে বেশি ঝুঁকি</small><b>${esc(v.second.label)}</b></div>` : ''}
        ${ctxBox}
        <div class="v-foot"><span class="conf ${confCls}">আস্থা: ${esc(conf)}</span>${(v.basis || []).map((b) => `<span class="basis">${esc(b)}</span>`).join('')}</div>
      </div>`;
    } else {
      verdict = `
      <div class="verdict none"><div class="v-main"><small>সম্মিলিত রায়</small><b>ডেটা নেই</b></div>
        <p class="muted">এই নিউজের জন্য নির্ভরযোগ্য বাজার-সম্ভাবনা বা নাউকাস্ট পাওয়া যায়নি — অনুমান করে সংখ্যা দেখানো হচ্ছে না, কোনো দিককে "সবচেয়ে সম্ভাব্য" বলা হচ্ছে না।</p>
        ${(v.basis || []).length ? `<div class="v-foot">${v.basis.map((b) => `<span class="basis">${esc(b)}</span>`).join('')}</div>` : ''}</div>`;
    }
    return `<section class="an-sec glass" id="sec-prob">${secHead('ঘ', 'সম্ভাবনা', 'প্রতিটি সূত্রের সংখ্যা, সময় ও লিংক — তারপর সম্মিলিত রায়', 'gauge')}
      ${verdict}
      <div class="src-grid">${(a.sources || []).map(sourceCard).join('')}</div>
    </section>`;
  }
  function scenarioPanel(s) {
    const [cls, arrow] = DIR[s.usd_bias] || DIR.flat;
    const mk = MARKETS.map(([k, ic, label]) => {
      const m = s.markets?.[k]; if (!m) return '';
      const [mc, ma] = DIR[m.dir] || DIR.flat;
      const fx = k === 'forex' ? `<div class="fx-row">${[['dxy', 'DXY'], ['eurusd', 'EURUSD'], ['usdjpy', 'USDJPY'], ['gbpusd', 'GBPUSD']].map(([p, n]) => `<span class="fx ${m[p] === '↑' ? 'up' : m[p] === '↓' ? 'down' : 'flat'}">${n} <b>${esc(m[p] || '→')}</b></span>`).join('')}</div>` : '';
      return `<div class="mk"><div class="mk-head">${icon(ic)}<b>${label}</b><span class="arrow ${mc}">${ma}</span></div>${fx}<p>${esc(m.text)}</p></div>`;
    }).join('');
    return `
      <div class="sc-top">
        <p class="sc-cond">${esc(s.condition)}</p>
        <div class="sc-badges"><span class="bias ${cls}">USD ${arrow}</span>${s.prob != null ? `<span class="badge">সম্ভাবনা ${pctTxt(s.prob)}</span>` : `<span class="badge muted">সম্ভাবনা: ${esc(s.prob_note || 'ডেটা নেই')}</span>`}${s.is_overall_top ? `<span class="badge hl">সবচেয়ে সম্ভাব্য${s.approx ? ' (আনুমানিক)' : ''}</span>` : ''}${s.happened ? '<span class="badge done">যা ঘটেছে</span>' : ''}</div>
      </div>
      ${s.note ? `<p class="sc-note">${icon('info')}${esc(s.note)}</p>` : ''}
      <div class="sc-why"><b>কেন</b><p>${esc(s.why)}</p></div>
      <div class="mk-grid">${mk}</div>
      <div class="watch"><b>${icon('eye')}প্রথম ১৫–৬০ মিনিটে কী দেখবেন</b><ul>${(s.watch || []).map((w) => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  }
  function sScen(a) {
    const sc = a.scenarios || [];
    if (!sc.length) return '';
    scenarioIdx = Math.min(scenarioIdx, sc.length - 1);
    return `<section class="an-sec glass" id="sec-scen">${secHead('ঙ', 'তিনটা সিনারিও', 'প্রতিটি মার্কেটে সম্ভাব্য প্রতিক্রিয়া — নিয়মভিত্তিক, ঐতিহাসিক প্রবণতা থেকে', 'branch')}
      <div class="sc-tabs" role="tablist">${sc.map((s, i) => `<button role="tab" id="sct-${i}" aria-controls="scp" aria-selected="${i === scenarioIdx}" data-sc="${i}" class="${i === scenarioIdx ? 'on' : ''}">
        <span class="sc-n">${bn(i + 1)}</span><span class="sc-t">${esc(s.title)}</span><span class="sc-p">${s.prob != null ? pctTxt(s.prob) : (i === 0 && s.approx ? 'আনুমানিক' : '')}</span></button>`).join('')}<span class="sc-ink" aria-hidden="true"></span></div>
      <div class="sc-panel" id="scp" role="tabpanel" aria-labelledby="sct-${scenarioIdx}">${scenarioPanel(sc[scenarioIdx])}</div>
      <p class="disclaimer">${icon('alert')}${esc(a.disclaimer || '')}</p>
    </section>`;
  }

  // ---------------------------------------------------------------- light view (no generated analysis)
  async function findPlain(eid) {
    const cal = core.calendar?.events || [];
    let e = cal.find((x) => x.id === eid);
    let week = hint.get('w');
    if (!e && validKey(week)) {
      const doc = await loadJSON(`data/weeks/${week}.json`, { optional: true });
      e = doc?.events?.find((x) => x.id === eid);
    }
    if (!e) return null;
    week = week || weekStart(dhakaKey(new Date(Date.parse(e.time_utc) - 5 * 3600e3)));
    // history: same currency+title in up to ~26 earlier stored weeks
    const idx = await loadJSON('data/weeks/index.json', { optional: true });
    const keys = (idx?.weeks || []).filter((k) => k <= week).slice(-27);
    const docs = await Promise.all(keys.map((k) => loadJSON(`data/weeks/${k}.json`, { optional: true })));
    const t0 = Date.parse(e.time_utc), seen = new Set();
    const hist = docs.flatMap((d) => d?.events || []).filter((x) => x.actual && x.currency === e.currency && x.title === e.title && Date.parse(x.time_utc) < t0)
      .sort((x, y) => Date.parse(y.time_utc) - Date.parse(x.time_utc))
      .map((x) => ({ date: dhakaKey(new Date(x.time_utc)), actual: x.actual, forecast: x.forecast, previous: x.previous, abw: x.abw || 0 }))
      .filter((r) => (seen.has(r.date) ? false : seen.add(r.date))).slice(0, 6);
    return { ...e, plain: true, history: hist, data: { actual: e.actual, forecast: e.forecast, previous: e.previous, revision: e.revision, abw: e.abw || 0 } };
  }
  function plainView(a) {
    const ffu = ffDayUrl(ffKeyOf(Date.parse(a.time_utc))) + (a.ff_id ? `#detail=${a.ff_id}` : '');
    return `<section class="an-sec glass" id="sec-what">${secHead('ক', 'নিউজটা কী', '', 'book')}
      <p class="lead">এই ইভেন্টের পূর্ণ বাংলা বিশ্লেষণ (সম্ভাবনা ও তিনটা সিনারিও) তৈরি হয় শুধু <b>আজ থেকে আগামী সপ্তাহের USD লাল/কমলা</b> নিউজের জন্য। নিচে সময়, ডেটা ও আগের রিলিজ দেখানো হলো।</p>
      <a class="ghost" href="${ffu}" target="_blank" rel="noopener">ForexFactory-তে বিস্তারিত ${icon('ext')}</a></section>
      ${sWhen(a)}${sData(a)}`;
  }

  // ---------------------------------------------------------------- render
  async function draw(soft) {
    const L = list();
    if (!id) {
      const d = defaultId();
      if (d) { id = d; history.replaceState(null, '', `#/analysis/${d}`); }
    }
    ev = id ? (core.analysis?.events?.[id] || null) : null;
    if (!ev && id) {
      el.innerHTML = `${picker(id)}<div class="an-main"><div class="an-sec glass sk"><div class="sk-row"></div><div class="sk-row"></div></div></div>`;
      ev = await findPlain(id);
    }
    if (!ev) {
      el.innerHTML = `<div class="an-layout">${picker(null)}<div class="an-main"><div class="empty glass reveal">${icon('scope')}
        <h2>${id ? 'ইভেন্টটি পাওয়া যায়নি' : 'এখন বিশ্লেষণের মতো কোনো নিউজ নেই'}</h2>
        <p>${id ? 'লিংকটি পুরোনো হতে পারে বা ডেটা আর্কাইভে নেই।' : 'আজ ও আগামী সপ্তাহে কোনো USD লাল/কমলা নিউজ পাওয়া যায়নি।'}</p>
        <a class="btn" href="#/calendar">ক্যালেন্ডার খুলুন</a></div></div></div>`;
      return;
    }
    const a = ev, t = new Date(a.time_utc);
    const toc = a.plain ? '' : `<div class="toc" role="navigation" aria-label="সেকশন">${[['what', 'নিউজটা কী'], ['when', 'কখন'], ['data', 'ডেটা'], ['prob', 'সম্ভাবনা'], ['scen', 'সিনারিও']].map(([k, n]) => `<button data-jump="sec-${k}">${n}</button>`).join('')}</div>`;
    el.innerHTML = `<div class="an-layout">
      ${picker(a.plain ? null : a.id)}
      <div class="an-main ${soft ? '' : 'reveal'}">
        <header class="an-hero glass">
          <div class="an-meta"><span class="cur-badge">${esc(a.currency)}</span>${impactIcon(a.impact)}<span>${IMPACT_BN[a.impact] || ''} ইমপ্যাক্ট</span><span class="dot-sep"></span><span>${dayShort(dhakaKey(t))} · ${a.time_special ? esc(a.time_special) : timeDhaka(t)} ঢাকা</span></div>
          <h1>${esc(a.title)}</h1>
          ${a.explainer?.name_bn ? `<p class="an-bn">${esc(a.explainer.name_bn)}</p>` : ''}
          ${toc}
        </header>
        ${a.plain ? plainView(a) : sWhat(a) + sWhen(a) + sData(a) + sProb(a) + sScen(a)}
        ${a.plain ? '' : `<p class="gen muted small">বিশ্লেষণ তৈরি: ${core.analysis?.generated_at ? stampDhaka(new Date(core.analysis.generated_at)) : '—'}</p>`}
      </div></div>`;
    el.querySelectorAll('.pick.on').forEach((p) => p.scrollIntoView({ block: 'nearest', inline: 'center' }));
    requestAnimationFrame(() => el.querySelectorAll('.bar-fill, .tri-track span, .vr-fill').forEach((b) => b.classList.add('go')));
    moveInk();
    tick();
  }
  function moveInk() {
    const ink = el.querySelector('.sc-ink'), on = el.querySelector('.sc-tabs button.on');
    if (ink && on) { ink.style.width = `${on.offsetWidth}px`; ink.style.transform = `translateX(${on.offsetLeft}px)`; }
  }
  el.addEventListener('click', (e) => {
    const j = e.target.closest('[data-jump]');
    if (j) { document.getElementById(j.dataset.jump)?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' }); return; }
    const b = e.target.closest('[data-sc]');
    if (b && ev?.scenarios) {
      scenarioIdx = Number(b.dataset.sc);
      el.querySelectorAll('.sc-tabs button').forEach((x, i) => { x.classList.toggle('on', i === scenarioIdx); x.setAttribute('aria-selected', String(i === scenarioIdx)); });
      const p = el.querySelector('.sc-panel');
      p.classList.add('swap');
      setTimeout(() => { p.innerHTML = scenarioPanel(ev.scenarios[scenarioIdx]); p.setAttribute('aria-labelledby', `sct-${scenarioIdx}`); p.classList.remove('swap'); }, 140);
      moveInk();
    }
  });
  el.addEventListener('keydown', (e) => {
    const b = e.target.closest('[data-sc]'); if (!b || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const n = ev.scenarios.length, i = (Number(b.dataset.sc) + (e.key === 'ArrowRight' ? 1 : n - 1)) % n;
    el.querySelector(`[data-sc="${i}"]`).focus(); el.querySelector(`[data-sc="${i}"]`).click();
  });
  const onResize = () => moveInk();
  addEventListener('resize', onResize);

  function tick() {
    const c = el.querySelector('[data-cd]'); if (!c) return;
    const ms = Date.parse(c.dataset.cd) - Date.now();
    const rel = el.querySelector('.released');
    if (ms <= 0) {
      c.hidden = true;
      if (rel) { rel.hidden = false; rel.querySelector('span').textContent = `রিলিজের সময় পেরিয়েছে (${agoBn(c.dataset.cd)})${ev?.data?.actual ? ` — Actual ${ev.data.actual}` : ' — Actual পরের আপডেটে আসবে'}`; }
      return;
    }
    const p = countdownParts(ms);
    c.querySelector('[data-u=d]').textContent = bn(p.d);
    c.querySelector('[data-u=h]').textContent = bn(pad(p.h));
    c.querySelector('[data-u=m]').textContent = bn(pad(p.m));
    c.querySelector('[data-u=s]').textContent = bn(pad(p.s));
  }
  ctx.onTick(tick);
  draw(false);
  return {
    update(r) { const nid = r.parts[0] || null; hint = r.params; if (nid !== id) { id = nid; scenarioIdx = 0; draw(false); window.scrollTo({ top: 0 }); } },
    refresh(c) { core = c; draw(true); },
    cleanup() { removeEventListener('resize', onResize); },
  };
}
