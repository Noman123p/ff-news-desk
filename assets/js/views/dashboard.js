// Dashboard: today's (Dhaka) USD red/orange events + short Bangla briefs.
import { bn, esc, icon, timeDhaka, todayKey, dhakaKey, dayLong, dayShort, countdownParts, pad, relShort, impactIcon, IMPACT_BN } from '../util.js';

const BIAS = {
  up: ['up', 'USD ↑'], down: ['down', 'USD ↓'], flat: ['flat', 'USD →'],
  true: ['up', 'USD ↑'], false: ['down', 'USD ↓'],
};
const biasChip = (b) => {
  const [cls, label] = BIAS[String(b)] || ['flat', 'দিক অনিশ্চিত'];
  return `<span class="bias ${cls}">${icon(cls === 'up' ? 'up' : cls === 'down' ? 'down' : 'flat')}${label}</span>`;
};
const val = (v, cls = '') => (v ? `<b class="${cls}">${esc(v)}</b>` : '<b class="na">—</b>');
const abwCls = (abw) => (abw === 1 ? 'beat' : abw === 2 ? 'miss' : '');

function verdictChip(a) {
  const v = a?.verdict;
  if (!v?.top) return '<span class="vchip none">সম্ভাবনা-ডেটা নেই</span>';
  const pct = v.top.pct != null ? ` · ${bn(v.top.pct)}%` : ' · আনুমানিক';
  return `<span class="vchip">${icon('gauge')}${esc(v.top.label)}${pct}</span>`;
}

export function mount(el, route, ctx) {
  let core = ctx.core || {};
  const draw = () => {
    const cal = core.calendar, briefs = core.briefs, an = core.analysis?.events || {};
    if (!cal) {
      el.innerHTML = `<div class="empty glass reveal">${icon('alert')}<h2>ডেটা লোড করা যায়নি</h2><p>একটু পরে আবার চেষ্টা করুন।</p></div>`;
      return;
    }
    const now = Date.now();
    const tk = todayKey();
    const usd = cal.events.filter((e) => e.currency === 'USD' && (e.impact === 'High' || e.impact === 'Medium'));
    const today = usd.filter((e) => dhakaKey(new Date(e.time_utc)) === tk);
    const next = usd.find((e) => Date.parse(e.time_utc) > now && e.impact === 'High') || usd.find((e) => Date.parse(e.time_utc) > now);
    const upNext = today.find((e) => Date.parse(e.time_utc) > now);

    const hero = next ? `
      <section class="hero glass reveal">
        <div class="hero-main">
          <span class="eyebrow ${next.impact === 'High' ? '' : 'med'}">পরবর্তী ${next.impact === 'High' ? 'হাই' : 'মিডিয়াম'}-ইমপ্যাক্ট USD নিউজ</span>
          <h1><a href="#/analysis/${esc(next.id)}">${esc(next.title)}</a></h1>
          <p class="hero-sub">${dayShort(dhakaKey(new Date(next.time_utc)))} · ${timeDhaka(new Date(next.time_utc))} (ঢাকা)
            ${next.forecast ? ` · Forecast <b>${esc(next.forecast)}</b>` : ''}${next.previous ? ` · আগের <b>${esc(next.previous)}</b>` : ''}</p>
        </div>
        <div class="countdown" data-cd="${esc(next.time_utc)}" aria-label="কাউন্টডাউন">
          <div><b data-u="h">--</b><span>ঘণ্টা</span></div><div><b data-u="m">--</b><span>মিনিট</span></div><div><b data-u="s">--</b><span>সেকেন্ড</span></div>
        </div>
        <a class="hero-cta" href="#/analysis/${esc(next.id)}">পূর্ণ বিশ্লেষণ ${icon('right')}</a>
      </section>` : `
      <section class="hero glass reveal"><div class="hero-main"><span class="eyebrow">এই ও আগামী সপ্তাহ</span><h1>আর কোনো লাল/কমলা USD নিউজ বাকি নেই</h1>
      <p class="hero-sub"><a href="#/calendar">ক্যালেন্ডারে পুরো সপ্তাহ দেখুন →</a></p></div></section>`;

    const rows = today.map((e, i) => {
      const t = Date.parse(e.time_utc), past = t <= now, a = an[e.id];
      return `
      <a class="ev-card glass reveal ${past ? 'past' : ''} ${e === upNext ? 'up-next' : ''} is-${e.impact}" style="--i:${i + 1}" href="#/analysis/${esc(e.id)}">
        <div class="ev-time">
          <span class="t">${e.time_special ? esc(e.time_special) : timeDhaka(new Date(t))}</span>
          <span class="cd" data-rel="${esc(e.time_utc)}">${past ? (e.actual ? 'রিলিজ হয়েছে' : 'সময় পেরিয়েছে') : relShort(t - now)}</span>
        </div>
        <div class="ev-body">
          <div class="ev-title">${impactIcon(e.impact)}<h3>${esc(e.title)}</h3></div>
          <div class="ev-meta">${a ? esc(a.explainer?.name_bn || '') : `${IMPACT_BN[e.impact]} ইমপ্যাক্ট`}</div>
          <div class="pfa">
            <span><small>Previous</small>${val(e.previous)}</span>
            <span><small>Forecast</small>${val(e.forecast)}</span>
            <span><small>Actual</small>${val(e.actual, `actual ${abwCls(e.abw)}`)}</span>
          </div>
        </div>
        <div class="ev-side">${verdictChip(a)}<span class="go">বিশ্লেষণ ${icon('right')}</span></div>
      </a>`;
    }).join('');

    const todayBlock = `
      <section class="section">
        <div class="section-head"><h2>${icon('calendar')}আজ <span class="muted">· ${dayLong(tk)}</span></h2>
          <p class="hint">USD-এর লাল (উচ্চ) ও কমলা (মাঝারি) ইমপ্যাক্ট নিউজ — ঢাকা সময়ে। কার্ডে ক্লিক করলে বিস্তারিত বিশ্লেষণ।</p></div>
        ${today.length ? `<div class="ev-list">${rows}</div>` : `
          <div class="empty glass reveal">${icon('calendar')}<h3>আজ কোনো লাল/কমলা USD নিউজ নেই</h3>
          <p>${next ? `পরবর্তী: <a href="#/analysis/${esc(next.id)}">${esc(next.title)}</a> — ${dayShort(dhakaKey(new Date(next.time_utc)))}, ${timeDhaka(new Date(next.time_utc))}` : ''}</p>
          <a class="btn" href="#/calendar">ক্যালেন্ডার খুলুন</a></div>`}
      </section>`;

    const bl = briefs?.briefs || [];
    const briefCards = bl.map((b, i) => {
      const L = b.likely || {}, imp = L.impact || {};
      return `
      <article class="brief glass reveal is-${esc(b.impact)}" style="--i:${i + 1}">
        <div class="brief-top"><span class="when">${icon('clock')}${timeDhaka(new Date(b.time_utc))} · ${dayShort(dhakaKey(new Date(b.time_utc)))}</span><span class="conf">আস্থা: ${esc(b.confidence_bn || '—')}</span></div>
        <h3>${esc(b.title)}</h3>
        <p class="cat">${esc(b.category_bn || '')}${b.events?.length > 1 ? ` · একসাথে ${bn(b.events.length)}টি রিলিজ` : ''}</p>
        <div class="likely"><div class="likely-label"><span>সম্ভাব্য</span>${biasChip(L.usd_bias)}</div><p>${esc(L.text || '')}</p></div>
        <ul class="mini-assets">
          <li>${icon('gold')}<b>Gold</b><span>${esc(imp.gold || '—')}</span></li>
          <li>${icon('btc')}<b>BTC</b><span>${esc(imp.btc || '—')}</span></li>
          <li>${icon('fx')}<b>Forex</b><span>${esc(imp.forex || '—')}</span></li>
        </ul>
        <a class="brief-link" href="#/analysis/${esc(b.id)}">তিনটা সিনারিও ও সম্ভাবনা ${icon('right')}</a>
      </article>`;
    }).join('');
    const briefBlock = `
      <section class="section">
        <div class="section-head"><h2>${icon('spark')}বাংলা ব্রিফ <span class="muted">· পরবর্তী ২৪ ঘণ্টা</span></h2>
          <p class="hint">নিয়মভিত্তিক সংক্ষিপ্ত ধারণা — Forecast, ট্রেন্ড ও প্রেডিকশন মার্কেট থেকে। নিশ্চয়তা নয়।</p></div>
        ${bl.length ? `<div class="briefs">${briefCards}</div>` : `<div class="empty glass reveal">${icon('spark')}<p>${esc(briefs?.empty_message || 'পরবর্তী ২৪ ঘণ্টায় কোনো লাল/কমলা USD নিউজ নেই।')}</p></div>`}
      </section>`;

    el.innerHTML = hero + todayBlock + briefBlock;
    tickFn();
  };

  const tickFn = () => {
    const now = Date.now();
    el.querySelectorAll('[data-cd]').forEach((c) => {
      const p = countdownParts(Date.parse(c.dataset.cd) - now);
      const h = p.d * 24 + p.h;
      c.querySelector('[data-u=h]').textContent = bn(pad(h));
      c.querySelector('[data-u=m]').textContent = bn(pad(p.m));
      c.querySelector('[data-u=s]').textContent = bn(pad(p.s));
    });
    el.querySelectorAll('[data-rel]').forEach((c) => {
      const t = Date.parse(c.dataset.rel);
      if (t > now) c.textContent = relShort(t - now);
    });
  };
  draw();
  ctx.onTick(tickFn);
  // re-render when the Dhaka day rolls over or an event passes
  let lastKey = todayKey();
  const roll = setInterval(() => { if (todayKey() !== lastKey) { lastKey = todayKey(); el.classList.add('no-anim'); draw(); } }, 30e3);
  return { cleanup: () => clearInterval(roll), refresh: (c) => { core = c; el.classList.add('no-anim'); draw(); } };
}
