// `/ws-workspace` 구독 — 서버가 이미 쏘고 있던 push를 처음으로 듣는 곳.
//
// 서버는 워크트리 생성·삭제·열기 때 `routes/worktree.py`의
// `_broadcast_worktrees_changed()`로 `{type:'worktrees_changed'}`를 쏘고,
// `/api/workspace` PUT 때 `workspace_updated`를 쏜다. 그런데 **이 채널에 붙는
// 클라이언트가 한 곳도 없었다** — push 경로가 만들어진 채로 죽어 있었고,
// 화면은 대신 8~10초 폴링으로 같은 정보를 다시 받아오고 있었다.
//
// 그 폴링 한 번이 서버에서 저장소마다 git 서브프로세스를 돌리는 1~2초짜리
// 작업이라(2026-09-16 정지 사고) 평시 부하의 대부분을 이 채널이 없애준다.
// 폴링은 지우지 않고 **안전망으로 남긴다** — WS가 끊긴 동안, 그리고 서버 밖에서
// 워크트리가 바뀌는 경우(터미널에서 직접 `git worktree add`)를 위해서다.
//
// ⚠ 이 모듈은 core 청크에 들어간다. 의존성을 늘리지 말 것(env.js만 본다).

import { WS_BASE, _tokenQuery } from './env.js';

const _subs = new Map();   // type -> Set<fn>
let _ws = null;
let _retries = 0;
let _stableTimer = null;
let _reconnectTimer = null;
let _started = false;

/** 워크스페이스 이벤트 구독. 해제 함수를 돌려준다. */
export function onWorkspaceEvent(type, fn) {
  if (!_subs.has(type)) _subs.set(type, new Set());
  _subs.get(type).add(fn);
  start();
  return () => {
    const set = _subs.get(type);
    if (set) set.delete(fn);
  };
}

function _emit(type, msg) {
  const set = _subs.get(type);
  if (!set) return;
  for (const fn of set) {
    // 구독자 하나가 던져도 나머지 구독자와 채널 자체는 살아 있어야 한다.
    try { fn(msg); } catch (_) { /* 무시 */ }
  }
}

function _scheduleReconnect() {
  clearTimeout(_reconnectTimer);
  // voice/notify.js와 같은 지수 백오프. 상한에 닿아도 포기하지 않는다
  // (모바일 장시간 세션의 flap에서도 채널이 살아 있어야 한다).
  const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(_retries, 5)));
  _retries += 1;
  _reconnectTimer = setTimeout(connect, delay);
}

function connect() {
  try {
    _ws = new WebSocket(`${WS_BASE}/ws-workspace${_tokenQuery}`);
  } catch (_) {
    return _scheduleReconnect();
  }

  _ws.onopen = () => {
    // [회귀 fb827a6와 같은 이유] accept 직후 닫히는 half-open flap에서 지수가
    // 자라지 못하는 것을 막기 위해, 3초 이상 안정적으로 열린 뒤에만 리셋한다.
    clearTimeout(_stableTimer);
    _stableTimer = setTimeout(() => { _retries = 0; }, 3000);
  };

  _ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg && msg.type) _emit(msg.type, msg);
  };

  _ws.onclose = () => {
    clearTimeout(_stableTimer);
    _ws = null;
    _scheduleReconnect();
  };

  // onerror 뒤에는 항상 onclose가 따라오므로 여기서 재연결을 걸지 않는다
  // — 걸면 타이머가 두 개가 되어 재연결이 배로 늘어난다.
  _ws.onerror = () => {};
}

/** 첫 구독자가 생길 때 한 번만 연결한다. */
export function start() {
  if (_started) return;
  _started = true;
  connect();
}
