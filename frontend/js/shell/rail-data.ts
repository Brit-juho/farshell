// 레일(Rail.tsx)과 폰 Fleet(Fleet.tsx)의 순수 로직. DOM도 fetch도 모른다.
//
// 규칙의 출처는 docs/ref/terminal.md 「Session and view rules」(Rail):
// - 구역은 둘이다. 「개입 필요」(waiting) · 「세션」(전체, 이름순만).
//   상태 우선순위 정렬은 없다 — 행이 오락가락하지 않게(2026-09-18 사용자 지적).
// - 행 3줄: 상태·에이전트·이름 / 메인·워크트리 아이콘·브랜치·변경량 / 상태 키워드·경과·만든 곳.
// - 만든 곳(origin) 3종(peer·agent·external)은 레일 설정에서 표시를 끈다.
import type { AgentState } from '../core/types.js';
import { STATE_LABEL, normalizeState } from '../design/state-classes.js';

// ── 행 모양 ────────────────────────────────────────────────────────────────

export type Origin = 'local' | 'peer' | 'agent' | 'external';
export type WaitKind = 'approval' | 'choice' | 'plan' | 'question';
export type DoneBadge = 'question' | 'plan';

export interface SessionRow {
  /** 이 브라우저 탭의 웹 세션 id. 열려 있지 않으면 빈 문자열. */
  sessionId: string;
  tmuxName: string | null;
  name: string;
  /** 이 브라우저 탭의 칸에 놓여 있다(열림). 상태가 아니라 배치다. */
  open: boolean;
  status: AgentState;
  /** 상태 변화를 아직 못 봤다 — 상태 아이콘 둘레 링. */
  unseen: boolean;
  /** 상태 진입 시각(epoch초). */
  since: number | null;
  /** 진행 중인·마지막 도구 이름. working에서만 보인다. */
  tool: string | null;
  /** 서버가 정한 waiting의 유형. 없으면 질문. */
  kind: WaitKind | null;
  /** done에 붙는 보조 표시(질문 있음/계획 있음). */
  badge: DoneBadge | null;
  diffFiles: number | null;
  changed?: { files: number; add: number; del: number } | null;
  /** waiting에서 감지된 질문 원문. 행에는 안 보이고 툴팁·승인 버튼에만 쓴다. */
  question?: string | null;
  options?: { key: string; label: string }[] | null;
  /** 승인 응답 경쟁을 막는 토큰(ADR-33). */
  promptId?: string | null;
  agent?: string | null;
  branch?: string | null;
  isMainWorktree?: boolean;
  worktreeId?: string | null;
  origin: Origin;
  /** 이 세션이 속한 묶음 뷰의 이름(묶음이 아니면 null). */
  viewLabel: string | null;
  /** 이 브라우저 탭 말고 이 세션에 붙어 있는 화면 수(다른 브라우저 탭·폰·맥 터미널). */
  otherScreens: number;
}

// ── 만든 곳 ────────────────────────────────────────────────────────────────

export const HIDEABLE_ORIGINS: Origin[] = ['peer', 'agent', 'external'];

export const ORIGIN_LABEL: Record<Origin, string> = {
  local: '이 화면에서 만듦',
  peer: '다른 맥에서 만듦',
  agent: '에이전트가 만듦',
  external: '직접 만듦 (CLI·tmux)',
};

/** 서버 값이 없거나(구버전 서버) 모르는 값이면 local — 모든 행에 외부 아이콘이 붙는 것보다 안전하다. */
export function normalizeOrigin(v: unknown): Origin {
  return v === 'peer' || v === 'agent' || v === 'external' ? v : 'local';
}

/** 서버가 준 kind. 모르는 값은 질문. */
export function normalizeKind(v: unknown): WaitKind {
  return v === 'approval' || v === 'choice' || v === 'plan' ? v : 'question';
}

export function normalizeBadge(v: unknown): DoneBadge | null {
  return v === 'question' || v === 'plan' ? v : null;
}

// ── 상태 키워드 ────────────────────────────────────────────────────────────

export const KIND_LABEL: Record<WaitKind, string> = {
  approval: '승인 요청',
  choice: '선택 요청',
  plan: '계획 확인',
  question: '질문',
};

export const BADGE_LABEL: Record<DoneBadge, string> = {
  question: '질문 있음',
  plan: '계획 있음',
};

export interface StateKeyword {
  /** 짧게 고정된 키워드 — 잘리지 않는다. */
  text: string;
  /** 작업 중의 도구 이름. 말줄임은 이쪽에서만 허용한다. */
  tool: string | null;
  /** done 옆의 보조 표시. */
  badge: string | null;
}

export function stateKeyword(
  row: Pick<SessionRow, 'status' | 'kind' | 'badge' | 'tool'>,
): StateKeyword {
  const s = normalizeState(row.status);
  if (s === 'waiting') return { text: KIND_LABEL[normalizeKind(row.kind)], tool: null, badge: null };
  if (s === 'working') return { text: STATE_LABEL.working, tool: row.tool || null, badge: null };
  if (s === 'done') {
    const b = normalizeBadge(row.badge);
    return { text: STATE_LABEL.done, tool: null, badge: b ? BADGE_LABEL[b] : null };
  }
  return { text: STATE_LABEL.idle, tool: null, badge: null };
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

/** 접힌 레일·툴팁·스크린리더가 쓰는 한 줄: "승인 요청 · 2분". */
export function statusSentence(row: Pick<SessionRow, 'status' | 'kind' | 'badge' | 'tool' | 'since'>, nowMs = Date.now()): string {
  const k = stateKeyword(row);
  const rel = formatRelativeTime(row.since, nowMs);
  return [k.text, k.tool, k.badge, rel].filter(Boolean).join(' · ');
}

// ── 설정(표시 켜기/끄기) ───────────────────────────────────────────────────

export const ATTENTION_SHOW_KEY = 'rail.attention.show';
export const ATTENTION_COLLAPSED_KEY = 'rail.attention.collapsed';
export function originShowKey(o: Origin): string {
  return `rail.origin.${o}.show`;
}

/** 설정을 아직 못 읽었을 때 쓸 기본값. ⚠ core/settings.js의 SCHEMA `def`와 같아야 한다
 * (지연 청크는 core를 정적 import할 수 없어 한 벌 더 적는다 — 테스트가 대조한다). */
export const RAIL_PREF_DEFAULTS: Record<string, boolean> = {
  [ATTENTION_SHOW_KEY]: true,
  [ATTENTION_COLLAPSED_KEY]: false,
  [originShowKey('peer')]: true,
  [originShowKey('agent')]: true,
  [originShowKey('external')]: true,
};

export interface RailPrefs {
  attentionShow: boolean;
  attentionCollapsed: boolean;
  hiddenOrigins: Origin[];
}

/** get: 값이 없으면 undefined/null을 돌려주는 설정 읽기(window.vtSettingsGet). */
export function readRailPrefs(get: (key: string) => unknown): RailPrefs {
  const flag = (key: string): boolean => {
    const v = get(key);
    return v === undefined || v === null ? RAIL_PREF_DEFAULTS[key] : Boolean(v);
  };
  return {
    attentionShow: flag(ATTENTION_SHOW_KEY),
    attentionCollapsed: flag(ATTENTION_COLLAPSED_KEY),
    hiddenOrigins: HIDEABLE_ORIGINS.filter((o) => !flag(originShowKey(o))),
  };
}

// ── 구역 ───────────────────────────────────────────────────────────────────

const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name);

export interface RailLists<T> {
  attention: T[];
  sessions: T[];
}

/**
 * 「개입 필요」= waiting 세션, 「세션」= 전체. 둘 다 이름순뿐이다.
 *
 * 만든 곳을 숨겨도 **열려 있는 세션과 waiting 세션은 남긴다** — 지금 보고 있는 세션이
 * 레일에서 사라지거나, 내 입력을 기다리는 세션이 안 보이면 안 된다.
 * 개입 필요를 끄면 waiting은 「세션」 안에서 키워드로만 말한다.
 */
export function buildRailLists<T extends Pick<SessionRow, 'name' | 'status' | 'origin' | 'open'>>(
  rows: T[],
  prefs: RailPrefs,
): RailLists<T> {
  const waiting = (r: T) => normalizeState(r.status) === 'waiting';
  const hidden = new Set<Origin>(prefs.hiddenOrigins);
  const sessions = rows.filter((r) => !hidden.has(r.origin) || r.open || waiting(r)).sort(byName);
  const attention = prefs.attentionShow ? rows.filter(waiting).sort(byName) : [];
  return { attention, sessions };
}

// ── 행 메뉴(⋯ 버튼·우클릭·폰 롱프레스가 같은 항목) ───────────────────────

export type RowMenuId = 'open' | 'close' | 'rename' | 'openOnMac' | 'screens' | 'kill';

export interface RowMenuItem {
  id: RowMenuId;
  label: string;
  detail?: string;
  danger?: boolean;
}

/** 열린 세션이면 닫기, 아니면 열기. 이름 변경·맥에서 열기·연결된 화면은 웹 세션이 있어야 하고,
 * 종료는 tmux 세션이면 늘 있다(확인창은 vtConfirmAndKillSession). */
export function rowMenuItems(row: Pick<SessionRow, 'open' | 'tmuxName'>): RowMenuItem[] {
  const items: RowMenuItem[] = [];
  if (row.open) {
    items.push(
      { id: 'close', label: '닫기' },
      { id: 'rename', label: '이름 변경' },
      { id: 'openOnMac', label: '맥에서 열기' },
      { id: 'screens', label: '연결된 화면' },
    );
  } else {
    items.push({ id: 'open', label: '열기' });
  }
  if (row.tmuxName) items.push({ id: 'kill', label: '종료', detail: '되돌릴 수 없음', danger: true });
  return items;
}

/** 메뉴 항목을 실행한다. 지연 청크는 세션 모듈을 정적 import 못 해 window 브리지를 받는다. */
export function runRowMenu(
  id: RowMenuId,
  row: Pick<SessionRow, 'sessionId' | 'tmuxName' | 'name'>,
  w: any,
  startRename: () => void,
): void {
  const sid = row.sessionId || null;
  switch (id) {
    case 'open': w.vtOpenSessionFromRail?.({ sessionId: sid, tmuxName: row.tmuxName }); break;
    case 'close': if (sid) w.vtCloseSession?.(sid); break;
    case 'rename': if (sid) startRename(); break;
    case 'openOnMac': if (sid) w.vtOpenSessionOnMac?.(sid); break;
    case 'screens': if (sid) w.vtShowClients?.(sid); break;
    case 'kill': w.vtConfirmAndKillSession?.(row.tmuxName, sid); break;
  }
}

// ── 아이콘 선택 ────────────────────────────────────────────────────────────

/** 둘째 줄 앞 아이콘 이름. 메인 워크트리 = 본선(git-commit), 딸린 워크트리 = 가지(git-branch). */
export function worktreeIconName(row: Pick<SessionRow, 'isMainWorktree'>): 'git-commit' | 'git-branch' {
  return row.isMainWorktree ? 'git-commit' : 'git-branch';
}

export const ORIGIN_ICON: Record<Exclude<Origin, 'local'>, string> = {
  peer: 'laptop',
  agent: 'bot',
  external: 'square-terminal',
};

/** 변경량 표기. "+142 −38"(줄 수가 있으면) 또는 "파일 3". 없으면 null. */
export function diffLabel(row: Pick<SessionRow, 'changed' | 'diffFiles'>): string | null {
  const c = row.changed;
  if (c && c.files > 0) return `+${c.add} −${c.del}`;
  return row.diffFiles != null && row.diffFiles > 0 ? `파일 ${row.diffFiles}` : null;
}

/**
 * 레일을 접은 채로 시작할지 — **저장된 값이 없을 때만** 쓰는 기본값.
 *
 * 저장값(ui.rail.collapsed)이 있으면 언제나 그게 이긴다: 사용자가 직접 정한
 * 것이고, 레일 상태는 기기별 설정이라 폰에서 접은 게 맥에 옮아가지 않는다
 * (DESIGN.md §6). 여기서 정하는 건 "아직 아무 말도 안 한 기기"의 첫 모습뿐이다.
 *
 * wide(≥1280) 미만에서 접는 이유는 자리 다툼이다. 레일 252 + dock 접힘 36을
 * 빼고 나면 터미널과 HUD가 나눠 쓸 폭이 얼마 안 남는다.
 */
export function defaultRailCollapsed(viewportWidth: number, regularMax: number): boolean {
  return viewportWidth < regularMax;
}
