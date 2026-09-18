// 터미널 배치 — 어느 세션의 xterm wrapper가 어느 pane 안에 들어가는가.
//
// **2026-09-18: 표면 레이어(N16)를 걷어냈다.** 그전까지 이 파일은 wrapper를
// `#vt-surface`라는 **별도 절대좌표 레이어**에 영구히 붙여두고, pane은 빈
// 상자만 그린 뒤 그 상자의 getBoundingClientRect()를 읽어 wrapper에
// transform/width/height를 찍어 "좌표로만" 둘을 붙였다.
//
// 왜 그랬나(`docs/plan-2.1/10-shell-layout.md §1`): 실측 문제 4건 중 셋은
// **과도한 refit**(레이아웃 변경마다 크롬 DOM 전체 교체·보이는 전 세션 fit·
// 활성 전환만으로 전체 재fit)이었고, 나머지 하나가 "appendChild로 xterm
// wrapper를 DOM 이동"이었다. 앞의 셋은 지금 코드에 이미 없다 — panes.js가
// split·pane을 id로 재사용하고(`_renderNode`), 이 파일의 refit 게이트가
// 크기가 실제로 변한 세션만 fit한다. 남은 근거는 "DOM 이동이 위험하다"
// 하나였다.
//
// 그 하나를 실측으로 반증했다(2026-09-18, Chromium, 실제 세션):
//   - **WebGL2 렌더러**가 붙은 wrapper를 pane-body로 appendChild 이동 →
//     `isContextLost()` false, `webglcontextlost` 0건, 캔버스 버퍼
//     1128×828 유지. 이동 후 휠 스크롤로 실제 GL 드로우를 강제했고 화면이
//     다시 그려졌다(스크린샷 해시 변화로 확인).
//   - canvas 렌더러도 동일하게 통과.
// DOM 이동은 노드를 떼었다 붙이는 것이지 컨텍스트를 버리는 게 아니다.
//
// 분리를 유지하는 값이 컸다: wrapper가 pane의 **자손이 아니므로**
//   - pane의 `overflow:hidden`·`border-radius`가 터미널을 자르지 못한다
//     (pane을 테두리 있는 상자로 만들면 터미널만 상자 밖으로 삐져나온다)
//   - 활성 표시를 1px outline 말고는 쓸 수 없다(면을 깔면 터미널이 덮는다)
//   - wrapper가 body의 px 크기로 강제돼, xterm의 셀 양자화 잔여(실측 22×8px)가
//     한쪽으로 몰리고 pane이 그걸 여백으로 돌릴 방법이 없다
//   - 좌표계 둘을 ResizeObserver로 영원히 동기화해야 한다
//
// 지금 구조:
//   .vt-pane > .vt-pane-body > .vt-term-wrap > .terminal.xterm   ← 보이는 세션
//   #vt-term-stage > .vt-term-wrap                               ← 배치 안 된 세션
//
// 스테이지(`#vt-term-stage`)가 남은 이유는 좌표가 아니라 **대기실**이다:
// xterm은 `term.open()` 시점에 붙을 곳이 필요하고, 어느 pane에도 안 올라간
// 세션도 DOM에서 제거하면 안 된다(재생성 비용). 스테이지는 컨테이너와 같은
// 크기라 대기 중인 터미널도 말이 되는 cols/rows를 유지한다.
//
// 이 파일에 남는 로직은 **refit 게이트** 하나다(순수 함수, surface.test.js).
// 그건 애초에 좌표와 무관했다 — "크기가 실제로 변했을 때만, 드래그 중이면
// 놓을 때 한 번만 fit한다".
import { getSession } from '../core/store.js';
import { fitAndResize } from '../term/resize.js';

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

let _stageEl = null;
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

// pane-body를 관찰한다(wrapper가 아니라). wrapper는 body를 100%로 채우므로
// 두 값이 같은데, body 쪽은 대기실로 옮겨간 뒤에도 요소가 그대로 남아 있어
// unobserve 타이밍을 헷갈릴 일이 없다.
function _onResizeObserved(entries) {
  for (const entry of entries) {
    const sid = entry.target.dataset.vtSessionId;
    if (!sid) continue;
    const box = entry.contentRect;
    _gate.report(sid, Math.round(box.width), Math.round(box.height));
  }
}

// 대기실 — 어느 pane에도 배치되지 않은 세션의 wrapper가 머무는 곳.
// `visibility:hidden`이라 안 보이지만 **레이아웃은 살아 있다**(display:none과
// 다른 점): 대기 중인 터미널도 컨테이너 크기만큼의 상자를 유지하므로,
// 배치되는 순간 0칸에서 늘어나는 게 아니라 비슷한 크기에서 fit만 한 번 한다.
function _ensureStage() {
  const container = document.getElementById('terminal-container');
  if (!container) return null;
  if (!_stageEl || !_stageEl.isConnected) {
    _stageEl = document.getElementById('vt-term-stage');
    if (!_stageEl) {
      _stageEl = document.createElement('div');
      _stageEl.id = 'vt-term-stage';
      container.appendChild(_stageEl);
    }
  }
  if (!_ro && typeof ResizeObserver !== 'undefined') _ro = new ResizeObserver(_onResizeObserved);
  return _stageEl;
}

// wrapper를 이 pane-body 안으로 옮긴다. 이미 그 안이면 아무것도 하지 않는다 —
// 렌더는 트리 구조가 바뀔 때마다 도는데, 매번 appendChild를 부르면 같은 자리에
// 다시 붙이는 것만으로도 xterm이 한 프레임 깜빡인다.
function _mount(sessionId, bodyEl) {
  const s = getSession(sessionId);
  if (!s || !s.wrapper) return;
  if (s.wrapper.parentElement !== bodyEl) bodyEl.appendChild(s.wrapper);
}

// 배치 밖으로 밀려난 세션 — 대기실로 되돌린다. DOM 제거는 하지 않는다
// (xterm 재생성 비용, 그리고 스크롤백이 그 인스턴스에 있다).
function _park(sessionId) {
  const s = getSession(sessionId);
  const stage = _ensureStage();
  if (s && s.wrapper && stage && s.wrapper.parentElement !== stage) stage.appendChild(s.wrapper);
  _gate.forget(sessionId);
}

// xterm-setup.js가 세션 생성 시 한 번만 부른다. term.open() 전에 DOM에 붙어
// 있어야 xterm이 셀 크기를 측정할 수 있어서, 일단 대기실에 넣는다.
export function adoptWrapper(sessionId, wrapperEl) {
  const stage = _ensureStage();
  wrapperEl.classList.add('vt-term-wrap');
  wrapperEl.dataset.sessionId = sessionId;
  if (stage && wrapperEl.parentElement !== stage) stage.appendChild(wrapperEl);
}

// panes.js가 'layout' 렌더 뒤 한 번 부른다: Map<sessionId, paneBodyEl>.
// 빠진 세션은 대기실로, 새로 들어온 세션은 그 body 안으로 옮기고 즉시 실측한다.
export function setPlacement(nextMap) {
  if (!_ensureStage()) return;

  for (const [sid, oldBody] of _placed) {
    if (nextMap.get(sid) !== oldBody) {
      if (_ro) _ro.unobserve(oldBody);
      delete oldBody.dataset.vtSessionId;
    }
  }
  for (const sid of _placed.keys()) if (!nextMap.has(sid)) _park(sid);
  for (const [sid, body] of nextMap) {
    _mount(sid, body);
    if (_placed.get(sid) !== body) {
      body.dataset.vtSessionId = sid;
      if (_ro) _ro.observe(body);
      // ResizeObserver의 첫 콜백을 기다리지 않고 바로 한 번 잰다 — 새로 올라온
      // 세션은 그 프레임에 fit돼야 한다(안 그러면 한 프레임 동안 옛 cols로 그려진다).
      const r = body.getBoundingClientRect();
      _gate.report(sid, Math.round(r.width), Math.round(r.height));
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
