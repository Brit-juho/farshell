// N16 표면 레이어 — xterm wrapper를 페인 트리(크롬) 밖의 절대좌표 레이어에 둔다.
// panes.js는 이제 "빈 상자"(.vt-pane-body)만 그리고, 그 상자의 실측 rect를
// 여기로 넘기면(setPlacement) 이 파일이 해당 세션의 wrapper를 그 자리로
// transform시킨다. wrapper는 한 번 #vt-surface에 붙으면 다시 옮기지 않는다
// (appendChild로 부모를 바꾸는 게 곧 xterm 재생성 위험·WebGL 컨텍스트 손실
// 위험이었다 — 10-shell-layout.md §1).
//
// 두 조각: 1) 순수 로직(rectToPlacement·createRefitGate) — DOM 없이 단위
// 테스트 가능(surface.test.js). 2) DOM 배선(adoptWrapper·setPlacement·
// beginDrag/endDrag) — ResizeObserver로 1을 구동한다.
import { getSession } from '../core/store.js';
import { fitAndResize } from '../term/resize.js';

// pane-body의 뷰포트 rect를 #vt-surface 기준 좌표로. DOMRect·plain object 둘 다 허용.
export function rectToPlacement(surfaceRect, bodyRect) {
  return {
    x: bodyRect.left - surfaceRect.left,
    y: bodyRect.top - surfaceRect.top,
    w: bodyRect.width,
    h: bodyRect.height,
  };
}

// refit 게이트 — 크기 변화 없으면 무시, 드래그 중이면 큐잉만 하고 종료 시
// flush(§1 수용 기준 2). onFit(id)엔 rAF로 fitAndResize를 모아 부르는 래퍼가 온다.
export function createRefitGate(onFit) {
  const lastSize = new Map(); // sessionId -> {w,h}
  const pending = new Set();
  let dragging = false;

  // 반환값은 테스트/디버그용: 'unchanged'|'pending'|'fit'.
  function report(id, w, h) {
    const last = lastSize.get(id);
    const changed = !last || last.w !== w || last.h !== h;
    lastSize.set(id, { w, h });
    if (!changed) return 'unchanged';
    if (dragging) { pending.add(id); return 'pending'; }
    onFit(id);
    return 'fit';
  }

  function forget(id) { lastSize.delete(id); pending.delete(id); }
  function beginDrag() { dragging = true; }
  // 드래그 종료 — 유예됐던 세션만 한 번씩 flush. 반환값은 flush된 id(테스트용).
  function endDrag() {
    dragging = false;
    const ids = Array.from(pending);
    pending.clear();
    for (const id of ids) onFit(id);
    return ids;
  }

  return { report, forget, beginDrag, endDrag, isDragging: () => dragging, pendingSize: () => pending.size };
}

// ---- DOM 배선 ----

let _surfaceEl = null;
let _ro = null;
const _placed = new Map(); // sessionId -> pane-body

const _fitQueue = new Set();
let _fitRaf = null;
function _scheduleFit(id) {
  _fitQueue.add(id);
  if (_fitRaf) return;
  _fitRaf = requestAnimationFrame(() => {
    _fitRaf = null;
    const ids = Array.from(_fitQueue);
    _fitQueue.clear();
    for (const sid of ids) fitAndResize(sid);
  });
}

const _gate = createRefitGate(_scheduleFit);

function _onResizeObserved(entries) {
  for (const entry of entries) {
    const sid = entry.target.dataset.vtSessionId;
    if (sid) _applyRect(sid, entry.target);
  }
}

function _ensureSurface() {
  const container = document.getElementById('terminal-container');
  if (!container) return null;
  if (!_surfaceEl || !_surfaceEl.isConnected) {
    _surfaceEl = document.getElementById('vt-surface');
    if (!_surfaceEl) {
      _surfaceEl = document.createElement('div');
      _surfaceEl.id = 'vt-surface';
      container.appendChild(_surfaceEl);
    }
  }
  if (!_ro && typeof ResizeObserver !== 'undefined') _ro = new ResizeObserver(_onResizeObserved);
  return _surfaceEl;
}

function _applyRect(sessionId, bodyEl) {
  const s = getSession(sessionId);
  if (!s || !s.wrapper || !_surfaceEl) return;
  const p = rectToPlacement(_surfaceEl.getBoundingClientRect(), bodyEl.getBoundingClientRect());
  const wrap = s.wrapper;
  wrap.style.visibility = 'visible';
  wrap.style.transform = `translate(${p.x}px, ${p.y}px)`;
  wrap.style.width = `${p.w}px`;
  wrap.style.height = `${p.h}px`;
  _gate.report(sessionId, p.w, p.h);
}

// 배치 밖으로 밀려난 세션 — §1 그대로 visibility:hidden + translate(-9999px,0).
// DOM 제거는 안 한다(WebGL 컨텍스트 보존).
function _hide(sessionId) {
  const s = getSession(sessionId);
  if (s && s.wrapper) {
    s.wrapper.style.visibility = 'hidden';
    s.wrapper.style.transform = 'translate(-9999px, 0)';
  }
  _gate.forget(sessionId);
}

// xterm-setup.js가 세션 생성 시 한 번만 부른다 — 이후 이 wrapper는 다시 안 옮긴다.
export function adoptWrapper(sessionId, wrapperEl) {
  const surface = _ensureSurface();
  wrapperEl.classList.add('vt-term-wrap');
  wrapperEl.dataset.sessionId = sessionId;
  if (surface && wrapperEl.parentElement !== surface) surface.appendChild(wrapperEl);
}

// panes.js가 'layout' 렌더 뒤 한 번 부른다: Map<sessionId, paneBodyEl>. 빠진
// 세션은 숨기고, 새로 들어온 세션은 즉시 실측(게이트가 곧바로 fit 예약).
export function setPlacement(nextMap) {
  const surface = _ensureSurface();
  if (!surface) return;

  for (const [sid, oldBody] of _placed) {
    if (nextMap.get(sid) !== oldBody) {
      if (_ro) _ro.unobserve(oldBody);
      delete oldBody.dataset.vtSessionId;
    }
  }
  for (const sid of _placed.keys()) if (!nextMap.has(sid)) _hide(sid);
  for (const [sid, body] of nextMap) {
    if (_placed.get(sid) !== body) {
      body.dataset.vtSessionId = sid;
      if (_ro) _ro.observe(body);
      _applyRect(sid, body);
    }
  }

  _placed.clear();
  for (const [sid, body] of nextMap) _placed.set(sid, body);
}

export function beginDrag() { _gate.beginDrag(); }
export function endDrag() { _gate.endDrag(); }
// N43 §8 — 리사이즈 오버레이가 "나머지 N개 유예" 문구를 그릴지 판단하는 데 쓴다.
// 실제 게이트 상태를 그대로 읽으므로, 게이트 동작이 바뀌면 문구도 저절로 맞는다.
export function pendingCount() { return _gate.pendingSize(); }
