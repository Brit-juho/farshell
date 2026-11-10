// 접힌 세션 띠(48px)의 링형 칸 — 에이전트 마크 둘레에 상태 링을 그린다(시안 rail-specimen-v2 「3-B」).
// 상태는 색만이 아니라 링 모양으로도 말한다: 유휴 점선 점 · 입력 대기 파선(숨쉬기) · 작업 중 회전 호 · 완료 실선.
// 색·회전·숨쉬기는 CSS가 data-state로 맡는다(styles/screens/70-worktree-rail.css) — 여기는 도형만 만든다.
// 순수 함수라 shell 청크 안에서 테스트한다(frontend/tests/rail-compact.test.js).
import { normalizeState } from '../design/state-classes.js';

const HEAD = '<svg class="vt-ring" viewBox="0 0 32 32" fill="none" stroke="currentColor" aria-hidden="true" focusable="false">';

/** 상태 → 링 SVG 마크업. 알 수 없는 상태는 유휴로 접는다(stateIcon과 같다). */
export function ringSvg(status: string | null | undefined): string {
  switch (normalizeState(status)) {
    case 'waiting': return `${HEAD}<circle cx="16" cy="16" r="14" stroke-width="2" stroke-dasharray="5 3.6"/></svg>`;
    case 'working': return `${HEAD}<path d="M16 2a14 14 0 1 0 14 14" stroke-width="2" stroke-linecap="round"/></svg>`;
    case 'done': return `${HEAD}<circle cx="16" cy="16" r="14" stroke-width="2"/></svg>`;
    default: return `${HEAD}<circle cx="16" cy="16" r="14" stroke-width="1.5" stroke-dasharray="0.1 4.4" stroke-linecap="round"/></svg>`;
  }
}

/** 칸 오른쪽 위 개수 배지(S1)에 쓸 값. 작업 중 서브에이전트 수(두 자리는 못 넣어 9+), 한도 도달은 「+」, 없으면 null. */
export function subBadge(workingCount: number, limited: boolean): { text: string; limited: boolean } | null {
  if (limited) return { text: '+', limited: true };
  if (workingCount <= 0) return null;
  return { text: workingCount > 9 ? '9+' : String(workingCount), limited: false };
}
