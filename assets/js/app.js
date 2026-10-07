// নিউজ ডেস্ক — app shell: hash router, ⋮ drawer, clock, status pill, source banner.
import { esc, icon, clockDhaka, stampDhaka, agoBn, loadCore, refreshBust } from './util.js';
import * as dashboard from './views/dashboard.js';
import * as calendar from './views/calendar.js';
import * as analysis from './views/analysis.js';

const VIEWS = { dashboard, calendar, analysis };
const NAMES = { dashboard: 'ড্যাশবোর্ড', calendar: 'ক্যালেন্ডার', analysis: 'বিশ্লেষণ' };
const STALE_H = 14;            // files rewrite on change + 12h heartbeat → >14h means Actions stopped
const REFRESH_MS = 10 * 60e3;
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const host = $('#view');
let core = null;
let current = { name: null, cleanup: null, api: null };
const ticks = new Set();

// ---------------------------------------------------------------- routing
export function parseHash(h = location.hash) {
  const raw = h.replace(/^#\/?/, '');
  const [path, qs = ''] = raw.split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || '', parts: parts.slice(1), params: new URLSearchParams(qs) };
}

async function route() {
  const r = parseHash();
  if (!VIEWS[r.name]) { history.replaceState(null, '', '#/dashboard'); return route(); }
  closeDrawer(false);
  markNav(r.name);
  document.title = `${NAMES[r.name]} · নিউজ ডেস্ক`;
  $('#viewName').textContent = NAMES[r.name];

  // same view (e.g. calendar date change, another analysis event): let the view update in place
  if (current.name === r.name && current.api?.update) {
    current.api.update(r);
    return;
  }
  const ctx = { core, onTick: (fn) => { ticks.add(fn); return () => ticks.delete(fn); }, go };
  const swap = () => {
    ticks.clear();
    try { current.cleanup?.(); } catch { /* ignore */ }
    host.innerHTML = '';
    const section = document.createElement('section');
    section.className = `view view-${r.name}`;
    host.append(section);
    const api = VIEWS[r.name].mount(section, r, ctx) || {};
    current = { name: r.name, cleanup: api.cleanup, api };
    if (!reduceMotion) { section.classList.add('view-enter'); requestAnimationFrame(() => requestAnimationFrame(() => section.classList.remove('view-enter'))); }
  };
  const old = host.firstElementChild;
  if (old && !reduceMotion && current.name) {
    old.classList.add('view-leave');
    await new Promise((res) => setTimeout(res, 160));
  }
  swap();
  if (r.name !== 'calendar' || !r.params.toString()) window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
}

export function go(hash, { replace = false } = {}) {
  if (replace) { history.replaceState(null, '', hash); route(); } else location.hash = hash;
}

function markNav(name) {
  $$('[data-nav]').forEach((a) => {
    const on = a.dataset.nav === name;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const ink = $('.tab-ink'), act = $('.tabs a.active');
  if (ink && act) { ink.style.width = `${act.offsetWidth}px`; ink.style.transform = `translateX(${act.offsetLeft}px)`; ink.style.opacity = 1; }
}

// ---------------------------------------------------------------- drawer
const drawer = $('#drawer'), scrim = $('#scrim'), menuBtn = $('#menuBtn');
function openDrawer() {
  scrim.hidden = false; drawer.removeAttribute('inert'); drawer.setAttribute('aria-hidden', 'false');
  requestAnimationFrame(() => { document.body.classList.add('drawer-open'); });
  menuBtn.setAttribute('aria-expanded', 'true');
  setTimeout(() => (drawer.querySelector('a.active') || drawer.querySelector('a'))?.focus(), 60);
}
function closeDrawer(focusBack = true) {
  if (!document.body.classList.contains('drawer-open')) return;
  document.body.classList.remove('drawer-open');
  drawer.setAttribute('inert', ''); drawer.setAttribute('aria-hidden', 'true');
  menuBtn.setAttribute('aria-expanded', 'false');
  setTimeout(() => { if (!document.body.classList.contains('drawer-open')) scrim.hidden = true; }, 380);
  if (focusBack) menuBtn.focus();
}
menuBtn.addEventListener('click', () => (document.body.classList.contains('drawer-open') ? closeDrawer() : openDrawer()));
$('#drawerClose').addEventListener('click', () => closeDrawer());
scrim.addEventListener('click', () => closeDrawer());
drawer.addEventListener('click', (e) => { if (e.target.closest('a')) closeDrawer(false); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeDrawer();
  if (e.key === 'Tab' && document.body.classList.contains('drawer-open')) {
    const f = $$('a, button', drawer); const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  if (!e.altKey && !e.ctrlKey && !e.metaKey && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || '')) {
    const k = { 1: 'dashboard', 2: 'calendar', 3: 'analysis' }[e.key];
    if (k) location.hash = `#/${k}`;
  }
});

// ---------------------------------------------------------------- shell widgets
const topbar = $('#topbar');
addEventListener('scroll', () => topbar.classList.toggle('scrolled', scrollY > 6), { passive: true });
addEventListener('resize', () => markNav(parseHash().name));

function lastUpdate() {
  const c = core || {};
  const ts = [c.calendar?.generated_at, c.briefs?.generated_at, c.analysis?.generated_at, c.status?.checked_at]
    .map((x) => Date.parse(x)).filter((x) => !isNaN(x));
  return ts.length ? new Date(Math.max(...ts)) : null;
}
function renderStatus() {
  const el = $('#status'), txt = $('#statusText');
  el.classList.remove('ok', 'stale', 'err');
  if (!core?.calendar) { el.classList.add('err'); txt.textContent = 'ডেটা লোড হয়নি'; return; }
  const gen = lastUpdate();
  if (!gen) { el.classList.add('stale'); txt.textContent = 'আপডেটের সময় অজানা'; return; }
  const ageH = (Date.now() - gen) / 3.6e6;
  const blocked = core.status?.ff_page_ok === false;
  if (core.calendar.stale || ageH > STALE_H) { el.classList.add('stale'); txt.textContent = `আপডেট ${agoBn(gen)} · পুরোনো হতে পারে`; }
  else if (blocked) { el.classList.add('stale'); txt.textContent = `আপডেট ${agoBn(gen)} · ব্যাকআপ ফিড`; }
  else { el.classList.add('ok'); txt.textContent = `আপডেট ${agoBn(gen)}`; }
  el.title = `শেষ ডেটা পরিবর্তন: ${stampDhaka(gen)} · ডেটা প্রতি ~২ ঘণ্টায় যাচাই হয়`;
  $('#footUpdated').textContent = `শেষ আপডেট: ${stampDhaka(gen)}`;
  const a = core.status?.archive;
  $('#drawerFoot').innerHTML = `${icon('db')}<span>ক্যালেন্ডার আর্কাইভ: <b>${a?.weeks ?? '—'}</b> সপ্তাহ${a?.first ? ` · ${esc(a.first)} থেকে` : ''}<br><span class="muted">${esc(txt.textContent)}</span></span>`;
}
function renderBanner() {
  const el = $('#ffBanner'), st = core?.status;
  if (!st || st.ff_page_ok !== false) { el.hidden = true; el.innerHTML = ''; return; }
  const since = Date.parse(st.checked_at);
  el.innerHTML = `${icon('alert')}<span><b>ForexFactory এখন আমাদের আপডেটার ব্লক করছে</b> — ${esc(st.fallback_in_use || 'ব্যাকআপ ফিড চলছে')}। নতুন Actual দেরিতে আসতে পারে।`
    + `${st.ff_reason ? ` <span class="muted">(${esc(st.ff_reason)}${isNaN(since) ? '' : ` · ${stampDhaka(new Date(since))}`})</span>` : ''}</span>`;
  el.hidden = false;
}

function tick() {
  $('#dhakaClock').textContent = clockDhaka(new Date());
  ticks.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
}

async function boot() {
  tick();
  setInterval(tick, 1000);
  try { core = await loadCore(); } catch (e) { console.error(e); core = {}; }
  renderStatus(); renderBanner();
  addEventListener('hashchange', route);
  await route();
  setInterval(async () => {
    refreshBust();
    try { core = await loadCore(); } catch { return; }
    renderStatus(); renderBanner();
    current.api?.refresh?.(core);
  }, REFRESH_MS);
  setInterval(renderStatus, 60e3);
}
boot();
