// Shared helpers: time (always Asia/Dhaka), Bangla digits, escaping, data loading.
export const TZ = 'Asia/Dhaka';
export const DHAKA_OFFSET_MS = 6 * 3600e3;           // Bangladesh has no DST
export const FF_OFFSET_MS = -5 * 3600e3;             // ForexFactory's anonymous calendar day

const BN = '০১২৩৪৫৬৭৮৯';
export const bn = (s) => String(s).replace(/\d/g, (d) => BN[d]);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const icon = (id, cls = 'ic') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;

const fmtTime = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true });
const fmtClock = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
const fmtDayLong = new Intl.DateTimeFormat('bn-BD', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
const fmtDayShort = new Intl.DateTimeFormat('bn-BD', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' });
const fmtDayMini = new Intl.DateTimeFormat('bn-BD', { timeZone: 'UTC', day: 'numeric', month: 'short' });
const fmtStamp = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });

export const timeDhaka = (d) => fmtTime.format(d);
export const clockDhaka = (d) => fmtClock.format(d);
export const stampDhaka = (d) => fmtStamp.format(d) + ' ঢাকা';

// "Dhaka date" keys are plain YYYY-MM-DD strings; Date objects for them are UTC midnight.
export const dhakaKey = (d) => new Date(d.getTime() + DHAKA_OFFSET_MS).toISOString().slice(0, 10);
export const todayKey = () => dhakaKey(new Date());
export const keyDate = (k) => new Date(k + 'T00:00:00Z');
export const addDays = (k, n) => new Date(keyDate(k).getTime() + n * 864e5).toISOString().slice(0, 10);
export const weekStart = (k) => addDays(k, -keyDate(k).getUTCDay());   // Sunday
export const validKey = (k) => /^\d{4}-\d{2}-\d{2}$/.test(k || '') && !isNaN(keyDate(k)) && keyDate(k).toISOString().slice(0, 10) === k;
export const dayLong = (k) => fmtDayLong.format(keyDate(k));
export const dayShort = (k) => fmtDayShort.format(keyDate(k));
export const dayMini = (k) => fmtDayMini.format(keyDate(k));
/** UTC instant range [start, end) of a Dhaka calendar day. */
export const dhakaDayRange = (k) => { const s = keyDate(k).getTime() - DHAKA_OFFSET_MS; return [s, s + 864e5]; };

const MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const ffParam = (k) => { const d = keyDate(k); return `${MON[d.getUTCMonth()]}${d.getUTCDate()}.${d.getUTCFullYear()}`; };
export const ffDayUrl = (k) => `https://www.forexfactory.com/calendar?day=${ffParam(k)}`;
export const ffWeekUrl = (k) => `https://www.forexfactory.com/calendar?week=${ffParam(k)}`;
/** FF calendar day (its own timezone) containing an instant. */
export const ffKeyOf = (ms) => new Date(ms + FF_OFFSET_MS).toISOString().slice(0, 10);

export function countdownParts(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return { d: Math.floor(s / 86400), h: Math.floor((s % 86400) / 3600), m: Math.floor((s % 3600) / 60), s: s % 60 };
}
export const pad = (n) => String(n).padStart(2, '0');
export function relShort(ms) {
  const { d, h, m } = countdownParts(Math.abs(ms));
  const t = d ? `${bn(d)} দিন ${bn(h)} ঘ` : h ? `${bn(h)} ঘ ${bn(m)} মি` : `${bn(m)} মি`;
  return ms >= 0 ? `${t} পরে` : `${t} আগে`;
}
export function agoBn(iso) {
  if (!iso) return '';
  const min = Math.round((Date.now() - new Date(iso)) / 6e4);
  if (min < 1) return 'এইমাত্র';
  if (min < 60) return `${bn(min)} মিনিট আগে`;
  if (min < 48 * 60) return `${bn(Math.round(min / 60))} ঘণ্টা আগে`;
  return `${bn(Math.round(min / 1440))} দিন আগে`;
}

export function parseNum(s) {
  const m = String(s ?? '').replace(/,/g, '').match(/(-?\d+(?:\.\d+)?)\s*([KMBT]?)/i);
  if (!m) return null;
  return parseFloat(m[1]) * ({ '': 1, K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[m[2].toUpperCase()]);
}

export const IMPACT_BN = { High: 'উচ্চ', Medium: 'মাঝারি', Low: 'কম', Holiday: 'ছুটি' };
export const impactIcon = (imp) =>
  `<span class="ffi ffi-${esc(imp)}" role="img" aria-label="ইমপ্যাক্ট: ${IMPACT_BN[imp] || imp}" title="${IMPACT_BN[imp] || imp} ইমপ্যাক্ট"><svg viewBox="0 0 18 15"><use href="#i-folder"/></svg></span>`;

// ---------- data -------------------------------------------------------------
const cache = new Map();
let bust = Math.floor(Date.now() / 6e5);   // changes every 10 min
export function refreshBust() { bust = Math.floor(Date.now() / 6e5); cache.clear(); }
export function loadJSON(path, { optional = false } = {}) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(`${path}?v=${bust}`, { cache: 'no-cache' }).then((r) => {
    if (!r.ok) { if (optional) return null; throw new Error(`${path}: HTTP ${r.status}`); }
    return r.json();
  }).catch((e) => { cache.delete(path); if (optional) return null; throw e; });
  cache.set(path, p);
  return p;
}
export const loadCore = () => Promise.all([
  loadJSON('data/calendar.json', { optional: true }),
  loadJSON('data/briefs.json', { optional: true }),
  loadJSON('data/status.json', { optional: true }),
  loadJSON('data/analysis.json', { optional: true }),
]).then(([calendar, briefs, status, analysis]) => ({ calendar, briefs, status, analysis }));

export const store = {
  get(k, def) { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? def; } catch { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

export const CUR_FLAG = { USD: 'us', EUR: 'eu', GBP: 'gb', JPY: 'jp', AUD: 'au', NZD: 'nz', CAD: 'ca', CHF: 'ch', CNY: 'cn', ALL: '' };
