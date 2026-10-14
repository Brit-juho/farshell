// 모든 CLI·목록이 같은 tmux context 스냅샷을 표시한다. 저장소/agent 저장소 의존 없음.
import { t } from '../core/i18n.js';
export function sessionContext(session) {
  const cwd = session.cwd || '';
  const ctx = session.context;
  if (!ctx || ctx.cwd !== cwd) return { cwd, root: null, branch: null, changed: null, status: ctx ? 'unavailable' : 'legacy' };
  return { cwd, root: ctx.root || null, branch: ctx.branch || null,
    changed: ctx.changed || null, status: ctx.status, worktree: ctx.worktree || null };
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
