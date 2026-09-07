// L3 0단계 — 분할 pane 트리의 상태를 들고 있는 유일한 곳. layout/tree.js는
// 순수 함수만 있고 "지금 트리가 뭔지"는 모른다 — 그 상태를 여기서 보관하고,
// 바뀔 때마다 구독자에게 알린다(렌더러는 1단계에서 이 스토어를 구독한다).
//
// activePaneId는 클릭 핸들러가 직접 바꾸는 값이 아니다 — 각 pane의 xterm에
// 실제로 focus 이벤트가 뜰 때만 setActivePane()을 부르도록 설계한다(1단계
// 이후 xterm-setup.js가 연결). 그래야 "테두리는 A pane인데 타이핑은 B로
// 들어간다" 같은 상태 불일치가 애초에 생길 수 없다 — 착수 전 설계 리뷰에서
// 정리한 원칙(30-layout-shell.md L3) 중 하나.
import { activeSessionId } from '../core/store.js';
import { makeLeaf, splitPane as _splitPane, closePane as _closePane, setSession as _setSession, setRatio as _setRatio, countLeaves as _countLeaves, findNode } from './tree.js';

function _genId(prefix) {
  const rand = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `${prefix}-${rand}`;
}

// 기본값: leaf 하나, session=현재 활성 세션. 분할을 한 번도 안 만들면 이
// 트리는 영원히 이 leaf 하나뿐이라 — 오늘과 화면상 완전히 같은 상태다.
let _tree = makeLeaf(_genId('pane'), activeSessionId());
let _activePaneId = _tree.id;

const _listeners = new Set();
function _notify() {
  for (const fn of _listeners) fn(_tree, _activePaneId);
}

// onLayoutChange(fn) → unsubscribe 함수. 1단계의 layout/panes.js가 이걸로
// "트리든 activePaneId든 뭐가 바뀌면 다시 그린다"를 구현한다.
export function onLayoutChange(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

export function getTree() {
  return _tree;
}

export function getActivePaneId() {
  return _activePaneId;
}

// xterm의 focus 이벤트 핸들러가 부른다 — 그 외의 경로로 activePaneId를
// 바꾸지 않는다(파일 상단 주석 참고).
export function setActivePane(paneId) {
  if (paneId === _activePaneId || !findNode(_tree, paneId)) return;
  _activePaneId = paneId;
  _notify();
}

// 탭 클릭 등 "이 세션을 화면에 보여줘"의 유일한 진입점. 기본은 활성
// pane에 배정 — term/session.js의 switchTo()가 1단계에서 기존 hide/show
// 직접 조작 대신 이 함수를 부르도록 바뀐다.
export function setPaneSession(sessionId, paneId = _activePaneId) {
  _tree = _setSession(_tree, paneId, sessionId);
  _notify();
}

// paneId를 분할해 새 leaf를 만들고, 그 leaf를 활성 pane으로 바꾼다(방금
// 만든 자리에 바로 세션을 골라 넣기 편하도록). 새 pane id를 반환한다(실패
// 시 null — paneId가 이미 없어졌다든가 하는 방어 상황).
// L3 3단계: pane 헤더 버튼은 "그 헤더가 달린 pane"을 분할해야 한다 —
// 반드시 활성 pane과 같을 필요가 없어 paneId를 인자로 받는 일반형으로 뒀다.
// newFirst: L5 DnD 전용 — 왼쪽/위 가장자리 드롭이면 true로 넘겨 새 leaf가 그
// 자리(a)에 먼저 오게 한다(tree.js splitPane 참고). 헤더 버튼은 항상 기본값(false).
export function splitPane(paneId, dir, sessionId = null, newFirst = false) {
  const newLeaf = makeLeaf(_genId('pane'), sessionId);
  const next = _splitPane(_tree, paneId, dir, newLeaf, _genId('split'), newFirst);
  if (next === _tree) return null;
  _tree = next;
  _activePaneId = newLeaf.id;
  _notify();
  return newLeaf.id;
}

// 키맵(`Mod+D` 등, S3)처럼 "지금 활성 pane"을 대상으로 하는 짧은 표기.
export function splitActivePane(dir, sessionId = null) {
  return splitPane(_activePaneId, dir, sessionId);
}

// paneId를 닫는다. 닫힌 pane이 활성 pane이었으면(부모가 collapse되며
// 사라졌으므로) 남은 leaf 중 아무거나(트리 순회상 첫 번째)로 활성을 옮긴다.
export function closePane(paneId) {
  _tree = _closePane(_tree, paneId);
  if (!findNode(_tree, _activePaneId)) {
    _activePaneId = _firstLeafId(_tree);
  }
  _notify();
}

// L8 — 저장된 스냅샷으로 트리를 통째로 갈아끼운다(layout/persist.js 전용).
// 이 경로만 예외적으로 activePaneId를 직접 받는다: 복원 시점엔 아직 xterm이
// focus를 받은 적이 없어 파일 상단의 "focus 이벤트로만 바꾼다" 원칙을 적용할
// 대상 자체가 없다. 넘어온 id가 트리에 없으면 첫 leaf로 떨어뜨린다.
// 잘못된 입력(null 등)이면 아무것도 안 바꾸고 false를 반환한다.
export function replaceTree(tree, activePaneId = null) {
  if (!tree || (tree.t !== 'leaf' && tree.t !== 'split')) return false;
  _tree = tree;
  _activePaneId = (activePaneId && findNode(_tree, activePaneId)) ? activePaneId : _firstLeafId(_tree);
  _notify();
  return true;
}

export function setRatio(splitId, ratio) {
  _tree = _setRatio(_tree, splitId, ratio);
  _notify();
}

export function countLeaves() {
  return _countLeaves(_tree);
}

function _firstLeafId(node) {
  return node.t === 'leaf' ? node.id : _firstLeafId(node.a);
}
