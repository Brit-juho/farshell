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
// WorktreeRailRowInput/OtherRailRowInput 전부 이 최소 모양을 만족한다.
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
// 30-worktree.md §4 — 데스크톱 레일(Rail.tsx) 전용 행 모양. "행 = Worktree",
// 「기타」 그룹은 "행 = 세션"(어떤 워크트리에도 안 속함). Fleet.tsx는 이
// 두 타입을 쓰지 않는다(모바일은 항상 세션 단위, 70-mobile.md 범위 밖).

export interface WorktreeRailRowInput {
  kind: 'worktree';
  worktreeId: string;
  /** isMain이면 repoName, 아니면 `${repoName}/${branch}` (30 §4 레일 표시 규칙). */
  label: string;
  repoName: string;
  branch: string;
  isMain: boolean;
  path: string;
  /** 이 워크트리를 연 웹 세션 id. 아직 웹에 안 열려 있으면(서버는 아는데 이
   * 브라우저 탭엔 없음) null — 행은 흐리게 그려지고 클릭하면 연다(30 §4). */
  primarySessionId: string | null;
  /** 서버가 준 tmux 세션 이름(primarySessionId가 없을 때 attach 대상). */
  primaryTmuxName: string | null;
  /** 저장소 최상위 경로(서버 `wt.repo`) — 2.1 D4, 탭의 정체성(repoId)으로
   * 그대로 쓴다. 같은 저장소의 워크트리는 전부 같은 값을 갖는다. */
  repoPath: string;
  /** 지금은 항상 `"local"`(서버 `wt.host`) — 2.2에서 원격 호스트가 생기면
   * 탭 dedup 키(repoId)가 호스트까지 구분해야 한다. */
  host: string;
  status: AgentState;
  since: number | null;
  tool: string | null;
  /** GET /api/worktrees의 changed 요약(add/del 라인 수) — 2.1.1부터 세션별
   * git status 폴링 대신 이걸 쓴다(30 §4, 10 §5 원문 "+142 −38"). */
  changed: { files: number; add: number; del: number } | null;
  question?: string | null;
  options?: { key: string; label: string }[] | null;
  /** 2.1.6 — 이 행이 가리키는 세션에 떠 있는 CLI 이름. 헤더의 워크트리 탭이
   * 이미 달고 있던 마크를 레일 행·플릿 행도 같이 달기 위한 필드다. 모르면
   * null/undefined이고 그때는 마크를 그리지 않는다("셸"과 "모름"은 다르다). */
  agent?: string | null;
  /** 98 §4 — `.git/config`의 origin에서 서버가 뽑아 준 `{host, owner, name}`.
   * remote가 없는 저장소는 null이고, 그때 둘째 줄은 비고 색은 이름 해시로
   * 떨어진다(동작 변화 없음). */
  remote?: RailRemote | null;
}

export interface OtherRailRowInput {
  kind: 'session';
  sessionId: string;
  tmuxName: string | null;
  name: string;
  status: AgentState;
  since: number | null;
  tool: string | null;
  diffFiles: number | null;
  question?: string | null;
  options?: { key: string; label: string }[] | null;
  /** 2.1.6 — 이 행이 가리키는 세션에 떠 있는 CLI 이름. 헤더의 워크트리 탭이
   * 이미 달고 있던 마크를 레일 행·플릿 행도 같이 달기 위한 필드다. 모르면
   * null/undefined이고 그때는 마크를 그리지 않는다("셸"과 "모름"은 다르다). */
  agent?: string | null;
  /** C1 — 다른 호스트의 세션. 로컬 세션 id 경로(switchTo 등)가 성립하지 않으므로
   * 클릭·컨텍스트 메뉴가 막히고 행이 흐리게 그려진다(원격 attach는 멀티호스트
   * 3단계). 선택 필드라 기존 호출부는 그대로 동작한다. */
  remote?: boolean;
}

export type DesktopRailRowInput = WorktreeRailRowInput | OtherRailRowInput;

// ---------------------------------------------------------------------------
// 20-design-system.md §5(O2) — 색점 램프. 저장소 이름 → --color-hash-1..8
// 중 하나. 순수 함수(DOM·fetch 없음, 파일 상단 주석과 같은 원칙) — 고정
// 입력에 고정 출력이 나와야 rail-hash.test.js가 검증할 수 있다.
//
// FNV-1a 32비트 (offset basis 2166136261 / prime 16777619). 호스트가 달라도
// 저장소 이름이 같으면 같은 색이 나와야 하므로(§5 "호스트가 달라도 저장소가
// 같으면 같은 색") 입력은 오직 repoName 문자열 하나 — 경로·호스트명은 섞지
// 않는다.
export function fnv1a(str: string): number {
  let hash = 0x811c9dc5; // 2166136261
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    // Math.imul로 32비트 곱셈 오버플로를 표준과 동일하게 재현한다.
    hash = Math.imul(hash, 0x01000193); // 16777619
  }
  return hash >>> 0; // unsigned 32비트로 정규화
}

/** §5 해시: fnv1a(key) % 8 → --color-hash-(index+1)에 쓸 0~7 인덱스. */
export function hashRepoColorIndex(repoName: string): number {
  return fnv1a(repoName) % 8;
}

/** git 원격 — `.git/config`의 origin에서 서버가 파싱해 준다(98 §4). */
export interface RailRemote {
  host: string;   // github | gitlab | bitbucket | <호스트명>
  owner: string;
  name: string;
}

/** 98 §4 — 행 둘째 줄의 `github/fornerds`. remote가 없으면 빈 문자열이고,
 * 그때 그 줄은 `:empty`로 접힌다(80-dock.css). */
export function remoteLabel(remote: RailRemote | null | undefined): string {
  if (!remote?.host || !remote?.owner) return '';
  return `${remote.host}/${remote.owner}`;
}

/** 98 §4 — 색 배정의 입력을 **소유자**로 바꾼다.
 *
 * 이름 해시(fnv1a(repoName))는 상태도 언어도 조직도 아닌 글자에서 나온 색이라
 * 정보가 없었다(디자인 리뷰 S1 「의미 없는 장식」). 소유자로 바꾸면 같은 조직의
 * 저장소가 같은 색이 되어 색이 처음으로 뜻을 갖는다. remote가 없으면 예전처럼
 * 이름으로 떨어진다 — 동작이 바뀌지 않는다.
 */
export function repoColorKey(repoName: string, remote?: RailRemote | null): string {
  if (remote?.owner) return `${remote.host || ''}/${remote.owner}`;
  return repoName;
}

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
