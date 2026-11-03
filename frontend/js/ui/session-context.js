// 모든 CLI·목록이 같은 tmux context 스냅샷을 표시한다. 저장소/agent 저장소 의존 없음.
import { t } from '../core/i18n.js';
export function sessionContext(session) {
  // 위치·브랜치는 터미널 pane이 아니라 에이전트 CLI 기준일 수 있다(서버가 contextCwd로 알린다).
  // 옛 서버/피어는 이 필드가 없어 pane cwd(`cwd`)를 그대로 쓴다.
  const cwd = session.contextCwd || session.cwd || '';
  // 에이전트 기준으로 읽었고 터미널 위치와 다를 때만 — 툴팁에 "터미널: <경로>"를 붙이는 용도.
  const terminalCwd = session.contextSource === 'agent' && session.cwd && session.cwd !== cwd ? session.cwd : null;
  const ctx = session.context;
  if (!ctx || ctx.cwd !== cwd) return { cwd, root: null, branch: null, changed: null, status: ctx ? 'unavailable' : 'legacy', terminalCwd };
  return { cwd, root: ctx.root || null, branch: ctx.branch || null,
    changed: ctx.changed || null, status: ctx.status, worktree: ctx.worktree || null, terminalCwd };
}

/** 위치 툴팁 — 에이전트 기준 경로, 터미널과 다르면 그 경로를 한 줄 덧붙인다. */
export function locationTip(ctx) {
  return ctx.terminalCwd ? `${ctx.cwd} · ${t('터미널: {cwd}', { cwd: ctx.terminalCwd })}` : ctx.cwd;
}

export function directoryLabel(cwd) {
  return (cwd || '').replace(/\/$/, '').split('/').pop() || cwd || '';
}

export function workspaceKind(ctx) {
  return ctx.worktree?.kind === 'main' ? t('메인') : ctx.worktree?.kind === 'linked' ? 'WT' : ctx.worktree ? t('미확인') : '';
}

export function workspaceDirectory(ctx) {
  const wt = ctx.worktree;
  if (!wt || !ctx.root) return directoryLabel(ctx.cwd);
  const relative = wt.relative || '';
  return wt.name + (relative ? '/' + (relative.length > 18 && relative.includes('/') ? '…/' + directoryLabel(relative) : relative) : '');
}

export function contextLabel(session) {
  const ctx = sessionContext(session);
  const branch = ctx.branch || (ctx.status === 'ready' || ctx.root ? t('분리된 HEAD') : '');
  return [[workspaceKind(ctx), workspaceDirectory(ctx)].filter(Boolean).join(' '), branch, ctx.status === 'unavailable' || ctx.worktree?.status === 'unavailable' ? t('정보 갱신 대기') : ''].filter(Boolean).join(' · ');
}
