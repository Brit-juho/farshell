// pane 헤더도 레일/카드와 같은 현재 작업 위치의 context를 사용한다.
// 생성 시 워크트리 소속으로 세션 이름을 자동 변경하던 옛 경로는 제거했다.
import { vtFetch } from '../core/api.js';
import { setBranchChipMap } from '../layout/panes.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
import { sessionContext, directoryLabel } from '../ui/session-context.js';
import { t } from '../core/i18n.js';
import { createPoll } from '../core/poll.js';

export function branchChipMap(sessions) {
  const map = new Map();
  for (const s of sessions || []) {
    const ctx = sessionContext(s);
    const branch = ctx.branch || (ctx.status === 'ready' || ctx.root ? t('분리된 HEAD') : null);
    if (branch) map.set(s.name, branch);
  }
  return map;
}

// 제목 막대의 경로(저장소 › 브랜치 › 세션)에 쓰는 저장소 이름. 링크된 워크트리라도 **메인 저장소**의 폴더 이름을
// 쓴다(서버 context.worktree.repositoryRoot) — 워크트리 폴더 이름은 이미 브랜치가 말한다. 모르면 저장소 최상위 폴더.
export function repoNameMap(sessions) {
  const map = new Map();
  for (const s of sessions || []) {
    const ctx = sessionContext(s);
    const root = (ctx.worktree && ctx.worktree.repositoryRoot) || ctx.root;
    const name = root ? directoryLabel(root) : '';
    if (name) map.set(s.name, name);
  }
  return map;
}

function publish(sessions) {
  setBranchChipMap(branchChipMap(sessions), repoNameMap(sessions));
}

let revision = 0;
// 실패는 던진다 — 폴러(core/poll.js)가 간격을 늘린다. 화면은 마지막 성공 값을 유지한다.
async function _load() {
  const request = ++revision;
  const sessions = await vtFetch('/api/tmux/sessions');
  if (request === revision && Array.isArray(sessions)) publish(sessions);
}

export async function refreshTabWorktreeLabels() {
  try { await _load(); } catch (_) { /* 마지막 성공 값을 유지한다 */ }
}

// 정본은 아래 `tmux_sessions` push다. 이 60초 조회는 그 채널이 끊긴 동안의 안전망 —
// 이전 조회가 안 끝났으면 건너뛰고, 숨은 탭에서는 쉰다.
createPoll(_load, { interval: 60000, immediate: true }).start();
onWorkspaceEvent('tmux_sessions', (msg) => {
  if (!Array.isArray(msg.sessions)) return;
  revision += 1;
  publish(msg.sessions);
});
