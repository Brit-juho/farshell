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

const GROUP_LABEL: Record<RailGroup, string> = {
  attention: '개입 필요',
  working: '작업 중',
  idle: '유휴',
};
export { GROUP_LABEL };

// 10-shell-layout.md §5: "그룹 순서 고정: 개입 필요(waiting·error) →
// 작업 중(working) → 유휴(idle·done)". done은 유휴에 남는다 — 완료
// 자체는 급한 게 아니고, 「읽지 않음」(N37) 배지가 행 안에서 따로 알린다.
function groupOf(status: AgentState): RailGroup {
  if (status === 'waiting' || status === 'error') return 'attention';
  if (status === 'working') return 'working';
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
    default: return '유휴' + (rel ? ` · ${rel}` : '');
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
  status: AgentState;
  since: number | null;
  tool: string | null;
  /** GET /api/worktrees의 changed 요약(add/del 라인 수) — 2.1.1부터 세션별
   * git status 폴링 대신 이걸 쓴다(30 §4, 10 §5 원문 "+142 −38"). */
  changed: { files: number; add: number; del: number } | null;
  question?: string | null;
  options?: { key: string; label: string }[] | null;
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

/** §5 해시: fnv1a(repoName) % 8 → --color-hash-(index+1)에 쓸 0~7 인덱스. */
export function hashRepoColorIndex(repoName: string): number {
  return fnv1a(repoName) % 8;
}
