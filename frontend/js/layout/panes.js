// L3 1단계 — 트리를 DOM으로 그리는 재귀 렌더러. split은 flex 컨테이너, leaf는
// **기존 세션 wrapper를 appendChild로 옮기기만 한다(재생성 금지)** — ADR-1
// 그대로. 구조용 div(.vt-split/.vt-pane)는 매 렌더마다 새로 만들어도 무해하다
// (진짜 상태를 담은 건 wrapper 하나뿐이다).
//
// 트리에 없는(=화면 밖 배경 탭) 세션의 wrapper는 #terminal-pool(숨김)에
// 대기시킨다 — 예전엔 모든 wrapper가 #terminal-container의 형제로 나란히
// 있으면서 하나만 display:block이었는데, 이제 "화면에 실제로 배치된 것"과
// "탭에는 있지만 어디에도 안 보이는 것"을 컨테이너 자체로 구분한다.
//
// 아직(1단계) 분할 UI가 없어 트리는 항상 leaf 하나뿐이다 — 그 경우 이 렌더러가
// 만드는 결과는 "#terminal-container 안에 .vt-pane 하나, 그 안에 활성 세션의
// wrapper 하나"로, 화면상 예전(래퍼가 #terminal-container 직계 자식이던 시절)과
// 동일하다(레이아웃에 영향 없는 한 겹의 div만 추가됨).
import { getSession, allSessions } from '../core/store.js';
import { getTree, getActivePaneId, onLayoutChange } from './store.js';
import { fitAndResize } from '../term/resize.js';

let _rootEl = null;
let _poolEl = null;

function _ensureContainers() {
  if (!_rootEl) _rootEl = document.getElementById('terminal-container');
  if (!_poolEl) {
    _poolEl = document.getElementById('terminal-pool');
    if (!_poolEl) {
      _poolEl = document.createElement('div');
      _poolEl.id = 'terminal-pool';
      // display:none인 조상 밑에서는 xterm이 컨테이너 크기를 0으로 측정해
      // fit()이 깨진다 — 하지만 여기 들어간 wrapper에는 애초에 fitAndResize를
      // 안 부르니(트리 밖이라 usedSessionIds에 없음) 문제되지 않는다.
      _poolEl.style.cssText = 'display:none;';
      document.body.appendChild(_poolEl);
    }
  }
  return _rootEl != null;
}

// node(split 또는 leaf)를 그 자리에 있어야 할 DOM 엘리먼트로 렌더링해 반환한다.
// usedSessionIds에는 이번 렌더에서 실제로 화면에 배치된 세션 id를 모은다(풀로
// 보낼 대상을 가려내기 위해 + 렌더 끝나고 fitAndResize를 부를 대상이기도 하다).
function _renderNode(node, activePaneId, usedSessionIds) {
  if (node.t === 'split') {
    let el = document.getElementById(`vt-split-${node.id}`);
    if (!el) {
      el = document.createElement('div');
      el.id = `vt-split-${node.id}`;
      el.className = 'vt-split';
    }
    el.style.flexDirection = node.dir === 'row' ? 'row' : 'column';
    const aEl = _renderNode(node.a, activePaneId, usedSessionIds);
    const bEl = _renderNode(node.b, activePaneId, usedSessionIds);
    aEl.style.flex = `${node.ratio} 1 0`;
    bEl.style.flex = `${1 - node.ratio} 1 0`;
    if (el.children[0] !== aEl || el.children[1] !== bEl) el.replaceChildren(aEl, bEl);
    return el;
  }

  // leaf
  let paneEl = document.getElementById(`vt-pane-${node.id}`);
  if (!paneEl) {
    paneEl = document.createElement('div');
    paneEl.id = `vt-pane-${node.id}`;
    paneEl.className = 'vt-pane';
  }
  paneEl.classList.toggle('active', node.id === activePaneId);
  if (node.session) {
    const s = getSession(node.session);
    if (s && s.wrapper) {
      usedSessionIds.add(node.session);
      if (s.wrapper.parentElement !== paneEl) paneEl.appendChild(s.wrapper);
      s.wrapper.style.display = 'block';
    }
  }
  return paneEl;
}

// 트리를 다시 그린다. layout/store.js의 onLayoutChange가 이 함수를 부른다 —
// term/session.js의 switchTo() 등은 스토어만 갱신하고, 실제 DOM 반영은 항상
// 이 한 곳을 거친다(직접 wrapper를 만지지 않는다).
export function renderLayout() {
  if (!_ensureContainers()) return; // #terminal-container가 아직 없으면(부팅 전) 대기
  const tree = getTree();
  const activePaneId = getActivePaneId();
  const usedSessionIds = new Set();

  const rootEl = _renderNode(tree, activePaneId, usedSessionIds);
  if (_rootEl.children[0] !== rootEl) _rootEl.replaceChildren(rootEl);

  // 트리에 없는 세션(배경 탭) wrapper는 풀로 — 재부착이 아니라 "이미 거기
  // 있으면 건드리지 않는다"라 매 렌더마다 강제 재배치하지 않는다.
  for (const [id, s] of Object.entries(allSessions())) {
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
