// 모든 CLI·목록이 같은 tmux context 스냅샷을 표시한다. 저장소/agent 저장소 의존 없음.
export function sessionContext(session) {
  const cwd = session.cwd || '';
  const ctx = session.context;
  if (!ctx || ctx.cwd !== cwd) return { cwd, root: null, branch: null, changed: null, status: ctx ? 'unavailable' : 'legacy' };
  return { cwd, root: ctx.root || null, branch: ctx.branch || null,
    changed: ctx.changed || null, status: ctx.status };
}

export function directoryLabel(cwd) {
  return (cwd || '').replace(/\/$/, '').split('/').pop() || cwd || '';
}

export function contextLabel(session) {
  const ctx = sessionContext(session);
  const branch = ctx.branch || (ctx.status === 'ready' || ctx.root ? '분리된 HEAD' : '');
  return [directoryLabel(ctx.cwd), branch, ctx.status === 'unavailable' ? '정보 갱신 대기' : ''].filter(Boolean).join(' · ');
}
