import { type JSX } from 'solid-js';
import { promptDialog } from '../core/dialog-lazy.js';
import { t } from '../core/i18n.js';
import type { SessionGroup } from './rail-data.js';

/** 레일/Fleet의 묶음 제목은 한 번만 표시. 열린/닫힌 묶음 모두 같은 제목 편집 경로. */
export function ViewGroup(props: { group: SessionGroup; onOpen: () => void; children: JSX.Element }) {
  const rename = async () => {
    const id = props.group.id;
    const next = await promptDialog(t('묶음 뷰 제목'), { title: t('묶음 이름 변경'), defaultValue: props.group.label });
    if (next === null || !next.trim()) return;
    if (!(window as any).vtRenameTab?.(id, next.trim())) (window as any).showToast?.(t('묶음 이름을 저장하지 못했습니다. 제목은 128자까지 입력할 수 있습니다.'));
  };
  return <section class="vt-view-group" aria-label={props.group.label} data-view-id={props.group.id}>
    <div class="vt-view-group-head">
      <button type="button" class="vt-view-group-title" onClick={props.onOpen} data-tip={t('묶음 열기')} data-tip-side="right">{props.group.label}</button>
      <span class="vt-view-group-count" aria-label={t('{n}개 세션', { n: props.group.rows.length })}>{props.group.rows.length}</span>
      <button type="button" class="vt-view-group-rename" onClick={() => void rename()} aria-label={t('{label} 묶음 이름 변경', { label: props.group.label })} data-tip={t('묶음 이름 변경')} data-tip-side="right">{t('편집')}</button>
    </div>
    {props.children}
  </section>;
}
