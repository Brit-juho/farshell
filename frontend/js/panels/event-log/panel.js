// 기록 패널 — 토스트·API 실패·JS 예외·연결 상태를 한 곳에서 본다(core/event-log.js).
// 두 보기: 「이 화면」(이 탭의 브라우저 기록, 모든 등급) / 「모든 화면」(서버 파일, 오류·경고, 다른 기기 포함).
// 서버 문자열은 전부 textContent 로 넣는다(스택·메시지에 무엇이 들어 있을지 모른다).
import { openPanel } from '../panel.js';
import { icon } from '../../ui/icons.js';
import { okFlash } from '../../ui/okflash.js';
import { t } from '../../core/i18n.js';
import { timedFetch } from '../../core/api.js';
import { screenId } from '../../core/screen-id.js';
import { confirmDialog } from '../../core/dialog-lazy.js';
import { getEvents, getEvent, subscribeEvents, markSeen, clearLocal } from '../../core/event-log.js';
import { reqLine, fmtClock, eventToText, reportText } from './format.js';

const PANEL_ID = 'vt-eventlog';
const LEVEL_GROUPS = [
  { id: 'error', label: () => t('오류'), levels: ['error'] },
  { id: 'warn', label: () => t('경고'), levels: ['warn'] },
  { id: 'info', label: () => t('알림'), levels: ['info', 'success'] },
  { id: 'debug', label: () => t('요청'), levels: ['debug'] },
];
const SOURCE_LABEL = () => ({
  toast: t('알림'), api: t('요청'), js: t('스크립트'), ws: t('연결'), net: t('네트워크'), ui: t('화면'), console: t('콘솔'), server: t('서버'),
});
const TONE = { error: 'err', warn: 'warn', success: 'ok', info: 'info', debug: 'dim' };

// 패널을 닫았다 열어도 고른 보기·필터가 이어지게 모듈에 둔다(탭을 닫으면 사라지는 per-page 상태).
let unsub = null;
let cur = null;   // 열려 있는 패널의 화면 조각들
const state = { view: 'local', on: new Set(['error', 'warn', 'info']), q: '', open: new Set(), server: null, serverErr: '', loading: false };

function h(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

function btn(label, ic, onClick, cls = '') {
  const b = h('button', `vt-btn sm ${cls}`.trim());
  b.type = 'button';
  if (ic) b.innerHTML = icon(ic, 14);
  b.append(document.createTextNode(label));
  b.addEventListener('click', onClick);
  return b;
}

function activeLevels() {
  return LEVEL_GROUPS.filter((g) => state.on.has(g.id)).flatMap((g) => g.levels);
}

async function loadServer(ui) {
  state.loading = true;
  state.serverErr = '';
  render(ui);
  try {
    const qs = new URLSearchParams({ limit: '300' });
    if (state.q) qs.set('q', state.q);
    // 중계하지 않는다 — 다른 맥 화면을 보고 있어도 이 화면을 내준 서버의 기록이다. noLog: 이 조회의 실패가 기록을 부풀리지 않게.
    const res = await timedFetch(`${window.API_BASE}/api/client-log?${qs}`, { noLog: true });
    if (!res.ok) throw new Error(res.status === 403 ? t('이 화면은 서버 기록을 쓸 수 없습니다 (로그인한 브라우저에서만 저장됩니다)') : `HTTP ${res.status}`);
    state.server = (await res.json()).items || [];
  } catch (e) {
    state.server = null;
    state.serverErr = e && e.message ? e.message : String(e);
  }
  state.loading = false;
  if (ui.root.isConnected) render(ui);
}

function visibleEvents() {
  if (state.view === 'server') {
    const lv = new Set(activeLevels());
    return (state.server || []).filter((e) => lv.has(e.level));
  }
  return getEvents({ levels: activeLevels(), q: state.q });
}

function relOf(e) {
  if (!e.rel) return null;
  return state.view === 'server' ? (state.server || []).find((x) => x.id === e.rel) || null : getEvent(e.rel);
}

function envInfo() {
  return { ua: navigator.userAgent, lang: document.documentElement.lang || navigator.language || '', now: Date.now() };
}

async function copyText(text, b) {
  try {
    const { copyToClipboard } = await import('../../term/clipboard.js');
    if (await copyToClipboard(text) && b) okFlash(b);
  } catch (_) { /* 클립보드가 막힌 브라우저 — 내려받기를 쓴다 */ }
}

function download(list) {
  const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), events: list }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = h('a');
  a.href = url;
  a.download = `farshell-log-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function detailBlock(e, ui) {
  const box = h('div', 'vt-ev-detail');
  const kv = (k, v) => { if (v) { const r = h('div', 'vt-ev-kv'); r.append(h('span', 'k', k), h('span', 'v', String(v))); box.appendChild(r); } };
  if (e.req) {
    kv(t('요청'), reqLine(e.req));
    kv(t('서버 코드'), e.req.code);
    kv(t('사유'), e.req.reason);
    kv(t('맥'), e.req.host);
  }
  kv(t('시각'), new Date(e.ts).toLocaleString());
  if (e.last !== e.ts) kv(t('마지막'), new Date(e.last).toLocaleString());
  if (e.ctx) {
    kv(t('화면'), [e.ctx.view, e.ctx.page].filter(Boolean).join(' '));
    kv(t('세션'), e.ctx.session);
    kv(t('브라우저'), e.ctx.ua);
    if (e.ctx.screen) kv(t('화면 ID'), String(e.ctx.screen).slice(0, 8) + (e.ctx.screen === screenId() ? ` (${t('이 탭')})` : ''));
  }
  const rel = relOf(e);
  if (e.rel) {
    const r = h('div', 'vt-ev-kv');
    r.appendChild(h('span', 'k', t('관련')));
    const link = h('button', 'vt-ev-link', rel ? (rel.req ? reqLine(rel.req) : rel.msg) : t('기록에서 밀려났습니다'));
    link.type = 'button';
    link.disabled = !rel;
    link.addEventListener('click', () => {
      const g = LEVEL_GROUPS.find((x) => x.levels.includes(rel.level));
      if (g && state.view === 'local') state.on.add(g.id);   // 관련 요청이 가려진 등급(보통 debug 4xx)이어도 보이게
      state.open.add(rel.id);
      render(ui);
    });
    r.appendChild(link);
    if (e.relGuess) r.appendChild(h('span', 'vt-ev-guess', t('추정')));
    box.appendChild(r);
  }
  if (e.detail) box.appendChild(h('pre', 'vt-ev-pre', e.detail));
  const actions = h('div', 'vt-ev-actions');
  actions.appendChild(btn(t('복사'), 'clipboard-copy', (ev) => copyText(eventToText(e, rel), ev.currentTarget)));
  box.appendChild(actions);
  return box;
}

function row(e, ui) {
  const open = state.open.has(e.id);
  const li = h('li', `vt-ev-row ${TONE[e.level] || ''}${open ? ' open' : ''}`);
  li.dataset.id = e.id;
  const line = h('button', 'vt-ev-line');
  line.type = 'button';
  line.setAttribute('aria-expanded', String(open));
  line.append(h('span', 'vt-ev-time', fmtClock(e.last)), h('span', 'vt-ev-dot'), h('span', 'vt-ev-msg', e.msg || '—'));
  if (e.count > 1) line.appendChild(h('span', 'vt-ev-count', `×${e.count}`));
  line.appendChild(h('span', 'vt-ev-src', SOURCE_LABEL()[e.source] || e.source));
  line.addEventListener('click', () => { if (open) state.open.delete(e.id); else state.open.add(e.id); render(ui); });
  li.appendChild(line);
  if (e.req && !open) li.appendChild(h('div', 'vt-ev-req', reqLine(e.req)));
  if (open) li.appendChild(detailBlock(e, ui));
  return li;
}

function toolbar(ui) {
  const bar = h('div', 'vt-ev-tools');
  const seg = h('div', 'vt-ev-seg');
  seg.setAttribute('role', 'tablist');
  for (const [id, label] of [['local', t('이 화면')], ['server', t('모든 화면')]]) {
    const b = h('button', `vt-ev-segbtn${state.view === id ? ' on' : ''}`, label);
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(state.view === id));
    b.addEventListener('click', () => { state.view = id; if (id === 'server') loadServer(ui); else render(ui); });
    seg.appendChild(b);
  }
  bar.appendChild(seg);
  const chips = h('div', 'vt-ev-chips');
  for (const g of LEVEL_GROUPS) {
    if (state.view === 'server' && (g.id === 'info' || g.id === 'debug')) continue;   // 서버에는 오류·경고만 있다
    const c = h('button', `vt-ev-chip${state.on.has(g.id) ? ' on' : ''}`, g.label());
    c.type = 'button';
    c.setAttribute('aria-pressed', String(state.on.has(g.id)));
    c.addEventListener('click', () => { if (state.on.has(g.id)) state.on.delete(g.id); else state.on.add(g.id); render(ui); });
    chips.appendChild(c);
  }
  bar.appendChild(chips);
  const q = h('input', 'vt-ev-q');
  q.type = 'search';
  q.placeholder = t('검색');
  q.value = state.q;
  q.setAttribute('aria-label', t('기록 검색'));
  let timer = null;
  q.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.q = q.value.trim();
      if (state.view === 'server') loadServer(ui); else renderList(ui);
    }, 200);
  });
  bar.appendChild(q);
  return bar;
}

function footer(ui) {
  const list = visibleEvents();
  const foot = h('div', 'vt-ev-foot');
  foot.appendChild(btn(t('이슈용 복사'), 'clipboard-copy', (ev) => {
    const errs = list.filter((e) => e.level === 'error' || e.level === 'warn').slice(0, 20);
    copyText(reportText(errs.length ? errs : list.slice(0, 20), envInfo(), relOf), ev.currentTarget);
  }));
  foot.appendChild(btn(t('JSON 내려받기'), 'download', () => download(list)));
  foot.appendChild(btn(t('지우기'), null, async () => {
    if (state.view === 'server') {
      if (!await confirmDialog(t('서버에 쌓인 모든 화면의 오류 기록을 지울까요?'), { title: t('서버 기록 지우기'), okLabel: t('지우기'), danger: true })) return;
      try { await timedFetch(`${window.API_BASE}/api/client-log`, { method: 'DELETE', noLog: true }); } catch (_) { /* 아래 다시 읽기가 실패를 보여 준다 */ }
      loadServer(ui);
    } else {
      clearLocal();
      state.open.clear();
      render(ui);
    }
  }, 'danger'));
  foot.appendChild(h('div', 'vt-ev-note', t('복사·내려받은 내용에는 경로·세션 이름이 들어 있을 수 있습니다. 공개하기 전에 확인하세요.')));
  return foot;
}

function renderList(ui) {
  const box = ui.list;
  box.textContent = '';
  if (state.view === 'server' && state.loading) { box.appendChild(h('div', 'vt-ev-empty', t('불러오는 중…'))); return; }
  if (state.view === 'server' && state.serverErr) {
    const m = h('div', 'vt-ev-empty err', state.serverErr);
    box.appendChild(m);
    box.appendChild(btn(t('다시 시도'), 'refresh-cw', () => loadServer(ui)));
    return;
  }
  const list = visibleEvents();
  if (!list.length) {
    box.appendChild(h('div', 'vt-ev-empty', state.q ? t('찾는 기록이 없습니다') : t('기록이 없습니다')));
    return;
  }
  const ul = h('ul', 'vt-ev-list');
  for (const e of list) ul.appendChild(row(e, ui));
  box.appendChild(ul);
}

function render(ui) {
  if (!ui.root.isConnected) return;
  const top = ui.list.scrollTop;
  ui.tools.replaceChildren(toolbar(ui));
  renderList(ui);
  ui.foot.replaceChildren(footer(ui));
  ui.list.scrollTop = top;
}

function focusOn(ui, focusId) {
  const e = getEvent(focusId);
  if (!e) return;
  state.view = 'local';
  const g = LEVEL_GROUPS.find((x) => x.levels.includes(e.level));
  if (g) state.on.add(g.id);
  state.open.add(focusId);
  render(ui);
  const target = ui.list.querySelector(`[data-id="${CSS.escape(focusId)}"]`);
  if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
}

export function showEventLog(focusId) {
  // 이미 열려 있을 때 「자세히」를 누르면 닫지 말고 그 항목으로 옮겨 간다(openPanel 은 재호출을 토글로 본다).
  if (cur && cur.root.isConnected) {
    if (focusId) { focusOn(cur, focusId); return; }
  }
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: t('기록'),
    headHTML: `<div class="vt-vw-title">${t('기록')}</div><span class="vt-ph-ctx">${t('알림 · 오류 · 요청')}</span>`,
    bodyId: 'vt-eventlog-body',
    extraClass: 'vt-evlog',
    onClose: () => { if (unsub) unsub(); unsub = null; cur = null; },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다
  const body = panel.ctx.byId('vt-eventlog-body');
  const ui = { root: body, tools: h('div'), list: h('div', 'vt-ev-scroll'), foot: h('div') };
  cur = ui;
  body.replaceChildren(ui.tools, ui.list, ui.foot);
  markSeen();
  let raf = 0;
  unsub = subscribeEvents((ev, kind) => {
    if (kind === 'seen' || state.view !== 'local' || raf) return;
    raf = requestAnimationFrame(() => { raf = 0; markSeen(); renderList(ui); });
  });
  render(ui);
  if (focusId) focusOn(ui, focusId);
  if (state.view === 'server') loadServer(ui);
}
