// 이벤트 기록 — 토스트·API 실패·JS 예외·연결 상태를 한 곳에 쌓는다.
//
// 왜 있나: 화면에 알리는 길은 showToast 하나로 이미 합쳐져 있었지만, 알림은 사라지면 끝이었고
// 오류 토스트는 e.message 만 남겼다(스택·HTTP 상태·요청 경로·서버 오류 코드는 버려졌다). 폰에는 개발자 도구가
// 없어서 난 오류를 다시 볼 방법이 없었다. 이 모듈은 사람에게 보이는 것과 무관하게 모든 사건을 기록한다.
//
// 보관: 메모리 링 버퍼 + sessionStorage(새로고침·앱이 죽었다 다시 열려도 직전 기록이 남는다).
// 서버로 보내는 일은 event-log-send.js 가 구독으로 맡는다(오류·경고만).
//
// 규칙
//   - 이 모듈은 절대 던지지 않는다. 기록 때문에 앱이 깨지면 안 된다.
//   - 기록 자체의 실패(저장소 막힘 등)는 기록하지 않는다 — 재귀를 만든다.
//   - 비밀값은 저장 전에 가린다(core/redact.js). 요청·응답 본문은 받지 않는다 — req 에는 메타만 들어온다.
import { redact } from './redact.js';
import { screenId } from './screen-id.js';

const STORE_KEY = 'vt.eventlog.v1';
const SEEN_KEY = 'vt.eventlog.seen';
const STORE_MAX_BYTES = 400 * 1024;
const MAX_EVENTS = 500;
const MAX_DEBUG = 200;
const MERGE_MS = 60_000;      // 같은 사건이 이 안에 다시 나면 한 줄로 합친다
const MERGE_LOOKBACK = 50;
const LEVELS = ['error', 'warn', 'info', 'success', 'debug'];
const SOURCES = ['toast', 'api', 'js', 'ws', 'net', 'ui', 'console'];

let events = [];            // 오래된 것 → 새 것
let seenAt = 0;
let ctxProvider = null;
let idSeq = 0;
let saveTimer = null;
const subs = new Set();

function safe(fn, fallback) {
  try { return fn(); } catch (_) { return fallback; }
}

function load() {
  const raw = safe(() => sessionStorage.getItem(STORE_KEY), null);
  const list = safe(() => JSON.parse(raw || '[]'), []);
  if (Array.isArray(list)) events = list.filter((e) => e && typeof e.id === 'string' && LEVELS.includes(e.level)).slice(-MAX_EVENTS);
  seenAt = Number(safe(() => localStorage.getItem(SEEN_KEY), 0)) || 0;
}
load();

function save() {
  saveTimer = null;
  safe(() => {
    let list = events;
    let json = JSON.stringify(list);
    while (json.length > STORE_MAX_BYTES && list.length > 10) {
      list = list.slice(Math.ceil(list.length / 4));
      json = JSON.stringify(list);
    }
    sessionStorage.setItem(STORE_KEY, json);
  });
}

function scheduleSave() {
  if (saveTimer == null) saveTimer = setTimeout(save, 250);
}

function emit(ev, kind) {
  for (const fn of [...subs]) safe(() => fn(ev, kind));
}

/** 앱이 현재 활성 세션 이름 같은 문맥을 알려 주는 함수를 등록한다(이 모듈이 store 를 import 하면 순환이 된다). */
export function setLogContext(fn) {
  ctxProvider = typeof fn === 'function' ? fn : null;
}

function shortUa() {
  const ua = safe(() => navigator.userAgent, '') || '';
  const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS'
    : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '?';
  const m = /(Edg|Firefox|CriOS|Chrome|Version)\/(\d+)/.exec(ua);
  const name = !m ? '?' : m[1] === 'Edg' ? 'Edge' : m[1] === 'CriOS' ? 'Chrome' : m[1] === 'Version' ? 'Safari' : m[1];
  return `${name} ${m ? m[2] : ''} / ${os}`.trim();
}

function context() {
  const extra = ctxProvider ? safe(ctxProvider, {}) || {} : {};
  return {
    page: safe(() => location.pathname, ''),
    view: safe(() => (window.innerWidth < 720 ? 'compact' : 'desktop'), ''),
    screen: safe(screenId, ''),
    ua: shortUa(),
    ...extra,
  };
}

function cut(v, n) {
  if (v == null) return '';
  const s = redact(typeof v === 'string' ? v : String(v));
  return s.length > n ? s.slice(0, n) : s;
}

function cleanReq(r) {
  if (!r || typeof r !== 'object') return undefined;
  const num = (x) => (Number.isFinite(Number(x)) ? Number(x) : undefined);
  return {
    method: cut(r.method, 10),
    path: cut(r.path, 300),
    status: num(r.status),
    ms: num(r.ms),
    code: cut(r.code, 80) || undefined,
    reason: cut(r.reason, 300) || undefined,
    host: cut(r.host, 80) || undefined,
  };
}

function dedupKey(e) {
  const r = e.req;
  return [e.level, e.source, e.msg, r ? r.method : '', r ? r.path : '', r ? r.status : ''].join('\u0000');
}

function newId() {
  idSeq += 1;
  return `e_${Date.now().toString(36)}${idSeq.toString(36)}${Math.random().toString(36).slice(2, 5)}`;
}

function trim() {
  const debug = events.filter((e) => e.level === 'debug');
  if (debug.length > MAX_DEBUG) {
    const drop = new Set(debug.slice(0, debug.length - MAX_DEBUG).map((e) => e.id));
    events = events.filter((e) => !drop.has(e.id));
  }
  if (events.length > MAX_EVENTS) events = events.slice(events.length - MAX_EVENTS);
}

/**
 * 한 건을 기록하고 id 를 돌려준다.
 * @param {{level:string, source?:string, msg:string, detail?:string, error?:any, req?:object, rel?:string, relGuess?:boolean}} o
 */
export function logEvent(o) {
  try {
    if (!o) return '';
    const level = LEVELS.includes(o.level) ? o.level : 'info';
    const source = SOURCES.includes(o.source) ? o.source : 'ui';
    const err = o.error && typeof o.error === 'object' ? o.error : null;
    const now = Date.now();
    const ev = {
      v: 1,
      id: newId(),
      ts: now,
      last: now,
      count: 1,
      level,
      source,
      msg: cut(o.msg != null && o.msg !== '' ? o.msg : (err && err.message) || '', 500),
    };
    const detail = cut(o.detail || (err && (err.stack || String(err))) || '', 4000);
    if (detail) ev.detail = detail;
    const req = cleanReq(o.req || (err && err.request));
    if (req) ev.req = req;
    const rel = o.rel || (err && err._logId);
    if (rel) { ev.rel = rel; if (o.relGuess) ev.relGuess = true; }

    const key = dedupKey(ev);
    for (let i = events.length - 1, n = 0; i >= 0 && n < MERGE_LOOKBACK; i -= 1, n += 1) {
      const prev = events[i];
      if (now - prev.last > MERGE_MS) break;
      if (dedupKey(prev) === key) {
        prev.count += 1;
        prev.last = now;
        if (ev.detail && !prev.detail) prev.detail = ev.detail;
        if (ev.rel && !prev.rel) { prev.rel = ev.rel; if (ev.relGuess) prev.relGuess = true; }
        if (err && !err._logId) err._logId = prev.id;
        scheduleSave();
        emit(prev, 'merge');
        return prev.id;
      }
    }
    ev.ctx = context();
    events.push(ev);
    trim();
    if (err && !err._logId) safe(() => { err._logId = ev.id; });
    scheduleSave();
    emit(ev, 'new');
    return ev.id;
  } catch (_) {
    return '';
  }
}

/**
 * 콘솔에 그대로 찍고 기록도 남긴다. 예전의 `console.error('[FarShell x]', e)` 자리에 쓴다.
 * @param {string} scope 어느 기능에서 났는지(짧은 이름)
 */
export function reportError(scope, err, opts) {
  safe(() => console.error(`[FarShell ${scope}]`, err));
  const msg = `${scope}: ${(err && err.message) || String(err)}`;
  return logEvent({ level: 'error', source: 'console', msg, error: err, ...(opts || {}) });
}

/** 최신순 목록. filter: { levels?: string[], q?: string, since?: number } */
export function getEvents(filter) {
  const f = filter || {};
  const q = f.q ? String(f.q).toLowerCase() : '';
  const out = [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (f.levels && !f.levels.includes(e.level)) continue;
    if (f.since && e.last < f.since) continue;
    if (q && !(e.msg + ' ' + (e.detail || '') + (e.req ? ` ${e.req.method} ${e.req.path} ${e.req.status}` : '')).toLowerCase().includes(q)) continue;
    out.push(e);
  }
  return out;
}

/** 최근 ms 안에 난 API 실패 기록의 id(없으면 ''). 오류 토스트의 원인을 추정해 묶을 때 쓴다. */
export function recentApiId(ms) {
  const now = Date.now();
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (now - e.last > ms) break;
    if (e.source === 'api') return e.id;
  }
  return '';
}

export function getEvent(id) {
  return events.find((e) => e.id === id) || null;
}

/** 마지막으로 기록을 본 뒤에 난 오류·경고 수 — 상태 막대 칩이 쓴다. */
export function unseenCount() {
  let n = 0;
  for (const e of events) if ((e.level === 'error' || e.level === 'warn') && e.last > seenAt) n += 1;
  return n;
}

export function markSeen() {
  seenAt = Date.now();
  safe(() => localStorage.setItem(SEEN_KEY, String(seenAt)));
  emit(null, 'seen');
}

export function clearLocal() {
  events = [];
  save();
  emit(null, 'clear');
}

/** fn(event|null, 'new'|'merge'|'seen'|'clear'). 해제 함수를 돌려준다. */
export function subscribeEvents(fn) {
  subs.add(fn);
  return () => subs.delete(fn);
}

// voice.js 는 app.js 와 따로 빌드되는 번들이라 import 할 수 없다(ui/toast.js 머리말과 같은 사정).
if (typeof window !== 'undefined') {
  window.vtLogEvent = logEvent;
  window.vtReportError = reportError;
}

/** 테스트 전용. */
export function _resetForTest() {
  events = [];
  seenAt = 0;
  ctxProvider = null;
  subs.clear();
  clearTimeout(saveTimer);
  saveTimer = null;
}
