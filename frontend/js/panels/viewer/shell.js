// 코드 뷰어 패널 셸 — F4에서 viewer.js에서 분리. 패널 열기(showViewer)·헤더
// 배선·표시모드(sheet/dock/full) 전환·리사이저 2종을 담당한다.
//
// tree.js/git.js와 서로를 참조하는 순환 import가 있다(shell.js는 패널을 열면서
// tree.js의 트리 렌더·git.js의 showGit을 부르고, tree.js/git.js는 헤더 상태를
// 갱신하려고 shell.js의 _setPath/_setTitle/_setActivePane을 부른다). ES 모듈은
// 이런 순환을 허용한다 — 전부 함수 본문 안에서만 서로를 참조하고 모듈
// 평가(top-level) 시점에는 아무도 상대를 실행하지 않으므로 TDZ 문제가 없다.
// 단일 파일이던 걸 쪼갠 자연스러운 결과라 억지로 한쪽 방향으로 펴지 않았다.
import { vtFetch } from '../../core/api.js';
import { openPanel, closePanel } from '../panel.js';
import { registerAction } from '../../core/dom.js';
import { fitAndResize } from '../../term/resize.js';
import { activeSessionId } from '../../core/store.js';
import {
  _viewerState, _setMsg,
  _ICON_SHEET, _ICON_DOCK, _ICON_FULL, _ICON_SIDEBAR, _ICON_PIN, _ICON_INSERT,
  _loadMode, _saveMode, _isMobile,
  _loadDockW, _saveDockW, _clampDockW,
  _loadTreeW, _saveTreeW, _clampTreeW,
} from './state.js';
import { _renderRootTree, _wirePathInput, _openAtTerminalCwd, _insertPathToTerminal } from './tree.js';
import { showGit } from './git.js';

export function closeViewer() { closePanel('vt-viewer'); }

// P1: highlight.min.js(127KB)는 초기 로드에서 빼고 코드 뷰어를 실제로 열 때만
// 불러온다. openFile()이 파일을 네트워크로 fetch하는 동안 대부분 로드가 끝나므로
// 체감 지연은 거의 없고, _hl()이 이미 `!window.hljs`를 하이라이트 없는 이스케이프
// 텍스트로 안전하게 폴백하므로 로드 전에 렌더링이 일어나도 깨지지 않는다.
let _hljsLoading = null;
function _ensureHljs() {
  if (window.hljs) return Promise.resolve();
  if (!_hljsLoading) {
    _hljsLoading = new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/highlight.min.js';
      s.onload = resolve;
      s.onerror = resolve; // 실패해도 _hl()의 이스케이프 폴백으로 뷰어는 계속 동작
      document.head.appendChild(s);
    });
  }
  return _hljsLoading;
}

export async function showViewer() {
  _ensureHljs(); // fire-and-forget — openFile()의 fetch와 겹쳐서 대기시간 없음
  const displayMode = _loadMode();
  const panel = openPanel({
    id: 'vt-viewer',
    ariaLabel: '코드 뷰어',
    extraClass: displayMode === 'sheet' ? '' : 'mode-' + displayMode,
    headHTML: `
      <button class="vt-vw-back" aria-label="트리로" title="트리로">‹</button>
      <div class="vt-vw-title" id="vt-vw-title">코드 뷰어</div>
      <button class="vt-vw-tree-toggle" id="vt-vw-tree-toggle" title="폴더 트리 접기/펼치기">${_ICON_SIDEBAR}</button>
      <button class="vt-vw-here" id="vt-vw-here" title="현재 터미널 위치로 열기">${_ICON_PIN}</button>
      <button class="vt-vw-insert-cur" id="vt-vw-insert-cur" title="현재 파일 경로를 터미널에 삽입">${_ICON_INSERT}</button>
      <div class="vt-vw-modes" role="group" aria-label="표시 모드">
        <button class="vt-vw-mode-btn" data-mode="sheet" title="시트">${_ICON_SHEET}</button>
        <button class="vt-vw-mode-btn" data-mode="dock" title="도킹 — 터미널과 함께 보기">${_ICON_DOCK}</button>
        <button class="vt-vw-mode-btn" data-mode="full" title="전체화면">${_ICON_FULL}</button>
      </div>
      <button class="vt-vw-diff" id="vt-vw-diff" title="Git 변경사항 · stage · commit">git</button>
    `,
    extraHTML: `
      <input class="vt-vw-path" id="vt-vw-path" type="text" spellcheck="false" autocapitalize="off" autocomplete="off" title="경로를 입력하고 Enter — 이 위치를 최상단으로 엽니다">
      <div class="vt-vw-resizer" id="vt-vw-resizer"></div>
    `,
    bodyId: 'vt-vw-body',
    bodyHTML: `
      <div class="vt-vw-tree-pane" id="vt-vw-tree"><div class="vt-vw-loading">불러오는 중…</div></div>
      <div class="vt-vw-tree-resizer" id="vt-vw-tree-resizer"></div>
      <div class="vt-vw-code-pane" id="vt-vw-code-pane"><div class="vt-vw-code-empty">파일을 선택하세요.</div></div>
    `,
    onKey: () => {
      // 도킹 모드는 터미널과 동시에 보이는 상태 — Esc는 vim 등 터미널 쪽에 쓰이므로
      // 패널을 닫지 않는다. 시트/전체화면에서는 기본 동작(닫기) 그대로.
      if (_viewerState.displayMode === 'dock') return true;
    },
    onClose: () => {
      // X · 배경 클릭 · Esc · 재호출 토글 — 어느 경로로 닫히든 panel.js가 이걸 불러준다.
      const t = document.getElementById('vt-rail-file');
      if (t) t.classList.remove('active');
      document.body.classList.remove('vt-docked');
    },
  });
  if (!panel) return;   // 토글 — 이미 열려 있어서 닫기만 했다

  // L4: 예전 상단바 #viewer-toggle이 rail의 「파일」 버튼(#vt-rail-file)으로
  // 대체됐다 — id만 바꿔 그 자리에 계속 active 하이라이트를 건다.
  const btn = document.getElementById('vt-rail-file');
  if (btn) btn.classList.add('active');

  document.getElementById('vt-vw-body').classList.add('split');
  // data-active 를 처음부터 세운다. 이게 없으면 sheet(폰)에서도 CSS의 한쪽만
  // 보여주는 규칙([data-active="tree"])이 걸리지 않아, .split 기본값인 좌우
  // 분할이 그대로 적용된다 — 390px 화면에서 트리 230px + 코드 160px로 쪼개져
  // 코드를 읽을 수 없었다. dock/full은 data-active와 무관하게 둘 다 보이므로
  // 넓은 화면 동작에는 영향이 없다.
  _setActivePane('tree');
  _viewerState.mode = 'tree';
  _viewerState.selectedPath = null;
  _viewerState.expanded = new Set();
  _viewerState.displayMode = displayMode;
  _applyDisplayMode(displayMode, panel.el);

  panel.el.querySelector('.vt-vw-back').addEventListener('click', () => _setActivePane('tree'));
  panel.el.querySelectorAll('.vt-vw-mode-btn').forEach(b => {
    b.addEventListener('click', () => _setDisplayMode(b.dataset.mode));
  });
  panel.el.querySelector('#vt-vw-diff').addEventListener('click', () => showGit());
  panel.el.querySelector('#vt-vw-tree-toggle').addEventListener('click', _toggleTreeCollapse);
  panel.el.querySelector('#vt-vw-here').addEventListener('click', _openAtTerminalCwd);
  panel.el.querySelector('#vt-vw-insert-cur').addEventListener('click', () => {
    if (_viewerState.mode === 'file' && _viewerState.selectedPath) {
      _insertPathToTerminal(_viewerState.selectedPath);
    } else {
      showToast('먼저 파일을 여세요');
    }
  });
  _wireResizer(panel.el);
  _wireTreeResizer(panel.el);
  _wirePathInput(panel.el);
  document.documentElement.style.setProperty('--vt-tree-w', _clampTreeW(_loadTreeW()) + 'px');

  const treeEl = document.getElementById('vt-vw-tree');
  try {
    const { roots } = await vtFetch('/api/fs/roots');
    if (!roots || !roots.length) {
      _setMsg(treeEl, 'vt-vw-empty', ['열람 가능한 루트가 없습니다.', 'VT_BROWSE_ROOTS 를 설정하세요.']);
      return;
    }
    _viewerState.root = roots[0];
    _viewerState.cwd = roots[0];
    _setPath(roots[0]);
    await _renderRootTree();
  } catch (e) {
    _setMsg(treeEl, 'vt-vw-empty', [e.message]);
  }
}

export function _setPath(p) {
  const el = document.getElementById('vt-vw-path');
  if (!el) return;
  if (document.activeElement === el) return;   // 입력 중이면 덮어쓰지 않는다
  el.value = p || '';
}

export function _setTitle(t) {
  const el = document.getElementById('vt-vw-title');
  if (el) el.textContent = t;
}

export function _setActivePane(which) {
  const body = document.getElementById('vt-vw-body');
  if (body) body.dataset.active = which;
  _viewerState.mode = which === 'tree' ? 'tree' : _viewerState.mode;
}

// --- 표시 모드 --------------------------------------------------------------

export function _applyDisplayMode(mode, el) {
  el = el || document.getElementById('vt-viewer');
  if (!el) return;
  el.classList.remove('mode-dock', 'mode-full');
  if (mode === 'dock' || mode === 'full') el.classList.add('mode-' + mode);
  el.querySelectorAll('.vt-vw-mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });

  const nowDocked = mode === 'dock';
  const wasDocked = document.body.classList.contains('vt-docked');
  if (nowDocked) {
    document.documentElement.style.setProperty('--vt-dock-w', _clampDockW(_loadDockW()) + 'px');
  }
  document.body.classList.toggle('vt-docked', nowDocked);

  // 도킹 진입/이탈만 터미널 폭을 바꾼다(margin-right). 그 전환(.18s)이 끝난 뒤
  // 딱 한 번만 fit() — resize.js 경고와 같은 이유로 전환 도중엔 부르지 않는다.
  // sheet↔full은 터미널 크기에 영향이 없으므로 fit이 필요 없다.
  if (wasDocked !== nowDocked) {
    try { setTimeout(() => fitAndResize(activeSessionId()), 200); } catch (_) {}
  }
}

export function _setDisplayMode(mode) {
  if (_isMobile()) mode = 'sheet';
  if (mode === _viewerState.displayMode) return;
  _viewerState.displayMode = mode;
  _saveMode(mode);
  _applyDisplayMode(mode);
}

// 도킹 폭 드래그 리사이저. 매 프레임 fit()을 부르면 xterm이 glyph 아틀라스를
// 계속 갈아엎으므로, 드래그 중엔 CSS 변수만 갱신하고 pointerup 시점에 딱 한 번만
// fitAndResize 한다.
export function _wireResizer(el) {
  const handle = el.querySelector('#vt-vw-resizer');
  if (!handle) return;
  let startX = 0, startW = 0;

  const onMove = (ev) => {
    const delta = startX - ev.clientX;   // 우측 도킹 — 왼쪽으로 끌수록 넓어진다
    document.documentElement.style.setProperty('--vt-dock-w', _clampDockW(startW + delta) + 'px');
  };
  const onUp = () => {
    document.removeEventListener('pointermove', onMove);
    handle.classList.remove('dragging');
    document.body.classList.remove('vt-resizing');
    const w = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-dock-w'), 10);
    if (Number.isFinite(w)) _saveDockW(w);
    try { fitAndResize(activeSessionId()); } catch (_) {}
  };
  handle.addEventListener('pointerdown', (ev) => {
    if (_viewerState.displayMode !== 'dock') return;
    ev.preventDefault();
    startX = ev.clientX;
    startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-dock-w'), 10) || _loadDockW();
    handle.classList.add('dragging');
    document.body.classList.add('vt-resizing');
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp, { once: true });
  });
}

// 트리 패널 폭 드래그 리사이저 — dock/full 2단 분할에서 트리↔코드 경계를 끈다.
// 터미널 폭에는 영향이 없으므로 fitAndResize 호출은 불필요.
export function _wireTreeResizer(el) {
  const handle = el.querySelector('#vt-vw-tree-resizer');
  if (!handle) return;
  let startX = 0, startW = 0;

  const onMove = (ev) => {
    const delta = ev.clientX - startX;   // 좌측 패널 — 오른쪽으로 끌수록 넓어진다
    document.documentElement.style.setProperty('--vt-tree-w', _clampTreeW(startW + delta) + 'px');
  };
  const onUp = () => {
    document.removeEventListener('pointermove', onMove);
    handle.classList.remove('dragging');
    const w = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-tree-w'), 10);
    if (Number.isFinite(w)) _saveTreeW(w);
  };
  handle.addEventListener('pointerdown', (ev) => {
    if (_viewerState.displayMode !== 'dock' && _viewerState.displayMode !== 'full') return;
    ev.preventDefault();
    startX = ev.clientX;
    startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--vt-tree-w'), 10) || _loadTreeW();
    handle.classList.add('dragging');
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp, { once: true });
  });
}

// 트리 패널 접기/펼치기 — dock/full 전용(CSS가 sheet에서는 버튼 자체를 숨긴다).
export function _toggleTreeCollapse() {
  const el = document.getElementById('vt-viewer');
  if (!el) return;
  const collapsed = el.classList.toggle('tree-collapsed');
  const btn = el.querySelector('#vt-vw-tree-toggle');
  if (btn) btn.classList.toggle('active', collapsed);
}

// F3(c): data-action 위임용 등록.
registerAction('viewer.show', () => showViewer());
