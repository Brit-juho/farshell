// L5 — DnD 공용 로직. 탭/pane 헤더를 pane 위로 드래그해 5구역 드롭존(가장자리
// 4곳=그 방향 분할, 중앙=세션 교체)으로 배정한다.
//
// 마우스는 네이티브 HTML5 DnD(dragstart/dragover/drop)를 그대로 쓴다(tab-dom.js의
// 기존 탭 재정렬 배선과 같은 API). 터치는 별개다 — iOS Safari는 임의 엘리먼트의
// HTML5 DnD를 터치로 아예 지원하지 않고 Android Chrome도 신뢰할 수 없어(터치로
// 시작한 dragstart가 안 뜨는 경우가 흔함), pointer:coarse는 이 파일의
// wireTouchDragSource()가 포인터 이벤트로 직접 흉내낸다. LONGPRESS_MS/
// MOVE_THRESHOLD는 이미 이 코드베이스가 쓰는 "롱프레스=탭이 아니다" 기준
// (term/xterm-setup.js:123 "500ms/8px", term/keybar.js R4)과 동일하게 맞췄다 —
// 값이 여기저기 다르면 사용자가 기기마다 다른 손맛을 느낀다.
//
// 판정 함수(computeDropZone)와 반영 함수(applyPaneDrop)는 DOM 이벤트와 분리된
// 순수 로직이라 단위 테스트 대상이다(layout-dnd.test.js).
import { splitPane, setPaneSession, countLeaves } from './store.js';

export const SESSION_MIME = 'text/vt-tab-id';

// 반응형 3구간별 pane 상한(20-design-system.md §2-1과 동일 값). "새로 분할하는
// 액션"만 막는 게이트다 — 이미 만들어진 트리는 화면이 좁아져도 잘리지 않는다
// (L3 3단계 원칙 그대로, panes.js의 헤더 분할 버튼과 이 파일의 가장자리 드롭
// 둘 다 여기 하나만 거친다).
const PANE_CAP = { compact: 720, regular: 1024 };
function _tierCap() {
  const w = window.innerWidth;
  if (w < PANE_CAP.compact) return 2;
  if (w < PANE_CAP.regular) return 4;
  return 6;
}
export function canSplit() {
  return countLeaves() < _tierCap();
}

// rect 기준 (x,y)가 어느 구역인지 판정한다. 네 가장자리까지의 상대 거리 중
// 가장 작은 값이 edge 임계값보다 작으면 그 방향, 아니면 center — VS
// Code/golden-layout과 같은 방식(코너 근처에서도 하나의 방향만 고르게 된다).
// DOM 없이 평범한 {left,top,width,height} 객체만 받아 순수 함수로 테스트 가능.
export function computeDropZone(rect, x, y, edge = 0.25) {
  if (!rect.width || !rect.height) return 'center';
  const relX = (x - rect.left) / rect.width;
  const relY = (y - rect.top) / rect.height;
  const dist = { left: relX, right: 1 - relX, top: relY, bottom: 1 - relY };
  let zone = 'center';
  let min = edge;
  for (const k of Object.keys(dist)) {
    if (dist[k] < min) { min = dist[k]; zone = k; }
  }
  return zone;
}

const ZONE_TO_SPLIT = {
  left: { dir: 'row', newFirst: true },
  right: { dir: 'row', newFirst: false },
  top: { dir: 'col', newFirst: true },
  bottom: { dir: 'col', newFirst: false },
};

// zone에 맞게 sessionId를 paneId 자리에 반영한다.
//  - center: 그 pane에 바로 배정한다. sessionId가 이미 다른 leaf에 있었으면
//    tree.js의 setSession()이 거기서 비운다(중복 attach 금지 정책, 0단계부터
//    있던 로직을 그대로 탄다) — 이 함수는 그 정책을 몰라도 된다.
//  - 가장자리: 그 방향으로 분할한 뒤 새 leaf에 배정한다. **빈 leaf로 먼저
//    분할하고 나서 setPaneSession()을 부르는 두 단계**로 하는 이유: splitPane이
//    바로 sessionId를 넣는 makeLeaf(id, sessionId) 경로는 dedup을 거치지 않아
//    이미 다른 pane에 있던 세션을 또 만들면 두 곳에 중복 배치될 수 있다.
//  - pane 상한(canSplit)에 걸리면 조용히 무시한다 — 헤더의 분할 버튼과 동일한
//    게이트 동작(별도 토스트 없음, 이미 그렇게 합의됨).
export function applyPaneDrop(paneId, zone, sessionId) {
  if (!sessionId) return;
  if (zone === 'center') {
    setPaneSession(sessionId, paneId);
    return;
  }
  const spec = ZONE_TO_SPLIT[zone];
  if (!spec || !canSplit()) return;
  const newPaneId = splitPane(paneId, spec.dir, null, spec.newFirst);
  if (newPaneId) setPaneSession(sessionId, newPaneId);
}

// ── 마우스(네이티브 HTML5 DnD) 드롭 타겟 배선 ───────────────────────────
// paneEl에 dragover/dragleave/drop을 걸어 data-dropzone 속성으로 오버레이를
// 표시하고(CSS가 소비), drop에서 applyPaneDrop을 부른다. 드래그 소스(탭 —
// tab-dom.js, pane 헤더 — panes.js)는 이 mime 타입으로 dataTransfer에 세션
// id를 실어 보내기만 하면 된다.
export function wirePaneDropTarget(paneEl, paneId) {
  paneEl.addEventListener('dragover', (e) => {
    if (!e.dataTransfer.types.includes(SESSION_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    paneEl.dataset.dropzone = computeDropZone(paneEl.getBoundingClientRect(), e.clientX, e.clientY);
  });
  paneEl.addEventListener('dragleave', (e) => {
    // 자식 엘리먼트로 옮겨가는 중이면(relatedTarget이 여전히 paneEl 안) 무시 —
    // 안 그러면 pane 안에서 마우스가 조금만 움직여도 오버레이가 깜빡인다.
    if (!paneEl.contains(e.relatedTarget)) delete paneEl.dataset.dropzone;
  });
  paneEl.addEventListener('drop', (e) => {
    if (!e.dataTransfer.types.includes(SESSION_MIME)) return;
    e.preventDefault();
    const zone = paneEl.dataset.dropzone || 'center';
    delete paneEl.dataset.dropzone;
    applyPaneDrop(paneId, zone, e.dataTransfer.getData(SESSION_MIME));
  });
}

// ── 터치(pointer:coarse) long-press 드래그 ──────────────────────────────
const LONGPRESS_MS = 500;
const MOVE_THRESHOLD = 8;

let _ghostEl = null;
function _startGhost(sourceEl, x, y) {
  _ghostEl = document.createElement('div');
  _ghostEl.className = 'vt-drag-ghost';
  _ghostEl.textContent = sourceEl.textContent.trim();
  _ghostEl.style.left = `${x}px`;
  _ghostEl.style.top = `${y}px`;
  document.body.appendChild(_ghostEl);
}
function _moveGhost(x, y) {
  if (!_ghostEl) return;
  _ghostEl.style.left = `${x}px`;
  _ghostEl.style.top = `${y}px`;
}
function _stopGhost() {
  if (_ghostEl) { _ghostEl.remove(); _ghostEl = null; }
}

let _hoverPaneEl = null;
function _clearPaneHighlight() {
  if (_hoverPaneEl) { delete _hoverPaneEl.dataset.dropzone; _hoverPaneEl = null; }
}
function _updatePaneHighlight(x, y) {
  const under = document.elementFromPoint(x, y);
  const paneEl = under ? under.closest('.vt-pane') : null;
  if (paneEl !== _hoverPaneEl) { _clearPaneHighlight(); _hoverPaneEl = paneEl; }
  if (!paneEl) return;
  paneEl.dataset.dropzone = computeDropZone(paneEl.getBoundingClientRect(), x, y);
}

// sourceEl에 long-press 기반 터치 드래그를 건다. getSessionId()는 pointerdown
// 시점에 한 번 호출해 옮길 세션 id를 확정한다 — null/undefined면(예: 빈 pane
// 헤더, 세션 없는 pane) 아예 드래그를 시작하지 않는다. 마우스(pointerType
// 'mouse')는 기존 네이티브 HTML5 DnD 경로를 그대로 쓰므로 여기서는 무시한다
// — 하이브리드 기기(터치 지원 노트북)에서도 이벤트별 pointerType으로 갈라
// 매체 판정을 정적 matchMedia에 기대지 않는다.
export function wireTouchDragSource(sourceEl, getSessionId) {
  let timer = null;
  let dragging = false;
  let start = null;
  let sid = null;

  function cleanupListeners() {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    document.removeEventListener('pointercancel', onCancel);
  }
  function cancelPending() {
    clearTimeout(timer);
    sourceEl.classList.remove('vt-drag-pending');
  }
  function end(commit) {
    cancelPending();
    cleanupListeners();
    if (!dragging) return;
    dragging = false;
    sourceEl.classList.remove('dragging');
    document.body.classList.remove('vt-touch-dragging');
    if (commit && _hoverPaneEl) {
      applyPaneDrop(_hoverPaneEl.dataset.paneId, _hoverPaneEl.dataset.dropzone || 'center', sid);
    }
    _clearPaneHighlight();
    _stopGhost();
    // 길게 눌러 드래그 모드까지 들어갔다면, 손을 뗀 직후 브라우저가 합성하는
    // click까지 탭 전환/포커스로 이어지면 안 된다 — 한 번만 삼킨다.
    sourceEl._suppressNextClick = true;
  }
  function onMove(e) {
    if (!dragging) {
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > MOVE_THRESHOLD) end(false);
      return;
    }
    _moveGhost(e.clientX, e.clientY);
    _updatePaneHighlight(e.clientX, e.clientY);
  }
  function onUp() { end(true); }
  function onCancel() { end(false); }

  sourceEl.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse') return;
    sid = getSessionId();
    if (!sid) return;
    start = { x: e.clientX, y: e.clientY };
    sourceEl.classList.add('vt-drag-pending');
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onCancel);
    timer = setTimeout(() => {
      dragging = true;
      sourceEl.classList.remove('vt-drag-pending');
      sourceEl.classList.add('dragging');
      document.body.classList.add('vt-touch-dragging');
      _startGhost(sourceEl, e.clientX, e.clientY);
    }, LONGPRESS_MS);
  });
  sourceEl.addEventListener('click', (e) => {
    if (sourceEl._suppressNextClick) {
      sourceEl._suppressNextClick = false;
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
}
