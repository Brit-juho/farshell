// 레일 설정 시트의 위쪽 구획 — 「개입 필요」 구역과 만든 곳별 표시 켜기/끄기.
// 아래의 저장소 표시는 RepoVisibility(같은 시트 안)가 이어서 그린다: 헤더 톱니가 예전에
// 열던 「저장소 관리」를 버리지 않고 이 시트의 한 구획으로 흡수했다.
//
// 값은 device 스코프 설정이라 기기마다 따로다(폰에서 끈 것이 맥에 새지 않는다).
// 지연 청크 제약상 core/settings.js는 window 브리지로만 읽는다(rail-prefs.ts).
import { For, createSignal, onMount, Show } from 'solid-js';

import { createRailPrefs } from './rail-prefs.js';
import { t } from '../core/i18n.js';
import {
  ATTENTION_SHOW_KEY, HIDEABLE_ORIGINS, ORIGIN_LABEL, originShowKey, type Origin,
  VIEW_KEY, RECENT_MAX, encodeRailView, type RailView,
} from './rail-data.js';
import { historyApi } from '../history/api.js';
import { numberSpinner } from '../ui/number-spinner.js';

function Toggle(props: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label class="vt-railset-row">
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.currentTarget.checked)}
      />
      <span class="vt-railset-label">{props.label}</span>
      {props.hint ? <span class="vt-railset-hint">{props.hint}</span> : null}
    </label>
  );
}

const AUTO_ARCHIVE_CHOICES = [0, 7, 14, 30, 90];

export function RailSettingsLead() {
  const { prefs, set } = createRailPrefs();
  const setView = (patch: Partial<RailView>) =>
    set(VIEW_KEY, encodeRailView({ ...prefs().view, ...patch }));
  // 기본 type=number 대신 공용 스피너(ui/number-spinner.js). 범위를 벗어난 입력은 스피너가 되돌린다.
  const recentSpinner = numberSpinner({ value: prefs().view.recent, min: 1, max: RECENT_MAX, step: 1, onCommit: (n: number) => { setView({ recent: n }); } });
  recentSpinner.element.vtSetLabel(t('최근 종료 표시 개수'));
  // 자동 보관 일수는 서버가 정한다(서버가 주기적으로 보관하므로 기기 설정이 아니다). 0 = 끔.
  const [autoDays, setAutoDays] = createSignal<number | null>(null);
  const [autoError, setAutoError] = createSignal('');
  onMount(() => { historyApi('/api/session-catalog?limit=1').then((d) => setAutoDays(Number(d.auto_archive_days) || 0)).catch(() => {}); });
  const saveAutoDays = async (days: number) => {
    setAutoError('');
    try { const r = await historyApi('/api/session-catalog/settings', { body: { auto_archive_days: days } }); setAutoDays(r.auto_archive_days); }
    catch (e: any) { setAutoError(e.message || t('작업을 완료하지 못했습니다')); }
  };
  const originOn = (o: Origin) => !prefs().hiddenOrigins.includes(o);
  return (
    <div class="vt-railset">
      <div class="vt-wtd-section-title">{t('구역')}</div>
      <Toggle
        label={t('개입 필요 구역 표시')}
        hint={t('입력을 기다리는 세션을 맨 위에 모아 보여줍니다')}
        checked={prefs().attentionShow}
        onChange={(v) => set(ATTENTION_SHOW_KEY, v)}
      />
      <div class="vt-wtd-section-title">{t('종료한 세션')}</div>
      <Toggle
        label={t('저장소별로 묶기')}
        hint={t('실행 중과 종료한 세션을 저장소 아래에 함께 보여줍니다. 이 보기에서는 이름순·수동 정렬과 위임 트리를 쓰지 않습니다.')}
        checked={prefs().view.byRepo}
        onChange={(v) => setView({ byRepo: v })}
      />
      <div class="vt-railset-row">
        <span class="vt-railset-label">{t('최근 종료 표시 개수')}</span>
        {recentSpinner.element}
      </div>
      <Show when={autoDays() !== null}>
        <label class="vt-railset-row">
          <span class="vt-railset-label">{t('자동 보관')}</span>
          <select value={autoDays()!} onChange={(e) => { void saveAutoDays(Number(e.currentTarget.value)); }}>
            <For each={AUTO_ARCHIVE_CHOICES.includes(autoDays()!) ? AUTO_ARCHIVE_CHOICES : [...AUTO_ARCHIVE_CHOICES, autoDays()!].sort((a, b) => a - b)}>
              {(d) => <option value={d} selected={d === autoDays()}>{d === 0 ? t('끄기') : t('종료 후 {n}일', { n: d })}</option>}
            </For>
          </select>
        </label>
        <div class="vt-railset-note">{t('자동으로는 보관만 하고 삭제하지 않습니다. 실행 중이거나 상태를 확인하지 못한 세션은 건드리지 않습니다.')}</div>
        <Show when={autoError()}><div class="vt-railset-note" role="status">{autoError()}</div></Show>
      </Show>
      <div class="vt-wtd-section-title">{t('만든 곳별 표시')}</div>
      <For each={HIDEABLE_ORIGINS}>
        {(o) => (
          <Toggle
            label={ORIGIN_LABEL[o]}
            checked={originOn(o)}
            onChange={(v) => set(originShowKey(o), v)}
          />
        )}
      </For>
      <div class="vt-railset-note">{t('끄더라도 열려 있는 세션과 입력을 기다리는 세션은 계속 보입니다.')}</div>
    </div>
  );
}
