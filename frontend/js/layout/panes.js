// L3 1·3단계 — 트리를 DOM으로 그리는 재귀 렌더러 + 분할 UI. split은 flex
// 컨테이너, leaf는 `.vt-pane > .vt-pane-head + .vt-pane-body`이고 xterm
// wrapper는 **그 body 안에 실제로 들어간다**(2026-09-18 — 그전까지는
// #vt-surface 레이어에 얹혀 좌표로만 붙어 있었다. 경위는 layout/surface.js
// 상단 주석). 이 파일은 여전히 "어느 pane-body가 어느 세션을 보여줘야
// 하는가"만 계산해 surface.setPlacement로 넘기고, 실제 이동·refit 게이트·
// 드래그 유예는 surface.js 책임이다.
// 구조용 div(.vt-split/.vt-pane)는 id로 재사용한다 — 매 렌더마다 새로 만들면
// 그 안의 터미널까지 같이 떨어져 나간다.
//
// pane 헤더의 분할·닫기 버튼은 "환경 무관 베이스라인"이다(착수 전 설계
// 리뷰 원칙) — 클릭이든 탭이든 항상 이걸로 전부 가능하다. 탭/헤더를 pane
// 위로 드래그하는 DnD(L5, layout/dnd.js)는 이 baseline 위에 얹는 "있으면
// 편한" 추가 경로다.
import { getSession, allSessions, sessionDisplayName } from '../core/store.js';
import {
  getTree, getActivePaneId, onLayoutChange, setActivePane,
  splitPane, closePane, setRatio,
} from './store.js';
import { findNode, collectSessions } from './tree.js';
import { detachSession } from '../term/session-actions.js';
import { paneElId, splitElId } from './dom-ids.js';
import { snapPx, cellSizeFrom } from './snap.js';
import { wireRatioResizer } from './resizer.js';
import { canSplit, tierCap, SESSION_MIME, wirePaneDropTarget, wireTouchDragSource } from './dnd.js';
import { openPanePicker } from './pane-picker.js';
import { isCompactMode, flattenLeaves, wireCompactSwipe } from './compact.js';
import { icon } from '../ui/icons.js';
import { vtFetch } from '../core/api.js';
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
    // 2026-09-18 — 한도에 걸린 버튼은 이름(「오른쪽 분할」)은 그대로 두고 **왜 못
    // 누르는지**를 보조 단으로 내린다. 한 줄에 붙여 쓰던 때는 버튼 이름이
    // 사유 문장에 묻혔다.
    btn.setAttribute('data-tip', label);
    if (ok) btn.removeAttribute('data-tip-sub'); else btn.setAttribute('data-tip-sub', reason);
    btn.setAttribute('aria-label', ok ? label : `${label} — ${reason}`);
  }
}

let _rootEl = null;

// _rootEl은 #terminal-container 자신이 아니라 그 안의 #vt-chrome-tree다.
// #terminal-container를 직접 replaceChildren 대상으로 쓰면 형제인 대기실
// (#vt-term-stage)까지 통째로 날아간다 — 실측으로 재현했다(트리 렌더가 한 번
// 일어나면 surface.js가 만들어 둔 대기실이 사라지고, 다음 setPlacement가 새
// 걸 만들면서 그 안에서 대기하던 wrapper들과 완전히 분리된다). 표면 레이어를
// 걷어낸 뒤에도 이 형제 관계는 그대로라 같은 함정이 남아 있다.
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
    // (pane 안의 wrapper도 대기실의 wrapper도 이 컨테이너의 후손이라 이벤트가
    // 그대로 버블링된다).
    wireCompactSwipe(container);
  }
  return _rootEl != null;
}

function _sessionLabel(sessionId) {
  return sessionDisplayName(sessionId);
}

function _paneSessionId(paneId) {
  const node = findNode(getTree(), paneId);
  return node && node.t === 'leaf' ? node.session : null;
}

// 2.1 D4 — 워크트리는 탭이 아니라 "pane 헤더 옆 브랜치 칩"으로 보여준다(탭은
// 이제 저장소 단위라 워크트리 여러 개를 탭 이름 하나로는 구분 못 한다).
// term/tab-worktree.js가 워크트리 목록을 받을 때마다 setBranchChipMap을
// 부른다 — 그 지도는 **저장소에 워크트리가 둘 이상일 때만** 항목을 담는다
// (워크트리가 하나뿐이면 구분할 게 없으니 칩 자체가 안 보여야 한다).
let _branchChips = new Map();   // tmux 세션 이름 → 브랜치

export function setBranchChipMap(map) {
  _branchChips = map || new Map();
  _paintAllChips();
}

function _paintChip(paneEl) {
  const chip = paneEl.querySelector('.vt-pane-branch');
  if (!chip) return;
  const sid = _paneSessionId(paneEl.dataset.paneId);
  const s = sid ? getSession(sid) : null;
  const tmux = s && (s.tmuxName || s.tmux_name);
  const branch = tmux ? _branchChips.get(tmux) : null;
  chip.textContent = branch || '';
  chip.hidden = !branch;
  if (branch) chip.setAttribute('data-tip', '이 세션이 속한 워크트리 브랜치'); else chip.removeAttribute('data-tip');
}

// 트리가 안 바뀌어도(브랜치 지도만 새로 왔을 때) 이미 그려진 pane들의 칩만
// 다시 칠한다 — 트리 전체를 다시 그리면 렌더 중인 다른 화면과 경합한다.
function _paintAllChips() {
  if (!_rootEl) return;
  for (const paneEl of _rootEl.querySelectorAll('.vt-pane')) _paintChip(paneEl);
}

function _buildPaneEl(paneId) {
  const paneEl = document.createElement('div');
  paneEl.id = paneElId(paneId);
  paneEl.dataset.paneId = paneId;
  paneEl.className = 'vt-pane';
  paneEl.innerHTML = `
    <div class="vt-pane-head">
      <span class="vt-pane-name"></span>
      <span class="vt-pane-branch" hidden></span>
      <button type="button" class="vt-icon-btn xs vt-pane-split-row" data-tip="오른쪽 분할" data-tip-side="bottom" aria-label="오른쪽 분할">${icon('columns-2', 13)}</button>
      <button type="button" class="vt-icon-btn xs vt-pane-split-col" data-tip="아래쪽 분할" data-tip-side="bottom" aria-label="아래쪽 분할">${icon('rows-2', 13)}</button>
      <button type="button" class="vt-icon-btn xs danger vt-pane-close" data-tip="pane 닫기" data-tip-side="bottom" aria-label="pane 닫기">${icon('x', 13)}</button>
    </div>
    <div class="vt-pane-body"></div>
  `;
  paneEl.querySelector('.vt-pane-split-row').addEventListener('click', () => {
    if (canSplit()) splitPane(paneId, 'row');
  });
  paneEl.querySelector('.vt-pane-split-col').addEventListener('click', () => {
    if (canSplit()) splitPane(paneId, 'col');
  });
  // 2026-09-18 후속(그룹 재정의, 사용자 요청: "제거하면 자동으로 풀리고
  // 제거한 건 잠잘 것") — 이 pane이 그룹(같은 탭에 세션 2개 이상)의
  // 일원이었으면, 닫을 때 그 세션을 재운다(웹 세션만 놓는다, tmux는
  // 계속 산다). 남은 세션은 혼자가 되어 자동으로 "개인"이 된다 — 그룹은
  // 태그가 아니라 pane 트리에서 도출되므로 따로 "그룹 해제"를 할 필요가
  // 없다. 드래그로 pane을 옮겨 빼는 경우(dnd.js)는 이 재우기를 안 탄다 —
  // "옮기는 것"과 "닫는 것"은 다른 의도다.
  paneEl.querySelector('.vt-pane-close').addEventListener('click', () => {
    const node = findNode(getTree(), paneId);
    const sessionId = node && node.t === 'leaf' ? node.session : null;
    const wasGrouped = sessionId && collectSessions(getTree()).length >= 2;
    closePane(paneId);
    if (wasGrouped) detachSession(sessionId);
  });

  // 활성 pane을 **포커스가** 정한다. layout/store.js 상단이 L3 설계 원칙으로
  // 적어둔 그대로다("각 pane의 xterm에 실제로 focus 이벤트가 뜰 때만
  // setActivePane()을 부른다 — 그래야 테두리는 A인데 타이핑은 B로 들어가는
  // 상태가 애초에 생길 수 없다"). 그런데 그 배선이 **한 번도 붙은 적이
  // 없었다**: 이 파일은 setActivePane을 import만 해두고 부르지 않았고, 실제
  // 호출부는 pane 선택 시트와 compact 스와이프뿐이었다. 그래서 분할된 화면에서
  // 터미널을 클릭해도 활성 표시가 따라오지 않았다 — 표시가 약한 것과 별개로
  // **가리키는 대상 자체가 틀려 있었다**.
  //
  // 원문은 이 배선을 xterm-setup.js에 두려 했는데 그쪽은 자기가 어느 pane에
  // 있는지 모른다. 터미널이 pane 안으로 들어온 지금은(2026-09-18, 표면 레이어
  // 제거) 그냥 pane에서 focusin을 들으면 된다 — xterm의 숨은 textarea가 받는
  // 포커스가 여기까지 버블링된다.
  //
  // click이 아니라 focusin인 게 핵심이다. 클릭으로 바꾸면 뷰어 칸을 눌렀을 때
  // 활성 표시만 그쪽으로 가고 키 입력은 여전히 터미널로 들어간다 — 위 원칙이
  // 막으려던 바로 그 상태다. setActivePane은 이미 활성이면 아무것도 안 한다.
  paneEl.addEventListener('focusin', () => setActivePane(paneId));

  // L5: 레일 세션 행이나 다른 pane 헤더(아래)를 이 pane 위로 드래그하면
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

// C3 — "못 닿는 중" 자리표시자. 빈 pane(세션을 고르는 자리)과 **다르게** 보여야
// 한다: 여기는 이미 주인이 정해져 있고 그 주인을 기다리는 칸이다. 그래서
// 세션 선택 시트를 열지 않고, 호스트 상태를 다시 확인하는 「다시 시도」만 준다.
function _renderUnreachableBody(bodyEl, node) {
  bodyEl.classList.remove('vt-pane-viewer');
  delete bodyEl.dataset.viewerFile;
  if (bodyEl.dataset.unreachable === node.unreachable.tmux) return;
  bodyEl.dataset.unreachable = node.unreachable.tmux;
  bodyEl.replaceChildren();

  const box = document.createElement('div');
  box.className = 'vt-pane-unreachable';
  const title = document.createElement('div');
  title.className = 'vt-pane-unreachable-title';
  title.textContent = `${node.unreachable.host} · 연결 안 됨`;
  const sub = document.createElement('div');
  sub.className = 'vt-pane-unreachable-sub';
  sub.textContent = `${node.unreachable.tmux} — 호스트가 돌아오면 이 자리에 복구됩니다.`;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'vt-btn sm vt-pane-unreachable-retry';
  btn.textContent = '다시 시도';
  btn.addEventListener('click', () => _retryUnreachable(node, sub, btn));
  const pick = document.createElement('button');
  pick.type = 'button';
  pick.className = 'vt-btn sm';
  pick.textContent = '다른 세션 고르기';
  // 기다리지 않기로 결정할 수 있어야 한다 — 호스트를 영영 안 켤 수도 있다.
  pick.addEventListener('click', () => openPanePicker(node.id));

  box.appendChild(title);
  box.appendChild(sub);
  const row = document.createElement('div');
  row.className = 'vt-pane-unreachable-acts';
  row.appendChild(btn);
  row.appendChild(pick);
  box.appendChild(row);
  bodyEl.appendChild(box);
}

async function _retryUnreachable(node, subEl, btn) {
  btn.disabled = true;
  const prev = subEl.textContent;
  subEl.textContent = '확인 중…';
  try {
    const data = await vtFetch('/api/hosts?fresh=1');
    const host = (data?.hosts || []).find((h) => h.id === node.unreachable.host);
    if (!host) {
      subEl.textContent = '이 호스트는 더 이상 등록돼 있지 않습니다 — 다른 세션을 고르세요.';
    } else if (!host.online) {
      subEl.textContent = `${host.label} — 아직 연결되지 않습니다.`;
    } else if (typeof window.attachRemoteSession === 'function') {
      // 멀티호스트 3단계가 붙여 주는 경로. 없으면(지금) 아래 안내로 떨어진다.
      await window.attachRemoteSession(host.id, node.unreachable.tmux);
      return;
    } else {
      subEl.textContent = `${host.label}는 다시 켜졌습니다 — 원격 세션 열기는 준비 중입니다.`;
    }
  } catch (_) {
    subEl.textContent = prev;
  } finally {
    btn.disabled = false;
  }
}

// node(split 또는 leaf)를 그 자리에 있어야 할 DOM 엘리먼트로 렌더링해 반환한다.
// placement는 Map<sessionId, paneBodyEl> — leaf 분기가 실제로 세션을 보여주게
// 되는 pane마다 자신의 .vt-pane-body를 여기 채운다. surface.js는 이 지도만
// 보고 어느 wrapper를 어디로 옮길지 정한다(DOM 트리 자체는 안 건드린다).
function _renderNode(node, activePaneId, isRootOnly, placement) {
  if (node.t === 'split') {
    let el = document.getElementById(splitElId(node.id));
    let resizerEl;
    if (!el) {
      el = document.createElement('div');
      el.id = splitElId(node.id);
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
      // 더블클릭 = 이 분할만 반반으로. VS Code·iTerm의 관용구라 따로 배우지
      // 않아도 되고, UI를 하나도 안 늘린다. 트리 전체는 `paneEven` 액션
      // (기본 Mod+Alt+D · 팔레트 `:` 모드)이 맡는다.
      resizerEl.addEventListener('dblclick', (e) => {
        e.preventDefault();
        setRatio(node.id, 0.5);
      });
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
    if (el.children[0] !== aEl || el.children[1] !== resizerEl || el.children[2] !== bEl) {
      el.replaceChildren(aEl, resizerEl, bEl);
    }
    // 자식을 붙인 뒤에 재야 한다 — 그 전엔 el.clientWidth가 0이라 스냅이 포기한다.
    applySplitFlex(el, node, aEl, bEl);
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
  let paneEl = document.getElementById(paneElId(node.id));
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
  _paintChip(paneEl);

  // N35 §6 — 뷰어 칸: 세션이 아니라 파일 하나를 그린다. 표면 레이어에는 아무
  // 것도 안 넘긴다(placement에 안 들어가므로 xterm이 이 자리를 덮지 않는다).
  if (node.kind === 'viewer') {
    nameEl.textContent = (node.file ? node.file.split('/').pop() : '파일 없음') + labelSuffix;
    if (node.file) nameEl.setAttribute('data-tip', node.file); else nameEl.removeAttribute('data-tip');
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
  } else if (node.unreachable) {
    // C3 — 원격 호스트가 잠깐 꺼진 칸. 빈 pane으로 강등하지 않고 배치를 지킨다
    // (강등하면 호스트를 다시 켜도 배치가 영영 사라진다, layout/persist.js 참고).
    nameEl.textContent = `${node.unreachable.tmux} · ${node.unreachable.host}` + labelSuffix;
    nameEl.setAttribute('data-tip', `${node.unreachable.host}에 연결할 수 없습니다`);
    _renderUnreachableBody(bodyEl, node);
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
  const el = document.getElementById(splitElId(splitId));
  if (!node || node.t !== 'split' || !el || el.children.length !== 3) return;
  applySplitFlex(el, node, el.children[0], el.children[2]);
}

// pane 헤더가 차지하는 높이. 세로 분할에서 칸은 헤더 **아래**에 놓이므로, 첫째
// 칸이 배수가 되려면 `헤더 + k × cellHeight`여야 한다. 첫째 칸이 leaf가 아니면
// (중첩 분할) 그 안에서 각자 스냅하므로 여기서는 0으로 둔다.
function _headChrome(child) {
  if (!child || child.t !== 'leaf') return 0;
  const el = document.getElementById(paneElId(child.id));
  const head = el && el.querySelector('.vt-pane-head');
  return head && head.offsetParent !== null ? head.offsetHeight : 0;
}

/**
 * 분할의 두 칸에 flex를 건다. 셀 크기를 알고 상자가 이미 실측되면 **첫째 칸을
 * 셀 배수로 못박고**(`flex: 0 0 Npx`) 둘째 칸이 나머지를 먹는다(`flex: 1 1 0`).
 * 아직 못 재는 첫 렌더에서는 예전처럼 비율로 깔고, 레이아웃이 잡힌 다음 프레임에
 * 다시 불려 스냅된다(_renderFull의 rAF).
 *
 * 스냅이 idempotent라 되풀이해도 값이 안 바뀐다 — 그래서 ResizeObserver →
 * fit → 렌더로 돌아와도 루프가 생기지 않는다.
 */
export function applySplitFlex(el, node, aEl, bEl) {
  const horiz = node.dir === 'row';
  const total = horiz ? el.clientWidth : el.clientHeight;
  const cell = cellSizeFrom(Object.values(allSessions()));
  let aPx = null;
  if (total > 0 && cell) {
    // 분할선 1px은 어느 칸의 것도 아니다 — 빼고 나눈다.
    const want = (total - 1) * node.ratio;
    aPx = snapPx(want, horiz ? cell.w : cell.h, horiz ? 0 : _headChrome(node.a));
  }
  if (aPx == null) {
    aEl.style.flex = `${node.ratio} 1 0`;
    bEl.style.flex = `${1 - node.ratio} 1 0`;
  } else {
    aEl.style.flex = `0 0 ${aPx}px`;
    bEl.style.flex = '1 1 0';
  }
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

  // N35 §6 — 뷰어 칸이 하나라도 있으면 화면이 비어 있는 게 아니다. 온보딩은
  // position:fixed + z-index:500이라 페인 위를 덮으므로 CSS로 눌러둔다.
  // "열 때 remove()"에 기대지 않는 이유: 세션 목록 응답이 늦게 오는 경로가
  // 온보딩을 **다시** 붙인다(실브라우저에서 그대로 재현됐다).
  document.body.classList.toggle('vt-has-viewer-pane', leaves.some((l) => l.kind === 'viewer'));
  // 최종 배치를 넘긴다 — 빠진 세션은 대기실로 보내고, 새로 들어온 세션은
  // 그 pane-body 안으로 옮긴 뒤 즉시 실측해 fit을 건다(surface.js 책임).
  surface.setPlacement(placement);

  // 셀 배수 스냅은 **한 프레임 뒤에 한 번 더** 돈다. 위 렌더에서 상자가 처음
  // 생긴 분할은 그 순간 clientWidth가 0이라 스냅이 포기하고 비율로 깔렸고,
  // 세션이 방금 배치된 pane은 아직 fit 전이라 셀 크기를 모를 수도 있다.
  // 값이 이미 맞으면 아무 스타일도 안 바뀌므로 추가 리플로우가 없다.
  if (_snapRaf) cancelAnimationFrame(_snapRaf);
  _snapRaf = requestAnimationFrame(() => {
    _snapRaf = 0;
    _snapAll(getTree());
  });
}

let _snapRaf = 0;
function _snapAll(node) {
  if (!node || node.t !== 'split') return;
  const el = document.getElementById(splitElId(node.id));
  if (el && el.children.length === 3) applySplitFlex(el, node, el.children[0], el.children[2]);
  _snapAll(node.a);
  _snapAll(node.b);
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
