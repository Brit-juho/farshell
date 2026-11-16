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
import { ATTENTION_SHOW_KEY, HIDEABLE_ORIGINS, ORIGIN_LABEL, originShowKey, SORT_KEY, encodeRailSort, type Origin } from './rail-data.js';
import { REPO_SORT_LEVELS, type RepoSort, type SessionSort, type TreeSort } from './repo-tree.js';
import { historyApi } from '../history/api.js';
import { archiveEnded } from '../history/session-actions.js';

function Choice<T extends string>(props: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <label class="vt-railset-row vt-railset-choice">
      <span class="vt-railset-label">{props.label}</span>
      <select class="vt-input sm" value={props.value} onChange={(e) => props.onChange(e.currentTarget.value as T)}>
        <For each={props.options}>{([v, name]) => <option value={v} selected={v === props.value}>{name}</option>}</For>
      </select>
    </label>
  );
}

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

const REPO_SORT_LABEL = (): Record<RepoSort, string> => ({
  sessions: t('세션 있는 저장소 먼저'), waiting: t('입력 대기 있는 저장소 먼저'), name: t('이름순'),
  registered: t('등록순(최근 등록 먼저)'), activity: t('최근 활동순'),
});
const LEVEL_LABEL = (): string[] => [t('저장소 1차'), t('2차'), t('3차')];

/** 보기 탭 — 표시 설정(이 기기)과 정렬(이 맥 — 그 맥을 보는 모든 기기가 같다). */
export function RailViewSettings() {
  const { prefs, set } = createRailPrefs();
  const originOn = (o: Origin) => !prefs().hiddenOrigins.includes(o);
  // 정렬 — 그 맥의 전역 설정(rail.sort). 저장소는 기준을 1~3단계로 잇고, 앞 단계에서 고른 기준은 뒤 단계 선택지에서 빠진다.
  const sort = () => prefs().sort;
  const setSort = (next: Partial<TreeSort>) => set(SORT_KEY, encodeRailSort({ ...sort(), ...next }));
  // 단계 i를 v로 — 「없음」('')이면 그 단계부터 뒤를 지운다. 1차는 「없음」이 없다.
  const setLevel = (i: number, v: RepoSort | '') => {
    const cur = sort().repos.slice(0, i);
    setSort({ repos: v ? [...cur, v, ...sort().repos.slice(i + 1).filter((k) => k !== v && !cur.includes(k))] : cur });
  };
  const levelOptions = (i: number): [RepoSort | '', string][] => {
    const used = sort().repos.slice(0, i);
    const label = REPO_SORT_LABEL();
    const opts = (Object.keys(label) as RepoSort[]).filter((k) => !used.includes(k)).map((k) => [k, label[k]] as [RepoSort, string]);
    return i === 0 ? opts : [['', t('없음')], ...opts];
  };
  const levels = () => Array.from({ length: Math.min(REPO_SORT_LEVELS, sort().repos.length + 1) }, (_, i) => i);
  return (
    <div class="vt-railset">
      <div class="vt-wtd-section-title">{t('정렬')}</div>
      <div class="vt-railset-note">{t('이 맥을 보는 모든 기기에 같은 순서로 보입니다. 「저장소 밖」은 세션이 있는 저장소들 바로 아래에 옵니다.')}</div>
      <For each={levels()}>{(i) => (
        <Choice<RepoSort | ''> label={LEVEL_LABEL()[i]} value={sort().repos[i] || ''} onChange={(v) => setLevel(i, v)}
          options={levelOptions(i)} />
      )}</For>
      <Choice<SessionSort> label={t('세션')} value={sort().sessions} onChange={(v) => setSort({ sessions: v })}
        options={[['created', t('만든 순')], ['name', t('이름순')], ['activity', t('최근 활동')], ['manual', t('수동(끌어서 옮기기)')]]} />
      <Toggle
        label={t('입력 대기 먼저')}
        hint={sort().sessions === 'manual' ? t('수동 정렬에서는 정한 순서를 그대로 둡니다') : t('입력을 기다리는 세션을 가지 안에서 위로 올립니다')}
        checked={sort().waitingFirst && sort().sessions !== 'manual'}
        disabled={sort().sessions === 'manual'}
        onChange={(v) => setSort({ waitingFirst: v })}
      />
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

interface CatalogSettings { auto_archive_days: number; ended_cleanup_days: number; auto_name: boolean; resume_ends_session: boolean }
const CLEANUP_CHOICES = [1, 3, 7, 14, 30];

/** 정리 탭 — 이 맥 서버의 세션 기록 설정. */
export function RailCleanupSettings() {
  const [values, setValues] = createSignal<CatalogSettings | null>(null);
  const [error, setError] = createSignal('');
  const [saving, setSaving] = createSignal(false);
  onMount(() => {
    historyApi('/api/session-catalog?limit=1&meta=false', { elevated: false }).then((d) => setValues({
      auto_archive_days: Number(d.auto_archive_days) || 0, ended_cleanup_days: Number(d.ended_cleanup_days) || 7,
      auto_name: d.auto_name !== false, resume_ends_session: d.resume_ends_session !== false,
    })).catch((e) => setError(e?.message || t('설정을 읽지 못했습니다')));
  });
  const save = async (patch: Partial<CatalogSettings>) => {
    setError(''); setSaving(true);
    try {
      const r = await historyApi('/api/session-catalog/settings', { body: patch });
      setValues({ auto_archive_days: r.auto_archive_days, ended_cleanup_days: Number(r.ended_cleanup_days) || 7,
                  auto_name: r.auto_name !== false, resume_ends_session: r.resume_ends_session !== false });
    } catch (e: any) { setError(e?.message || t('작업을 완료하지 못했습니다')); }
    finally { setSaving(false); }
  };
  const [cleaning, setCleaning] = createSignal(false);
  const cleanAll = (days: number) => {
    setCleaning(true);
    archiveEnded({ olderThanDays: days }).catch((e: any) => setError(e?.message || t('정리하지 못했습니다'))).finally(() => setCleaning(false));
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
          <div class="vt-wtd-section-title">{t('종료 대화 정리')}</div>
          <label class="vt-railset-row">
            <span class="vt-railset-label">{t('「N일 지난 것만」의 기준')}</span>
            <select value={v().ended_cleanup_days} disabled={saving()} onChange={(e) => { void save({ ended_cleanup_days: Number(e.currentTarget.value) }); }}>
              <For each={CLEANUP_CHOICES.includes(v().ended_cleanup_days) ? CLEANUP_CHOICES : [...CLEANUP_CHOICES, v().ended_cleanup_days].sort((a, b) => a - b)}>
                {(d) => <option value={d} selected={d === v().ended_cleanup_days}>{t('{n}일', { n: d })}</option>}
              </For>
            </select>
          </label>
          <div class="vt-railset-row vt-railset-actions">
            <button type="button" class="vt-btn sm" disabled={cleaning()} onClick={() => cleanAll(v().ended_cleanup_days)}>{t('모든 저장소: {n}일 지난 것만 보관', { n: v().ended_cleanup_days })}</button>
            <button type="button" class="vt-btn sm" disabled={cleaning()} onClick={() => cleanAll(0)}>{t('모든 저장소: 모두 보관')}</button>
          </div>
          <div class="vt-railset-note">{t('각 저장소의 「종료 N」 줄 ⋯ 메뉴에서도 그 저장소만 정리할 수 있습니다. 실행 중인 대화는 건너뛰고, 정리한 뒤 알림에서 되돌릴 수 있습니다.')}</div>
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
