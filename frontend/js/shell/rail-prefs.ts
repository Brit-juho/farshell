// 레일 표시 설정(개입 필요 구역·만든 곳별 표시)을 Solid 시그널로 거울처럼 든다.
// 정본은 device 스코프 설정 저장소(core/settings.js)다. 지연 청크는 core를 정적
// import 할 수 없어 window 브리지(vtSettingsGet/Set/Subscribe)로만 읽는다.
//
// 값이 없을 때(브리지가 아직 안 떴을 때 포함)는 RAIL_PREF_DEFAULTS로 떨어지고,
// 설정이 늦게 도착하면 구독이 다시 맞춘다 — Boolean(undefined)가 기본값을 조용히
// 뒤집던 레이스(2026-09-23)를 반복하지 않는다.
import { createSignal, onCleanup } from 'solid-js';

import { readRailPrefs, type RailPrefs } from './rail-data.js';

export function createRailPrefs() {
  const read = (): RailPrefs => readRailPrefs((k) => (window as any).vtSettingsGet?.(k));
  const [prefs, setPrefs] = createSignal<RailPrefs>(read());
  const unsub = (window as any).vtSettingsSubscribe?.((changed: any) => {
    if (!changed) return;
    if (!Object.keys(changed).some((k) => k.startsWith('rail.'))) return;
    setPrefs(read());
  });
  onCleanup(() => unsub?.());
  const set = (key: string, value: boolean | string) => {
    (window as any).vtSettingsSet?.(key, value);
    setPrefs(read());
  };
  return { prefs, set };
}
