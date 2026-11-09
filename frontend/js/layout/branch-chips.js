// tmux 세션 이름 → 브랜치. term/tab-worktree.js가 목록을 받을 때마다 panes.js `setBranchChipMap`을 통해
// 채운다. pane 헤더 옆 브랜치 칩(panes.js)과 다른 소비자가 같은 값을 읽도록 따로 뒀다 —
// panes.js(분할·드래그 등 무거운 의존)를 import하지 않게 하려는 것이기도 하다.
//
// 리디자인 2단계(2026-11) — 제목 막대의 경로(저장소 › 브랜치 › 세션)도 이 지도를 읽는다. 그래서 저장소 이름
// 지도도 같이 들고, 지도가 바뀌면 구독자(제목 막대)에게 알린다. 같은 `/api/tmux/sessions` 응답 하나에서
// 두 지도를 함께 만들므로 둘이 서로 다른 시점을 말하지 않는다.
let _chips = new Map();
let _repos = new Map();
const _subs = new Set();

export function setBranchChips(map, repos) {
  _chips = map || new Map();
  if (repos) _repos = repos;
  for (const fn of [..._subs]) {
    try { fn(); } catch (_) { /* 구독자 하나의 실패가 칸 칩 갱신을 막지 않는다 */ }
  }
}
export function branchOf(tmuxName) { return tmuxName ? (_chips.get(tmuxName) || null) : null; }
/** tmux 세션 이름 → 저장소 이름(메인 워크트리 폴더 이름). 저장소 밖이면 null. */
export function repoOf(tmuxName) { return tmuxName ? (_repos.get(tmuxName) || null) : null; }
/** 지도가 바뀔 때마다 부른다. 해제 함수를 돌려준다. */
export function onBranchChips(fn) {
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}
