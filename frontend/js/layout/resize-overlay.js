// N43 §8 · R5(화면 2g) — 리사이즈 오버레이. 분할선을 드래그하는 동안 활성
// pane 중앙에 `142 × 38`(픽셀) + `cols × rows`(칸 수) 캡션을 띄운다.
//
// xterm의 fit()은 여기서 절대 부르지 않는다 — 표면 레이어(surface.js)의
// refit 게이트가 드래그 중 fit 자체를 미루는 게 §1의 핵심 최적화인데, 오버레이가
// 그걸 우회해 매 프레임 fit()을 부르면 그 최적화가 무의미해진다. 대신 드래그
// 시작 시점의 셀 크기(wrapper 실측 ÷ term.cols/rows) 하나로 매 프레임 칸 수를
// 추정한다 — wrapper의 CSS 폭/높이는 드래그 중에도 surface.js가 즉시 갱신하므로
// (지연되는 건 xterm 내부 fit()뿐이다) 이 추정치는 실제 fit 결과와 거의 같다.
import { getTree, getActivePaneId } from './store.js';
import { findNode } from './tree.js';
import { getSession } from '../core/store.js';
import * as surface from './surface.js';

const FADE_MS = 200;

let _raf = null;
let _hideTimer = null;
let _overlayEl = null;
let _baseline = null; // { sessionId, cellW, cellH } — 터미널 칸일 때만

function _ensureEl() {
  if (_overlayEl && _overlayEl.isConnected) return _overlayEl;
  _overlayEl = document.createElement('div');
  _overlayEl.id = 'vt-resize-overlay';
  _overlayEl.hidden = true;
  _overlayEl.innerHTML = `
    <div class="vt-ro-dims"></div>
    <div class="vt-ro-cells"></div>
    <div class="vt-ro-defer"></div>
  `;
  document.body.appendChild(_overlayEl);
  return _overlayEl;
}

function _activePane() {
  const paneId = getActivePaneId();
  const leaf = findNode(getTree(), paneId);
  const paneEl = document.getElementById(`vt-pane-${paneId}`);
  return { leaf, paneEl };
}

function _tick() {
  const { leaf, paneEl } = _activePane();
  const el = _ensureEl();
  if (!paneEl) { _raf = requestAnimationFrame(_tick); return; }

  const body = paneEl.querySelector('.vt-pane-body');
  const r = (body || paneEl).getBoundingClientRect();
  const pr = paneEl.getBoundingClientRect();
  el.style.left = `${pr.left + pr.width / 2}px`;
  el.style.top = `${pr.top + pr.height / 2}px`;

  el.querySelector('.vt-ro-dims').textContent = `${Math.round(r.width)} × ${Math.round(r.height)}`;

  const cellsEl = el.querySelector('.vt-ro-cells');
  if (leaf && leaf.t === 'leaf' && leaf.kind === 'terminal' && _baseline && _baseline.sessionId === leaf.session) {
    const cols = Math.max(1, Math.floor(r.width / _baseline.cellW));
    const rows = Math.max(1, Math.floor(r.height / _baseline.cellH));
    cellsEl.textContent = `${cols} × ${rows}`;
    cellsEl.hidden = false;
  } else {
    cellsEl.hidden = true;
  }

  // "표면 레이어의 refit 게이트가 실제로 그렇게 동작할 때만" 보여준다(문서 §8) —
  // 게이트에 유예된 세션이 없으면(예: pane이 하나뿐) 이 줄 자체가 없다.
  const deferEl = el.querySelector('.vt-ro-defer');
  const pending = surface.pendingCount();
  if (pending > 0) {
    deferEl.textContent = `SIGWINCH 활성 pane만 · 나머지 ${pending}개 유예`;
    deferEl.hidden = false;
  } else {
    deferEl.hidden = true;
  }

  _raf = requestAnimationFrame(_tick);
}

/** 분할선 드래그 시작 — panes.js의 split 리사이저 onStart에서만 부른다. */
export function beginResizeOverlay() {
  const { leaf } = _activePane();
  _baseline = null;
  if (leaf && leaf.t === 'leaf' && leaf.kind === 'terminal' && leaf.session) {
    const s = getSession(leaf.session);
    if (s && s.wrapper && s.term && s.term.cols > 0 && s.term.rows > 0
        && s.wrapper.clientWidth > 0 && s.wrapper.clientHeight > 0) {
      _baseline = {
        sessionId: leaf.session,
        cellW: s.wrapper.clientWidth / s.term.cols,
        cellH: s.wrapper.clientHeight / s.term.rows,
      };
    }
  }

  clearTimeout(_hideTimer);
  const el = _ensureEl();
  el.classList.remove('vt-ro-hide');
  el.hidden = false;
  if (_raf) cancelAnimationFrame(_raf);
  _tick();
}

/** 드래그 종료 — 200ms 뒤 사라진다(`prefers-reduced-motion`이면 즉시). */
export function endResizeOverlay() {
  if (_raf) cancelAnimationFrame(_raf);
  _raf = null;
  if (!_overlayEl) return;

  const reduceMotion = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) {
    _overlayEl.hidden = true;
    return;
  }
  _overlayEl.classList.add('vt-ro-hide');
  _hideTimer = setTimeout(() => { if (_overlayEl) _overlayEl.hidden = true; }, FADE_MS);
}
