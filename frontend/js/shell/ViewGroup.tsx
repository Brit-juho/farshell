import { Show, type JSX } from 'solid-js';
import { promptDialog } from '../core/dialog-lazy.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import type { SessionGroup } from './rail-data.js';

/** 레일/Fleet의 묶음 제목은 한 번만 표시. 열린/닫힌 묶음 모두 같은 제목 편집 경로.
 * 머리는 구역 머리(28)와 같은 줄이다: (접기 캐럿) · 묶음 아이콘 · 제목(누르면 묶음 열기) · 개수 · 연필(이름 변경).
 * `onToggle`을 넘기면 캐럿 버튼이 생기고 `collapsed`면 구성원 행을 그리지 않는다(레일 — 접힘은 rail.view, 기기별).
 * 제목 버튼은 계속 「묶음 열기」라 접기는 별도 버튼이다 — 한 버튼에 두 뜻을 싣지 않는다. */
export function ViewGroup(props: { group: SessionGroup; onOpen: () => void; children: JSX.Element; collapsed?: boolean; onToggle?: () => void }) {
  const rename = async () => {
    const id = props.group.id;
    const next = await promptDialog(t('묶음 뷰 제목'), { title: t('묶음 이름 변경'), okLabel: t('저장'), defaultValue: props.group.label });
    if (next === null || !next.trim()) return;
    if (!(window as any).vtRenameTab?.(id, next.trim())) (window as any).showToast?.(t('묶음 이름을 저장하지 못했습니다. 제목은 128자까지 입력할 수 있습니다.'));
  };
  return <section class="vt-view-group" aria-label={props.group.label} data-view-id={props.group.id}>
    <div class="vt-view-group-head">
      <Show when={props.onToggle}>
        <button type="button" class="vt-view-group-toggle" classList={{ collapsed: !!props.collapsed }}
          aria-expanded={!props.collapsed}
          aria-label={props.collapsed ? t('{label} 묶음 펼치기', { label: props.group.label }) : t('{label} 묶음 접기', { label: props.group.label })}
          onClick={() => props.onToggle?.()} innerHTML={icon('chevron-down', 12, 2)} />
      </Show>
      <span class="vt-view-group-ico" aria-hidden="true" innerHTML={icon('layout-panels', 12, 2)} />
      <button type="button" class="vt-view-group-title" onClick={props.onOpen} data-tip={t('묶음 열기')} data-tip-side="right">{props.group.label}</button>
      <span class="vt-view-group-count" aria-label={t('{n}개 세션', { n: props.group.rows.length })}>{props.group.rows.length}</span>
      <button type="button" class="vt-view-group-rename" onClick={() => void rename()} aria-label={t('{label} 묶음 이름 변경', { label: props.group.label })} data-tip={t('묶음 이름 변경')} data-tip-side="right" innerHTML={icon('pencil', 14, 2)} />
    </div>
    <Show when={!props.collapsed}>{props.children}</Show>
  </section>;
}
