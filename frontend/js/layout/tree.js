// L3 0단계 — 분할 pane 레이아웃의 자료구조. 순수 함수만 둔다(DOM·세션 스토어
// 어느 것도 모른다) — 30-layout-shell.md §6이 이진 트리를 고른 이유가 그대로
// 적용된다: pane을 닫을 때 부모 split을 살아남은 형제로 교체하면 끝이라
// 트리 정리가 한 줄이고, 그래서 이 파일의 모든 함수도 "새 트리를 반환"으로
// 끝날 수 있다(입력 트리는 절대 변형하지 않는다 — 참조 비교로 테스트하기
// 쉽게 하려는 것도 있지만, layout/store.js가 구독자에게 "바뀐 트리"를
// 알릴 때 참조가 실제로 달라야 하기 때문이기도 하다).
//
// id는 이 파일이 생성하지 않는다 — 순수성을 지키기 위해 호출자(layout/store.js)가
// 만들어 넘긴다. 그래야 이 파일은 진짜 결정론적 순수 함수만 남고, 단위테스트가
// "같은 입력 → 같은 출력"을 그대로 비교할 수 있다.

// leaf: 화면에 실제로 배치되는 한 칸. session이 null이면 빈 pane(세션 선택 시트로 이어짐).
// worktree/host는 ADR-10이 미리 뚫어둔 확장 자리 — 2.0에서는 항상 이 값(null/'local')이다.
export function makeLeaf(id, session = null) {
  return { t: 'leaf', id, session, worktree: null, host: 'local' };
}

// split: leaf 두 개(또는 다른 split을 포함한 서브트리) 사이의 구분선.
// dir이 wide/regular에서는 실제 배치 방향, compact 렌더 모드에서는 스와이프
// 순서 힌트로 재해석된다(30-layout-shell.md L3 착수 전 설계 리뷰 참고) — 그
// 재해석은 layout/panes.js(렌더러)의 몫이고, 이 파일은 dir 값을 그대로 보관만 한다.
export function makeSplit(id, dir, a, b, ratio = 0.5) {
  return { t: 'split', id, dir, a, b, ratio };
}

// 트리에서 id로 노드를 찾되, 부모 노드와 그 노드가 부모의 'a'/'b' 중 어느
// 쪽인지도 함께 반환한다 — closePane(형제로 교체)에 필요한 정보라 탐색을
// 한 번만 하려고 이렇게 묶었다.
function _findWithParent(node, id, parent = null, key = null) {
  if (node.id === id) return { node, parent, key };
  if (node.t === 'split') {
    return _findWithParent(node.a, id, node, 'a') || _findWithParent(node.b, id, node, 'b');
  }
  return null;
}

export function findNode(tree, id) {
  const found = _findWithParent(tree, id);
  return found ? found.node : null;
}

export function countLeaves(tree) {
  return tree.t === 'leaf' ? 1 : countLeaves(tree.a) + countLeaves(tree.b);
}

// targetId를 가진 노드를 replacement로 통째로 바꾼 새 트리를 반환한다.
// targetId가 트리에 없으면 원본과 구조적으로 동일한(그러나 새로 만들어진) 트리를 반환한다.
function _replace(node, targetId, replacement) {
  if (node.id === targetId) return replacement;
  if (node.t === 'leaf') return node;
  return { ...node, a: _replace(node.a, targetId, replacement), b: _replace(node.b, targetId, replacement) };
}

// paneId가 가리키는 leaf를 split으로 바꾼다. 기본은 a=기존 leaf, b=newLeaf(헤더의
// "오른쪽/아래쪽 분할" 버튼이 쓰는 순서). newFirst=true면 순서를 뒤집어 새 leaf가
// a(왼쪽/위쪽)로 간다 — L5 DnD의 5구역 드롭존에서 "왼쪽/위 가장자리에 놓으면 그
// 방향으로, 놓은 자리가 먼저 오게" 배치하기 위해 필요하다(가장자리 판정 자체는
// layout/dnd.js의 몫이고, 이 함수는 순서 뒤집기만 안다).
// paneId가 leaf가 아니거나(split id를 잘못 넘김) 존재하지 않으면 원본 트리를 그대로 반환한다
// — 호출자가 존재하지 않는 pane을 잘못 참조해도 트리가 깨지지 않게 방어한다.
export function splitPane(tree, paneId, dir, newLeaf, splitId, newFirst = false) {
  const found = _findWithParent(tree, paneId);
  if (!found || found.node.t !== 'leaf') return tree;
  const [a, b] = newFirst ? [newLeaf, found.node] : [found.node, newLeaf];
  return _replace(tree, paneId, makeSplit(splitId, dir, a, b, 0.5));
}

// paneId를 닫는다.
//  - paneId가 root(유일한 pane)면 트리에서 뺄 수 없으니 session만 비운다
//    (빈 pane이 된다 — 화면에서 pane 자체가 사라지는 건 아니다).
//  - 그 외에는 부모 split을 살아남은 형제로 교체(collapse)한다.
export function closePane(tree, paneId) {
  const found = _findWithParent(tree, paneId);
  if (!found) return tree;
  if (!found.parent) return { ...found.node, session: null };
  const sibling = found.key === 'a' ? found.parent.b : found.parent.a;
  return _replace(tree, found.parent.id, sibling);
}

// paneId(leaf)에 sessionId를 배정한다. 다른 leaf가 이미 같은 sessionId를 갖고
// 있으면 그쪽은 비운다 — "같은 세션 중복 attach 금지" 정책을 이 함수 하나가
// 지킨다(호출자가 따로 검사할 필요가 없다). sessionId가 null이면 단순히 비운다.
export function setSession(tree, paneId, sessionId) {
  function walk(node) {
    if (node.t === 'leaf') {
      if (node.id === paneId) return node.session === sessionId ? node : { ...node, session: sessionId };
      if (sessionId != null && node.session === sessionId) return { ...node, session: null };
      return node;
    }
    const a = walk(node.a), b = walk(node.b);
    return a === node.a && b === node.b ? node : { ...node, a, b };
  }
  return walk(tree);
}

// splitId가 가리키는 split의 ratio를 바꾼다. 0.1~0.9로 clamp — 어느 한쪽이
// 완전히 0이 되면 구분선을 다시 잡을 수 없는 상태가 된다.
export function setRatio(tree, splitId, ratio) {
  const clamped = Math.min(0.9, Math.max(0.1, ratio));
  function walk(node) {
    if (node.t === 'leaf') return node;
    const a = walk(node.a), b = walk(node.b);
    if (node.id === splitId) return { ...node, ratio: clamped, a, b };
    return a === node.a && b === node.b ? node : { ...node, a, b };
  }
  return walk(tree);
}
