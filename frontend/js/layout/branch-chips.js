// tmux 세션 이름 → 브랜치. term/tab-worktree.js가 목록을 받을 때마다 panes.js `setBranchChipMap`을 통해
// 채운다. pane 헤더 옆 브랜치 칩(panes.js)과 뷰 탭의 구성원 툴팁(tabbar.js)이 같은 값을 읽도록 따로 뒀다 —
// tabbar.js가 panes.js(분할·드래그 등 무거운 의존)를 import하지 않게 하려는 것이기도 하다.
let _chips = new Map();

export function setBranchChips(map) { _chips = map || new Map(); }
export function branchOf(tmuxName) { return tmuxName ? (_chips.get(tmuxName) || null) : null; }
