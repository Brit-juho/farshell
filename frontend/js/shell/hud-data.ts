// N34 §7 — HUD(상태바 24px)가 그릴 칩 목록을 만드는 **순수 함수**. DOM도
// fetch도 모른다: 여러 API 응답을 모아 놓은 평범한 객체 하나를 받아 칩 배열을
// 돌려준다. Hud.tsx는 이 결과를 그대로 렌더만 한다.
//
// 핵심 규칙(10-shell-layout.md §7): **값이 없는 항목은 숨긴다.** 사용량
// provider가 없으면 사용량 칩이 아예 없고, 터널이 안 돌면 터널 칩이 없다.
// "없음"이나 "—"를 그리지 않는다 — 2.0의 usage 게이팅과 같은 원칙이다.

export type HudTone = 'ok' | 'warn' | 'err' | 'plain';

export interface HudChip {
  id: string;
  label: string;
  value?: string;
  hint?: string;
  tone: HudTone;
  dot?: boolean;
  /** 클릭 시 발동할 core/dom.js 액션 id. 없으면 클릭 불가. */
  action?: string;
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
  /** 바깥에서 접근 가능한 상태인가. 보안 감각에 직결된다. */
  tunnel: 4,
  /** 평상시 사용량 — dock 사용량 탭에 같은 값이 더 자세히 있다. */
  usage: 5,
  /** 화면 1개(=나뿐). 알아두면 좋지만 없어도 곤란하지 않다. */
  screens: 6,
  /** 개발 중에만 켜는 토글들. 켠 사람은 켠 걸 안다. */
  devToggle: 7,
  /** 버전 — 설정 → 정보에 늘 있다. 가장 먼저 양보한다. */
  version: 8,
} as const;

export interface HudInput {
  /** location.port — 서버 칩에 그대로 쓴다. */
  port?: string;
  /** /api/capabilities 응답. null이면 아직 로드 전(서버 칩도 안 그린다). */
  caps?: { version?: string; tunnel?: unknown } | null;
  /** /api/tunnel/status 응답. */
  tunnel?: { running?: boolean; mode?: string; url?: string } | null;
  /** term/e2e.js의 E2E_ENABLED — 켜졌을 때만 칩이 생긴다. */
  e2e?: boolean;
  /** /api/safe-mode 응답. */
  safeMode?: { enabled?: boolean } | null;
  /** /api/tmux/clients의 clients.length. 0이면 숨긴다. */
  screens?: number | null;
  /** /api/usage 응답. */
  usage?: UsageSnapshot | null;
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
  return '곧';
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
    label: name,
    value: `${pct}%`,
    hint: resets ? `· ${resets} 후 초기화` : undefined,
    tone,
    action: 'usage.open',
    side: 'right',
    // 순위가 tone을 따라간다 — 한도가 임박했을 때만 앞자리로 올라온다.
    priority: tone === 'plain' ? PRIORITY.usage : PRIORITY.usageAlert,
  };
}

export function buildHudChips(input: HudInput): HudChip[] {
  const chips: HudChip[] = [];

  // ── 좌측: "지금 이 서버가 어떤 상태인가" ────────────────────────────────
  // capabilities가 아직 안 왔으면 서버 칩 자체를 안 그린다 — 부팅 중에 잘못된
  // 상태(예: 터널 없음)를 잠깐 보여주는 게 아무것도 안 보여주는 것보다 나쁘다.
  if (input.caps) {
    chips.push({
      id: 'server',
      label: '서버',
      value: input.port ? `:${input.port}` : '',
      tone: 'ok',
      dot: true,
      side: 'left',
      priority: PRIORITY.server,
    });
  }

  if (input.tunnel?.running) {
    chips.push({
      id: 'tunnel',
      label: '터널',
      value: input.tunnel.mode === 'named' ? 'named' : '익명',
      tone: 'plain',
      side: 'left',
      priority: PRIORITY.tunnel,
    });
  }

  if (input.e2e) {
    chips.push({ id: 'e2e', label: 'E2E', value: 'ON', tone: 'ok', side: 'left', priority: PRIORITY.devToggle });
  }

  if (input.safeMode?.enabled) {
    chips.push({ id: 'safe-mode', label: '세이프모드', value: 'ON', tone: 'ok', side: 'left', priority: PRIORITY.devToggle });
  }

  // 연결된 화면 — 2.0에서는 clients.length < 2면 패널 자체를 숨겨서 "내 화면
  // 말고 누가 더 붙어 있나"를 확인할 방법이 사실상 없었다. HUD에서는 1개여도
  // 보여준다(내 화면 1개라는 사실 자체가 정보다). 0은 조회 실패이므로 숨긴다.
  if (typeof input.screens === 'number' && input.screens > 0) {
    chips.push({
      id: 'screens',
      label: '연결된 화면',
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
  if (input.usage?.available) {
    for (const p of input.usage.profiles || []) {
      const chip = usageChip(p);
      if (chip) chips.push(chip);
    }
  }

  const version = (input.caps?.version || '').trim();
  if (version) {
    chips.push({ id: 'version', label: '', value: `v${version}`, tone: 'plain', side: 'right', priority: PRIORITY.version });
  }

  return chips;
}
