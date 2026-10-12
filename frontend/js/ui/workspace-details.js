import { openDialog, nextId } from '../core/dialog.js';
import { vtEsc } from '../core/api.js';
import { sessionContext, directoryLabel } from './session-context.js';

/** 조회 전용. 세션/묶음 이름이나 현재 작업 위치를 변경하지 않는다. */
export function showWorkspaceDetails(session) {
  const ctx = sessionContext(session);
  const wt = ctx.worktree;
  const kind = wt?.kind === 'main' ? '메인 작업 공간' : wt?.kind === 'linked' ? '별도 워크트리' : ctx.status === 'nonrepo' ? 'Git 저장소 밖' : '미확인';
  const fields = [
    ['작업 공간 종류', kind],
    ['저장소', wt?.repositoryRoot ? directoryLabel(wt.repositoryRoot) : '미확인'],
    ['작업 공간 경로', ctx.root || '없음'],
    ['현재 디렉터리', ctx.cwd || '미확인'],
    ['브랜치', ctx.branch || (ctx.root ? '분리된 HEAD' : '없음')],
  ];
  if (ctx.changed) fields.push(['변경량', `${ctx.changed.files}개 파일 · +${ctx.changed.add} −${ctx.changed.del}`]);
  if (ctx.status === 'unavailable' || wt?.status === 'unavailable') fields.push(['갱신 상태', '정보 갱신 대기']);
  openDialog({ id: nextId(), title: '작업 위치', bodyHTML: `<dl class="vt-workspace-details">${fields.map(([k, v]) => `<dt>${vtEsc(k)}</dt><dd>${vtEsc(v)}</dd>`).join('')}</dl>` });
}
