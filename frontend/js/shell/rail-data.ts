// N36 §5 — 워크트리 레일의 순수 로직. DOM도 fetch도 모른다 — 여러 소스(세션
// 목록·에이전트 상태·git status·워크트리 API)를 합친 평범한 입력을 받아
// 그룹핑된 행 목록을 돌려준다.
//
// 2.1.1(N8/N44)부터 buildRailSections는 제네릭이다 — Fleet.tsx(모바일 플릿
// 홈, 70-mobile.md)는 여전히 "행 = 세션" 모양(RailRowInput/RailRow, 아래)을
// 그대로 쓰고, Rail.tsx(데스크톱)는 "행 = Worktree"(WorktreeRailRow) 또는
// "행 = 기타 세션"(OtherRailRow)의 합집합(DesktopRailRowInput)을 넘긴다.
// 그룹핑·정렬·문장 로직은 status/since/tool만 보므로 두 모양 모두에 그대로
// 재사용된다 — Fleet.tsx의 계약(정확히 RailRowInput 모양)을 바꾸지 않는다.
import type { AgentState } from '../core/types.js';

export type RailGroup = 'attention' | 'working' | 'idle';

export interface RailRowInput {
  sessionId: string;
  tmuxName: string | null;
  name: string;
  status: AgentState;
  /** since epoch초 — 상태 진입 시각. 상대시간·정렬 둘 다 이걸로. */
  since: number | null;
  /** 진행 중인·마지막 도구 이름("Edit" 등). working에서만 의미 있다. */
  tool: string | null;
  diffFiles: number | null; // git 아니거나 아직 조회 전이면 null
  /** 2.1.6 — 이 세션에 떠 있는 CLI 이름. 데스크톱 레일 행과 폰의 플릿 행이
   * **헤더 탭과 같은 마크**를 달기 위해 필요하다. 모르면 null이고, 그때는
   * 마크를 그리지 않는다(agent-shell로 떨어뜨리지 않는다 — "셸이다"와
   * "아직 모른다"는 다르고, 후자에 확신 있는 마크를 그리면 거짓말이 된다.
   * agent/badges.js가 같은 이유로 같은 선택을 했다). */
  agent?: string | null;
  /** Codex App Server가 직접 상태를 보내는 세션인가. 관찰형과 구분한다. */
  managed?: boolean;
  /** 구조화 이벤트가 주는 표시용 대화 단계(기존 5개 상태를 대체하지 않는다). */
  phase?: string | null;
  /** N38(70-mobile.md §2) — waiting 상태에서 감지된 질문 1줄. Fleet.tsx가
   * 이 필드로 인라인 승인 버튼을 그린다. 선택 필드라 Rail.tsx는 안 넘겨도
   * 그대로 동작한다. */
  question?: string | null;
  /** toml `options` 캡처로 뽑은 번호 선택지. 못 뽑았으면 null(또는 undefined)
   * — 그 경우 Fleet.tsx는 버튼 대신 「터미널로」를 그린다. */
  options?: { key: string; label: string }[] | null;
}

export interface RailRow extends RailRowInput {
  statusSentence: string;
}

export interface RailSectionOut<TRow = RailRow> {
  group: RailGroup;
  rows: TRow[];
}

// 98 §2 — 「유휴」를 버렸다. 사용자가 두 번 "유휴가 뭐냐"고 물었고, 그 13행은
// "세션은 있는데 노는 중"이 아니라 **세션이 아예 없는 것**이었다(§0-2). 라벨이
// 사실과 달랐던 것이지 표현이 어려웠던 게 아니다. `done`은 §6-1 확정으로
// 「작업 중」으로 옮겨서, 이 그룹에는 정말로 안 열린 것만 남는다.
const GROUP_LABEL: Record<RailGroup, string> = {
  attention: '개입 필요',
  working: '작업 중',
  idle: '열려 있지 않음',
};
export { GROUP_LABEL };

// §2 — 접을 수 있는 그룹. `attention`은 여기 없다: 승인 대기가 접힌 채 숨으면
// 그 그룹이 존재하는 이유가 사라진다(접기 버튼 자체를 안 그린다).
export const COLLAPSIBLE_GROUPS: RailGroup[] = ['working', 'idle'];
export function groupCollapseKey(group: RailGroup): string {
  return `rail.group.${group}.collapsed`;
}

/** 설정을 아직 못 읽었을 때 쓸 기본값.
 *
 * ⚠ `core/settings.js`의 SCHEMA `def`와 **같은 값이어야 한다**. 지연 청크는
 * core를 정적 import할 수 없어(Rail.tsx 머리말) 여기 한 벌 더 적는다 —
 * 어긋나면 `rail-data.test.js`의 「기본값이 스키마와 같다」가 실패한다.
 *
 * 왜 필요한가(실측): Rail이 마운트되는 시점에 `window.vtSettingsGet`이 아직
 * 없을 수 있다. `Boolean(undefined)`는 false라, 그 순간 "기본은 접힘"이
 * 조용히 "펼침"으로 뒤집혔다 — 같은 화면이 새로고침마다 다르게 열리는
 * 레이스였다. 값이 없으면 스키마 기본으로 떨어지고, 설정이 늦게 도착하면
 * 구독(`vtSettingsSubscribe`)이 다시 맞춘다. */
export const GROUP_COLLAPSED_DEFAULT: Record<string, boolean> = {
  working: false,
  idle: true,
};

// 10-shell-layout.md §5: "그룹 순서 고정: 개입 필요(waiting·error) →
// 작업 중(working) → 유휴(idle·done)".
//
// ⚠ 2026-09-18(98 §6-1) — **`done`을 「작업 중」으로 옮겼다.** 10 §5의 원래
// 결정("done은 유휴에 남는다")을 뒤집은 것이다. 유휴 그룹의 이름이
// 「열려 있지 않음」이 되면서 완료된 세션이 그 이름에 안 맞게 됐다 — 완료는
// 열려 있는 세션이고, 오히려 결과를 보러 가야 하는 쪽에 가깝다.
function groupOf(status: AgentState): RailGroup {
  if (status === 'waiting' || status === 'error') return 'attention';
  if (status === 'working' || status === 'done') return 'working';
  return 'idle';
}

// 30-worktree.md §4: "상태는 sessions 중 가장 '시급한' 것(waiting > error >
// working > done > idle)". 워크트리 행이 여러 세션을 대표할 때 쓴다. 세션이
// 하나도 없으면(흐리게 표시되는 행) 'idle'로 취급한다 — groupOf가 idle을
// 유휴로 보내는 것과 같은 결이다.
const URGENCY: AgentState[] = ['waiting', 'error', 'working', 'done', 'idle'];
export function mostUrgentStatus(statuses: AgentState[]): AgentState {
  if (statuses.length === 0) return 'idle';
  let best = statuses[0];
  for (const s of statuses) {
    if (URGENCY.indexOf(s) < URGENCY.indexOf(best)) best = s;
  }
  return best;
}

export function formatRelativeTime(sinceEpochSec: number | null, nowMs = Date.now()): string {
  if (sinceEpochSec == null) return '';
  const sec = Math.max(0, Math.round(nowMs / 1000 - sinceEpochSec));
  if (sec < 60) return `${sec}초`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}분`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}시간`;
  return `${Math.round(hr / 24)}일`;
}

export function statusSentence(status: AgentState, since: number | null, tool: string | null, nowMs = Date.now()): string {
  const rel = formatRelativeTime(since, nowMs);
  switch (status) {
    case 'waiting': return rel ? `승인 대기 · ${rel}` : '승인 대기';
    case 'working': return tool ? `${tool} 실행 · ${rel}` : (rel ? `작업 중 · ${rel}` : '작업 중');
    case 'error': return '에러';
    case 'done': return '완료';
    // idle은 **그룹 헤더가 이미 「유휴」라고 말한다**(Rail.tsx의 vt-wgrail-group-head,
    // Fleet.tsx의 vt-fleet-group-head는 개수까지 붙인다). 행마다 또 "유휴"를
    // 쓰면 목록 전체가 같은 단어로 덮여 이름을 훑는 눈을 방해한다 — 실제로
    // 저장소 13개 화면이 "유휴"를 14번 반복하고 있었다. 계획서가 상태 dot에
    // 대해 정한 규칙("idle이면 dot을 안 그린다 — 상시로 붙은 회색 점은 정보가
    // 아니라 노이즈다")을 글자에도 그대로 적용한다.
    // 마지막 활동 시각은 그룹 헤더가 말해주지 않는 **행별 정보**라 남긴다.
    default: return rel || '';
  }
}

// 그룹 순서 고정 + 그룹 안은 최근 활동순(since 내림차순, 없으면 맨 뒤).
// 제네릭: status/since/tool만 요구한다 — Fleet.tsx의 RailRowInput, Rail.tsx의
// OtherRailRowInput(원격 호스트 경로), remoteSessionRows 전부 이 최소 모양을
// 만족한다. 로컬 세션 경로(ADR-29 B)는 buildSessionSections를 쓴다.
export function buildRailSections<T extends { status: AgentState; since: number | null; tool: string | null }>(
  inputs: T[],
  nowMs = Date.now(),
): RailSectionOut<T & { statusSentence: string }>[] {
  const rows: (T & { statusSentence: string })[] = inputs.map((r) => ({
    ...r,
    statusSentence: statusSentence(r.status, r.since, r.tool, nowMs),
  }));

  const buckets: Record<RailGroup, (T & { statusSentence: string })[]> = { attention: [], working: [], idle: [] };
  for (const row of rows) buckets[groupOf(row.status)].push(row);

  const order: RailGroup[] = ['attention', 'working', 'idle'];
  for (const g of order) {
    buckets[g].sort((a, b) => (b.since ?? -Infinity) - (a.since ?? -Infinity));
  }
  return order.map((group) => ({ group, rows: buckets[group] })).filter((s) => s.rows.length > 0);
}

// ---------------------------------------------------------------------------
// ADR-29 B — 데스크톱 레일(Rail.tsx) 전용 행 모양. "행 = 세션"(더 이상
// 워크트리가 아니다 — 그 결정이 ADR-20이었고, ADR-29가 대체했다). 세션 하나가
// 워크트리에 속하면 그 저장소·브랜치를 배지로 달고 다닐 뿐, 행 자체의 정체는
// 언제나 세션이다. Fleet.tsx는 이 타입을 안 쓴다(모바일은 원래부터 세션
// 단위, 70-mobile.md 범위 밖 — `RailRowInput` 그대로).
//
// `remote?: boolean`(C1, 다른 호스트의 세션)는 뜻이 전혀 다른 별개 필드다.
export interface OtherRailRowInput {
  kind: 'session';
  /** 웹 세션 id. **잠든 세션은 빈 문자열**(브라우저 탭이 없다) — `awake`로
   * 그 뜻을 명시적으로도 들고 있다(빈 문자열 하나로 두 가지를 겹쳐 읽지
   * 않기 위해서). */
  sessionId: string;
  tmuxName: string | null;
  name: string;
  /** 이 브라우저에 웹 세션이 있는가 — ADR-29의 1차 축. false면 레일의
   * 「잠자는 중」 구역에 산다. tmux에는 살아 있지만 이 기기에서 안 열어둔
   * 상태(다른 기기가 열어뒀을 수도 있다 — 재우기/깨우기는 기기별 개념). */
  awake: boolean;
  /** 유효 그룹 id — 2026-09-18 후속(그룹 재정의, 사용자 요청: "같은 화면에
   * 묶인 것만 그룹"). 더 이상 태그(`@fsh_grp`)가 아니라, 이 세션이 지금
   * 어느 탭(pane 트리)에 다른 세션과 같이 떠 있으면 그 **탭 id** — 혼자면
   * null(「묶지 않음」). Rail.tsx가 `window.getTabGroups()`로 매 렌더 계산해
   * 채운다. */
  groupId: string | null;
  status: AgentState;
  since: number | null;
  tool: string | null;
  diffFiles: number | null;
  /** GET /api/repos가 주는 워크트리의 changed 요약(add/del 라인 수) — 있으면
   * diffFiles보다 우선한다(30 §4 원문 "+142 −38" 서식). 이 세션이 속한
   * 워크트리를 못 찾았으면 null이고, 그때는 diffFiles(파일 개수)로 떨어진다. */
  changed?: { files: number; add: number; del: number } | null;
  question?: string | null;
  options?: { key: string; label: string }[] | null;
  /** 2.1.6 — 이 세션에 떠 있는 CLI 이름. 모르면 null/undefined — "셸"과
   * "모름"은 다르다. */
  agent?: string | null;
  /** Codex App Server의 구조화 이벤트를 직접 받는 관리형 세션인가. */
  managed?: boolean;
  phase?: string | null;
  /** 이 세션이 속한 워크트리의 저장소 이름·브랜치(있으면). 배지 표시와
   * 그룹 라벨 폴백(사용자가 그룹 이름을 안 지었을 때)의 입력이다. 워크트리에
   * 안 속하는 세션(순수 셸 등)은 셋 다 null. */
  repoName?: string | null;
  branch?: string | null;
  isMainWorktree?: boolean;
  /** ADR-29 D — 이 세션이 속한 워크트리의 id. 세션을 열 때 그 그룹의 탭에
   * "지금 보는 워크트리"로 넘겨 pane 헤더 브랜치 칩이 맞게 뜨도록 한다
   * (layout/store.js의 setTabWorktree). 워크트리에 안 속하면 null. */
  worktreeId?: string | null;
  /** C1 — 다른 호스트의 세션. 로컬 세션 id 경로(switchTo 등)가 성립하지 않으므로
   * 클릭·컨텍스트 메뉴가 막히고 행이 흐리게 그려진다(원격 attach는 멀티호스트
   * 3단계). 선택 필드라 기존 호출부는 그대로 동작한다. */
  remote?: boolean;
}

export type DesktopRailRowInput = OtherRailRowInput;

// ---------------------------------------------------------------------------
// ADR-29 B — 세션 행을 그룹 섹션으로 묶는다. buildRailSections(위)와는 다른
// 축이다: 그건 "얼마나 급한가"로 3버킷, 이건 "누구 것인가"로 N버킷 + 「개입
// 필요」 고정 뷰 하나. 같은 세션이 「개입 필요」와 자기 그룹 양쪽에 다 나온다
// — 그룹 안에 접혀 승인 대기가 묻히면 안 된다(최종 설계안 §4).

export interface SessionSectionOut {
  /** React/Solid key + 접기 상태 키로 쓴다. attention='attention',
   * ungrouped='ungrouped', 그 외에는 groupId 그대로. */
  key: string;
  kind: 'attention' | 'group' | 'ungrouped';
  groupId: string | null;
  label: string;
  rows: (OtherRailRowInput & { statusSentence: string })[];
}

/** 깨어있는 세션만 대상으로 한다 — 잠자는 세션은 buildSleepingEntries가
 * 별도로 다룬다(둘을 한 함수에 넣으면 "그룹 안에 잠든 게 흐리게 끼어드는"
 * 1판의 실수를 반복하기 쉽다).
 *
 * `groupLabels`는 호출자(Rail.tsx)가 `window.getTabGroups()`로 이미 계산해
 * 넘긴다 — 2026-09-18 후속(그룹 재정의)으로 그룹은 태그가 아니라 화면(탭)에서
 * 실시간으로 도출되므로, 그 계산 자체는 여기(순수 데이터 함수)가 아니라
 * layout/store.js의 몫이다. 이 함수는 이미 정해진 groupId·label을 가진
 * 행들을 섹션으로 접는 것만 한다. */
export function buildSessionSections(
  inputs: OtherRailRowInput[],
  groupLabels: Map<string, string>,
  nowMs = Date.now(),
): SessionSectionOut[] {
  const awake = inputs.filter((r) => r.awake);
  const withSentence = awake.map((r) => ({ ...r, statusSentence: statusSentence(r.status, r.since, r.tool, nowMs) }));
  // 2026-09-18 후속 — 정렬 기준을 최근 활동(since)에서 이름으로 바꿨다.
  // since로 정렬하면 상태가 바뀔 때마다(4~5초 폴링) 순서가 흔들려 행이
  // 오르락내리락했다(사용자 지적: "뭐 할 때마다 움직여서 불편하다"). 이름
  // 정렬은 그 값이 바뀌지 않는 한 순서가 절대 안 흔들린다.
  const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name);

  const sections: SessionSectionOut[] = [];

  const attentionRows = withSentence.filter((r) => r.status === 'waiting' || r.status === 'error');
  if (attentionRows.length) {
    sections.push({ key: 'attention', kind: 'attention', groupId: null, label: '개입 필요',
      rows: [...attentionRows].sort(byName) });
  }

  const byGroup = new Map<string, typeof withSentence>();
  const ungrouped: typeof withSentence = [];
  for (const r of withSentence) {
    if (r.groupId) {
      if (!byGroup.has(r.groupId)) byGroup.set(r.groupId, []);
      byGroup.get(r.groupId)!.push(r);
    } else {
      ungrouped.push(r);
    }
  }

  // 그룹 순서: 라벨 가나다순 — 고정 규칙이라 세션 활동과 무관하게 항상
  // 같은 순서로 보인다(위 byName과 같은 이유).
  const order = Array.from(byGroup.keys())
    .sort((a, b) => (groupLabels.get(a) || '').localeCompare(groupLabels.get(b) || ''));
  for (const gid of order) {
    sections.push({
      key: gid, kind: 'group', groupId: gid,
      label: groupLabels.get(gid) || '그룹',
      rows: [...byGroup.get(gid)!].sort(byName),
    });
  }

  if (ungrouped.length) {
    sections.push({ key: 'ungrouped', kind: 'ungrouped', groupId: null, label: '묶지 않음',
      rows: [...ungrouped].sort(byName) });
  }
  return sections;
}

/** 잠자는 세션 한 행 — 전부 낱개다(아래 함수 주석 참고). */
export interface SleepEntryOut {
  key: string;
  label: string;
  row: OtherRailRowInput;
}

/** 잠자는 세션 — 전부 낱개 행이다. 2026-09-18 후속(그룹 재정의) — 그룹은
 * 이제 태그가 아니라 **한 탭의 pane 트리에 실시간으로 떠 있는 세션들**이라
 * (사용자 요청: "같은 화면에 묶인 것만 그룹"), 탭을 재우면(sleepTab이 탭을
 * 닫는다) 그 순간 근거 자체가 사라진다 — 잠든 채로 "그룹이었다"를 붙들고
 * 있을 태그가 더 이상 없다(덩어리 잠자는 구역은 이 후속에서 없앴다).
 * 덩어리로 재웠던 세션들은 깨어나서 다시 같은 화면에 놓이면 그룹으로
 * 다시 보인다. */
export function buildSleepingEntries(
  inputs: OtherRailRowInput[],
): SleepEntryOut[] {
  const asleep = inputs.filter((r) => !r.awake);
  const entries: SleepEntryOut[] = asleep.map((r) => (
    { key: r.tmuxName || r.sessionId, label: r.name, row: r }
  ));
  entries.sort((a, b) => a.label.localeCompare(b.label));
  return entries;
}

// ---------------------------------------------------------------------------
// 2026-09-18 후속 — 저장소 색점 램프(fnv1a/hashRepoColorIndex/repoColorKey,
// 「98 §4 — 색 배정의 입력을 소유자로」)와 그 배지(remoteLabel/RailRemote)를
// 여기서 완전히 지웠다. ADR-29로 저장소가 레일의 주어에서 내려온 뒤에도
// 세션 행 맨 앞에 저장소 색이 남아 있던 게 사용자 지적으로 드러났다 —
// "저장소를 없앴으면 이 색도 없어져야 하는 거 아니냐"는 게 정확한 지적이라,
// 코드도 지웠다(레거시로 반쯤 남겨두지 않는다). `--color-hash-1..8` 디자인
// 토큰(tokens.css/skins.css/theme-import.js)도 이 기능 하나만을 위한
// 것이었어서 함께 지웠다.

/**
 * 레일을 접은 채로 시작할지 — **저장된 값이 없을 때만** 쓰는 기본값.
 *
 * 저장값(ui.rail.collapsed)이 있으면 언제나 그게 이긴다: 사용자가 직접 정한
 * 것이고, 레일 상태는 기기별 설정이라 폰에서 접은 게 맥에 옮아가지 않는다
 * (DESIGN.md §6). 여기서 정하는 건 "아직 아무 말도 안 한 기기"의 첫 모습뿐이다.
 *
 * wide(≥1280) 미만에서 접는 이유는 자리 다툼이다. 레일 252 + dock 접힘 36을
 * 빼고 나면 터미널과 HUD가 나눠 쓸 폭이 얼마 안 남는다 — 760px 창에서 HUD가
 * 472px밖에 못 받아 칩을 다 못 실었다. Dock.tsx가 이미 같은 판단을
 * (`innerWidth < WIDE_MAX`) 하고 있었는데 레일에만 없어서, 좁은 화면을 처음
 * 여는 기기가 양쪽 다 펼친 상태로 시작했다.
 */
export function defaultRailCollapsed(viewportWidth: number, regularMax: number): boolean {
  return viewportWidth < regularMax;
}
