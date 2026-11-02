// 상태(idle/working/waiting/done) 표현·집계의 **유일한 정의**. 레일·탭·Fleet·
// 상태 점·파비콘이 전부 여기서 라벨과 우선순위를 읽는다.
//
// D3(20-design-system.md §3·§4-1) 동적 클래스 규약: Tailwind는 소스를 문자열
// 스캔해 클래스를 생성하므로 `el.className = 'bg-st-' + state` 같은 조립은
// 빌드에 클래스가 안 생겨 조용히 실패한다. 상태→표현은 항상 이 파일 같은
// 정적 객체 리터럴로 선언한다. `.status-dot`·`.vt-state-icon`은 `data-state`
// 속성 셀렉터 하나로 색·애니메이션이 따라오므로 이 맵 없이도 동작한다.
//
// error: 서버가 예약만 해 둔 값(진입 경로 없음, agent_status.py)이라 UI에서
// 뺐다. 서버가 언젠가 보내더라도 normalizeState가 idle로 접는다 — 색 토큰
// (--color-st-error)은 위험·실패 표시에 쓰이므로 남아 있다.

import { t } from '../core/i18n.js';

export const STATES = ['idle', 'working', 'waiting', 'done'];

export const STATE_LABEL = {
  idle:    t('유휴'),
  working: t('작업 중'),
  waiting: t('입력 대기'),
  done:    t('완료'),
};

/** 알 수 없는 값(error·undefined·미래의 새 상태)은 전부 idle. */
export function normalizeState(status) {
  return typeof status === 'string' && Object.prototype.hasOwnProperty.call(STATE_LABEL, status)
    ? status : 'idle';
}

export function stateLabel(status) {
  return STATE_LABEL[normalizeState(status)];
}

// 한 tmux 세션에 엔트리가 여럿일 때(훅 + pane 자기보고) 어느 쪽을 쓸지 —
// 서버 agent_status._URGENCY의 상대 순서와 같다(frontend 테스트가 서버 파일을
// 읽어 대조한다). 읽지 않음 개념이 없다: 엔트리끼리의 비교다.
export const URGENCY = { waiting: 0, done: 1, working: 2, idle: 4 };

/** 엔트리 간 비교용. 알 수 없는 상태는 idle. */
export function entryUrgency(status) {
  return URGENCY[normalizeState(status)];
}

// 대표 상태 순위(작을수록 급하다): waiting > done(읽지 않음) > working >
// done(확인함) = idle. 「확인함」 done은 더 알릴 게 없으니 idle과 같다.
const RANK_WAITING = 0;
const RANK_DONE_UNSEEN = 1;
const RANK_WORKING = 2;
const RANK_QUIET = 3;

export function stateRank(status, unseen = false) {
  const s = normalizeState(status);
  if (s === 'waiting') return RANK_WAITING;
  if (s === 'done' && unseen) return RANK_DONE_UNSEEN;
  if (s === 'working') return RANK_WORKING;
  return RANK_QUIET;
}

/**
 * 여러 세션을 하나로 대표하는 상태 — 탭·그룹·집계가 같은 규칙을 쓴다.
 * entries: [{ status, unseen? }]. 비면 idle. 같은 순위끼리는 앞선 것이 이기되
 * 가장 낮은 순위(done 확인함/idle)는 항상 'idle'로 돌려준다.
 * unseen: 대표가 된 순위의 세션 중 하나라도 읽지 않음이면 true.
 */
export function representativeState(entries) {
  let rank = RANK_QUIET;
  let status = 'idle';
  let unseen = false;
  let first = true;
  for (const e of entries || []) {
    const s = normalizeState(e && e.status);
    const u = !!(e && e.unseen);
    const r = stateRank(s, u);
    if (first || r < rank) {
      rank = r; status = r === RANK_QUIET ? 'idle' : s; unseen = u; first = false;
    } else if (r === rank && u) {
      unseen = true;
    }
  }
  return { status, unseen };
}

// ── 뷰 탭의 상태 바 ─────────────────────────────────────────────────────
// 탭 하나 = 칸(세션)마다 같은 폭의 세그먼트 하나. 색은 상태, 굵기는 읽지 않음
// (굵음 = 아직 확인 안 함 / 얇음 = 확인함). 색만으로 말하지 않도록 툴팁과
// aria-label이 같은 문장을 싣는다. 대표 상태로 접지 않는다 — 칸마다 따로 보인다.

/** entries: 칸 순서(왼쪽 위부터)의 [{ name, status, unseen? }]. 이름 없는 칸(빈 칸)은 세지 않는다. */
export function viewSegments(entries) {
  const out = [];
  for (const e of entries || []) {
    if (!e || !e.name) continue;
    const status = normalizeState(e.status);
    const unseen = !!e.unseen;
    out.push({ name: e.name, status, unseen, thick: unseen, label: stateLabel(status), branch: e.branch || null });
  }
  return out;
}

/** 호버 툴팁 — 세션마다 한 줄: "이름 · 상태[ · 브랜치][ · 확인 안 함]". 브랜치는 알 때만 넣는다. */
export function segmentsTooltip(segs) {
  return segs.map((s) => {
    const base = s.branch ? `${s.name} · ${s.label} · ${s.branch}` : `${s.name} · ${s.label}`;
    return s.unseen ? `${base} · ${t('확인 안 함')}` : base;
  }).join('\n');
}

/** 스크린 리더용 한 줄 — "api, 작업 중 · web, 입력 대기(확인 안 함)". */
export function segmentsAria(segs) {
  return segs.map((s) => (s.unseen ? t('{name}, {label}(확인 안 함)', { name: s.name, label: s.label }) : `${s.name}, ${s.label}`)).join(' · ');
}
