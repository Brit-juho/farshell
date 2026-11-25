// 저장소 표식 선택판(2026-11) — 레일 저장소 머리 우클릭 · 폰 저장소 머리 꾹 누름 · 저장소 관리 시트의 표식 버튼.
// 아이콘이나 색을 누르면 바로 저장하고(그 맥의 전역 설정, repo-marks.ts) 판은 열어 둔다 — 아이콘과 색을 한 번에 고른다.
// 닫기: Esc · 바깥 누르기 · 「닫기」. 자리 잡기는 행 메뉴(RailRow.tsx Menu)와 같다(보이는 화면 안으로 당긴다).
import { For, onCleanup, onMount } from 'solid-js';

import { t } from '../core/i18n.js';
import { pushScope } from '../core/keyboard-scopes.js';
import { icon } from '../ui/icons.js';
import {
  MARK_COLOR_NAMES, MARK_COLORS, MARK_ICONS, markColorLabel, markIconHtml, markIconLabel, markStyle, type RepoMark,
} from './repo-marks.js';

export interface RepoMarkPickerProps {
  x: number;
  y: number;
  /** 저장소 이름(제목·접근성 이름). */
  name: string;
  mark: RepoMark | null;
  /** null = 기본(폴더·기본 색)으로 되돌리기. */
  onChange: (mark: Partial<RepoMark> | null) => void;
  onClose: () => void;
}

export function RepoMarkPicker(props: RepoMarkPickerProps) {
  let ref: HTMLDivElement | undefined;
  const previousFocus = document.activeElement as HTMLElement | null;
  // 꾹 눌러 연 손가락을 뗄 때 브라우저가 흉내 내는 mousedown이 바깥 누르기로 읽혀 판이 바로 닫히지 않게, 연 직후 잠깐은 무시한다.
  const openedAt = Date.now();
  const onDocDown = (e: Event) => {
    if (Date.now() - openedAt < 400) return;
    if (ref && !ref.contains(e.target as Node)) props.onClose();
  };
  document.addEventListener('mousedown', onDocDown, true);
  document.addEventListener('touchstart', onDocDown, true);
  // 키 소유는 스택 맨 위(core/keyboard-scopes.js) — 저장소 관리 시트 위에서 열려도 Esc는 이 판만 닫고, Tab은 판 안을 돈다.
  const disposeKeys = pushScope({ element: () => ref, id: 'RepoMarkPicker', modal: true, restoreFocus: false, onKey: (e: KeyboardEvent) => {
    if (e.key === 'Escape') { props.onClose(); return true; }
    return false;
  } });
  onCleanup(() => {
    disposeKeys();
    document.removeEventListener('mousedown', onDocDown, true);
    document.removeEventListener('touchstart', onDocDown, true);
    if (previousFocus?.isConnected) previousFocus.focus();
  });
  onMount(() => {
    if (!ref) return;
    const vv = window.visualViewport;
    const vh = vv ? vv.height : window.innerHeight;
    const vw = vv ? vv.width : window.innerWidth;
    const pad = 8;
    const r = ref.getBoundingClientRect();
    ref.style.maxHeight = `${Math.max(120, vh - pad * 2)}px`;
    ref.style.overflowY = 'auto';
    const h = Math.min(r.height, vh - pad * 2);
    ref.style.top = `${Math.max(pad, Math.min(props.y, vh - h - pad))}px`;
    ref.style.left = `${Math.max(pad, Math.min(props.x, vw - r.width - pad))}px`;
    ref.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  });

  const curIcon = () => props.mark?.icon || 'folder';
  const curColor = () => props.mark?.color || '';
  const tint = () => markStyle(props.mark);

  return (
    <div ref={ref} class="vt-menu vt-mark-pick" role="dialog" aria-label={t('{name} 표식', { name: props.name })}
      style={{ left: `${props.x}px`, top: `${props.y}px`, right: 'auto' }}>
      <div class="vt-mark-pick-head">
        <span class="vt-mark-pick-prev" style={tint()} innerHTML={markIconHtml(curIcon(), 14, 2)} />
        <span class="vt-mark-pick-name">{props.name}</span>
      </div>
      <div class="vt-mark-pick-label">{t('아이콘')}</div>
      <div class="vt-mark-pick-icons" role="radiogroup" aria-label={t('아이콘')}>
        <For each={MARK_ICONS}>{(name) => (
          <button type="button" class="vt-mark-pick-ic" role="radio" aria-checked={curIcon() === name}
            aria-label={markIconLabel(name)} data-tip={markIconLabel(name)}
            style={curIcon() === name ? tint() : undefined}
            onClick={() => props.onChange({ icon: name })} innerHTML={markIconHtml(name, 15, 2)} />
        )}</For>
      </div>
      <div class="vt-mark-pick-label">{t('색')}</div>
      <div class="vt-mark-pick-colors" role="radiogroup" aria-label={t('색')}>
        <For each={MARK_COLOR_NAMES}>{(name) => (
          <button type="button" class="vt-mark-pick-sw" classList={{ none: !name }} role="radio" aria-checked={curColor() === name}
            aria-label={markColorLabel(name)} data-tip={markColorLabel(name)}
            style={name ? { background: MARK_COLORS[name] } : undefined}
            onClick={() => props.onChange({ color: name })} />
        )}</For>
      </div>
      <div class="vt-mark-pick-foot">
        <button type="button" class="vt-btn sm" disabled={!props.mark} onClick={() => props.onChange(null)}>{t('기본으로')}</button>
        <button type="button" class="vt-btn sm" onClick={() => props.onClose()}>
          <span innerHTML={icon('check', 13, 2)} />{t('닫기')}
        </button>
      </div>
    </div>
  );
}
