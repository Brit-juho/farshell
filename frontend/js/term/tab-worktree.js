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
import { allSessions } from '../core/store.js';
import { vtFetch } from '../core/api.js';

const POLL_MS = 10000;  // 워크트리 목록은 서버가 5초 캐시라 자주 불러도 싸다.

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
  for (const s of Object.values(allSessions())) {
    const tmux = s && (s.tmuxName || s.tmux_name);
    if (!tmux || !canRelabel(s)) continue;
    const label = map.get(tmux);
    const nameEl = s.tabEl && s.tabEl.querySelector('.tab-name');
    if (!label || !nameEl) continue;
    if (nameEl.textContent !== label) {
      nameEl.textContent = label;
      // 워크트리 라벨은 여러 저장소에서 같아 보일 수 있다(같은 브랜치명) —
      // 실제 tmux 세션 이름을 툴팁으로 남겨 구분할 수 있게 한다.
      nameEl.title = `${label} · ${tmux}`;
    }
  }
}

export async function refreshTabWorktreeLabels() {
  try {
    const data = await vtFetch('/api/worktrees');
    applyLabels(worktreeLabelMap(data?.worktrees));
  } catch (_) { /* 워크트리 API가 없거나 실패 — 탭은 기존 이름 그대로 쓴다 */ }
}

refreshTabWorktreeLabels();
setInterval(() => { if (!document.hidden) refreshTabWorktreeLabels(); }, POLL_MS);
