// 10-shell-layout.md §4 — "탭 = 열려 있는 워크트리"의 **1단계: 렌더만**.
//
// 원래 계획은 탭 하나가 워크트리 하나를 대표하고 그 안에 세션 여러 개가 들어가는
// 것이었는데, 조사해 보니 pane 트리가 화면 전체에 하나뿐인 구조라 진짜 다중
// 세션(2단계)은 `/api/workspace` 스키마까지 건드리는 큰 작업이다. 그래서 이
// 파일은 저위험 부분만 한다: **탭에 적히는 이름을 워크트리 단위로 바꾼다.**
// 탭 개수·수명·드래그·닫기 동작은 그대로다(세션 단위).
//
// 규칙 둘:
//  - 사용자가 직접 지은 이름(`renamed`)은 절대 덮지 않는다.
//  - 워크트리에 안 속한 세션은 그대로 둔다(레일의 「기타」 그룹과 같은 규칙).
import { allSessions, setSessionDisplayName } from '../core/store.js';
import { vtFetch } from '../core/api.js';
import { setWorktreeSessionMap } from '../layout/tabbar.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';

// 정본은 `/ws-workspace`의 `worktrees_changed` push(맨 아래). 이 폴링은 WS가
// 끊긴 동안과, 터미널에서 직접 워크트리를 만들어 서버가 변경을 모르는 경우를
// 위한 안전망이다 — Rail.tsx의 WORKTREES_POLL_MS와 같은 이유·같은 값.
const POLL_MS = 60000;

/** 워크트리 목록 → `tmux 세션 이름 → 표시 라벨` 지도. 라벨 규칙은 레일과 같다
 * (30-worktree.md §4: isMain이면 repoName, 아니면 `repo/branch`). 순수 함수. */
export function worktreeLabelMap(worktrees) {
  const map = new Map();
  for (const wt of worktrees || []) {
    const label = wt.isMain ? wt.repoName : `${wt.repoName}/${wt.branch}`;
    for (const name of wt.sessions || []) {
      // 한 세션이 두 워크트리에 속할 수는 없다 — 먼저 온 것을 남긴다(서버가
      // 중복을 내려주는 비정상 상황에서도 라벨이 깜빡이지 않게).
      if (!map.has(name)) map.set(name, label);
    }
  }
  return map;
}

/** 이 탭의 이름을 자동 라벨로 바꿔도 되는가. 사용자가 지은 이름은 보존한다. */
export function canRelabel(session) {
  return !!session && !session.renamed;
}

function applyLabels(map) {
  for (const [id, s] of Object.entries(allSessions())) {
    const tmux = s && (s.tmuxName || s.tmux_name);
    if (!tmux || !canRelabel(s)) continue;
    const label = map.get(tmux);
    if (!label || s.displayName === label) continue;
    // 이름의 출처는 레코드다(core/store.js) — 탭 DOM은 setSessionDisplayName이
    // 같이 맞춘다. 3단계에서 그 DOM이 사라져도 이 경로는 그대로 동작한다.
    setSessionDisplayName(id, label);
  }
}

/** 세션 이름 → worktreeId. 탭 바가 "이 세션이 이 탭 소속인가"를 판정할 때 쓴다.
 * 라벨 지도와 같은 응답에서 뽑으므로 조회가 한 번이면 된다. */
export function worktreeOwnerMap(worktrees) {
  const map = new Map();
  for (const wt of worktrees || []) {
    for (const name of wt.sessions || []) if (!map.has(name)) map.set(name, wt.id);
  }
  return map;
}

export async function refreshTabWorktreeLabels() {
  try {
    const data = await vtFetch('/api/worktrees');
    applyLabels(worktreeLabelMap(data?.worktrees));
    setWorktreeSessionMap(worktreeOwnerMap(data?.worktrees));
  } catch (_) { /* 워크트리 API가 없거나 실패 — 탭은 기존 이름 그대로 쓴다 */ }
}

refreshTabWorktreeLabels();
setInterval(() => { if (!document.hidden) refreshTabWorktreeLabels(); }, POLL_MS);
onWorkspaceEvent('worktrees_changed', () => { refreshTabWorktreeLabels(); });
