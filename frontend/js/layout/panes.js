// L3 1·3단계 — 트리를 DOM으로 그리는 재귀 렌더러 + 분할 UI. split은 flex
// 컨테이너, leaf는 **기존 세션 wrapper를 appendChild로 옮기기만 한다(재생성
// 금지)** — ADR-1 그대로. 구조용 div(.vt-split/.vt-pane)는 매 렌더마다 새로
// 만들어도 무해하다(진짜 상태를 담은 건 wrapper 하나뿐이다).
//
// 트리에 없는(=화면 밖 배경 탭) 세션의 wrapper는 #terminal-pool(숨김)에
// 대기시킨다.
//
// pane 헤더의 분할·닫기 버튼은 "환경 무관 베이스라인"이다(착수 전 설계
// 리뷰 원칙) — 클릭이든 탭이든 항상 이걸로 전부 가능하다. 탭/헤더를 pane
// 위로 드래그하는 DnD(L5, layout/dnd.js)는 이 baseline 위에 얹는 "있으면
// 편한" 추가 경로다.
import { getSession, allSessions } from '../core/store.js';
import { createSession } from '../term/session.js';
import {
  getTree, getActivePaneId, onLayoutChange, setActivePane,
  splitPane, closePane, setRatio, countLeaves,
} from './store.js';
import { findNode } from './tree.js';
import { fitAndResize } from '../term/resize.js';
import { wireRatioResizer } from './resizer.js';
import { canSplit, SESSION_MIME, wirePaneDropTarget, wireTouchDragSource } from './dnd.js';
import { icon } from '../ui/icons.js';

export { canSplit };

let _rootEl = null;
let _poolEl = null;

function _ensureContainers() {
  if (!_rootEl) _rootEl = document.getElementById('terminal-container');
  if (!_poolEl) {
    _poolEl = document.getElementById('terminal-pool');
    if (!_poolEl) {
      _poolEl = document.createElement('div');
      _poolEl.id = 'terminal-pool';
      _poolEl.style.cssText = 'display:none;';
      document.body.appendChild(_poolEl);
    }
  }
  return _rootEl != null;
}

function _sessionLabel(sessionId) {
  const s = getSession(sessionId);
  return s?.tabEl?.querySelector('.tab-name')?.textContent || sessionId.slice(0, 8);
}

function _paneSessionId(paneId) {
  const node = findNode(getTree(), paneId);
  return node && node.t === 'leaf' ? node.session : null;
}

function _buildPaneEl(paneId) {
  const paneEl = document.createElement('div');
  paneEl.id = `vt-pane-${paneId}`;
  paneEl.dataset.paneId = paneId;
  paneEl.className = 'vt-pane';
  paneEl.innerHTML = `
    <div class="vt-pane-head">
      <span class="vt-pane-name"></span>
      <button type="button" class="vt-pane-btn vt-pane-split-row" title="오른쪽 분할" aria-label="오른쪽 분할">${icon('columns-2', 13)}</button>
      <button type="button" class="vt-pane-btn vt-pane-split-col" title="아래쪽 분할" aria-label="아래쪽 분할">${icon('rows-2', 13)}</button>
      <button type="button" class="vt-pane-btn vt-pane-close" title="pane 닫기" aria-label="pane 닫기">${icon('x', 13)}</button>
    </div>
    <div class="vt-pane-body"></div>
  `;
  paneEl.querySelector('.vt-pane-split-row').addEventListener('click', () => {
    if (canSplit()) splitPane(paneId, 'row');
  });
  paneEl.querySelector('.vt-pane-split-col').addEventListener('click', () => {
    if (canSplit()) splitPane(paneId, 'col');
  });
  paneEl.querySelector('.vt-pane-close').addEventListener('click', () => closePane(paneId));

  // L5: 탭(tab-dom.js)이나 다른 pane 헤더(아래)를 이 pane 위로 드래그하면
  // 5구역 드롭존으로 배정한다 — 마우스는 네이티브 HTML5 DnD, 터치는 헤더 쪽
  // long-press 배선(아래)이 같은 wirePaneDropTarget이 세팅한
  // data-dropzone/data-pane-id를 그대로 읽는다.
  wirePaneDropTarget(paneEl, paneId);

  // pane 헤더 자체도 드래그 소스다 — "이 pane에 지금 배치된 세션을 다른
  // pane으로 옮긴다"(이동/교환). 헤더 버튼(baseline)이 이미 분할·닫기·세션
  // 전환(탭 클릭)을 전부 커버하므로 DnD는 그 위에 얹는 추가 경로일 뿐이다.
  const head = paneEl.querySelector('.vt-pane-head');
  head.draggable = true;
  head.addEventListener('dragstart', (e) => {
    const sid = _paneSessionId(paneId);
    if (!sid) { e.preventDefault(); return; }
    e.dataTransfer.setData(SESSION_MIME, sid);
    e.dataTransfer.effectAllowed = 'move';
  });
  wireTouchDragSource(head, () => _paneSessionId(paneId));

  return paneEl;
}

function _renderEmptyBody(bodyEl, paneId) {
  if (bodyEl.querySelector('.vt-pane-empty')) return;
  bodyEl.replaceChildren();
  const ph = document.createElement('div');
  ph.className = 'vt-pane-empty';
  ph.innerHTML = `<button type="button" class="vt-btn-secondary">+ 새 세션</button>`;
  ph.querySelector('button').addEventListener('click', () => {
    // 이 pane에 바로 배정되도록 활성 pane으로 지정한 뒤 만든다 — createSession()이
    // 끝에서 부르는 switchTo()가 "현재 활성 pane"에 세션을 넣기 때문.
    setActivePane(paneId);
    createSession();
  });
  bodyEl.appendChild(ph);
}

// node(split 또는 leaf)를 그 자리에 있어야 할 DOM 엘리먼트로 렌더링해 반환한다.
function _renderNode(node, activePaneId, isRootOnly, usedSessionIds) {
  if (node.t === 'split') {
    let el = document.getElementById(`vt-split-${node.id}`);
    let resizerEl;
    if (!el) {
      el = document.createElement('div');
      el.id = `vt-split-${node.id}`;
      el.className = 'vt-split';
      resizerEl = document.createElement('div');
      resizerEl.className = 'vt-split-resizer';
      el._resizer = resizerEl;
    } else {
      resizerEl = el._resizer;
    }
    el.classList.toggle('vt-split-row', node.dir === 'row');
    el.classList.toggle('vt-split-col', node.dir !== 'row');

    if (!resizerEl._wired) {
      resizerEl._wired = true;
      wireRatioResizer(resizerEl, {
        dir: node.dir,
        getContainerSize: () => (node.dir === 'row' ? el.clientWidth : el.clientHeight),
        getStartRatio: () => findNode(getTree(), node.id)?.ratio ?? 0.5,
        onRatio: (r) => setRatio(node.id, r),
        onEnd: () => {
          for (const id of _leafSessionIds(getTree())) {
            requestAnimationFrame(() => fitAndResize(id));
          }
        },
      });
    }

    const aEl = _renderNode(node.a, activePaneId, false, usedSessionIds);
    const bEl = _renderNode(node.b, activePaneId, false, usedSessionIds);
    aEl.style.flex = `${node.ratio} 1 0`;
    bEl.style.flex = `${1 - node.ratio} 1 0`;
    if (el.children[0] !== aEl || el.children[1] !== resizerEl || el.children[2] !== bEl) {
      el.replaceChildren(aEl, resizerEl, bEl);
    }
    return el;
  }

  // leaf
  let paneEl = document.getElementById(`vt-pane-${node.id}`);
  if (!paneEl) paneEl = _buildPaneEl(node.id);
  paneEl.classList.toggle('active', node.id === activePaneId);
  const head = paneEl.querySelector('.vt-pane-head');
  head.style.display = isRootOnly ? 'none' : '';

  const bodyEl = paneEl.querySelector('.vt-pane-body');
  // 죽은 세션 참조(예: 서버 재시작으로 세션은 사라졌는데 트리엔 id가 남은
  // 경우) → 조용히 빈 pane 취급. 트리 자체는 안 건드린다(다음 실제 배정이
  // 오면 자연히 덮어써진다), 렌더링에서만 관대하게 처리한다.
  const s = node.session ? getSession(node.session) : null;
  const nameEl = paneEl.querySelector('.vt-pane-name');
  if (s && s.wrapper) {
    usedSessionIds.add(node.session);
    nameEl.textContent = _sessionLabel(node.session);
    const empty = bodyEl.querySelector('.vt-pane-empty');
    if (empty) empty.remove();
    if (s.wrapper.parentElement !== bodyEl) bodyEl.appendChild(s.wrapper);
    s.wrapper.style.display = 'block';
  } else {
    nameEl.textContent = '빈 pane';
    _renderEmptyBody(bodyEl, node.id);
  }
  return paneEl;
}

function _leafSessionIds(node) {
  if (node.t === 'leaf') return node.session ? [node.session] : [];
  return [..._leafSessionIds(node.a), ..._leafSessionIds(node.b)];
}

// 트리를 다시 그린다. layout/store.js의 onLayoutChange가 이 함수를 부른다 —
// term/session.js의 switchTo() 등은 스토어만 갱신하고, 실제 DOM 반영은 항상
// 이 한 곳을 거친다(직접 wrapper를 만지지 않는다).
export function renderLayout() {
  if (!_ensureContainers()) return; // #terminal-container가 아직 없으면(부팅 전) 대기
  const tree = getTree();
  const activePaneId = getActivePaneId();
  const usedSessionIds = new Set();

  const rootEl = _renderNode(tree, activePaneId, countLeaves() === 1, usedSessionIds);
  if (_rootEl.children[0] !== rootEl) _rootEl.replaceChildren(rootEl);

  // 트리에 없는 세션(배경 탭) wrapper는 풀로 — 이미 거기 있으면 건드리지 않는다.
  for (const [id, s] of Object.entries(allSessions())) {
    if (s.tabEl) s.tabEl.classList.toggle('placed', usedSessionIds.has(id));
    if (usedSessionIds.has(id) || !s.wrapper) continue;
    if (s.wrapper.parentElement !== _poolEl) _poolEl.appendChild(s.wrapper);
    s.wrapper.style.display = 'none';
  }

  // re-attach(부모가 바뀌었을 수 있는) 직후엔 레이아웃이 아직 안 잡혀 fit이
  // stale 크기를 잡는다 — rAF로 확정 후 fit + PTY 크기 통보(기존 탭 전환
  // 패턴, term/resize.js 그대로 재사용).
  for (const id of usedSessionIds) {
    requestAnimationFrame(() => fitAndResize(id));
  }
}

onLayoutChange(renderLayout);
