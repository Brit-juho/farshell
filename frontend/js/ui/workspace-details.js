import { openDialog, nextId } from '../core/dialog.js';
import { vtEsc } from '../core/api.js';
import { sessionContext, directoryLabel } from './session-context.js';
import { t } from '../core/i18n.js';

/** 조회 전용. 세션/묶음 이름이나 현재 작업 위치를 변경하지 않는다. */
export function showWorkspaceDetails(session) {
  const ctx = sessionContext(session);
  const wt = ctx.worktree;
  const kind = wt?.kind === 'main' ? t('메인 작업 공간') : wt?.kind === 'linked' ? t('별도 워크트리') : ctx.status === 'nonrepo' ? t('Git 저장소 밖') : t('미확인');
  const fields = [
    [t('작업 공간 종류'), kind],
    [t('저장소'), wt?.repositoryRoot ? directoryLabel(wt.repositoryRoot) : t('미확인')],
    [t('작업 공간 경로'), ctx.root || t('없음')],
    [t('현재 디렉터리'), ctx.cwd || t('미확인')],
    [t('브랜치'), ctx.branch || (ctx.root ? t('분리된 HEAD') : t('없음'))],
  ];
  if (ctx.changed) fields.push([t('변경량'), t('{n}개 파일 · +{add} −{del}', { n: ctx.changed.files, add: ctx.changed.add, del: ctx.changed.del })]);
  if (ctx.status === 'unavailable' || wt?.status === 'unavailable') fields.push([t('갱신 상태'), t('정보 갱신 대기')]);
  openDialog({ id: nextId(), title: t('작업 위치'), bodyHTML: `<dl class="vt-workspace-details">${fields.map(([k, v]) => `<dt>${vtEsc(k)}</dt><dd>${vtEsc(v)}</dd>`).join('')}</dl>` });
}
