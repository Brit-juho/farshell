// ADR-44 — 파일 뷰어 오버레이. pane 트리 밖에 있어서
// 열고 닫아도 터미널 cols×rows가 바뀌지 않는다(fit도 SIGWINCH도 안 일어난다).
import { openPanel, closePanel } from '../panel.js';
import { renderFile, refreshFile, refreshGap, jumpToLine, disposeViewer } from './file.js';
import { isCrossHost } from './file-load.js';
import { watchPath, coalesce } from '../../shell/fs-watch.js';
import { _viewerState, _shareFile } from './state.js';
import { icon } from '../../ui/icons.js';
import { get, set, subscribe, SCHEMA } from '../../core/settings.js';
import { openViewerPane } from '../../layout/store.js';
import { isCompactMode } from '../../layout/compact.js';
import { COMPACT_MAX } from '../../layout/breakpoints.js';
import { wireRatioResizer } from '../../layout/resizer.js';
import { t } from '../../core/i18n.js';

export const PANEL_ID = 'vt-file';
const FLOAT_MIN_W = 320;
const FLOAT_MIN_H = 200;
// 창이 화면 밖으로 밀려도 헤더의 이만큼은 남겨 둔다 — 되찾을 손잡이가 헤더뿐이다.
const GRAB_W = 48;
const GRAB_H = 28;

export function clampSheetPct(pct) {
  const { min, max } = SCHEMA['viewer.sheet.height'];
  return Math.min(max, Math.max(min, pct));
}

// 설정값은 서버에서 온 문자열이다. 모양이 조금이라도 어긋나면 "저장값 없음"으로 본다.
export function parseRect(raw) {
  let r;
  try { r = JSON.parse(raw); } catch (_) { return null; }
  if (!r || typeof r !== 'object') return null;
  const { x, y, w, h } = r;
  if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;
  return { x, y, w, h };
}

// 오른쪽 위 — 프롬프트가 있는 왼쪽 아래를 덜 가린다.
export function defaultRect(cw, ch) {
  const w = Math.min(720, cw * 0.55);
  return { x: Math.max(0, cw - w - 24), y: 24, w, h: ch * 0.7 };
}

export function clampRect(r, cw, ch) {
  return {
    x: Math.max(0, Math.min(r.x, cw - GRAB_W)),
    y: Math.max(0, Math.min(r.y, ch - GRAB_H)),
    w: Math.max(FLOAT_MIN_W, Math.min(r.w, cw)),
    h: Math.max(FLOAT_MIN_H, Math.min(r.h, ch)),
  };
}

const _round = (r) => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) });

function _container() {
  return document.getElementById('terminal-container');
}

function _surface() {
  return get('viewer.surface') === 'float' ? 'float' : 'sheet';
}

function _headHTML() {
  return '<div class="vt-file-title"><span class="vt-file-name"></span><span class="vt-file-dir"></span></div>'
    + `<button type="button" class="vt-icon-btn vt-file-refresh" aria-label="${t('새로고침')}" data-tip="${t('새로고침')}">${icon('refresh-cw', 14)}</button>`
    + `<button type="button" class="vt-icon-btn vt-file-share" aria-label="${t('공유 링크 만들기')}" data-tip="${t('공유 링크 만들기')}">${icon('link', 14)}</button>`
    + `<button type="button" class="vt-icon-btn vt-file-float" aria-pressed="false">${icon('app-window', 14)}</button>`
    + `<button type="button" class="vt-icon-btn vt-file-topane" aria-label="${t('페인으로 열기')}" data-tip="${t('페인으로 열기')}">${icon('columns-2', 14)}</button>`;
}

function _refresh(el) {
  const path = el.dataset.viewerFile;
  if (!path) return Promise.resolve();
  const body = el.querySelector('.vt-vw-body');
  return refreshFile(body, path, () => el.isConnected && el.dataset.viewerFile === path, { host: el._vtHost || null })
    .catch(() => { /* 다음 변경·클릭 때 다시 시도한다 */ });
}

// 보고 있는 파일이 디스크에서 바뀌면 다시 그린다. 파일을 바꾸면 구독도 옮긴다.
// 다른 호스트의 파일(원격 칸의 터미널 링크)은 감시하지 않는다 — 감시 소켓은 지금 화면의 호스트에 붙어 있어서
// 같은 경로의 **이 호스트** 파일을 보게 된다(호스트별 감시는 트리 재작성 단계). 새로고침 버튼으로 다시 읽는다.
function _watch(el, path) {
  if (el._vtUnwatch) { el._vtUnwatch(); el._vtUnwatch = null; }
  if (isCrossHost(el._vtHost)) return;
  // 큰 파일은 최소 간격을 두고 묶는다(refreshGap) — 자라는 로그를 볼 때 변경마다 다시 읽지 않게.
  el._vtUnwatch = watchPath('file', path, coalesce(() => _refresh(el), {
    minGap: () => refreshGap(el.querySelector('.vt-vw-body')),
  }));
}

function _setHead(el, path) {
  const parts = path.split('/');
  const name = parts.pop() || path;
  let dir = parts.join('/') || '/';
  const root = (_viewerState.root || '').replace(/\/$/, '');
  if (root && dir.startsWith(`${root}/`)) dir = dir.slice(root.length + 1);
  else if (root && dir === root) dir = '.';
  // 구분에 쓰이는 건 경로의 끝이다. 전체 경로는 툴팁에 있다.
  const segs = dir.split('/').filter(Boolean);
  if (segs.length > 2) dir = `…/${segs.slice(-2).join('/')}`;
  el.querySelector('.vt-file-name').textContent = name;
  el.querySelector('.vt-file-dir').textContent = dir;
  el.querySelector('.vt-file-title').dataset.tip = el._vtHost && isCrossHost(el._vtHost) ? `${el._vtHost}:${path}` : path;
}

// 파일 창이 터미널 위에서 열리고·움직이고·크기가 바뀌고·닫힐 때 터미널 그림을 통째로 다시 그린다.
// 실사용에서 창이 겹친 가로 범위의 터미널 아래쪽(Claude Code 입력 상자의 테두리선 등)이 갱신되지 않고
// 검게 남았다(2026-10-15 스크린샷) — 터미널 내용이 아니라 GPU 렌더러의 화면이 어긋난 것이라
// 글리프 캐시를 비우고 전체 행을 다시 그리면 풀린다. 연속 호출은 한 번으로 모은다.
let _repaintTimer = null;
function _repaintTerminals() {
  if (_repaintTimer) clearTimeout(_repaintTimer);
  _repaintTimer = setTimeout(() => {
    _repaintTimer = null;
    const map = typeof window.allSessions === 'function' ? window.allSessions() : null;
    if (!map) return;
    for (const id of Object.keys(map)) {
      const term = map[id] && map[id].term;
      if (!term) continue;
      try {
        if (term.clearTextureAtlas) term.clearTextureAtlas();
        if (term.refresh) term.refresh(0, term.rows - 1);
      } catch (_) { /* 그리기 보정일 뿐 — 실패해도 파일 창 동작과 무관하다 */ }
    }
  }, 120);
}

// 창이 떠 있는 동안에도 터미널 출력은 계속 들어온다. 위 보정은 열기·이동·닫기 순간에만 돌아서
// 그 사이에 그려지는 팝업 밑 구역이 비는 것을 못 막는다(2026-10-23 스크린샷 — 팝업 가로 범위 아래
// 글자가 통째로 사라짐). 열려 있는 동안만 보이는 세션의 행을 다시 그린다. 아틀라스는 비우지 않는다.
const REPAINT_TICK_MS = 300;
function _startRepaintTick() {
  return setInterval(() => {
    if (document.hidden) return;
    const id = typeof window.activeSessionId === 'function' ? window.activeSessionId() : null;
    const map = typeof window.allSessions === 'function' ? window.allSessions() : null;
    const term = id && map && map[id] && map[id].term;
    if (!term || !term.refresh) return;
    try { term.refresh(0, term.rows - 1); } catch (_) { /* 그리기 보정일 뿐 */ }
  }, REPAINT_TICK_MS);
}

// opts: {host, line, col} — 터미널 링크가 그 세션의 호스트와 줄을 넘긴다.
function _setFile(el, path, opts = {}) {
  const fileHost = opts.host || null;
  const body = el.querySelector('.vt-vw-body');
  // 같은 파일을 다시 그리면 스크롤이 튄다 — 뷰어 페인(panes.js)과 같은 규칙. 줄 이동만 한다.
  if (el.dataset.viewerFile === path && (el._vtHost || null) === fileHost) {
    const cur = body.querySelector(':scope > .vt-vw-code-pane');
    if (opts.line && cur) jumpToLine(cur, opts.line, opts.col).catch(() => {});
    return;
  }
  el.dataset.viewerFile = path;
  el._vtHost = fileHost;
  _setHead(el, path);
  // 다른 호스트의 파일은 공유(이 호스트의 스냅샷 경로)와 페인으로 열기(페인은 호스트를 기억하지 않는다)를 막는다.
  const cross = isCrossHost(fileHost);
  el.querySelector('.vt-file-share').hidden = cross;
  el.querySelector('.vt-file-topane').hidden = cross;
  // 요청마다 새 호스트에 그린다. 늦게 도착한 이전 파일은 떨어져 나간 호스트에 그려져 무해하다.
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  disposeViewer(body);   // 앞 파일의 편집기(창 수신기)를 지금 거둔다
  body.replaceChildren(host);
  body.scrollTop = 0;
  renderFile(host, path, { host: fileHost, line: opts.line, col: opts.col })
    .catch((e) => { host.textContent = String(e && e.message ? e.message : e); })
    .finally(_repaintTerminals);
  _watch(el, path);
}

function _setRect(el, r) {
  el._vtRect = r;
  for (const k of ['x', 'y', 'w', 'h']) el.style.setProperty(`--vt-file-${k}`, `${Math.round(r[k])}px`);
}

// 좁은 화면에서는 dock이 터미널을 밀지 않고 위에 덮인다. 그때 오버레이가 dock 밑으로
// 파고들어 코드가 가려지므로, 겹친 폭만큼 오른쪽을 비운다(밀리는 폭에서는 0).
function _dockInset(mount) {
  const dock = document.getElementById('vt-dock');
  if (!dock || !mount) return 0;
  const d = dock.getBoundingClientRect();
  const m = mount.getBoundingClientRect();
  if (d.width === 0) return 0;
  return Math.max(0, Math.round(m.right - d.left));
}

function _syncDockInset(el) {
  el.style.setProperty('--vt-file-inset-r', `${_dockInset(_container())}px`);
}

// 머리줄의 「떠 있는 창 ↔ 위쪽에 붙이기」 버튼 — 지금 표면에 맞춰 이름과 눌림 상태를 맞춘다.
function _syncSurfaceBtn(el, surface) {
  const b = el.querySelector('.vt-file-float');
  if (!b) return;
  const label = surface === 'float' ? t('위쪽에 붙이기') : t('떠 있는 창으로 옮기기');
  b.setAttribute('aria-pressed', String(surface === 'float'));
  b.setAttribute('aria-label', label);
  b.dataset.tip = label;
}

function _applyGeometry(el) {
  const surface = _surface();
  _syncSurfaceBtn(el, surface);
  _syncDockInset(el);
  _repaintTerminals();
  el.classList.toggle('surface-sheet', surface === 'sheet');
  el.classList.toggle('surface-float', surface === 'float');
  if (surface === 'sheet') {
    el.style.setProperty('--vt-sheet-h', `${get('viewer.sheet.height')}%`);
    return;
  }
  const c = _container();
  if (!c) return;
  const cw = c.clientWidth - _dockInset(c);
  const ch = c.clientHeight;
  _setRect(el, clampRect(parseRect(get('viewer.float.rect')) || defaultRect(cw, ch), cw, ch));
}

function _wireSheetGrip(el, grip) {
  let pct = 0;
  wireRatioResizer(grip, {
    dir: 'col',
    getContainerSize: () => (_container() || {}).clientHeight || 0,
    getStartRatio: () => get('viewer.sheet.height') / 100,
    onStart: () => { pct = 0; },
    // 드래그 중에는 CSS 변수만 바꾼다. 설정 저장은 서버 PUT이라 놓을 때 한 번만.
    onRatio: (ratio) => { pct = clampSheetPct(ratio * 100); el.style.setProperty('--vt-sheet-h', `${pct}%`); },
    onEnd: () => { if (pct) set('viewer.sheet.height', Math.round(pct)); _repaintTerminals(); },
  });
}

function _wireFloatDrag(el, handle, mode) {
  handle.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !el.classList.contains('surface-float')) return;
    // 닫기·페인으로 버튼을 누르려다 창이 딸려 오면 안 된다.
    if (mode === 'move' && ev.target.closest('button')) return;
    const c = _container();
    if (!c || !el._vtRect) return;
    ev.preventDefault();
    const cw = c.clientWidth;
    const ch = c.clientHeight;
    const start = el._vtRect;
    const sx = ev.clientX;
    const sy = ev.clientY;
    if (handle.setPointerCapture) { try { handle.setPointerCapture(ev.pointerId); } catch (_) {} }
    document.body.classList.add('vt-resizing');
    el.classList.add('dragging');
    const onMove = (e) => {
      const dx = e.clientX - sx;
      const dy = e.clientY - sy;
      const next = mode === 'move'
        ? { ...start, x: start.x + dx, y: start.y + dy }
        // 모서리 손잡이가 컨테이너 밖으로 나가면 다시 잡을 수 없다.
        : { ...start, w: Math.min(start.w + dx, cw - start.x), h: Math.min(start.h + dy, ch - start.y) };
      _setRect(el, clampRect(next, cw, ch));
    };
    const onUp = () => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
      document.body.classList.remove('vt-resizing');
      el.classList.remove('dragging');
      set('viewer.float.rect', JSON.stringify(_round(el._vtRect)));
      _repaintTerminals();
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    document.addEventListener('pointercancel', onUp);
  });
}

// Esc는 vim·less·fzf에 가야 하는 키다. 포커스가 뷰어 안에 있을 때만 닫는다.
// 코드 본문(CodeMirror)의 찾기·줄 이동 창에 포커스가 있으면 그 창이 Esc를 받아 닫는다 — 뷰어까지 닫지 않는다.
function _escClosesOnlyWhenFocused() {
  const el = document.getElementById(PANEL_ID);
  const active = document.activeElement;
  return !(el && el.contains(active)) || !!(active && active.closest && active.closest('.cm-panel'));
}

export function openFileOverlay(path, opts = {}) {
  if (!path) return;
  const existing = document.getElementById(PANEL_ID);
  if (existing) { _setFile(existing, path, opts); return; }

  // 폰에서는 표면 설정과 무관하게 전체화면 시트 + 뒤로가기 닫기(panel.js의 mobile route).
  const compact = isCompactMode();
  const mount = compact ? null : _container();
  let ro = null;
  let tick = null;
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: t('파일 보기'),
    headHTML: _headHTML(),
    bodyId: 'vt-file-body',
    extraClass: compact ? 'vt-file-compact' : '',
    ...(mount ? { mount, modal: false, onKey: _escClosesOnlyWhenFocused } : {}),
    onClose: () => {
      _repaintTerminals();
      if (tick) { clearInterval(tick); tick = null; }
      if (ro) ro.disconnect();
      if (panel && panel.el._vtUnwatch) { panel.el._vtUnwatch(); panel.el._vtUnwatch = null; }
      if (panel) disposeViewer(panel.el);
    },
  });
  if (!panel) return;
  const { el, body } = panel;

  el.querySelector('.vt-file-refresh').addEventListener('click', () => { _refresh(el); });
  el.querySelector('.vt-file-share').addEventListener('click', () => { _shareFile(el.dataset.viewerFile); });
  const floatBtn = el.querySelector('.vt-file-float');
  // 폰(compact)은 설정과 무관하게 전체 화면 시트라 옮길 곳이 없다.
  if (!mount) floatBtn.hidden = true;
  floatBtn.addEventListener('click', () => set('viewer.surface', _surface() === 'float' ? 'sheet' : 'float'));
  el.querySelector('.vt-file-topane').addEventListener('click', () => {
    const file = el.dataset.viewerFile;
    closePanel(el);
    if (file) openViewerPane(file);
  });

  if (mount) {
    const card = el.querySelector('.vt-viewer-card');
    const grip = document.createElement('div');
    grip.className = 'vt-file-grip';
    const corner = document.createElement('div');
    corner.className = 'vt-file-corner';
    card.append(grip, corner);
    _wireSheetGrip(el, grip);
    _wireFloatDrag(el, el.querySelector('.vt-viewer-head'), 'move');
    _wireFloatDrag(el, corner, 'resize');
    _applyGeometry(el);
    tick = _startRepaintTick();
    // dock·레일을 접고 펴도 컨테이너 폭이 바뀐다 — 창 크기 이벤트만으로는 못 잡는다.
    if (typeof window.ResizeObserver === 'function') {
      ro = new window.ResizeObserver(() => {
        _syncDockInset(el);
        if (!el.classList.contains('surface-float') || !el._vtRect) return;
        _setRect(el, clampRect(el._vtRect, mount.clientWidth - _dockInset(mount), mount.clientHeight));
      });
      ro.observe(mount);
      const dockEl = document.getElementById('vt-dock');
      if (dockEl) ro.observe(dockEl);
    }
    // 키보드로 바로 스크롤하고 Esc로 닫을 수 있게. 닫으면 panel.js가 열기 전 포커스(터미널)로 돌려준다.
    body.tabIndex = -1;
    body.focus({ preventScroll: true });
  }

  _setFile(el, path, opts);
}

// 표면을 바꿔도 보고 있던 파일과 스크롤은 유지한다(§8.3).
subscribe((changed) => {
  if (!changed || !('viewer.surface' in changed)) return;
  const el = document.getElementById(PANEL_ID);
  if (!el || el.classList.contains('vt-file-compact')) return;
  if (changed['viewer.surface'] === 'pane' && !isCrossHost(el._vtHost)) {
    const file = el.dataset.viewerFile;
    closePanel(el);
    if (file) openViewerPane(file);
    return;
  }
  _applyGeometry(el);
});

// compact 경계를 넘나들면 붙는 자리(body ↔ #terminal-container)가 바뀌므로 다시 연다.
const _compactMq = typeof window.matchMedia === 'function'
  ? window.matchMedia(`(max-width: ${COMPACT_MAX - 0.02}px)`) : null;
if (_compactMq && typeof _compactMq.addEventListener === 'function') {
  _compactMq.addEventListener('change', () => {
    const el = document.getElementById(PANEL_ID);
    if (!el) return;
    const file = el.dataset.viewerFile;
    const fileHost = el._vtHost || null;
    closePanel(el, { history: 'replace' });
    if (file) openFileOverlay(file, { host: fileHost });
  });
}
