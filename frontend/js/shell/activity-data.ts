// 활동 막대의 항목 — 무엇이 있고, 어느 패널을 열고, 어떤 capability에 걸리는가(데이터만).
//
// 위 무리는 사이드바를 바꾸는 **활동**(role="tab")이다. 「세션」은 지금 레일(Rail.tsx)을,
// 나머지는 지금 dock 본문(Dock.tsx — 기존 패널 렌더러)을 사이드바에 띄운다. 아래 무리는
// 사이드바를 바꾸지 않는 **동작**(MCP 팝업·음성·설정)이라 탭이 아니다.
//
// 도구 활동의 panelId·action은 옛 dock 탭과 같다 — 팔레트·키맵·패널 모듈이 이 id들을
// 그대로 부르고, panels/panel.js가 `window.vtDockHost`에 붙일 자리를 묻는다.
import { t } from '../core/i18n.js';
import { SESSIONS_ACTIVITY } from './side-prefs.js';

export type ActivityCap = 'ports' | 'usage';
export type ActivityBadge = 'queue' | 'ports';

export interface Activity {
  id: string;
  label: string;
  /** ui/icons.js(또는 ui/icons-shell.js) 이름. */
  icon: string;
  /** 기존 패널 렌더러의 루트 id. 세션은 null(레일이 그린다). */
  panelId: string | null;
  /** 그 패널을 여는 액션 id(팔레트·키맵이 참조하는 그 id). */
  action: string | null;
  /** /api/capabilities에 없으면 항목 자체가 사라진다. */
  cap?: ActivityCap;
  badge?: ActivityBadge;
}

export const ACTIVITIES: Activity[] = [
  { id: SESSIONS_ACTIVITY, label: t('세션'), icon: 'list', panelId: null, action: null },
  { id: 'scm', label: t('변경'), icon: 'git-branch', panelId: 'vt-dock-scm', action: 'scm.show' },
  { id: 'dir', label: t('파일'), icon: 'folder', panelId: 'vt-dir', action: 'dir.show' },
  { id: 'queue', label: t('큐'), icon: 'clipboard', panelId: 'vt-queue', action: 'queue.show', badge: 'queue' },
  { id: 'files', label: t('첨부'), icon: 'paperclip', panelId: 'vt-files', action: 'files.show' },
  { id: 'snippets', label: t('스니펫'), icon: 'braces', panelId: 'vt-snippets', action: 'snippets.show' },
  { id: 'ports', label: t('포트'), icon: 'plug', panelId: 'vt-ports', action: 'ports.show', cap: 'ports', badge: 'ports' },
  { id: 'usage', label: t('사용량'), icon: 'gauge', panelId: 'vt-usage', action: 'usage.open', cap: 'usage' },
  { id: 'system', label: t('시스템'), icon: 'bar-chart', panelId: 'vt-system', action: 'system.open' },
];

/** 도구 활동(세션 제외) — Dock.tsx가 패널을 여닫는 대상. */
export const TOOL_ACTIVITIES: Activity[] = ACTIVITIES.filter((a) => a.panelId);

/**
 * 지금 보일 활동. capabilities 응답 전(null)에는 게이팅 대상을 그리지 않는다 —
 * 나타났다 사라지는 것보다 늦게 나타나는 쪽이 낫다(옛 dock 탭과 같은 규칙).
 */
export function availableActivities(caps: Record<string, unknown> | null): Activity[] {
  return ACTIVITIES.filter((a) => !a.cap || (caps ? !!caps[a.cap] : false));
}

export function activityById(id: string): Activity | undefined {
  return ACTIVITIES.find((a) => a.id === id);
}

export function activityByPanelId(panelId: string): Activity | undefined {
  return ACTIVITIES.find((a) => a.panelId === panelId);
}

/** 배지 수치의 툴팁 문구. 수치가 없으면 undefined. */
export function badgeTip(a: Activity, n: number): string | undefined {
  if (!a.badge || !n) return undefined;
  return a.badge === 'queue' ? t('{n}개 대기', { n }) : t('{n}개 열림', { n });
}

/**
 * ↑↓·Home·End로 옮겨 갈 다음 활동의 위치(순환). 활동 막대는 「수동 활성화」 탭 목록이라
 * 화살표는 초점만 옮기고, 여는 것은 Enter·Space(=버튼 클릭)다.
 */
export function stepActivityIndex(current: number, key: string, count: number): number {
  if (count <= 0) return -1;
  const i = current < 0 ? 0 : current;
  if (key === 'ArrowDown') return (i + 1) % count;
  if (key === 'ArrowUp') return (i - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return i;
}
