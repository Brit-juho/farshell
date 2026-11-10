// 코드 뷰어 자동 갱신 — `/ws-fs-watch` 구독(server/fs_watch.py).
//
// 열린 파일(`file`)·디렉토리(`dir`)·소스컨트롤의 저장소(`repo`)·파일 트리 루트(`tree`)를
// 구독하면, 디스크에서 바뀌었을 때 콜백이 불린다. 서버는 "바뀌었다"만 알리고
// 내용은 안 보낸다 — 콜백이 기존 /api/fs/*·/api/git/*로 다시 읽는다.
// `tree`만 예외로 **바뀐 디렉토리 목록**을 함께 준다(server/fs_watch.py) — 콜백의 첫 인자가
// `{dirs, overflow}`다. 다시 붙었을 때는 `{reconnect: true}`(목록 없음 = 받아 둔 것 전부 다시 읽기).
//
// 연결은 구독이 하나라도 있을 때만 연다. 마지막 구독이 풀리면 닫는다 — 뷰어를
// 안 쓰는 동안 서버에 감시가 남지 않게. 구독 목록은 매번 **전체**를 보낸다
// (서버가 상태를 추측할 필요가 없고, 재연결 때 그대로 다시 보내면 된다).
//
// 끊겼다 붙으면 그 사이에 놓친 변경이 있을 수 있으므로 모든 콜백을 한 번씩
// 부른다. 연결 자체가 안 되면 자동 갱신만 잃는다 — 새로고침 버튼은 남는다.
//
// 자리: `shell` 청크(2026-09-30). 뷰어(panels 청크)와 레일(shell 청크)이 같은
// 인스턴스를 써야 연결이 하나다. core.js는 300KiB 상한 턱밑이라 못 넣고, shell은
// 부팅 때 어차피 받는 청크라 여기 두면 추가로 내려받는 것이 없다.

import { WS_BASE, _tokenQuery } from '../core/env.js';
import { relayWsPath } from '../core/host-route.js';
import { watchSocketLiveness } from '../core/ws-watchdog.js';
import { waitForAuthentication } from '../core/auth-state.js';

const _subs = new Map();   // `${kind}\n${path}` -> Set<fn>
let _ws = null;
let _open = false;
let _everOpened = false;
let _retries = 0;
let _stableTimer = null;
let _reconnectTimer = null;
let _syncQueued = false;
let _failed = false;          // 서버가 감시 실패를 알렸다 — 다음 (재)구독까지
let _rejected = new Set();    // 서버가 거절한 구독 키(경계 밖·저장소 아님 등)

const _key = (kind, path) => `${kind}\n${path}`;

/**
 * 경로 변경 구독. kind: 'file' | 'dir' | 'repo' | 'tree'. 해제 함수를 돌려준다.
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

function _emit(k, detail) {
  const set = _subs.get(k);
  if (!set) return;
  for (const fn of Array.from(set)) {
    // 구독자 하나가 던져도 나머지와 채널은 살아 있어야 한다.
    try { fn(detail); } catch (_) { /* 무시 */ }
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

// 로그인 화면이 떠 있는 동안은 붙지 않는다(인증 없는 접속이 사전 인증 실패로 세어진다). 재연결 타이머도 여기를 지난다.
let _waitingAuth = false;
function _connect() {
  if (window.__vtAuthed === false) {
    if (_waitingAuth) return;
    _waitingAuth = true;
    waitForAuthentication().then((ok) => {
      _waitingAuth = false;
      if (ok && _subs.size && !_ws) _connect();
    });
    return;
  }
  _connectNow();
}

function _connectNow() {
  let ws;
  try {
    ws = new WebSocket(`${WS_BASE}${relayWsPath(`/ws-fs-watch${_tokenQuery}`)}`);
  } catch (_) {
    _scheduleReconnect();
    return;
  }
  _ws = ws;
  // 생존 감시(core/ws-watchdog.js) — 서버 ping(15초)이 45초 동안 안 오면 닫아 재연결을 돌린다.
  let liveness = null;
  ws.onopen = () => {
    liveness?.stop();
    liveness = watchSocketLiveness(ws);
    _open = true;
    clearTimeout(_stableTimer);
    _stableTimer = setTimeout(() => { _retries = 0; }, 3000);
    const reconnected = _everOpened;
    _everOpened = true;
    _sync();
    // 끊긴 동안 바뀐 것을 놓쳤을 수 있다 — 전부 한 번씩 다시 읽게 한다.
    if (reconnected) for (const k of Array.from(_subs.keys())) _emit(k, { reconnect: true });
  };
  ws.onmessage = (e) => {
    liveness?.alive();
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg && msg.type === 'ping') {
      try { ws.send(JSON.stringify({ type: 'pong' })); } catch (_) { /* 답하지 않아도 된다 */ }
      return;
    }
    if (msg && msg.type === 'watching') {
      _failed = false;
      _rejected = new Set((Array.isArray(msg.rejected) ? msg.rejected : [])
        .filter((r) => r && typeof r.kind === 'string' && typeof r.path === 'string')
        .map((r) => _key(r.kind, r.path)));
      return;
    }
    if (msg && msg.type === 'error') { _failed = true; return; }
    if (!msg || msg.type !== 'changed' || !Array.isArray(msg.subs)) return;
    // `tree` 알림은 구독 하나에 바뀐 디렉토리 목록(루트 기준 상대경로)이 붙어 온다.
    const detail = Array.isArray(msg.dirs)
      ? { dirs: msg.dirs.filter((d) => typeof d === 'string'), overflow: msg.overflow === true }
      : (msg.overflow === true ? { overflow: true } : undefined);
    for (const s of msg.subs) {
      if (s && typeof s.kind === 'string' && typeof s.path === 'string') _emit(_key(s.kind, s.path), detail);
    }
  };
  ws.onclose = () => {
    liveness?.stop();
    liveness = null;
    clearTimeout(_stableTimer);
    // 이미 다른 소켓으로 갈아탔거나(_close 뒤 새 연결) 닫힌 뒤면 재연결을 또 걸지 않는다.
    if (_ws !== ws) return;
    _ws = null; _open = false; _rejected = new Set();
    _scheduleReconnect();
  };
  // onerror 뒤에는 항상 onclose가 온다 — 여기서 재연결을 걸면 두 번 건다.
  ws.onerror = () => {};
}

/**
 * 이 구독이 **지금 실제로 감시되고 있는가.** 연결이 열려 있고, 서버가 거절하지
 * 않았고, 감시가 실패하지 않았을 때만 true다. 변경 알림에 기대어 캐시를 오래
 * 두는 쪽(레일의 git 배지)은 이게 false면 원래의 짧은 TTL로 돌아가야 한다 —
 * 안 그러면 알림이 영영 안 오는데 캐시는 "깨끗하다"고 믿는다.
 */
export function isWatchLive(kind, path) {
  const k = _key(kind, path);
  return _open && !_failed && _subs.has(k) && !_rejected.has(k);
}

/**
 * 콜백을 묶는다: 실행 중에 또 불리면 끝난 뒤 한 번만 더 돈다. 저장 한 번이
 * 이벤트 여러 개로 오거나 상태 조회가 느릴 때 요청이 쌓이지 않게.
 * `opts.minGap`(ms, 또는 그 값을 돌려주는 함수)을 주면 한 번 돈 뒤 그만큼은 다시 돌지 않고 그 사이 온 부름을
 * 한 번으로 모은다 — 큰 파일을 보는 뷰어가 계속 바뀌는 파일에 끌려 쉬지 않고 다시 받지 않게(file.js refreshGap).
 * 첫 부름은 기다리지 않는다.
 */
export function coalesce(fn, opts = {}) {
  let running = false;
  let again = false;
  let timer = null;
  let last = -Infinity;
  const gap = () => {
    let g = 0;
    try { g = Number(typeof opts.minGap === 'function' ? opts.minGap() : opts.minGap) || 0; } catch (_) { g = 0; }
    return g > 0 ? g : 0;
  };
  const wait = () => last + gap() - Date.now();
  const run = async () => {
    if (running) { again = true; return; }
    const w = wait();
    if (w > 0) {
      if (!timer) timer = setTimeout(() => { timer = null; run(); }, w);
      return;
    }
    running = true;
    try {
      do {
        again = false;
        last = Date.now();
        await fn();
        if (again) {
          const w2 = wait();
          if (w2 > 0) await new Promise((r) => { setTimeout(r, w2); });
        }
      } while (again);
    } catch (_) { /* 다음 변경 때 다시 시도한다 */ } finally {
      running = false;
    }
  };
  return run;
}
