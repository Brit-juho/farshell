// 코드 뷰어 자동 갱신 — `/ws-fs-watch` 구독(server/fs_watch.py).
//
// 열린 파일(`file`)·트리의 펼친 디렉토리(`dir`)·소스컨트롤의 저장소(`repo`)를
// 구독하면, 디스크에서 바뀌었을 때 콜백이 불린다. 서버는 "바뀌었다"만 알리고
// 내용은 안 보낸다 — 콜백이 기존 /api/fs/*·/api/git/*로 다시 읽는다.
//
// 연결은 구독이 하나라도 있을 때만 연다. 마지막 구독이 풀리면 닫는다 — 뷰어를
// 안 쓰는 동안 서버에 감시가 남지 않게. 구독 목록은 매번 **전체**를 보낸다
// (서버가 상태를 추측할 필요가 없고, 재연결 때 그대로 다시 보내면 된다).
//
// 끊겼다 붙으면 그 사이에 놓친 변경이 있을 수 있으므로 모든 콜백을 한 번씩
// 부른다. 연결 자체가 안 되면 자동 갱신만 잃는다 — 새로고침 버튼은 남는다.

import { WS_BASE, _tokenQuery } from '../../core/env.js';
import { relayWsPath } from '../../core/host-route.js';

const _subs = new Map();   // `${kind}\n${path}` -> Set<fn>
let _ws = null;
let _open = false;
let _everOpened = false;
let _retries = 0;
let _stableTimer = null;
let _reconnectTimer = null;
let _syncQueued = false;

const _key = (kind, path) => `${kind}\n${path}`;

/**
 * 경로 변경 구독. kind: 'file' | 'dir' | 'repo'. 해제 함수를 돌려준다.
 * 같은 (kind, path)를 여럿이 구독해도 서버 구독은 하나다.
 */
export function watchPath(kind, path, fn) {
  if (!path || typeof fn !== 'function') return () => {};
  const k = _key(kind, path);
  if (!_subs.has(k)) { _subs.set(k, new Set()); _queueSync(); }
  _subs.get(k).add(fn);
  let done = false;
  return () => {
    if (done) return;
    done = true;
    const set = _subs.get(k);
    if (!set) return;
    set.delete(fn);
    if (!set.size) { _subs.delete(k); _queueSync(); }
  };
}

function _emit(k) {
  const set = _subs.get(k);
  if (!set) return;
  for (const fn of Array.from(set)) {
    // 구독자 하나가 던져도 나머지와 채널은 살아 있어야 한다.
    try { fn(); } catch (_) { /* 무시 */ }
  }
}

// 같은 틱의 구독·해제(트리 전체 다시 그리기 등)를 한 번에 보낸다.
function _queueSync() {
  if (_syncQueued) return;
  _syncQueued = true;
  queueMicrotask(() => { _syncQueued = false; _sync(); });
}

function _sync() {
  if (!_subs.size) { _close(); return; }
  if (!_ws) { _connect(); return; }   // 열리면 onopen이 보낸다
  if (!_open) return;
  const subs = Array.from(_subs.keys()).map((k) => {
    const i = k.indexOf('\n');
    return { kind: k.slice(0, i), path: k.slice(i + 1) };
  });
  try { _ws.send(JSON.stringify({ type: 'watch', subs })); } catch (_) { /* onclose가 재연결한다 */ }
}

function _close() {
  clearTimeout(_reconnectTimer);
  clearTimeout(_stableTimer);
  _reconnectTimer = null;
  _retries = 0;
  _everOpened = false;
  const ws = _ws;
  _ws = null;
  _open = false;
  if (ws) { ws.onclose = null; try { ws.close(); } catch (_) {} }
}

function _scheduleReconnect() {
  clearTimeout(_reconnectTimer);
  if (!_subs.size) return;
  // core/workspace-ws.js와 같은 지수 백오프. 상한에 닿아도 포기하지 않는다.
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(_retries, 5)));
  _retries += 1;
  _reconnectTimer = setTimeout(() => { _reconnectTimer = null; if (_subs.size && !_ws) _connect(); }, delay);
}

function _connect() {
  let ws;
  try {
    ws = new WebSocket(`${WS_BASE}${relayWsPath(`/ws-fs-watch${_tokenQuery}`)}`);
  } catch (_) {
    _scheduleReconnect();
    return;
  }
  _ws = ws;
  ws.onopen = () => {
    _open = true;
    clearTimeout(_stableTimer);
    _stableTimer = setTimeout(() => { _retries = 0; }, 3000);
    const reconnected = _everOpened;
    _everOpened = true;
    _sync();
    // 끊긴 동안 바뀐 것을 놓쳤을 수 있다 — 전부 한 번씩 다시 읽게 한다.
    if (reconnected) for (const k of Array.from(_subs.keys())) _emit(k);
  };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (!msg || msg.type !== 'changed' || !Array.isArray(msg.subs)) return;
    for (const s of msg.subs) {
      if (s && typeof s.kind === 'string' && typeof s.path === 'string') _emit(_key(s.kind, s.path));
    }
  };
  ws.onclose = () => {
    clearTimeout(_stableTimer);
    if (_ws === ws) { _ws = null; _open = false; }
    _scheduleReconnect();
  };
  // onerror 뒤에는 항상 onclose가 온다 — 여기서 재연결을 걸면 두 번 건다.
  ws.onerror = () => {};
}

/**
 * 콜백을 묶는다: 실행 중에 또 불리면 끝난 뒤 한 번만 더 돈다. 저장 한 번이
 * 이벤트 여러 개로 오거나 git status가 느릴 때 요청이 쌓이지 않게.
 */
export function coalesce(fn) {
  let running = false;
  let again = false;
  const run = async () => {
    if (running) { again = true; return; }
    running = true;
    try {
      do { again = false; await fn(); } while (again);
    } catch (_) { /* 다음 변경 때 다시 시도한다 */ } finally {
      running = false;
    }
  };
  return run;
}
