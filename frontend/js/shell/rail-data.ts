// N36 §5 — 워크트리 레일(2.1.0은 세션을 워크트리 자리에 임시로 그린다,
// ADR-10 「기타」 규칙)의 순수 로직. DOM도 fetch도 모른다 — 여러 소스(세션
// 목록·에이전트 상태·git status)를 합친 평범한 입력을 받아 그룹핑된 행
// 목록을 돌려준다.
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
  /** N38(70-mobile.md §2) — waiting 상태에서 감지된 질문 1줄. 데스크톱 레일은
   * 아직 안 쓰지만(2.1.0 gap이 이제 서버 쪽엔 메워졌다 — agent_prompt_detect의
   * question/options), 같은 데이터 소스를 쓰는 Fleet.tsx가 이 필드로 인라인
   * 승인 버튼을 그린다. 선택 필드라 Rail.tsx는 안 넘겨도 그대로 동작한다. */
  question?: string | null;
  /** toml `options` 캡처로 뽑은 번호 선택지. 못 뽑았으면 null(또는 undefined)
   * — 그 경우 Fleet.tsx는 버튼 대신 「터미널로」를 그린다. */
  options?: { key: string; label: string }[] | null;
}

export interface RailRow extends RailRowInput {
  statusSentence: string;
}

export interface RailSectionOut {
  group: RailGroup;
  rows: RailRow[];
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
export function buildRailSections(inputs: RailRowInput[], nowMs = Date.now()): RailSectionOut[] {
  const rows: RailRow[] = inputs.map((r) => ({
    ...r,
    statusSentence: statusSentence(r.status, r.since, r.tool, nowMs),
  }));

  const buckets: Record<RailGroup, RailRow[]> = { attention: [], working: [], idle: [] };
  for (const row of rows) buckets[groupOf(row.status)].push(row);

  const order: RailGroup[] = ['attention', 'working', 'idle'];
  for (const g of order) {
    buckets[g].sort((a, b) => (b.since ?? -Infinity) - (a.since ?? -Infinity));
  }
  return order.map((group) => ({ group, rows: buckets[group] })).filter((s) => s.rows.length > 0);
}
