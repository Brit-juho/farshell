// N34 §7 — HUD(리디자인 2단계부터 아래 상태 막대 24px)가 그릴 칩 목록을 만드는 **순수 함수**. DOM도
// fetch도 모른다: 여러 API 응답을 모아 놓은 평범한 객체 하나를 받아 칩 배열을
// 돌려준다. Hud.tsx는 이 결과를 그대로 렌더만 한다.
//
// 핵심 규칙(10-shell-layout.md §7): **값이 없는 항목은 숨긴다.** 사용량
// provider가 없으면 사용량 칩이 아예 없고, 터널이 안 돌면 터널 칩이 없다.
// "없음"이나 "—"를 그리지 않는다 — 2.0의 usage 게이팅과 같은 원칙이다.

import { t } from '../core/i18n.js';

export type HudTone = 'ok' | 'warn' | 'err' | 'plain';

// 배치(어떤 칩을 어느 쪽에 어떤 순서로)의 규칙은 hud-layout.js에 있다 — 설정 화면(일반 JS)도 같은 함수를
// 써야 하는데 테스트 하네스가 .ts를 직접 못 읽어서다. 여기서는 타입과 함께 다시 내보내기만 한다.
import {
  HUD_KINDS, PINNED_HUD_KINDS, ALERT_HUD_KINDS, DEFAULT_HUD_LAYOUT, parseHudLayout, serializeHudLayout, sameHudLayout, hudZoneOf,
  moveHudKind, stepHudKind,
} from './hud-layout.js';
import type { HudKind, HudLayout } from './hud-layout.js';

export {
  HUD_KINDS, PINNED_HUD_KINDS, ALERT_HUD_KINDS, DEFAULT_HUD_LAYOUT, parseHudLayout, serializeHudLayout, sameHudLayout, hudZoneOf,
  moveHudKind, stepHudKind,
};
export type { HudKind, HudLayout };
export type { HudZone } from './hud-layout.js';

export interface HudChip {
  id: string;
  /** 설정 화면이 다루는 종류(위 HUD_KINDS). 배치를 적용할 때만 쓴다. */
  kind: HudKind;
  label: string;
  value?: string;
  hint?: string;
  tooltip?: string;
  tone: HudTone;
  dot?: boolean;
  /** 클릭 시 발동할 core/dom.js 액션 id. 없으면 클릭 불가. */
  action?: string;
  /** 액션에 넘길 인자(예: `settings.section`의 섹션 id). */
  actionArg?: string;
  side: 'left' | 'right';
  /**
   * 자리가 모자랄 때 **버티는 순서**. 낮을수록 끝까지 남는다(1이 최우선).
   *
   * "값이 없으면 숨긴다"는 규칙과 다른 축이다 — 그건 "보여줄 게 없다"이고
   * 이건 "보여줄 건 있는데 폭이 없다"이다. 24px 한 줄이라 레일·dock이 넓으면
   * 칩 전체가 컨테이너를 넘치는데(실측: 900px 뷰포트에서 612px 칸에 667px),
   * 순위가 없으면 그냥 뒤에서부터 잘려 나간다 — 즉 **버전이 아니라 사용량이
   * 먼저 사라질 수도 있다.** 그래서 무엇이 마지막까지 남아야 하는지를
   * 데이터 쪽에서 정한다.
   *
   * 기준: 위험을 알리는 칩(err/warn)이 평상시 칩을 이긴다. 다른 데서 같은 걸
   * 볼 수 있으면(dock 탭이 있는 사용량·연결된 화면) 그만큼 양보할 수 있다.
   */
  priority: number;
}

/**
 * 순위표. 숫자를 칩마다 흩어 적으면 "이게 저것보다 위인가"를 매번 파일을
 * 뒤져 비교해야 하므로 한자리에 모은다.
 */
const PRIORITY = {
  /** 서버가 떠 있나 — 이게 없으면 나머지가 다 무의미하다. */
  server: 1,
  /** 한도가 임박한 사용량. 지금 손을 써야 하는 유일한 칩이라 서버 다음이다. */
  usageAlert: 2,
  /** 나 말고 다른 화면이 붙음 — 화면이 오락가락하는 원인이라 경고로 취급한다. */
  screensAlert: 3,
  /** 훅이 깨졌다 — 상태 배지·큐·TTS가 조용히 멎은 상태라 에이전트 개입 다음으로 끝까지 남는다. */
  hooksAlert: 2,
  /** 평상시 사용량 — dock 사용량 탭에 같은 값이 더 자세히 있다. */
  usage: 5,
  /** 이 기계의 CPU·RAM·네트워크 — 임계를 넘으면 경고 순위(screensAlert와 같은 자리)로 올라온다. */
  stats: 6,
  statsAlert: 3,
  /** 화면 1개(=나뿐). 알아두면 좋지만 없어도 곤란하지 않다. */
  screens: 6,
  /** 개발 중에만 켜는 토글들. 켠 사람은 켠 걸 안다. */
  devToggle: 7,
  /** 버전 — 설정 → 정보에 늘 있다. 가장 먼저 양보한다. */
  version: 8,
  /** 시계 — 맥 메뉴바 오른쪽 끝의 그 시계. OS 시계가 바로 옆에 있어 양보할 수 있지만 버전보다는 위다. */
  clock: 7,
  /** 푸시 알림 켜짐/꺼짐 — 설정에서도 바꿀 수 있어 평상시에는 양보한다. */
  push: 7,
  /** 개입이 필요한 에이전트 — 이 제품이 존재하는 이유다. 서버 다음으로 끝까지 남는다. */
  agentsAlert: 1,
  /** 개입 필요는 없고 작업 중·완료만 있을 때. */
  agents: 4,
} as const;

export interface HudInput {
  /** location.port — 서버 칩에 그대로 쓴다. */
  port?: string;
  /** /api/capabilities 응답. null이면 아직 로드 전(서버 칩도 안 그린다). */
  caps?: { version?: string; tunnel?: unknown } | null;
  /** term/e2e.js의 E2E_ENABLED — 켜졌을 때만 칩이 생긴다. */
  e2e?: boolean;
  /** /api/safe-mode 응답. */
  safeMode?: { enabled?: boolean } | null;
  /** /api/tmux/clients의 clients.length. 0이면 숨긴다. */
  screens?: number | null;
  /** /api/usage 응답. */
  usage?: UsageSnapshot | null;
  /** /api/system/stats 응답. 읽지 못한 항목은 null이고, 그 칩은 그리지 않는다. */
  stats?: SystemStats | null;
  /** 에이전트 상태 집계(agent/state.js의 4상태). 셋 다 0이면 칩이 없다. */
  agents?: AgentCounts | null;
  /** 훅 건강 점검(서버 hook_health) — CLI별 상태와 빠진·어긋난 이벤트 이름. 이상이 없으면 칩이 없다. */
  hooks?: HooksHealth | null;
  /** 푸시 알림 상태. `unavailable`(이 브라우저에서 못 씀)·null(아직 모름)이면 칩이 없다. */
  push?: 'on' | 'off' | 'unavailable' | null;
  /** 시계 칩에 쓸 지금 시각(ms). 없으면 시계 칩이 없다 — 순수 함수라 시계를 직접 읽지 않는다. */
  now?: number | null;
  /** 시계 표기에 쓸 로케일(예: `ko-KR`). 비면 브라우저 기본. */
  locale?: string;
}

export type HooksHealth = Record<string, { state: string; events?: string[] }>;

export interface AgentCounts {
  /** 입력 대기(승인·선택·질문) — 사람이 와야 풀린다. */
  waiting: number;
  working: number;
  /** 끝났는데 아직 이 브라우저에서 확인하지 않은 것. */
  done: number;
}

export interface SystemStats {
  cpu?: { pct: number; cores: number } | null;
  gpu?: { pct: number; name?: string | null; count?: number } | null;
  ram?: { used: number; total: number; pct: number } | null;
  net?: { rx_bps: number; tx_bps: number; interfaces?: string[] } | null;
}

interface UsageWindow {
  label?: string;
  pct?: number;
  resets_in_sec?: number | null;
}

interface UsageProfile {
  name?: string;
  active?: boolean;
  windows?: UsageWindow[];
}

interface UsageSnapshot {
  available?: boolean;
  profiles?: UsageProfile[];
}

// 초 → "4h12m" / "12m" / "곧". 서버가 resets_in_sec을 계산해 주므로(clauth.py
// 주석: 클라이언트 시계가 틀어져 있어도 맞게 보이도록) 여기서는 포맷만 한다.
export function formatResetsIn(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h${m}m`;
  if (m > 0) return `${m}m`;
  return t('곧');
}

// 사용량 프로필 하나 → 칩 하나. 창(window)이 여럿이면 **가장 많이 쓴 것**을
// 대표로 쓴다 — HUD는 한 줄이라 전부 못 싣고, 사용자가 알아야 하는 건 "지금
// 제일 먼저 바닥나는 것"이다. 자세한 건 dock 사용량 탭(60 §5)이 보여준다.
function usageChip(p: UsageProfile): HudChip | null {
  const windows = (p.windows || []).filter((w) => Number.isFinite(w.pct));
  if (!windows.length) return null;
  const top = windows.reduce((a, b) => ((b.pct ?? 0) > (a.pct ?? 0) ? b : a));
  const pct = Math.round(top.pct ?? 0);
  const name = (p.name || '').trim();
  if (!name) return null;

  const resets = formatResetsIn(top.resets_in_sec);
  // 임계값은 dock 게이지와 같은 감각으로: 90% 넘으면 위험, 75% 넘으면 주의.
  const tone: HudTone = pct >= 90 ? 'err' : pct >= 75 ? 'warn' : 'plain';
  return {
    id: `usage:${name}`,
    kind: 'usage',
    label: name,
    value: `${pct}%`,
    hint: resets ? `· ${resets}` : undefined,
    tooltip: resets ? t('· {resets} 후 초기화', { resets }) : undefined,
    tone,
    action: 'usage.open',
    side: 'right',
    // 순위가 tone을 따라간다 — 한도가 임박했을 때만 앞자리로 올라온다.
    priority: tone === 'plain' ? PRIORITY.usage : PRIORITY.usageAlert,
  };
}

// 초당 바이트 → "0" / "340K" / "1.2M" / "12M". 칩 폭을 지키려고 단위는 한 글자다(초당은 라벨이 말한다).
export function formatRate(bps: number | null | undefined): string {
  if (bps == null || !Number.isFinite(bps) || bps < 1) return '0';
  const units = ['B', 'K', 'M', 'G'];
  let v = bps;
  let i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1024; i += 1; }
  const n = v >= 100 || i === 0 ? Math.round(v) : Math.round(v * 10) / 10;
  return `${n}${units[i]}`;
}

// CPU·RAM·네트워크 칩. 읽지 못한 항목(null)은 그 칩을 아예 그리지 않는다 — 「—」를 그리지 않는 HUD의 규칙.
function statsChips(stats: SystemStats): HudChip[] {
  const out: HudChip[] = [];
  const level = (pct: number, warn: number, err: number): HudTone => (pct >= err ? 'err' : pct >= warn ? 'warn' : 'plain');
  const push = (id: string, kind: HudKind, label: string, value: string, tone: HudTone, tooltip?: string) => out.push({
    id, kind, label, value, tooltip, tone, action: 'system.open', side: 'right',
    priority: tone === 'plain' ? PRIORITY.stats : PRIORITY.statsAlert,
  });
  if (stats.cpu && Number.isFinite(stats.cpu.pct)) {
    push('stats:cpu', 'cpu', 'CPU', `${Math.round(stats.cpu.pct)}%`, level(stats.cpu.pct, 75, 90));
  }
  if (stats.gpu && Number.isFinite(stats.gpu.pct)) {
    push('stats:gpu', 'gpu', 'GPU', `${Math.round(stats.gpu.pct)}%`, level(stats.gpu.pct, 75, 90));
  }
  if (stats.ram && Number.isFinite(stats.ram.pct)) {
    push('stats:ram', 'ram', 'RAM', `${Math.round(stats.ram.pct)}%`, level(stats.ram.pct, 80, 90));
  }
  if (stats.net) {
    push('stats:net', 'net', 'NET', `\u2193${formatRate(stats.net.rx_bps)} \u2191${formatRate(stats.net.tx_bps)}`, 'plain');
  }
  return out;
}

// 시계 문구 — "11월 3일 (화) 14:05". 날짜까지 싣는 이유는 맥 메뉴바와 같다: 터미널 앞에 오래 앉아 있으면
// 며칠째 같은 화면인지 잊는다. 24시간제로 고정한다 — 로케일이 12시간제여도 칩 폭이 요일·AM/PM으로 흔들리지 않게.
export function formatClock(now: number, locale?: string): string {
  const d = new Date(now);
  if (Number.isNaN(d.getTime())) return '';
  try {
    const date = new Intl.DateTimeFormat(locale || undefined, { month: 'short', day: 'numeric', weekday: 'short' }).format(d);
    const time = new Intl.DateTimeFormat(locale || undefined, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d);
    return `${date} ${time}`;
  } catch (_) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
}

// 에이전트 집계 칩 — 「개입 2 · 작업 1 · 완료 1」. 0인 항목은 적지 않고, 셋 다 0이면 칩을 안 만든다.
// 개입이 있으면 값(굵은 글씨)이 개입 수이고 주의 색이다. 없으면 가장 급한 것(작업 > 완료)이 값이 된다.
function agentsChip(c: AgentCounts): HudChip | null {
  const waiting = Math.max(0, c.waiting | 0);
  const working = Math.max(0, c.working | 0);
  const done = Math.max(0, c.done | 0);
  if (!waiting && !working && !done) return null;
  const parts: Array<[string, number]> = [];
  if (waiting) parts.push([t('개입'), waiting]);
  if (working) parts.push([t('작업'), working]);
  if (done) parts.push([t('완료'), done]);
  const [label, value] = parts[0];
  const rest = parts.slice(1).map(([l, n]) => `${l} ${n}`).join(' · ');
  const tooltip = waiting
    ? t('입력을 기다리는 세션 {n}개 — 누르면 가장 오래 기다린 세션으로 이동', { n: waiting })
    : t('에이전트 상태 — 작업 중이거나 확인하지 않은 완료');
  return {
    id: 'agents',
    kind: 'agents',
    label,
    value: String(value),
    hint: rest ? `· ${rest}` : undefined,
    tooltip,
    tone: waiting ? 'warn' : 'plain',
    // 개입할 게 없을 때 누르면 아무 데도 못 가므로 클릭 불가로 둔다.
    action: waiting ? 'agents.jump' : undefined,
    side: 'left',
    priority: waiting ? PRIORITY.agentsAlert : PRIORITY.agents,
  };
}

// 훅 점검 칩 — 이상(빠짐·어긋남·읽기 실패)이 있는 CLI 이름만 값으로 적는다. 이상이 없거나 그 CLI를 안 쓰면(absent) 칩이 없다.
const HOOK_BAD = new Set(['missing', 'mismatch', 'unreadable']);
const HOOK_NAMES: Record<string, string> = { claude: 'Claude', codex: 'Codex' };
function hooksChip(h: HooksHealth): HudChip | null {
  const bad = Object.entries(h).filter(([, v]) => v && HOOK_BAD.has(v.state)).map(([cli]) => HOOK_NAMES[cli] || cli);
  if (!bad.length) return null;
  return {
    id: 'hooks',
    kind: 'hooks',
    label: t('훅 점검 필요'),
    value: bad.join(' · '),
    tooltip: t('에이전트 훅이 빠졌거나 어긋났습니다 — 누르면 설정 → 정보에서 이유와 고치는 방법을 봅니다'),
    tone: 'warn',
    action: 'settings.section',
    actionArg: 'about',
    side: 'left',
    priority: PRIORITY.hooksAlert,
  };
}

export function buildHudChips(input: HudInput, layout: HudLayout = DEFAULT_HUD_LAYOUT): HudChip[] {
  const chips: HudChip[] = [];

  // ── 좌측: "지금 이 서버가 어떤 상태인가" ────────────────────────────────
  // capabilities가 아직 안 왔으면 서버 칩 자체를 안 그린다 — 부팅 중에 잘못된
  // 상태(예: 터널 없음)를 잠깐 보여주는 게 아무것도 안 보여주는 것보다 나쁘다.
  if (input.caps) {
    chips.push({
      id: 'server',
      kind: 'server',
      label: t('서버'),
      value: input.port ? `:${input.port}` : '',
      tone: 'ok',
      dot: true,
      side: 'left',
      priority: PRIORITY.server,
    });
  }

  if (input.e2e) {
    chips.push({ id: 'e2e', kind: 'e2e', label: 'E2E', value: 'ON', tone: 'ok', side: 'left', priority: PRIORITY.devToggle });
  }

  if (input.safeMode?.enabled) {
    chips.push({ id: 'safe-mode', kind: 'safe-mode', label: t('세이프모드'), value: 'ON', tone: 'ok', side: 'left', priority: PRIORITY.devToggle });
  }

  // 연결된 화면 — 2.0에서는 clients.length < 2면 패널 자체를 숨겨서 "내 화면
  // 말고 누가 더 붙어 있나"를 확인할 방법이 사실상 없었다. HUD에서는 1개여도
  // 보여준다(내 화면 1개라는 사실 자체가 정보다). 0은 조회 실패이므로 숨긴다.
  if (typeof input.screens === 'number' && input.screens > 0) {
    chips.push({
      id: 'screens',
      kind: 'screens',
      label: t('연결된 화면'),
      value: String(input.screens),
      // 나 말고 다른 화면이 붙어 있으면 주의 색 — tmux가 가장 작은 클라이언트에
      // 맞춰 리레이아웃하므로 "화면이 오락가락"의 원인이 바로 이 상태다.
      tone: input.screens > 1 ? 'warn' : 'plain',
      action: 'clients.show',
      side: 'left',
      priority: input.screens > 1 ? PRIORITY.screensAlert : PRIORITY.screens,
    });
  }

  // ── 우측: 사용량 + 버전 ────────────────────────────────────────────────
  if (input.stats) chips.push(...statsChips(input.stats));

  if (input.usage?.available) {
    for (const p of input.usage.profiles || []) {
      const chip = usageChip(p);
      if (chip) chips.push(chip);
    }
  }

  if (input.agents) {
    const chip = agentsChip(input.agents);
    if (chip) chips.push(chip);
  }

  if (input.hooks) {
    const chip = hooksChip(input.hooks);
    if (chip) chips.push(chip);
  }

  // 푸시: 이 브라우저에서 못 쓰면(비보안 컨텍스트·미지원) 칩 자체가 없다 — 눌러 봐야 사유만 나오는 칩은 소음이다.
  if (input.push === 'on' || input.push === 'off') {
    const on = input.push === 'on';
    chips.push({
      id: 'push', kind: 'push', label: t('알림'), value: on ? t('켜짐') : t('꺼짐'),
      tooltip: on ? t('푸시 알림 끄기') : t('푸시 알림 켜기'),
      tone: on ? 'ok' : 'plain', action: 'push.toggle', side: 'right', priority: PRIORITY.push,
    });
  }

  const version = (input.caps?.version || '').trim();
  if (version) {
    chips.push({ id: 'version', kind: 'version', label: '', value: `v${version}`, tone: 'plain', side: 'right', priority: PRIORITY.version });
  }

  if (typeof input.now === 'number') {
    const text = formatClock(input.now, input.locale);
    if (text) chips.push({ id: 'clock', kind: 'clock', label: '', value: text, tone: 'plain', side: 'right', priority: PRIORITY.clock });
  }

  // 배치 적용 — 고정 종류(에이전트)가 늘 맨 왼쪽이고, 나머지는 칩이 스스로 정한 side를 무시하고 사용자가
  // 정한 구역·순서를 따른다. 배치에 없는 종류는 여기서 빠진다. 배치에 고정 종류가 적혀 있어도(옛 저장값을
  // 손으로 넘긴 경우) 두 번 그리지 않는다. 같은 종류의 칩이 여럿(사용량 프로필)이면 만든 순서를 유지한다.
  const pinned = new Set<string>(PINNED_HUD_KINDS);
  const placed: HudChip[] = [];
  for (const kind of [...PINNED_HUD_KINDS, ...ALERT_HUD_KINDS]) {
    for (const c of chips) if (c.kind === kind) placed.push({ ...c, side: 'left' });
  }
  for (const side of ['left', 'right'] as const) {
    for (const kind of layout[side]) {
      if (pinned.has(kind)) continue;
      for (const c of chips) if (c.kind === kind) placed.push({ ...c, side });
    }
  }
  return placed;
}

