// 「세션 목록 설정」 시트의 탭 둘(세션 로직 점검 단계 6, 설계 P3 — 시트는 저장소 · 보기 · 정리 탭이다).
//   보기   「개입 필요」 구역과 만든 곳별 표시 — 기기마다 따로(device 스코프, 폰에서 끈 것이 맥에 새지 않는다).
//   정리   자동 보관 · CLI 시작 때 자동 이름 · 이어서 시작한 세션의 끝 — 이 맥 서버의 값이다(카탈로그 설정,
//          바꿀 때 비밀번호를 다시 묻는다). 다른 맥을 보고 있으면 Rail.tsx가 이 탭을 숨긴다(그 맥의 값이 아니다).
// 저장소 탭은 RepoVisibility 본문이 그대로 맡는다.
//
// 지연 청크 제약상 core/settings.js는 window 브리지로만 읽는다(rail-prefs.ts).
import { For, createSignal, onMount, Show } from 'solid-js';

import { createRailPrefs } from './rail-prefs.js';
import { t } from '../core/i18n.js';
import { ATTENTION_SHOW_KEY, HIDEABLE_ORIGINS, ORIGIN_LABEL, originShowKey, type Origin } from './rail-data.js';
import { historyApi } from '../history/api.js';

function Toggle(props: { label: string; hint?: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label class="vt-railset-row">
      <input
        type="checkbox"
        checked={props.checked}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.currentTarget.checked)}
      />
      <span class="vt-railset-label">{props.label}</span>
      {props.hint ? <span class="vt-railset-hint">{props.hint}</span> : null}
    </label>
  );
}

const AUTO_ARCHIVE_CHOICES = [0, 7, 14, 30, 90];

/** 보기 탭 — 이 기기의 표시 설정. */
export function RailViewSettings() {
  const { prefs, set } = createRailPrefs();
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
      <div class="vt-railset-note">{t('끄더라도 열려 있는 세션과 입력을 기다리는 세션은 계속 보입니다. 이 기기에만 저장됩니다.')}</div>
    </div>
  );
}

interface CatalogSettings { auto_archive_days: number; auto_name: boolean; resume_ends_session: boolean }

/** 정리 탭 — 이 맥 서버의 세션 기록 설정. */
export function RailCleanupSettings() {
  const [values, setValues] = createSignal<CatalogSettings | null>(null);
  const [error, setError] = createSignal('');
  const [saving, setSaving] = createSignal(false);
  onMount(() => {
    historyApi('/api/session-catalog?limit=1&meta=false', { elevated: false }).then((d) => setValues({
      auto_archive_days: Number(d.auto_archive_days) || 0, auto_name: d.auto_name !== false, resume_ends_session: d.resume_ends_session !== false,
    })).catch((e) => setError(e?.message || t('설정을 읽지 못했습니다')));
  });
  const save = async (patch: Partial<CatalogSettings>) => {
    setError(''); setSaving(true);
    try {
      const r = await historyApi('/api/session-catalog/settings', { body: patch });
      setValues({ auto_archive_days: r.auto_archive_days, auto_name: r.auto_name !== false, resume_ends_session: r.resume_ends_session !== false });
    } catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); }
    finally { setSaving(false); }
  };
  const choices = () => {
    const d = values()?.auto_archive_days ?? 0;
    return AUTO_ARCHIVE_CHOICES.includes(d) ? AUTO_ARCHIVE_CHOICES : [...AUTO_ARCHIVE_CHOICES, d].sort((a, b) => a - b);
  };
  return (
    <div class="vt-railset">
      <div class="vt-railset-note">{t('이 맥의 세션 기록 설정입니다. 바꿀 때 비밀번호를 다시 확인합니다.')}</div>
      <Show when={values()} fallback={<Show when={!error()}><div class="vt-repovis-empty">{t('불러오는 중…')}</div></Show>}>
        {(v) => <>
          <div class="vt-wtd-section-title">{t('자동 보관')}</div>
          <label class="vt-railset-row">
            <span class="vt-railset-label">{t('끝난 세션을 보관함으로')}</span>
            <select value={v().auto_archive_days} disabled={saving()} onChange={(e) => { void save({ auto_archive_days: Number(e.currentTarget.value) }); }}>
              <For each={choices()}>
                {(d) => <option value={d} selected={d === v().auto_archive_days}>{d === 0 ? t('끄기') : t('마지막 활동 후 {n}일', { n: d })}</option>}
              </For>
            </select>
          </label>
          <div class="vt-railset-note">{t('자동으로는 보관만 하고 삭제하지 않습니다. 실행 중이거나 상태를 확인하지 못한 세션은 건드리지 않습니다.')}</div>
          <div class="vt-wtd-section-title">{t('이름')}</div>
          <Toggle
            label={t('CLI를 켜면 저장소 이름으로')}
            hint={t('직접 이름을 붙이지 않은 세션은 Claude Code·Codex가 시작될 때 그 폴더의 저장소 이름이 보이는 이름이 됩니다. tmux 이름은 바꾸지 않습니다.')}
            checked={v().auto_name}
            disabled={saving()}
            onChange={(on) => { void save({ auto_name: on }); }}
          />
          <div class="vt-wtd-section-title">{t('이어서 시작')}</div>
          <Toggle
            label={t('CLI가 끝나면 세션도 끝내기')}
            hint={t('끄면 셸을 띄우고 명령을 쳐 넣어, CLI가 끝나도 셸이 남습니다.')}
            checked={v().resume_ends_session}
            disabled={saving()}
            onChange={(on) => { void save({ resume_ends_session: on }); }}
          />
        </>}
      </Show>
      <Show when={error()}><div class="vt-railset-note" role="status">{error()}</div></Show>
    </div>
  );
}
