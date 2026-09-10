// L3 1·3단계 — 트리를 DOM으로 그리는 재귀 렌더러 + 분할 UI. split은 flex
// 컨테이너, leaf(.vt-pane-body)는 **빈 상자일 뿐이다** — 실제 xterm wrapper는
// N16(표면 레이어, layout/surface.js)이 절대좌표로 따로 얹는다. 이 파일은
// "어느 pane-body가 어느 세션을 보여줘야 하는가"만 계산해 surface.setPlacement로
// 넘기고, 실제 배치·refit 게이트·드래그 유예는 전부 surface.js 책임이다.
// 구조용 div(.vt-split/.vt-pane)는 매 렌더마다 새로 만들어도 무해하다(진짜
// 상태를 담은 건 세션의 wrapper 하나뿐이고, 그건 이제 여기서 아예 건드리지
// 않는다).
//
// pane 헤더의 분할·닫기 버튼은 "환경 무관 베이스라인"이다(착수 전 설계
// 리뷰 원칙) — 클릭이든 탭이든 항상 이걸로 전부 가능하다. 탭/헤더를 pane
// 위로 드래그하는 DnD(L5, layout/dnd.js)는 이 baseline 위에 얹는 "있으면
// 편한" 추가 경로다.
import { getSession, allSessions } from '../core/store.js';
import {
  getTree, getActivePaneId, onLayoutChange, setActivePane,
  splitPane, closePane, setRatio,
} from './store.js';
import { findNode } from './tree.js';
import { wireRatioResizer } from './resizer.js';
import { canSplit, tierCap, SESSION_MIME, wirePaneDropTarget, wireTouchDragSource } from './dnd.js';
import { openPanePicker } from './pane-picker.js';
import { isCompactMode, flattenLeaves, wireCompactSwipe } from './compact.js';
import { icon } from '../ui/icons.js';
// N35 §6 — 뷰어 leaf의 내용은 지연 청크가 그린다(정적 import 금지, ADR-26).
import { loadViewer } from '../panels/viewer-lazy.js';
import * as surface from './surface.js';
import { beginResizeOverlay, endResizeOverlay } from './resize-overlay.js';

export { canSplit };

// L8 — pane 상한에 걸린 분할 버튼을 실제로 비활성화하고 이유를 붙인다.
// 지금까지는 클릭해도 canSplit()이 조용히 무시했는데, 버튼이 멀쩡해 보이니
// "눌렀는데 아무 일도 안 일어난다"로 읽혔다. 드롭존(가장자리 DnD)은 기존대로
// 조용히 무시한다 — 드래그 중에는 하이라이트가 안 뜨는 것 자체가 피드백이다.
function _applySplitCap(paneEl) {
  const ok = canSplit();
  const reason = `분할 한도에 도달했습니다 (현재 화면 폭에서는 최대 ${tierCap()}개)`;
  for (const [sel, label] of [['.vt-pane-split-row', '오른쪽 분할'], ['.vt-pane-split-col', '아래쪽 분할']]) {
    const btn = paneEl.querySelector(sel);
    if (!btn) continue;
    btn.disabled = !ok;
    btn.title = ok ? label : reason;
    btn.setAttribute('aria-label', ok ? label : `${label} — ${reason}`);
  }
}

let _rootEl = null;

// N16 — _rootEl은 더 이상 #terminal-container 자신이 아니라 그 안의
// #vt-chrome-tree(신규, 페인 크롬 전용 마운트 포인트)다. #terminal-container를
// 직접 replaceChildren 대상으로 쓰면 형제인 #vt-surface(표면 레이어)까지
// 통째로 날아간다 — 실측으로 재현: 트리 렌더가 한 번이라도 일어나면 surface.js가
// 이미 만들어 둔 #vt-surface가 사라지고 다음 setPlacement가 새 걸 만들면서
// 그 안의 wrapper들과 완전히 분리된다. 이 파일은 §3 전면 개편(index.html에
// #vt-chrome 도입) 전까지 이 div를 JS로 lazy 생성해 그 문제를 막는다.
function _ensureContainers() {
  if (!_rootEl) {
    const container = document.getElementById('terminal-container');
    if (!container) return false;
    _rootEl = document.getElementById('vt-chrome-tree');
    if (!_rootEl) {
      _rootEl = document.createElement('div');
      _rootEl.id = 'vt-chrome-tree';
      _rootEl.style.cssText = 'width:100%;height:100%;';
      container.appendChild(_rootEl);
    }
    // L3 2단계: "터미널 영역 한정"(§8) — 스와이프 리스너는 컨테이너 전체에 건다
    // (표면 레이어의 wrapper도 이 컨테이너의 후손이라 이벤트가 그대로 버블링된다).
    wireCompactSwipe(container);
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

// L3 5단계: 임시 "+ 새 세션" 버튼(4단계까지의 최소 placeholder)을 썸네일
// 기반 세션 선택 시트로 교체 — pane 전체가 클릭 대상이라 좁은 화면에서도
// 작은 버튼을 정확히 누를 필요가 없다.
function _renderEmptyBody(bodyEl, paneId) {
  bodyEl.classList.remove('vt-pane-viewer');
  delete bodyEl.dataset.viewerFile;
  if (bodyEl.querySelector('.vt-pane-empty')) return;
  bodyEl.replaceChildren();
  const ph = document.createElement('button');
  ph.type = 'button';
  ph.className = 'vt-pane-empty';
  ph.setAttribute('aria-label', '이 화면에 표시할 세션 선택');
  ph.innerHTML = `${icon('plus', 20)}<span>세션 선택</span>`;
  ph.addEventListener('click', () => openPanePicker(paneId));
  bodyEl.appendChild(ph);
}

// node(split 또는 leaf)를 그 자리에 있어야 할 DOM 엘리먼트로 렌더링해 반환한다.
// placement는 Map<sessionId, paneBodyEl> — leaf 분기가 실제로 세션을 보여주게
// 되는 pane마다 자신의 .vt-pane-body를 여기 채운다. surface.js는 이 지도만
// 보고 어느 wrapper를 어디로 옮길지 정한다(DOM 트리 자체는 안 건드린다).
function _renderNode(node, activePaneId, isRootOnly, placement) {
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
        // N16 — 드래그 중엔 표면 레이어에 fit을 미루게 하고(§1 수용 기준 2:
        // 드래그 중 0건), 뗀 순간 실제로 크기가 바뀐 세션만 한 번씩 flush한다.
        // 예전처럼 트리 전체 leaf를 매번 fit하지 않는다.
        onStart: () => { surface.beginDrag(); beginResizeOverlay(); },
        onEnd: () => { surface.endDrag(); endResizeOverlay(); },
      });
    }

    const aEl = _renderNode(node.a, activePaneId, false, placement);
    const bEl = _renderNode(node.b, activePaneId, false, placement);
    aEl.style.flex = `${node.ratio} 1 0`;
    bEl.style.flex = `${1 - node.ratio} 1 0`;
    if (el.children[0] !== aEl || el.children[1] !== resizerEl || el.children[2] !== bEl) {
      el.replaceChildren(aEl, resizerEl, bEl);
    }
    return el;
  }

  // leaf
  return _renderLeaf(node, activePaneId, isRootOnly, placement);
}

// split 렌더러(_renderNode)와 compact 렌더러(_renderCompactActive) 둘 다
// "leaf 하나를 실제 DOM으로 반영"하는 이 로직을 공유한다 — L3 2단계에서
// _renderNode의 leaf 분기를 그대로 뽑아냈다(동작 변경 없음). labelSuffix는
// compact의 위치 표시(" · 2/3")용. N16부터는 wrapper를 직접 옮기지 않고
// placement에 "이 세션은 이 pane-body"라고만 적어 둔다.
function _renderLeaf(node, activePaneId, isRootOnly, placement, labelSuffix = '') {
  let paneEl = document.getElementById(`vt-pane-${node.id}`);
  if (!paneEl) paneEl = _buildPaneEl(node.id);
  paneEl.classList.toggle('active', node.id === activePaneId);
  _applySplitCap(paneEl);
  const head = paneEl.querySelector('.vt-pane-head');
  head.style.display = isRootOnly ? 'none' : '';

  const bodyEl = paneEl.querySelector('.vt-pane-body');
  // 죽은 세션 참조(예: 서버 재시작으로 세션은 사라졌는데 트리엔 id가 남은
  // 경우) → 조용히 빈 pane 취급. 트리 자체는 안 건드린다(다음 실제 배정이
  // 오면 자연히 덮어써진다), 렌더링에서만 관대하게 처리한다.
  const nameEl = paneEl.querySelector('.vt-pane-name');

  // N35 §6 — 뷰어 칸: 세션이 아니라 파일 하나를 그린다. 표면 레이어에는 아무
  // 것도 안 넘긴다(placement에 안 들어가므로 xterm이 이 자리를 덮지 않는다).
  if (node.kind === 'viewer') {
    nameEl.textContent = (node.file ? node.file.split('/').pop() : '파일 없음') + labelSuffix;
    nameEl.title = node.file || '';
    _renderViewerBody(bodyEl, node.file);
    return paneEl;
  }

  const s = node.session ? getSession(node.session) : null;
  if (s && s.wrapper) {
    placement.set(node.session, bodyEl);
    bodyEl.classList.remove('vt-pane-viewer');
    delete bodyEl.dataset.viewerFile;
    nameEl.textContent = _sessionLabel(node.session) + labelSuffix;
    const empty = bodyEl.querySelector('.vt-pane-empty');
    if (empty) empty.remove();
  } else {
    nameEl.textContent = '빈 pane' + labelSuffix;
    _renderEmptyBody(bodyEl, node.id);
  }
  return paneEl;
}

// 뷰어 칸의 내용. 같은 파일을 다시 그리지 않는다(렌더는 트리 구조가 바뀔
// 때마다 도는데, 그때마다 파일을 다시 fetch하면 스크롤 위치가 튄다) —
// 그려둔 경로를 dataset에 적어 두고 비교한다.
function _renderViewerBody(bodyEl, file) {
  bodyEl.classList.add('vt-pane-viewer');
  if (!file) {
    bodyEl.replaceChildren();
    bodyEl.dataset.viewerFile = '';
    const empty = document.createElement('div');
    empty.className = 'vt-vw-empty';
    empty.textContent = '파일이 지정되지 않았습니다.';
    bodyEl.appendChild(empty);
    return;
  }
  if (bodyEl.dataset.viewerFile === file) return;
  bodyEl.dataset.viewerFile = file;
  bodyEl.replaceChildren();
  const host = document.createElement('div');
  host.className = 'vt-vw-code-pane';
  bodyEl.appendChild(host);
  loadViewer()
    .then((v) => v.renderFile(host, file))
    .catch((e) => { host.textContent = String(e && e.message ? e.message : e); });
}

// L3 2단계 — compact(<720px + pointer:coarse) 렌더 모드: 활성 leaf 하나만
// 화면 전체로 그린다. 헤더는 숨기지 않는다(isRootOnly=false) — 분할/닫기
// 버튼과 위치 표시(" · n/N")가 여기서만 나온다. 단 accent 테두리(.active)는
// 뗀다 — 화면에 pane이 하나뿐이라 "여럿 중 활성"이라는 뜻이 성립하지 않는다
// (비교 대상 자체가 안 보인다).
function _renderCompactActive(leaves, activePaneId, placement) {
  const idx = Math.max(0, leaves.findIndex((l) => l.id === activePaneId));
  const active = leaves[idx] ?? leaves[0];
  const paneEl = _renderLeaf(active, activePaneId, false, placement, ` · ${idx + 1}/${leaves.length}`);
  paneEl.classList.remove('active');
  return paneEl;
}

// 활성 pane만 바뀌었을 때(포커스 이동 등)의 경량 경로 — 트리 구조·세션 배정은
// 그대로이므로 .active 클래스 토글만 하고 끝난다. 표면 레이어는 전혀 건드리지
// 않으므로 서버로 resize가 나갈 일이 없다(§1 수용 기준 1).
function _applyActiveOnly() {
  const activePaneId = getActivePaneId();
  for (const el of _rootEl.querySelectorAll('.vt-pane')) {
    el.classList.toggle('active', el.dataset.paneId === activePaneId);
  }
}

// 특정 split 하나의 비율만 바뀌었을 때(분할선 드래그)의 경량 경로 — 그
// split의 두 자식 flex만 갱신한다. 실제 크기 변화 감지·refit은 각 pane-body의
// ResizeObserver(surface.js)가 알아서 하므로, 여기서는 CSS만 만지고 끝난다.
function _applyRatioOnly(splitId) {
  const node = findNode(getTree(), splitId);
  const el = document.getElementById(`vt-split-${splitId}`);
  if (!node || node.t !== 'split' || !el || el.children.length !== 3) return;
  el.children[0].style.flex = `${node.ratio} 1 0`;
  el.children[2].style.flex = `${1 - node.ratio} 1 0`;
}

// 트리 구조 자체가 바뀌었을 때(분할·닫기·세션 배정·복원)의 전체 경로 —
// 크롬 DOM을 다시 그리고, 배치 지도를 surface.setPlacement로 넘긴다.
function _renderFull() {
  const tree = getTree();
  const activePaneId = getActivePaneId();
  const placement = new Map(); // sessionId -> pane-body
  const leaves = flattenLeaves(tree);
  // L3 2단계: 트리는 그대로, 렌더 방식만 갈린다(설계 원칙 1) — leaf가
  // 하나뿐이면 compact 여부와 무관하게 항상 지금과 같은 전체화면 렌더.
  const compact = leaves.length > 1 && isCompactMode();

  const rootEl = compact
    ? _renderCompactActive(leaves, activePaneId, placement)
    : _renderNode(tree, activePaneId, leaves.length === 1, placement);
  if (_rootEl.children[0] !== rootEl) _rootEl.replaceChildren(rootEl);

  for (const [id, s] of Object.entries(allSessions())) {
    if (s.tabEl) s.tabEl.classList.toggle('placed', placement.has(id));
  }
  // N35 §6 — 뷰어 칸이 하나라도 있으면 화면이 비어 있는 게 아니다. 온보딩은
  // position:fixed + z-index:500이라 페인 위를 덮으므로 CSS로 눌러둔다.
  // "열 때 remove()"에 기대지 않는 이유: 세션 목록 응답이 늦게 오는 경로가
  // 온보딩을 **다시** 붙인다(실브라우저에서 그대로 재현됐다).
  document.body.classList.toggle('vt-has-viewer-pane', leaves.some((l) => l.kind === 'viewer'));
  // 표면 레이어에게 최종 배치를 넘긴다 — 빠진 세션은 숨기고, 새로 들어온
  // 세션은 즉시 실측해 transform·fit을 건다(surface.js 책임).
  surface.setPlacement(placement);
}

// 트리를 다시 그린다. layout/store.js의 onLayoutChange가 이 함수를 부른다 —
// term/session.js의 switchTo() 등은 스토어만 갱신하고, 실제 DOM 반영은 항상
// 이 한 곳을 거친다(직접 wrapper를 만지지 않는다).
//
// N16: kind로 세 경로를 가른다 — 'active'(포커스 이동)와 'ratio'(분할선
// 드래그, extra=splitId)는 compact가 아닌 한 크롬 DOM을 다시 만들지 않는
// 경량 경로를 탄다. compact 모드에서는 "지금 보이는 leaf가 바뀔 수 있다"는
// 전제가 깨지므로 항상 전체 경로로 떨어진다.
function renderLayout(_tree, _activePaneId, kind = 'layout', extra) {
  if (!_ensureContainers()) return; // #terminal-container가 아직 없으면(부팅 전) 대기
  const compact = flattenLeaves(getTree()).length > 1 && isCompactMode();
  if (!compact) {
    if (kind === 'active') return _applyActiveOnly();
    if (kind === 'ratio') return _applyRatioOnly(extra);
  }
  _renderFull();
}

onLayoutChange(renderLayout);

// L3 2단계: compact 여부는 뷰포트 폭에 달려 있다 — 트리가 안 바뀌어도
// 창 크기 변경/회전만으로 렌더 모드가 바뀔 수 있으므로 resize에도 다시
// 그린다. 디바운스 120ms는 term/ws.js의 세션별 resize 핸들러와 같은 값
// (모바일 키보드/뷰포트 흔들림 대비 관행). 전체 경로로 고정 — 창 크기 변경은
// kind 분류 대상이 아니다(항상 트리 재검토가 필요할 수 있다).
let _resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(_renderFull, 120);
});
