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
import { makeLeaf, splitPane as _splitPane, closePane as _closePane, setSession as _setSession, setLeafViewer as _setLeafViewer, setRatio as _setRatio, evenRatios as _evenRatios, countLeaves as _countLeaves, findNode } from './tree.js';

function _genId(prefix) {
  const rand = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  return `${prefix}-${rand}`;
}

// 기본값: leaf 하나, session=현재 활성 세션. 분할을 한 번도 안 만들면 이
// 트리는 영원히 이 leaf 하나뿐이라 — 오늘과 화면상 완전히 같은 상태다.
// 10-shell-layout.md §4 2단계 — **탭 = 워크트리**. 여기서부터 화면에는 pane
// 트리가 하나가 아니라 **탭마다 하나씩** 있다. 그래도 기존 API(getTree /
// setPaneSession / splitPane …)는 전부 "활성 탭의 트리"로 그대로 동작한다 —
// 그게 이 층을 스토어 안에 넣은 이유다. 바깥에서 보면 달라진 게 없고, 탭을
// 아는 코드(탭 바·영속화)만 새 API를 쓴다.
//
// 탭 하나의 모양: `{ id, repoId, worktreeId, hostId, label, tree, activePaneId }`.
// 2.1 D4("저장소 1급화" 4단계) 전에는 `worktreeId`가 탭의 정체성이었다 — 저장소
// 하나에 워크트리가 둘이면 탭도 둘이었다. 이제 **`repoId`가 정체성**이고
// `worktreeId`는 "이 탭에서 지금 보고 있는 워크트리"라는 부가 정보로
// 내려간다(pane 헤더 옆 브랜치 칩이 이 값을 읽는다) — 같은 저장소의 두
// 워크트리를 각각 탭으로 열 수는 없다는 뜻이고(분할 pane을 쓰면 된다), 그
// 대가로 탭 개수가 "내가 지금 보는 저장소 수"와 일치한다.
// `repoId`가 null인 탭은 어떤 저장소에도 속하지 않는 작업 공간이다(레일의
// 「기타」와 같은 개념 — 워크트리가 0개인 환경에서도 화면이 성립한다).
// `hostId`는 2.2 원격 호스트 기반이다(지금은 항상 'local') — 다른 호스트의
// 같은 경로 저장소가 탭을 공유하지 않도록 미리 자리를 잡아 둔다.
function _makeTab({ repoId = null, worktreeId = null, hostId = 'local', label = '작업 공간', tree = null } = {}) {
  const t = tree || makeLeaf(_genId('pane'), activeSessionId());
  return { id: _genId('tab'), repoId, worktreeId, hostId, label, tree: t, activePaneId: _firstLeafId(t) };
}

const _tabs = [_makeTab()];
let _activeTabIndex = 0;

// 활성 탭의 트리/활성 pane을 기존 이름 그대로 노출하기 위한 얇은 별칭.
// **이 두 값을 직접 대입하는 코드는 전부 아래 _sync()를 거친다** — 탭 안의
// 사본과 밖의 사본이 갈라지면 "분할했는데 탭을 바꿨다 오면 사라진다"가 된다.
let _tree = _tabs[0].tree;
let _activePaneId = _tabs[0].activePaneId;

function _sync() {
  _tabs[_activeTabIndex].tree = _tree;
  _tabs[_activeTabIndex].activePaneId = _activePaneId;
}

const _listeners = new Set();
// N16 — kind로 무엇이 바뀌었는지 구분해 넘긴다: 'active'(활성 pane만) ·
// 'ratio'(분할 비율, extra=splitId) · 'layout'(트리 구조 자체, 기본값).
// 구독자가 kind를 안 받아도(기존 283건 계약) 동작은 그대로다 — 인자를
// 늘렸을 뿐 기존 두 인자(tree, activePaneId)는 자리·의미가 안 바뀐다.
function _notify(kind = 'layout', extra) {
  _sync();
  for (const fn of _listeners) fn(_tree, _activePaneId, kind, extra);
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
  _notify('active');
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

// N35 §6 — 파일을 **페인으로** 연다(모달 코드 뷰어를 대체한 경로).
// 규칙: 활성 pane이 비어 있거나 이미 뷰어면 그 자리에 연다. 터미널이
// 들어 있으면 오른쪽으로 쪼갠다 — 보고 있던 터미널을 파일이 덮어버리면
// "잠깐 열어보려던" 것이 작업을 끊는다. 쪼개기가 실패하면(트리에 없는 pane 등)
// 활성 pane을 그대로 쓴다. 연 pane id를 반환한다.
//
// pane 상한(dnd.js canSplit)은 여기서 안 본다 — dnd.js가 이 모듈을 import
// 하므로 반대 방향 import는 순환이 된다. 상한은 사용자가 직접 분할할 때의
// 화면 규칙이다.
export function openViewerPane(file, paneId = _activePaneId) {
  const node = findNode(_tree, paneId);
  let target = paneId;
  if (node && node.t === 'leaf' && node.session && node.kind !== 'viewer') {
    const split = splitPane(paneId, 'row');
    if (split) target = split;
  }
  _tree = _setLeafViewer(_tree, target, file);
  _activePaneId = target;
  _notify();
  return target;
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
  _notify('ratio', splitId);
}

/**
 * 모든 분할을 반반으로 되돌린다. 바뀐 게 없으면 알리지 않는다.
 * 'ratio'가 아니라 'layout'으로 알리는 이유: 'ratio'의 경량 경로는
 * (panes.js `_applyRatioOnly`) **분할 하나**의 flex만 고치도록 되어 있어서,
 * 트리 전체가 바뀐 이 경우를 표현할 수 없다.
 */
export function evenRatios() {
  const next = _evenRatios(_tree);
  if (next === _tree) return false;
  _tree = next;
  _notify('layout');
  return true;
}

export function countLeaves() {
  return _countLeaves(_tree);
}

function _firstLeafId(node) {
  return node.t === 'leaf' ? node.id : _firstLeafId(node.a);
}

// ── 탭(작업 공간) API — 10 §4 2단계 ────────────────────────────────────────
// 탭을 아는 코드는 탭 바(layout/tabbar.js)와 영속화(layout/persist.js) 둘뿐이다.

/** 읽기 전용 사본 — 바깥에서 tree를 직접 바꾸지 못하게 얕은 복사로 준다. */
export function getTabs() {
  return _tabs.map((t) => ({ id: t.id, repoId: t.repoId, worktreeId: t.worktreeId, hostId: t.hostId, label: t.label }));
}

export function getActiveTabId() {
  return _tabs[_activeTabIndex].id;
}

/** 저장소 id로 이미 열린 탭 찾기 — 같은 저장소를 두 번 열지 않는다(D4:
 * 탭의 정체성은 이제 워크트리가 아니라 저장소다). */
export function findTabByRepo(repoId) {
  if (!repoId) return null;
  const t = _tabs.find((x) => x.repoId === repoId);
  return t ? t.id : null;
}

/** 이 탭에서 지금 보는 워크트리를 바꾼다(같은 저장소의 다른 워크트리로
 * 이동할 때 — 탭은 그대로, "지금 보는 브랜치"만 갱신). */
export function setTabWorktree(tabId, worktreeId) {
  const t = _tabs.find((x) => x.id === tabId);
  if (!t) return false;
  t.worktreeId = worktreeId || null;
  _notify();
  return true;
}

export function switchLayoutTab(tabId) {
  const i = _tabs.findIndex((t) => t.id === tabId);
  if (i < 0 || i === _activeTabIndex) return false;
  _sync();                       // 떠나는 탭의 현재 트리를 먼저 굳힌다
  _activeTabIndex = i;
  _tree = _tabs[i].tree;
  _activePaneId = _tabs[i].activePaneId;
  _notify();
  return true;
}

/** 새 탭. 같은 저장소 탭이 이미 있으면 전환만 하고, 다른 워크트리를 보는
 * 중이었다면 그 탭의 `worktreeId`를 지금 연 워크트리로 갱신한다(D4). */
export function openLayoutTab({ repoId = null, worktreeId = null, hostId = 'local', label = '작업 공간', sessionId = null } = {}) {
  const existing = findTabByRepo(repoId);
  if (existing) {
    switchLayoutTab(existing);
    if (worktreeId) setTabWorktree(existing, worktreeId);
    return existing;
  }
  _sync();
  const tab = _makeTab({ repoId, worktreeId, hostId, label });
  if (sessionId) tab.tree = _setSession(tab.tree, tab.tree.id, sessionId);
  _tabs.push(tab);
  _activeTabIndex = _tabs.length - 1;
  _tree = tab.tree;
  _activePaneId = tab.activePaneId;
  _notify();
  return tab.id;
}

/** 탭을 닫는다. **마지막 한 개는 닫지 않는다** — 탭이 0개면 화면에 pane
 * 트리가 존재하지 않게 되어 렌더러가 그릴 대상을 잃는다. */
export function closeLayoutTab(tabId) {
  if (_tabs.length <= 1) return false;
  const i = _tabs.findIndex((t) => t.id === tabId);
  if (i < 0) return false;
  _tabs.splice(i, 1);
  if (_activeTabIndex >= _tabs.length) _activeTabIndex = _tabs.length - 1;
  else if (i < _activeTabIndex) _activeTabIndex -= 1;
  _tree = _tabs[_activeTabIndex].tree;
  _activePaneId = _tabs[_activeTabIndex].activePaneId;
  _notify();
  return true;
}

export function setTabLabel(tabId, label) {
  const t = _tabs.find((x) => x.id === tabId);
  if (!t || !label) return false;
  t.label = String(label);
  _notify();
  return true;
}

/** L8 영속화 전용 — 탭 전체를 스냅샷으로 갈아끼운다. */
export function replaceTabs(tabs, activeTabId = null) {
  if (!Array.isArray(tabs) || tabs.length === 0) return false;
  const clean = tabs.filter((t) => t && t.tree && (t.tree.t === 'leaf' || t.tree.t === 'split'));
  if (!clean.length) return false;
  _tabs.length = 0;
  for (const t of clean) {
    _tabs.push({
      id: t.id || _genId('tab'),
      repoId: t.repoId || null,
      worktreeId: t.worktreeId || null,
      hostId: t.hostId || 'local',
      label: t.label || '작업 공간',
      tree: t.tree,
      activePaneId: (t.activePaneId && findNode(t.tree, t.activePaneId)) ? t.activePaneId : _firstLeafId(t.tree),
    });
  }
  const i = _tabs.findIndex((t) => t.id === activeTabId);
  _activeTabIndex = i >= 0 ? i : 0;
  _tree = _tabs[_activeTabIndex].tree;
  _activePaneId = _tabs[_activeTabIndex].activePaneId;
  _notify();
  return true;
}

/** 영속화가 읽는 전체 상태(트리 포함). getTabs()와 달리 tree를 그대로 준다. */
export function getTabsWithTrees() {
  _sync();
  return _tabs.map((t) => ({ ...t }));
}

// N36(워크트리 레일) — shell/Rail.tsx는 지연 청크라 이 모듈을 직접 import하면
// 트리 상태가 복제된다(ADR-26/N35 커밋의 core/store.js와 같은 이유). ⌘클릭
// 분할에 필요한 splitActivePane만 다른 파일들처럼 window로 노출한다.
window.splitActivePane = splitActivePane;
