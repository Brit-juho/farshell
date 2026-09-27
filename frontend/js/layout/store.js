// L3 0단계 — 분할 pane 트리의 상태를 들고 있는 유일한 곳. layout/tree.js는
// 순수 함수만 있고 "지금 트리가 뭔지"는 모른다 — 그 상태를 여기서 보관하고,
// 바뀔 때마다 구독자에게 알린다(렌더러는 1단계에서 이 스토어를 구독한다).
//
// activePaneId는 클릭 핸들러가 직접 바꾸는 값이 아니다 — 각 pane의 xterm에
// 실제로 focus 이벤트가 뜰 때만 setActivePane()을 부르도록 설계한다(1단계
// 이후 xterm-setup.js가 연결). 그래야 "테두리는 A pane인데 타이핑은 B로
// 들어간다" 같은 상태 불일치가 애초에 생길 수 없다 — 착수 전 설계 리뷰에서
// 정리한 원칙(30-layout-shell.md L3) 중 하나.
import { activeSessionId, sessionDisplayName } from '../core/store.js';
import { makeLeaf, splitPane as _splitPane, closePane as _closePane, setSession as _setSession, setLeafViewer as _setLeafViewer, setLeafPanel as _setLeafPanel, setRatio as _setRatio, evenRatios as _evenRatios, countLeaves as _countLeaves, findNode, findPaneBySession, collectSessions } from './tree.js';

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
// 탭 하나의 모양: `{ id, worktreeId, hostId, label, tree, activePaneId }`.
// 2.1 D4("저장소 1급화" 4단계)는 `repoId`를, ADR-29 D(2026-09-18)는 `groupId`
// (`@fsh_grp` 태그)를 탭의 정체성으로 삼았었다. **2026-09-18 후속(그룹
// 재정의, 사용자 요청)이 그 필드 자체를 없앤다** — "그룹"은 더 이상 탭에
// 붙는 태그가 아니라 **그 탭의 pane 트리에 지금 떠 있는 세션이 몇 개인가**로
// 매 순간 계산된다(2개 이상이면 그룹, 1개면 개인 — layout/tabbar.js의
// `tabMemberSessionIds`, `tabGroups()` 아래). 탭은 그저 세션들이 같은
// 화면(pane 트리)을 공유하는 자리일 뿐, 별도 id를 가질 이유가 없다.
// `worktreeId`는 여전히 "이 탭에서 지금 보고 있는 워크트리"라는 부가
// 정보로 내려간다(pane 헤더 옆 브랜치 칩이 이 값을 읽는다).
// `label`은 사용자가 직접 지은 이름만 담는다(없으면 null — layout/
// tabbar.js의 `tabDisplayLabel`이 그 자리에 현재 구성원 이름을 계산해
// 채운다. "작업 공간" 같은 무의미한 기본값을 붙이지 않는다).
// `hostId`는 2.2 원격 호스트 기반이다(지금은 항상 'local').
function _makeTab({ worktreeId = null, hostId = 'local', label = null, tree = null } = {}) {
  const t = tree || makeLeaf(_genId('pane'), activeSessionId());
  return { id: _genId('tab'), worktreeId, hostId, label, tree: t, activePaneId: _firstLeafId(t) };
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

// 2026-09-21 버그 수정 — 같은 세션이 두 탭에 동시에 떠 있던 문제. tree.js의
// setSession()은 **같은 트리 안**의 중복만 지운다(파일 111행 주석). 세션
// 행을 레일에서 다른 탭의 pane으로 끌어다 놓아 그룹을 만들면(dnd.js) 그
// 세션의 원래(혼자였던) 탭은 전혀 안 건드려져서, 같은 세션이 옛 탭과 새
// 그룹 탭 양쪽에 동시에 남았다 — 옛 탭이 "그룹을 만들어도 위에 그대로
// 떠 있다"로 보였고, findTabBySessionId가 배열에서 먼저 만나는 탭(대개
// 옛 탭)을 돌려줘서 레일에서 그 세션을 클릭하면 그룹이 아니라 옛 솔로
// 탭으로 가버렸다("그룹에서 떨어져 나가는 것처럼" 보인 증상).
//
// 활성 탭이 아닌 다른 탭에 이 세션이 남아 있으면 거기서 지운다. 그 탭에
// 남은 세션이 하나도 없어지면(원래 혼자였던 탭) 탭 자체를 닫는다 — 그
// 탭도 그룹이었다면(다른 멤버가 남아있다면) 그 자리만 비우고 탭은 그대로
// 둔다(빈 pane은 이미 다른 곳에서도 쓰는 정상적인 자리표시자 상태다).
function _detachFromOtherTabs(sessionId) {
  const activeId = _tabs[_activeTabIndex].id;
  for (let i = _tabs.length - 1; i >= 0; i--) {
    const t = _tabs[i];
    if (t.id === activeId) continue;
    if (!collectSessions(t.tree).includes(sessionId)) continue;
    const cleared = _setSession(t.tree, null, sessionId);
    if (collectSessions(cleared).length === 0) {
      _tabs.splice(i, 1);
      if (i < _activeTabIndex) _activeTabIndex -= 1;
    } else {
      t.tree = cleared;
    }
  }
}

// 탭 클릭 등 "이 세션을 화면에 보여줘"의 유일한 진입점. 기본은 활성
// pane에 배정 — term/session.js의 switchTo()가 1단계에서 기존 hide/show
// 직접 조작 대신 이 함수를 부르도록 바뀐다.
export function setPaneSession(sessionId, paneId = _activePaneId) {
  if (sessionId) _detachFromOtherTabs(sessionId);
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

// N4 — 패널을 pane 칸에 연다. `openViewerPane`과 **같은 규칙**이다: 지정한
// 칸이 이미 세션을 들고 있으면 덮지 않고 옆으로 쪼갠다(사용자가 보고 있던
// 터미널을 말없이 지우면 안 된다). 빈 칸·뷰어 칸·다른 패널 칸이면 그 자리를
// 그대로 쓴다.
export function openPanelPane(panel, paneId = _activePaneId) {
  const node = findNode(_tree, paneId);
  let target = paneId;
  if (node && node.t === 'leaf' && node.session && node.kind === 'terminal') {
    const split = splitPane(paneId, 'row');
    if (split) target = split;
  }
  _tree = _setLeafPanel(_tree, target, panel);
  _activePaneId = target;
  _notify();
  return target;
}

// N4 3단계 — 지정한 칸을 **그대로** 패널 칸으로 만든다(쪼개지 않는다).
// `openPanelPane`과 갈라놓은 이유: 그쪽의 "터미널이 들어 있으면 옆으로
// 쪼갠다"는 휴리스틱은 **사용자가 칸을 고르지 않았을 때**의 보험이다
// (팔레트·액션은 활성 pane을 기본값으로 쓸 뿐이다). dock 탭을 끌어다
// 떨어뜨린 경우는 사용자가 그 칸을 직접 겨냥한 것이라 보험이 오히려
// 방해가 된다 — 겨냥한 자리가 아닌 옆 칸에 열리기 때문이다. 5구역 중
// 가장자리를 고르면 분할이 되는데, 중앙까지 분할이면 center와 edge가
// 같은 결과가 되어 구역 판정이 의미를 잃는다.
// 세션은 tree.js `setLeafPanel`이 비운다 — 한 칸이 터미널이면서 동시에
// 패널일 수는 없다. 세션 자체는 죽지 않고 탭·레일에 그대로 살아 있다.
export function setPanePanel(panel, paneId = _activePaneId) {
  const node = findNode(_tree, paneId);
  if (!node || node.t !== 'leaf') return false;
  _tree = _setLeafPanel(_tree, paneId, panel);
  _activePaneId = paneId;
  _notify();
  return true;
}

// 키맵(`Mod+D` 등, S3)처럼 "지금 활성 pane"을 대상으로 하는 짧은 표기.
// sessionId가 있으면(Rail.tsx의 ⌘클릭 분할) **빈 leaf로 먼저 나누고 나서
// setPaneSession으로 배정한다** — dnd.js와 같은 두 단계 관용구다(dnd.js
// 88행 주석 참고). splitPane이 sessionId를 바로 makeLeaf에 넣는 경로는
// dedup을 안 거쳐서, 이미 다른 탭에 떠 있는 세션을 ⌘클릭으로 분할하면
// 두 탭에 동시에 남는 같은 버그가 났다.
export function splitActivePane(dir, sessionId = null) {
  const paneId = splitPane(_activePaneId, dir, null);
  if (paneId && sessionId) setPaneSession(sessionId, paneId);
  return paneId;
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
  return _tabs.map((t) => ({ id: t.id, worktreeId: t.worktreeId, hostId: t.hostId, label: t.label }));
}

export function getActiveTabId() {
  return _tabs[_activeTabIndex].id;
}

/** 활성 탭이 속한 호스트 — 활성 칸이 비었을 때 "지금 어느 호스트를 보나"의
 * 답이다(term/host-view.js). 2.2까지는 늘 'local'이던 자리. */
export function getActiveTabHostId() {
  return _tabs[_activeTabIndex].hostId || 'local';
}

// 세션·자리표시자·뷰어·패널이 하나도 없는 트리 — "그냥 비어 있는 화면".
function _isBlankTree(tree) {
  if (!tree) return true;
  if (tree.t === 'leaf') {
    return !tree.session && !tree.unreachable && (tree.kind || 'terminal') === 'terminal';
  }
  return _isBlankTree(tree.a) && _isBlankTree(tree.b);
}

/** 호스트 스위처(term/remote.js `focusHost`)가 재사용할 빈 탭을 찾을 때 쓰는 요약. */
export function getTabHostSummaries() {
  _sync();
  return _tabs.map((t) => ({ id: t.id, hostId: t.hostId || 'local', empty: _isBlankTree(t.tree) }));
}

/** 이 세션이 지금 어느 탭의 pane 트리에 떠 있는가 — 없으면 null(아직 어느
 * 화면에도 없다: 처음 여는 세션, 또는 방금 깨운 세션). 2026-09-18 후속
 * (그룹 재정의)의 핵심 조회 — "같은 화면에 이미 떠 있으면 그 화면으로,
 * 아니면 새 화면으로"를 여기 하나로 판정한다. */
export function findTabBySessionId(sessionId) {
  if (!sessionId) return null;
  _sync();
  for (const t of _tabs) {
    if (collectSessions(t.tree).includes(sessionId)) return t.id;
  }
  return null;
}

// 2026-09-21(사용자 지적) — "레일에서 세션을 클릭하면 그 화면으로 간다"의
// **이동** 버전. `openLayoutTab`+`switchTo` 조합은 탭까지는 맞게 옮겨도,
// switchTo가 실은 `setPaneSession`(활성 pane에 배정)이라 그 탭의 기억된
// 활성 pane이 **짝 세션의 칸**이면 짝을 밀어내고 자기 자리를 차지했다 —
// 화면은 분할된 채인데 짝이 사라지는 증상이 그래서 났다. 이 함수는 트리를
// **전혀 바꾸지 않는다**: 탭을 전환하고, 그 세션이 이미 들어 있는 pane을
// 활성 pane으로 삼을 뿐이다. 어느 탭에도 없으면 false — 호출자
// (term/session.js `revealSession`)가 `openLayoutTab`으로 새 탭을 연다.
// switchTo로 그냥 떨어지지 않는 이유는 지금 활성 pane 자체가 다른 그룹의
// 칸일 수 있어서다(그러면 이 함수가 막으려던 것과 같은 사고가 난다).
export function revealSessionPane(sessionId) {
  if (!sessionId) return false;
  _sync();
  for (let i = 0; i < _tabs.length; i++) {
    const paneId = findPaneBySession(_tabs[i].tree, sessionId);
    if (!paneId) continue;
    // 탭을 바꿨으면(다른 트리를 그려야 한다) 기본 kind('layout')로 알려
    // panes.js가 DOM을 통째로 다시 그리게 한다 — 'active'는 지금 그려진
    // DOM에 `.active` 클래스만 옮기는 경량 경로라, 다른 탭의 트리에는
    // 애초에 대응하는 pane 엘리먼트가 없다(같은 탭 안에서 활성 pane만
    // 바뀔 때 쓰는 경로, xterm-setup.js의 focusin이 그 경우다).
    const switchedTab = i !== _activeTabIndex;
    if (switchedTab) {
      _activeTabIndex = i;
      _tree = _tabs[i].tree;
    }
    _activePaneId = paneId;
    _notify(switchedTab ? 'layout' : 'active');
    return true;
  }
  return false;
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

/** 세션 하나를 화면에 연다. 그 세션이 이미 어느 탭의 pane 트리에 떠
 * 있으면(다른 세션과 화면을 공유 중 = 그룹) 전환만 하고, 다른 워크트리를
 * 보는 중이었다면 그 탭의 `worktreeId`를 지금 연 워크트리로 갱신한다.
 * 2026-09-18 후속 — `groupId` 대신 `sessionId`로 찾는다(그룹은 이제 탭에
 * 붙는 태그가 아니라 pane 트리 구성원 자체다, `findTabBySessionId`). */
export function openLayoutTab({ worktreeId = null, hostId = 'local', label = null, sessionId = null } = {}) {
  const existing = findTabBySessionId(sessionId);
  if (existing) {
    switchLayoutTab(existing);
    if (worktreeId) setTabWorktree(existing, worktreeId);
    return existing;
  }
  _sync();
  // 세션 없이 여는 **다른 호스트의** 탭은 정말로 비어 있어야 한다. `_makeTab`의
  // 기본 트리는 지금 활성 세션을 담아 시작하므로(탭 하나뿐인 부팅 상태를 위한
  // 기본값), 그대로 두면 호스트 스위처가 연 "M5 빈 탭"에 로컬 세션이 들어가
  // 활성 칸이 다시 로컬이 된다(2026-09-28 실브라우저에서 확인).
  const blank = !sessionId && hostId !== 'local' ? makeLeaf(_genId('pane'), null) : null;
  const tab = _makeTab({ worktreeId, hostId, label, tree: blank });
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

/** 탭 순서를 바꾼다(헤더 탭 바 드래그 재정렬, 데스크톱 전용 — layout/tabbar.js).
 * toIndex는 **이동 후** 그 탭이 있어야 할 위치. 활성 탭은 id로 다시 찾으므로
 * 순서가 바뀌어도 활성 탭 자체는 안 바뀐다. 탭이 하나거나 범위 밖이면 false. */
export function moveLayoutTab(tabId, toIndex) {
  const from = _tabs.findIndex((t) => t.id === tabId);
  if (from < 0 || _tabs.length < 2) return false;
  const activeId = _tabs[_activeTabIndex].id;
  const clamped = Math.max(0, Math.min(toIndex, _tabs.length - 1));
  if (clamped === from) return false;
  const [moved] = _tabs.splice(from, 1);
  _tabs.splice(clamped, 0, moved);
  _activeTabIndex = _tabs.findIndex((t) => t.id === activeId);
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
      worktreeId: t.worktreeId || null,
      hostId: t.hostId || 'local',
      label: t.label || null,
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

/** 표시용 탭 이름 — 사용자가 직접 지었으면(`tab.label`) 그대로, 아니면
 * 지금 그 탭에 떠 있는 세션(들)의 이름으로 계산한다. "작업 공간" 같은
 * 무의미한 기본값을 없앤다(사용자 요청 — 헤더는 항상 실제 개인/그룹
 * 이름을 보여줘야 한다). layout/tabbar.js와 아래 window 브리지(Rail.tsx가
 * 그룹 섹션 라벨로 쓴다)가 같은 답을 내도록 여기 한 곳에만 둔다. */
export function tabDisplayLabel(tab) {
  if (tab.label) return tab.label;
  const ids = collectSessions(tab.tree);
  if (ids.length === 0) return '빈 화면';
  if (ids.length === 1) return sessionDisplayName(ids[0]);
  return ids.map((id) => sessionDisplayName(id)).join(' + ');
}

/** 지금 화면에서 실시간으로 계산한 그룹 목록 — 2026-09-18 후속(그룹
 * 재정의, 사용자 요청: "같은 화면에 묶인 것만 그룹, 그 외는 전부 개인").
 * 한 탭의 pane 트리에 서로 다른 세션이 2개 이상 떠 있으면 그룹이다. 탭
 * id를 그룹 id로 그대로 쓴다 — 그룹은 탭과 별개의 정체성을 가질 이유가
 * 없다(탭이 곧 "같은 화면"이므로). */
export function tabGroups() {
  _sync();
  return _tabs
    .map((t) => ({ id: t.id, label: tabDisplayLabel(t), sessionIds: collectSessions(t.tree) }))
    .filter((g) => g.sessionIds.length >= 2);
}

// N36(워크트리 레일) — shell/Rail.tsx는 지연 청크라 이 모듈을 직접 import하면
// 트리 상태가 복제된다(ADR-26/N35 커밋의 core/store.js와 같은 이유). ⌘클릭
// 분할에 필요한 splitActivePane와, 레일이 그룹을 그리는 데 필요한 tabGroups만
// 다른 파일들처럼 window로 노출한다.
window.splitActivePane = splitActivePane;
// N4 — 팔레트·빈 pane 시트가 지연 청크라서 이 모듈을 정적 import 할 수
// 없다(ADR-26). `openFileInPane`이 같은 이유로 같은 브리지를 쓴다.
window.openPanelPane = openPanelPane;
window.getTabGroups = tabGroups;
// Rail.tsx가 화면(pane 트리)이 바뀔 때마다 그룹을 다시 계산하려면 이
// 이벤트가 필요하다 — 세션 스토어 구독(storeSubscribe)만으로는 "이미 있는
// 세션을 다른 pane으로 옮겼다" 같은 순수 레이아웃 변경을 못 잡는다.
window.onLayoutChange = onLayoutChange;
