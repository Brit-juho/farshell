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
//  - 워크트리에 안 속한 세션은 그대로 둔다(레일의 「묶지 않음」과 같은 규칙).
//
// ADR-29 D(2026-09-18) — 탭 소속 판정(setGroupSessionMap이 먹이는 지도)이
// `/api/worktrees`의 저장소 **경로**가 아니라 `/api/repos`의 저장소 **id**
// (sha1, ADR-29 B가 레일 그룹 자동 제안에 쓰는 것과 같은 값)로 바뀌었고,
// `@fsh_grp`가 있으면 그게 우선이다(`/api/tmux/sessions`의 `grp_id`) —
// 그래야 이 탭 소속 판정이 레일의 그룹 판정과 항상 같은 답을 낸다.
import { allSessions, setSessionDisplayName } from '../core/store.js';
import { vtFetch } from '../core/api.js';
import { setGroupSessionMap } from '../layout/tabbar.js';
import { setBranchChipMap } from '../layout/panes.js';
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

/** 세션 이름 → 저장소 id(sha1, `repo.id` — ADR-29 A/B와 같은 자동 제안
 * 그룹 값). 저장소별로 이미 묶인 `/api/repos` 응답을 받는다(worktreeLabelMap/
 * branchChipMap용 평평한 목록과는 다른 모양이라 별도 인자). */
export function repoOwnerMap(repos) {
  const map = new Map();
  for (const repo of repos || []) {
    for (const wt of repo.worktrees || []) {
      for (const name of wt.sessions || []) if (!map.has(name)) map.set(name, repo.id);
    }
  }
  return map;
}

/** 탭 바가 "이 세션이 이 탭(그룹) 소속인가"를 판정할 때 쓰는 최종 지도 —
 * ADR-29 D: 탭의 정체성은 그룹이다. `@fsh_grp`(사용자가 직접 묶은 것)가
 * 있으면 그게 우선이고, 없으면 저장소 자동 제안(`repoOwnerMap`)으로
 * 떨어진다 — 레일의 groupId 계산(rail-data.ts)과 같은 폴백 순서. */
export function effectiveGroupMap(repos, tmuxSessions) {
  const owner = repoOwnerMap(repos);
  const map = new Map();
  for (const t of tmuxSessions || []) {
    if (!t || !t.name) continue;
    map.set(t.name, t.grp_id || owner.get(t.name) || null);
  }
  return map;
}

/** 세션 이름 → 브랜치. **저장소에 워크트리가 둘 이상일 때만** 항목이
 * 생긴다(D4: "워크트리가 1개뿐인 저장소는 축이 안 보인다") — 구분할 대상이
 * 하나뿐이면 칩은 정보가 아니라 잡음이다. */
export function branchChipMap(worktrees) {
  const perRepo = new Map();
  for (const wt of worktrees || []) perRepo.set(wt.repo, (perRepo.get(wt.repo) || 0) + 1);
  const map = new Map();
  for (const wt of worktrees || []) {
    if ((perRepo.get(wt.repo) || 0) < 2) continue;
    for (const name of wt.sessions || []) if (!map.has(name)) map.set(name, wt.branch);
  }
  return map;
}

export async function refreshTabWorktreeLabels() {
  try {
    const [repoData, tmuxSessions] = await Promise.all([
      vtFetch('/api/repos'),
      vtFetch('/api/tmux/sessions'),
    ]);
    const worktrees = (repoData?.repos || []).flatMap((r) => r.worktrees || []);
    applyLabels(worktreeLabelMap(worktrees));
    setGroupSessionMap(effectiveGroupMap(repoData?.repos, tmuxSessions));
    setBranchChipMap(branchChipMap(worktrees));
  } catch (_) { /* 워크트리 API가 없거나 실패 — 탭은 기존 이름 그대로 쓴다 */ }
}

refreshTabWorktreeLabels();
setInterval(() => { if (!document.hidden) refreshTabWorktreeLabels(); }, POLL_MS);
onWorkspaceEvent('worktrees_changed', () => { refreshTabWorktreeLabels(); });
