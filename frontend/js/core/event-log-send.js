// 오류·경고 기록을 서버(/api/client-log)로 보낸다. 보내는 쪽 규칙:
//   - 오류·경고만. 새 항목은 바로 대기열에, 같은 항목이 반복돼 횟수가 2·4·8…이 될 때 한 번 더(서버가 id 별 최신 줄을 쓴다).
//   - 2초 모아서 한 번에(50건·48KB 이하). 실패하면 5초 → 15초 → 60초. 서버에 못 닿는 동안(도달 불가)에는 보내지 않는다.
//   - 페이지를 떠날 때(pagehide·hidden)는 keepalive 로 남은 것을 비운다.
//   - 항상 **이 화면을 내준 서버**로 간다 — 다른 맥 화면을 보고 있어도 중계하지 않는다(timedFetch 는 주소·헤더를 안 바꾼다).
//   - 401/403(로그인 없음·기계 토큰 화면·비밀번호 없는 서버)은 이 탭에서 보내기를 끈다. 429 는 Retry-After 를 따른다.
//   - 전송 자신의 실패는 기록하지 않는다(noLog) — 전송 → 실패 → 기록 → 전송 고리가 된다.
import { getEvent, subscribeEvents } from './event-log.js';
import { timedFetch } from './api.js';
import { isUnreachable } from './reachability.js';

const QKEY = 'vt.eventlog.q';
const BATCH = 50;
const BATCH_BYTES = 48 * 1024;
const QUEUE_MAX = 200;
const BACKOFF = [5000, 15000, 60000];

let queue = [];           // 보낼 id (오래된 것 → 새 것)
const sent = new Map();   // id -> 마지막으로 보낸 count
let timer = null;
let fails = 0;
let state = 'ok';         // 'ok' | 'retry' | 'disabled'
let started = false;
let inflight = false;

const safe = (fn) => { try { return fn(); } catch (_) { return undefined; } };
const wanted = (e) => e && (e.level === 'error' || e.level === 'warn');

export function sendState() { return state; }

function saveQueue() { safe(() => sessionStorage.setItem(QKEY, JSON.stringify(queue))); }

function schedule(ms) {
  if (state === 'disabled') return;
  clearTimeout(timer);
  timer = setTimeout(() => flush(false), ms);
}

function enqueue(id) {
  if (!queue.includes(id)) queue.push(id);
  if (queue.length > QUEUE_MAX) queue = queue.slice(queue.length - QUEUE_MAX);
  saveQueue();
  if (!timer && !inflight) schedule(2000);   // 이미 걸린 타이머를 미루지 않는다 — 오류가 계속 나도 2초 안에 한 번은 나간다
}

function take() {
  const events = [];
  let bytes = 0;
  for (const id of queue) {
    const e = getEvent(id);
    if (!e) { queue = queue.filter((x) => x !== id); continue; }
    const size = JSON.stringify(e).length;
    if (events.length && (events.length >= BATCH || bytes + size > BATCH_BYTES)) break;
    events.push(e);
    bytes += size;
  }
  return events;
}

async function flush(leaving) {
  timer = null;
  if (inflight || state === 'disabled' || !queue.length) return;
  if (!leaving && isUnreachable()) { schedule(15000); return; }
  inflight = true;
  try { await drain(leaving); } finally {
    inflight = false;
    if (queue.length && !timer && state === 'ok') schedule(2000);   // 보내는 동안 쌓인 것
  }
}

async function drain(leaving) {
  let batch = take();
  while (batch.length) {
    let res;
    try {
      res = await timedFetch(`${window.API_BASE}/api/client-log`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events: batch }),
        keepalive: leaving || undefined,
        noLog: true,
      });
    } catch (_) { return retry(BACKOFF[Math.min(fails, 2)]); }
    if (res.status === 401 || res.status === 403) { state = 'disabled'; queue = []; saveQueue(); return; }
    if (res.status === 429) { const s = Number(res.headers.get('Retry-After')) || 30; return retry(Math.min(s, 120) * 1000); }
    if (res.status === 413 && batch.length > 1) { batch = batch.slice(0, Math.ceil(batch.length / 2)); continue; }
    if (res.ok || res.status === 400 || res.status === 413) {
      // 400·단건 413 은 다시 보내도 같다 — 버린다. 고리를 만들지 않는다.
      const done = new Set(batch.map((e) => e.id));
      for (const e of batch) sent.set(e.id, e.count);
      queue = queue.filter((id) => !done.has(id));
      saveQueue();
      fails = 0;
      state = 'ok';
      batch = leaving ? [] : take();
      continue;
    }
    return retry(BACKOFF[Math.min(fails, 2)]);
  }
}

function retry(ms) {
  fails += 1;
  state = 'retry';
  schedule(ms);
}

export function startEventSender() {
  if (started || typeof window === 'undefined') return;
  started = true;
  const saved = safe(() => JSON.parse(sessionStorage.getItem(QKEY) || '[]'));
  if (Array.isArray(saved)) queue = saved.filter((x) => typeof x === 'string').slice(-QUEUE_MAX);
  subscribeEvents((e, kind) => {
    if (!wanted(e)) return;
    if (kind === 'new') enqueue(e.id);
    // 반복 횟수는 2의 거듭제곱일 때만 다시 보낸다 — 폴링이 같은 오류를 계속 내도 요청이 늘지 않는다.
    else if (kind === 'merge' && (e.count & (e.count - 1)) === 0 && e.count > (sent.get(e.id) || 0)) enqueue(e.id);
  });
  const leave = () => { if (queue.length && state !== 'disabled') flush(true); };
  window.addEventListener('pagehide', leave);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leave(); });
  if (queue.length) schedule(3000);
}

/** 테스트 전용. */
export function _resetSendForTest() {
  clearTimeout(timer);
  timer = null;
  queue = [];
  sent.clear();
  fails = 0;
  state = 'ok';
  started = false;
  inflight = false;
}
