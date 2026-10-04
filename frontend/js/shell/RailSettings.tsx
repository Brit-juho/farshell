// 레일 설정 시트의 위쪽 구획 — 「개입 필요」 구역과 만든 곳별 표시 켜기/끄기.
// 아래의 저장소 표시는 RepoVisibility(같은 시트 안)가 이어서 그린다: 헤더 톱니가 예전에
// 열던 「저장소 관리」를 버리지 않고 이 시트의 한 구획으로 흡수했다.
//
// 값은 device 스코프 설정이라 기기마다 따로다(폰에서 끈 것이 맥에 새지 않는다).
// 지연 청크 제약상 core/settings.js는 window 브리지로만 읽는다(rail-prefs.ts).
import { For } from 'solid-js';

import { createRailPrefs } from './rail-prefs.js';
import {
  ATTENTION_SHOW_KEY, HIDEABLE_ORIGINS, ORIGIN_LABEL, originShowKey, type Origin,
} from './rail-data.js';

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

export function RailSettingsLead() {
  const { prefs, set } = createRailPrefs();
  const originOn = (o: Origin) => !prefs().hiddenOrigins.includes(o);
  return (
    <div class="vt-railset">
      <div class="vt-wtd-section-title">구역</div>
      <Toggle
        label="개입 필요 구역 표시"
        hint="입력을 기다리는 세션을 맨 위에 모아 보여줍니다"
        checked={prefs().attentionShow}
        onChange={(v) => set(ATTENTION_SHOW_KEY, v)}
      />
      <div class="vt-wtd-section-title">만든 곳별 표시</div>
      <For each={HIDEABLE_ORIGINS}>
        {(o) => (
          <Toggle
            label={ORIGIN_LABEL[o]}
            checked={originOn(o)}
            onChange={(v) => set(originShowKey(o), v)}
          />
        )}
      </For>
      <div class="vt-railset-note">끄더라도 열려 있는 세션과 입력을 기다리는 세션은 계속 보입니다.</div>
    </div>
  );
}
