// 레일(Rail.tsx)과 폰 Fleet(Fleet.tsx)의 순수 로직. DOM도 fetch도 모른다.
//
// 규칙의 출처는 docs/ref/terminal.md 「Session and view rules」(Rail):
// - 구역은 둘이다. 「개입 필요」(waiting) · 「세션」(전체, 이름순만).
//   상태 우선순위 정렬은 없다 — 행이 오락가락하지 않게(2026-09-18 사용자 지적).
// - 행 3줄: 상태·에이전트·이름 / 작업 공간 종류·경로·브랜치·변경량 / 상태 키워드·경과·만든 곳.
// - 만든 곳(origin) 2종(peer·external)은 레일 설정에서 표시를 끈다.
import type { AgentState } from '../core/types.js';
import { STATE_LABEL, normalizeState } from '../design/state-classes.js';
import { t } from '../core/i18n.js';
import { alertDialog } from '../core/dialog-lazy.js';
import { timedFetch } from '../core/api.js';

// ── 행 모양 ────────────────────────────────────────────────────────────────

export type Origin = 'local' | 'peer' | 'external';
/** 에이전트에게 공유된 세션(서버 `share`). 모드는 읽기 전용(read) 또는 읽기·쓰기(readwrite). */
export interface ShareInfo { nick: string; mode: 'read' | 'readwrite'; }
export type WaitKind = 'approval' | 'choice' | 'plan' | 'question';
export type DoneBadge = 'question' | 'plan';

export interface SessionRow {
  physicalKey?: string;
  catalogSessionId?: string;
  runtimeId?: string;
  subagents?: unknown;
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
  /** done·working에 붙는 보조 표시(질문 있음/계획 있음). */
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
  cwd?: string;
  directory?: string;
  workspaceKind?: string;
  /** 위치 툴팁 — 에이전트 기준 경로(+ 터미널과 다르면 터미널 위치). */
  locationTip?: string;
  context?: any;
  contextStatus?: string;
  isMainWorktree?: boolean;
  worktreeId?: string | null;
  origin: Origin;
  /** peer가 만든 세션이면 만든 맥의 표시 이름(서버 `originHost`). 기록 없음·구버전 서버는 없다/빈 값. */
  originHost?: string;
  /** 에이전트 공유 표시. 공유 안 된 세션·구버전 서버는 없다/null. */
  share?: ShareInfo | null;
  /** 이 세션이 속한 묶음 뷰의 이름(묶음이 아니면 null). */
  viewLabel: string | null;
  viewId?: string | null;
  /** 이 브라우저 탭 말고 이 세션에 붙은 **웹** 화면 수(다른 브라우저 탭·폰). */
  otherScreens: number;
  /** 웹이 아닌 attach 수(맥 터미널 등). 구버전 서버가 값을 안 주면 0. */
  otherClients: number;
  /** 에이전트 위임으로 만든 하위 세션이면 그 계보(서버 위임 장부 기준). 아니면 없다/null. */
  lineage?: Lineage | null;
}

// ── 계보(에이전트 위임) ───────────────────────────────────────────────────────
// 출처는 서버 위임 장부(server/delegation.py)다. tmux 옵션(@fsh_parent)은 에이전트가 고칠 수
// 있어 쓰지 않는다 — 레일 트리를 위조할 수 없게. 깊이는 1이라 트리는 부모 → 자식 한 단계뿐이다.

export type LineageState = 'running' | 'reported' | 'closed' | 'orphaned';
export interface Lineage {
  delegation: string;
  /** 부모 세션의 지금 이름. 부모가 사라졌으면 null. */
  parent: string | null;
  state: LineageState;
  outcome: 'succeeded' | 'failed' | null;
  createdAt: number;
  task: string;
  summary: string;
}

const LINEAGE_STATES: LineageState[] = ['running', 'reported', 'closed', 'orphaned'];

export function normalizeLineage(v: unknown): Lineage | null {
  const o = v as Record<string, unknown> | null;
  if (!o || typeof o !== 'object' || typeof o.delegation !== 'string' || !o.delegation) return null;
  const state = LINEAGE_STATES.includes(o.state as LineageState) ? (o.state as LineageState) : 'running';
  return {
    delegation: o.delegation,
    parent: typeof o.parent === 'string' && o.parent ? o.parent : null,
    state,
    outcome: o.outcome === 'succeeded' || o.outcome === 'failed' ? o.outcome : null,
    createdAt: Number(o.createdAt) || 0,
    task: typeof o.task === 'string' ? o.task : '',
    summary: typeof o.summary === 'string' ? o.summary : '',
  };
}

/** 하위 세션 한 줄 표시: 「진행 중」「보고 · 성공」「보고 · 실패」「닫힘」「부모 없음」. */
export function lineageLabel(l: Pick<Lineage, 'state' | 'outcome'>): string {
  if (l.state === 'reported') return l.outcome === 'failed' ? t('보고 · 실패') : t('보고 · 성공');
  if (l.state === 'closed') return t('닫힘');
  if (l.state === 'orphaned') return t('부모 없음');
  return t('진행 중');
}

export type RailSort = 'name' | 'manual';

export interface TreeEntry<T> {
  row: T;
  /** 열린 위임의 하위 세션(진행 중·보고됨) — 만든 순서. */
  kids: T[];
  /** 닫힌 위임의 하위 세션 — 「닫힌 하위 N」으로 접혀 있다. */
  closedKids: T[];
}

/**
 * 묶음에 속하지 않은 행들을 부모 → 자식 트리로 묶는다.
 * - 자식은 이름순·수동 정렬과 상관없이 부모 바로 밑에, 만든 순서로 선다.
 * - 부모가 목록에 없거나(사라짐·숨김) 고아면 자식은 최상위로 올라온다.
 * - 최상위 순서: `name`은 이름순, `manual`은 저장된 순서(모르는 이름은 뒤에, 이름순).
 */
export function buildSessionTree<T extends Pick<SessionRow, 'name' | 'tmuxName'> & { lineage?: Lineage | null }>(
  rows: T[], sort: RailSort = 'name', order: string[] = [],
): TreeEntry<T>[] {
  const byTmux = new Map<string, T>();
  for (const r of rows) if (r.tmuxName) byTmux.set(r.tmuxName, r);
  const kidsOf = new Map<T, T[]>();
  const top: T[] = [];
  for (const r of rows) {
    const l = r.lineage;
    const parent = l && l.state !== 'orphaned' && l.parent ? byTmux.get(l.parent) : undefined;
    // 자기 자신을 부모로 가리키는 값이나 부모가 다시 자식인 값은 서버가 만들지 않지만, 받아도 평평하게 그린다.
    if (parent && parent !== r && !parent.lineage?.parent) {
      const list = kidsOf.get(parent) || [];
      list.push(r);
      kidsOf.set(parent, list);
    } else {
      top.push(r);
    }
  }
  const rank = new Map(order.map((n, i) => [n, i]));
  const key = (r: T) => r.tmuxName || r.name;
  top.sort((a, b) => {
    if (sort === 'manual') {
      const ra = rank.get(key(a)) ?? Infinity, rb = rank.get(key(b)) ?? Infinity;
      if (ra !== rb) return ra - rb;
    }
    return a.name.localeCompare(b.name);
  });
  const byCreated = (a: T, b: T) => (a.lineage?.createdAt || 0) - (b.lineage?.createdAt || 0);
  return top.map((row) => {
    const all = (kidsOf.get(row) || []).sort(byCreated);
    return {
      row,
      kids: all.filter((k) => k.lineage!.state === 'running' || k.lineage!.state === 'reported'),
      closedKids: all.filter((k) => k.lineage!.state === 'closed'),
    };
  });
}

/** 부모 행의 칩: 「하위 보고 1/2」 — 열린 하위 중 보고한 수 / 열린 하위 수. 열린 하위가 없으면 null. */
export function kidsProgress<T extends { lineage?: Lineage | null }>(kids: T[]): { reported: number; open: number } | null {
  if (kids.length === 0) return null;
  return { reported: kids.filter((k) => k.lineage?.state === 'reported').length, open: kids.length };
}

/** 수동 정렬에서 한 항목을 옮긴 새 순서. `current`는 지금 화면의 최상위 순서(이름). */
export function moveInOrder(current: string[], name: string, target: string, place: 'before' | 'after'): string[] {
  if (name === target) return current.slice();
  const rest = current.filter((n) => n !== name);
  const i = rest.indexOf(target);
  if (i < 0) return current.slice();
  rest.splice(place === 'before' ? i : i + 1, 0, name);
  return rest;
}

/** 위/아래로 한 칸. 끝이면 그대로. */
export function stepInOrder(current: string[], name: string, dir: -1 | 1): string[] {
  const i = current.indexOf(name);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= current.length) return current.slice();
  const out = current.slice();
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** 설정 값(JSON 문자열) → 이름 배열. 깨졌으면 빈 배열. */
export function parseNameList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string');
  if (typeof v !== 'string' || !v) return [];
  try {
    const a = JSON.parse(v);
    return Array.isArray(a) ? a.filter((x): x is string => typeof x === 'string').slice(0, 500) : [];
  } catch { return []; }
}

// ── 만든 곳 ────────────────────────────────────────────────────────────────

export const HIDEABLE_ORIGINS: Origin[] = ['peer', 'external'];

export const ORIGIN_LABEL: Record<Origin, string> = {
  local: t('이 화면에서 만듦'),
  peer: t('다른 맥에서 만듦'),
  external: t('직접 만듦 (CLI·tmux)'),
};
/** 행 3줄에 아이콘과 함께 쓰는 짧은 글(시안 D02). 긴 문장(ORIGIN_LABEL)은 툴팁과 스크린 리더가 읽는다.
 * 만든 맥의 이름이 기록된 세션은 `originShort`가 「{host}에서 만듦」으로 바꾼다. 이 상수는 이름 기록이 없을 때의 문구다. */
export const ORIGIN_SHORT: Record<Exclude<Origin, 'local'>, string> = {
  peer: t('다른 맥에서 만듦'),
  external: t('직접 만듦'),
};

/** 만든 곳 문구. peer 세션에 만든 맥의 이름(`originHost`)이 기록돼 있으면 「{host}에서 만듦」, 없으면(옛 세션·옛 서버) 「다른 맥에서 만듦」 그대로. */
export function originShort(origin: Origin, host?: string | null): string {
  if (origin === 'local') return '';
  const h = typeof host === 'string' ? host.trim() : '';
  if (origin === 'peer' && h) return t('{host}에서 만듦', { host: h });
  return ORIGIN_SHORT[origin];
}
export function originLabel(origin: Origin, host?: string | null): string {
  const h = typeof host === 'string' ? host.trim() : '';
  return origin === 'peer' && h ? t('{host}에서 만듦', { host: h }) : ORIGIN_LABEL[origin];
}

/** 서버 값이 없거나(구버전 서버) 모르는 값이면 local — 모든 행에 외부 아이콘이 붙는 것보다 안전하다. */
export function normalizeOrigin(v: unknown): Origin {
  return v === 'peer' || v === 'external' ? v : 'local';
}

/** 서버가 준 kind. 모르는 값은 질문. */
export function normalizeKind(v: unknown): WaitKind {
  return v === 'approval' || v === 'choice' || v === 'plan' ? v : 'question';
}

export function normalizeBadge(v: unknown): DoneBadge | null {
  return v === 'question' || v === 'plan' ? v : null;
}

// ── 에이전트 공유 ──────────────────────────────────────────────────────────
// ⚠ share/share-data.js(지연 청크)와 같은 규칙이다 — shell 청크는 그 청크를 정적 import 할 수 없어 한 벌 더 적는다.

export const SHARE_MODE_LABEL: Record<ShareInfo['mode'], string> = { read: t('읽기 전용'), readwrite: t('읽기·쓰기') };
const SHARE_NICK_RE = /^[A-Za-z0-9_-]{1,32}$/;
/** 서버가 받는 세션 이름(`routes/agent_share.py`). 한글 이름 세션은 공유할 수 없다. */
const SHARE_NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;

/** 이 이름의 세션을 에이전트에게 공유할 수 있는가(서버 규칙과 같다). 접힌 띠 카드가 「공유 안 함」을 말해도 되는 세션인지 가르는 데 쓴다. */
export function canShareName(name: string | null | undefined): boolean {
  return typeof name === 'string' && SHARE_NAME_RE.test(name);
}

/** 서버 값 → 공유 표시. 닉네임 규칙을 못 지키면 공유 아님(서버도 같은 판단을 한다), 모르는 모드는 읽기 전용. */
export function normalizeShare(v: unknown): ShareInfo | null {
  const o = v as { nick?: unknown; mode?: unknown } | null;
  if (!o || typeof o !== 'object' || typeof o.nick !== 'string' || !SHARE_NICK_RE.test(o.nick)) return null;
  return { nick: o.nick, mode: o.mode === 'readwrite' ? 'readwrite' : 'read' };
}

/** 「공유 · 닉네임 · 읽기 전용」 — 요약줄·접힌 레일 툴팁이 쓴다. */
export function shareSentence(share: ShareInfo | null | undefined): string {
  return share ? t('공유 · {nick} · {mode}', { nick: share.nick, mode: SHARE_MODE_LABEL[share.mode] }) : '';
}

// ── 상태 키워드 ────────────────────────────────────────────────────────────

export const KIND_LABEL: Record<WaitKind, string> = {
  approval: t('승인 요청'),
  choice: t('선택 요청'),
  plan: t('계획 확인'),
  question: t('질문'),
};

export const BADGE_LABEL: Record<DoneBadge, string> = {
  question: t('질문 있음'),
  plan: t('계획 있음'),
};

export interface StateKeyword {
  /** 짧게 고정된 키워드 — 잘리지 않는다. */
  text: string;
  /** 작업 중의 도구 이름. 말줄임은 이쪽에서만 허용한다. */
  tool: string | null;
  /** done·working 옆의 보조 표시. */
  badge: string | null;
}

export function stateKeyword(
  row: Pick<SessionRow, 'status' | 'kind' | 'badge' | 'tool'>,
): StateKeyword {
  const s = normalizeState(row.status);
  if (s === 'waiting') return { text: KIND_LABEL[normalizeKind(row.kind)], tool: null, badge: null };
  // 보조 표시(질문 있음/계획 있음)는 done뿐 아니라 working에도 붙는다 — agy는 턴 종료 신호가
  // 없어 「계획 있음」 배지가 working 엔트리에 달린다(상태는 바꾸지 않는다). waiting은
  // 유형 키워드가 이미 그 뜻이라 안 붙이고, idle에 남은 배지는 숨긴다.
  const b = normalizeBadge(row.badge);
  const badge = b ? BADGE_LABEL[b] : null;
  if (s === 'working') return { text: STATE_LABEL.working, tool: row.tool || null, badge };
  if (s === 'done') return { text: STATE_LABEL.done, tool: null, badge };
  return { text: STATE_LABEL.idle, tool: null, badge: null };
}

export function formatRelativeTime(sinceEpochSec: number | null, nowMs = Date.now()): string {
  if (sinceEpochSec == null) return '';
  const sec = Math.max(0, Math.round(nowMs / 1000 - sinceEpochSec));
  if (sec < 60) return t('{n}초', { n: sec });
  const min = Math.round(sec / 60);
  if (min < 60) return t('{n}분', { n: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('{n}시간', { n: hr });
  return t('{n}일', { n: Math.round(hr / 24) });
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
  [originShowKey('external')]: true,
};

// 정렬(전역 — 기기마다 다르면 헷갈린다)과 계보 접기(기기별)를 각각 설정 키 하나에 JSON으로 묶는다.
// core/settings.js의 스키마 줄을 아끼려는 것이다 — 초기 번들(app.js + core.js)이 300KiB 상한에 닿아 있고
// 그 파일은 주석까지 번들에 남는다. 그래서 설명은 여기에 둔다.
/** 전역 설정 하나에 `{"sort":"name"|"manual","order":[이름…]}`. */
export const ORDER_KEY = 'rail.order';
/** 기기별 설정 하나에 `{"collapsed":[하위를 접은 부모…],"closedOpen":[닫힌 하위를 펼친 부모…]}`. 기본은 펼침·접힘. */
export const LINEAGE_KEY = 'rail.lineage';

function parseObj(v: unknown): Record<string, unknown> {
  if (typeof v !== 'string' || !v) return {};
  try { const o = JSON.parse(v); return o && typeof o === 'object' && !Array.isArray(o) ? o : {}; } catch { return {}; }
}

/** 정렬 설정 값 만들기 — Rail·Fleet가 저장할 때 쓴다. */
export function encodeOrder(sort: RailSort, order: string[]): string {
  return JSON.stringify({ sort, order: order.slice(0, 500) });
}
export function encodeLineagePrefs(collapsed: string[], closedOpen: string[]): string {
  return JSON.stringify({ collapsed: collapsed.slice(0, 200), closedOpen: closedOpen.slice(0, 200) });
}

/** 기기별 설정 하나에 `{"byRepo":bool,"recent":N,"recentCollapsed":bool,"runningCollapsed":bool,"groupsCollapsed":[묶음 id…]}`.
 * 키를 아끼려고 JSON 한 덩어리로 묶는다(LINEAGE_KEY와 같은 이유). 접힘은 폰에서 접은 것이 맥에 새면 안 되므로 기기별이다.
 * 「실행 중」 구역 접힘은 행 수를 세는 머리만 남기고, 묶음 접힘은 묶음 id(layout/store.js의 뷰 id)로 기억한다 —
 * 묶음 제목은 바뀔 수 있고 같은 제목의 묶음이 둘일 수 있다. 사라진 묶음의 id는 남아도 무해하다(200개에서 자른다). */
export const VIEW_KEY = 'rail.view';
export const RECENT_DEFAULT = 5;
export const RECENT_MAX = 50;
export const GROUPS_COLLAPSED_MAX = 200;
export interface RailView { byRepo: boolean; recent: number; recentCollapsed: boolean; runningCollapsed: boolean; groupsCollapsed: string[] }
export function parseRailView(v: unknown): RailView {
  const o = parseObj(v);
  const n = Number(o.recent);
  return {
    byRepo: o.byRepo === true,
    recent: Number.isInteger(n) && n >= 1 && n <= RECENT_MAX ? n : RECENT_DEFAULT,
    recentCollapsed: o.recentCollapsed === true,
    runningCollapsed: o.runningCollapsed === true,
    groupsCollapsed: parseNameList(o.groupsCollapsed).filter(Boolean).slice(0, GROUPS_COLLAPSED_MAX),
  };
}
export function encodeRailView(v: RailView): string {
  return JSON.stringify({
    byRepo: v.byRepo, recent: v.recent, recentCollapsed: v.recentCollapsed,
    runningCollapsed: !!v.runningCollapsed, groupsCollapsed: (v.groupsCollapsed || []).slice(-GROUPS_COLLAPSED_MAX),
  });
}
/** 묶음 하나의 접힘을 뒤집은 새 목록. 새로 접은 것은 끝에 붙어, 자를 때 오래된 것부터 빠진다. */
export function toggleGroupCollapsed(list: string[], id: string): string[] {
  if (!id) return list;
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

export interface RailPrefs {
  view: RailView;
  attentionShow: boolean;
  attentionCollapsed: boolean;
  hiddenOrigins: Origin[];
  sort: RailSort;
  order: string[];
  kidsCollapsed: string[];
  closedOpen: string[];
}

/** get: 값이 없으면 undefined/null을 돌려주는 설정 읽기(window.vtSettingsGet). */
export function readRailPrefs(get: (key: string) => unknown): RailPrefs {
  const flag = (key: string): boolean => {
    const v = get(key);
    return v === undefined || v === null ? RAIL_PREF_DEFAULTS[key] : Boolean(v);
  };
  return {
    view: parseRailView(get(VIEW_KEY)),
    attentionShow: flag(ATTENTION_SHOW_KEY),
    attentionCollapsed: flag(ATTENTION_COLLAPSED_KEY),
    hiddenOrigins: HIDEABLE_ORIGINS.filter((o) => !flag(originShowKey(o))),
    sort: parseObj(get(ORDER_KEY)).sort === 'manual' ? 'manual' : 'name',
    order: parseNameList(parseObj(get(ORDER_KEY)).order),
    kidsCollapsed: parseNameList(parseObj(get(LINEAGE_KEY)).collapsed),
    closedOpen: parseNameList(parseObj(get(LINEAGE_KEY)).closedOpen),
  };
}

// ── 구역 ───────────────────────────────────────────────────────────────────

const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name);

export interface RailLists<T> {
  attention: T[];
  sessions: T[];
}

export interface SessionGroup<T = SessionRow> {
  id: string; label: string; rows: T[];
}

/** 같은 제목의 다른 뷰를 섞지 않는다. 묶음은 view id, 구성원은 pane 순서로 묶는다. */
export function groupSessionRows<T extends Pick<SessionRow, 'sessionId' | 'tmuxName' | 'name'>>(
  rows: T[], groups: { id: string; label: string; sessionIds: string[]; tmuxNames?: string[] }[],
): SessionGroup<T>[] {
  const used = new Set<T>();
  const out: SessionGroup<T>[] = [];
  for (const g of groups) {
    const members = [...g.sessionIds.map((id) => rows.find((r) => r.sessionId === id)),
      ...(g.tmuxNames || []).map((name) => rows.find((r) => r.tmuxName === name && !r.sessionId))]
      .filter((r): r is T => !!r && !used.has(r));
    if (members.length < 2) continue;
    members.forEach((r) => used.add(r));
    out.push({ id: g.id, label: g.label, rows: members });
  }
  for (const r of rows) if (!used.has(r)) out.push({ id: '', label: '', rows: [r] });
  return out;
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

export interface CompactEntry<T> {
  row: T;
  /** 하위 세션(lineage 자식)이면 부모 밑 가지선을 그리는 데 쓰는 값. 최상위·개입 필요 행은 null. */
  kid: { last: boolean; merged: boolean; failed: boolean; closed: boolean } | null;
}

/**
 * 접힌 레일(48px 띠)의 칸 순서 — **펼친 레일과 같은 트리 순서**다. 순서의 출처를 새로 만들지 않고
 * 펼침이 쓰는 값(개입 필요 목록 · 분할 묶음 · `buildSessionTree`의 정렬·수동 순서·부모 밑 자식)을 그대로 받아 평평하게 편다.
 *
 *   개입 필요(waiting, 이름순) → 분할 묶음 구성원 → 트리(설정된 정렬 · 수동 순서, 하위 세션은 부모 바로 아래)
 *
 * 구역 머리글이 없어 waiting 세션은 한 번만 나온다(「개입 필요」에 올라간 행은 `groups`·`tree`에 이미 없다).
 * 하위 세션이 waiting이면 개입 필요로 올라가 평평하게 나오므로 부모 밑에 없다 — 펼침과 같다.
 * 부모가 하위를 접어 둔 것(`kidsCollapsed`)과 닫힌 하위(`closedOpen`을 펼쳐야 보임)도 펼침과 같은 규칙을 따른다.
 * `split`은 개입 필요와 나머지 사이에 경계선을 그릴 위치(위쪽 개수, 0이면 없음).
 * 펼친 레일은 이 함수를 안 쓴다 — 거기서는 구역 머리글 아래에 그대로 그린다.
 */
export function compactRailOrder<T extends { lineage?: Lineage | null }>(
  attention: T[],
  groups: SessionGroup<T>[],
  tree: TreeEntry<T>[],
  opts: { kidsCollapsed?: (r: T) => boolean; closedOpen?: (r: T) => boolean } = {},
): { entries: CompactEntry<T>[]; split: number } {
  const entries: CompactEntry<T>[] = attention.map((row) => ({ row, kid: null }));
  const top = entries.length;
  for (const g of groups) if (g.id) for (const row of g.rows) entries.push({ row, kid: null });
  for (const e of tree) {
    entries.push({ row: e.row, kid: null });
    if (opts.kidsCollapsed?.(e.row)) continue;
    const shown = [...e.kids, ...(opts.closedOpen?.(e.row) ? e.closedKids : [])];
    shown.forEach((row, i) => entries.push({
      row,
      kid: {
        last: i === shown.length - 1,
        merged: row.lineage?.state === 'reported',
        failed: row.lineage?.outcome === 'failed',
        closed: row.lineage?.state === 'closed',
      },
    }));
  }
  return { entries, split: entries.length > top ? top : 0 };
}

/**
 * 「실행 중」 구역 = 「세션」 전체에서 「개입 필요」에 이미 올라간 행을 뺀 것.
 * 개입 필요 구역이 꺼져 있으면(`attention`이 비어 있다) 빼는 것이 없다.
 * 접힌 레일(compactRailOrder)은 이 함수의 결과(펼침의 「실행 중」)를 그대로 받아 쓴다.
 */
export function splitRunning<T>(lists: RailLists<T>): T[] {
  if (lists.attention.length === 0) return lists.sessions;
  const top = new Set<T>(lists.attention);
  return lists.sessions.filter((r) => !top.has(r));
}

/** 카탈로그 세션이 마지막으로 움직인 시각(epoch초). */
export function lastActiveOf(s: { ended?: number | null; observed?: number | null; updated?: number | null }): number {
  return s.ended || s.observed || s.updated || 0;
}

/**
 * 「최근 종료」 구역에 나올 세션. 보관 안 된 것 중 지금 실행 중으로 그려지지 않는 것을
 * 최근에 움직인 순으로 `limit`개. `total`은 자르기 전 개수(「N개 더 보기」용).
 * 종료 시각이 없지만 실행 중 행도 없는 세션(상태 확인 필요·아직 실행 전)도 여기에 남긴다 —
 * 어디에도 안 보이면 「다시 확인」을 누를 곳이 없다.
 */
export function pickRecentEnded<S extends { archived?: number | null; ended?: number | null; observed?: number | null; updated?: number | null }>(
  sessions: S[], isLive: (s: S) => boolean, limit: number,
): { shown: S[]; total: number } {
  const all = sessions.filter((s) => !s.archived && !isLive(s)).sort((a, b) => lastActiveOf(b) - lastActiveOf(a));
  return { shown: all.slice(0, Math.max(0, limit)), total: all.length };
}

// ── 행 메뉴(⋯ 버튼·우클릭·폰 롱프레스가 같은 항목) ───────────────────────

export type RowMenuId =
  | 'open' | 'close' | 'rename' | 'openOnMac' | 'screens' | 'kill' | 'splitRight' | 'splitDown'
  | 'sessionAccess' | 'share' | 'shareSettings' | 'unshare' | 'agentPerms'
  | 'moveUp' | 'moveDown' | 'report' | 'closeDelegation' | 'openParent' | 'record';

export interface RowMenuItem {
  id: RowMenuId;
  label: string;
  detail?: string;
  danger?: boolean;
}

/**
 * 행 메뉴 맨 위의 요약 한 줄 — 호버 툴팁이 없는 터치에서 행의 숨은 정보를 읽는 유일한 자리다
 * (ui/tooltip.js는 터치 포인터에서 꺼져 있다). 상태 · (입력 대기면) 유형 · 웹 외 attach 수 ·
 * 만든 곳 · 브랜치. 값이 없는 조각은 건너뛴다. 선택 불가 헤더 행(`.vt-menu-summary`)에 그린다.
 */
export function rowSummary(
  row: Pick<SessionRow, 'status' | 'kind' | 'otherClients' | 'origin' | 'originHost' | 'branch'> & { share?: ShareInfo | null; cwd?: string },
): string {
  const s = normalizeState(row.status);
  const parts: string[] = [(STATE_LABEL as Record<string, string>)[s]];
  if (s === 'waiting') parts.push(KIND_LABEL[normalizeKind(row.kind)]);
  if (row.share) parts.push(shareSentence(row.share));
  if (row.otherClients > 0) parts.push(t('웹 외 {n}', { n: row.otherClients }));
  parts.push(originLabel(normalizeOrigin(row.origin), row.originHost));
  if (row.cwd) parts.push(row.cwd);
  if (row.branch) parts.push(row.branch);
  return parts.join(' · ');
}

export interface RowSummaryDetails {
  status: string;
  cwd?: string;
  project?: string;
  branch?: string;
  accessible: string;
}

export function rowSummaryDetails(row: Parameters<typeof rowSummary>[0]): RowSummaryDetails {
  const cwd = row.cwd || undefined;
  return {
    status: rowSummary({ ...row, cwd: undefined, branch: undefined }),
    cwd,
    project: cwd ? cwd.replace(/\/+$/, '').split('/').pop() || cwd : undefined,
    branch: row.branch || undefined,
    accessible: rowSummary(row),
  };
}

export interface RowMenuOpts {
  /** 「오른쪽에 나란히 열기」「아래에 열기」를 넣는다. 폭 720px 이상 + 터치일 때만 true로 준다
   * (term/session-actions.js `touchSplitAvailable`). 터치 드래그를 만들지 않는 대신의 길이다. */
  split?: boolean;
  /** 에이전트 공유 항목을 넣는다. **이 맥을 보고 있을 때만** true — 승인·공유는 이 맥 전용이다(다른 맥으로 중계되지 않는다). */
  share?: boolean;
  /** 수동 정렬의 키보드 대안(드래그를 못 쓰는 사람·터치). 최상위 행에서만, 끝이면 그 방향은 뺀다. */
  move?: { up: boolean; down: boolean };
  /** 이 행이 카탈로그 세션과 이어져 있다 — 작업 기록 항목을 넣고, 열려 있지 않아도 이름 변경을 허용한다. */
  record?: boolean;
}

/** 열린 세션이면 닫기, 아니면 열기. 이름 변경·맥에서 열기·연결된 화면은 웹 세션이 있어야 하고,
 * 종료는 tmux 세션이면 늘 있다(확인창은 vtConfirmAndKillSession). */
export function rowMenuItems(row: Pick<SessionRow, 'open' | 'tmuxName'> & { share?: ShareInfo | null; cwd?: string; lineage?: Lineage | null }, opts: RowMenuOpts = {}): RowMenuItem[] {
  // 카탈로그에 기록된 세션이면 작업 기록을 연다(종료 행 메뉴와 같은 항목). 행 아래 별도 텍스트 줄을 대신한다.
  const record: RowMenuItem[] = opts.record ? [{ id: 'record', label: t('작업 기록') }] : [];
  const items: RowMenuItem[] = [];
  // 위임 하위 세션: 보고 보기 · 부모로 이동 · 위임 닫기(세션은 남는다). 위임 관련은 이 맥에서만(장부가 이 맥에 있다).
  const lin = row.lineage;
  const delegationItems: RowMenuItem[] = [];
  if (lin && opts.share) {
    if (lin.summary) delegationItems.push({ id: 'report', label: t('보고 보기') });
    if (lin.parent) delegationItems.push({ id: 'openParent', label: t('부모 세션으로 이동'), detail: lin.parent });
    if (lin.state === 'running' || lin.state === 'reported') {
      delegationItems.push({ id: 'closeDelegation', label: t('위임 닫기'), detail: t('세션은 남김') });
    }
  }
  const move: RowMenuItem[] = [];
  if (opts.move?.up) move.push({ id: 'moveUp', label: t('위로 이동') });
  if (opts.move?.down) move.push({ id: 'moveDown', label: t('아래로 이동') });
  const split: RowMenuItem[] = opts.split
    ? [{ id: 'splitRight', label: t('오른쪽에 나란히 열기') }, { id: 'splitDown', label: t('아래에 열기') }]
    : [];
  if (row.open) {
    items.push(
      { id: 'close', label: t('닫기') },
      ...split,
      { id: 'rename', label: t('이름 변경') },
      { id: 'openOnMac', label: t('맥에서 열기') },
      { id: 'screens', label: t('연결된 화면') },
    );
  } else {
    items.push({ id: 'open', label: t('열기') }, ...split, ...(opts.record ? [{ id: 'rename' as const, label: t('이름 변경') }] : []));
  }
  items.push(...record, ...delegationItems, ...move);
  if (opts.share && row.tmuxName) {
    items.push({ id: 'sessionAccess', label: t('공유 및 권한…') });
  }
  if (row.tmuxName) items.push({ id: 'kill', label: t('종료'), detail: t('되돌릴 수 없음'), danger: true });
  return items;
}

/** 메뉴 항목을 실행한다. 지연 청크는 세션 모듈을 정적 import 못 해 window 브리지를 받는다. */
export function runRowMenu(
  id: RowMenuId,
  row: Pick<SessionRow, 'sessionId' | 'tmuxName' | 'name'> & { share?: ShareInfo | null; cwd?: string; context?: any; lineage?: Lineage | null },
  w: any,
  startRename: () => void,
  move?: (dir: -1 | 1) => void,
): void {
  const sid = row.sessionId || null;
  switch (id) {
    case 'sessionAccess': if (row.tmuxName) w.vtSessionAccess?.(row.tmuxName, row.share ?? null); break;
    case 'open': w.vtOpenSessionFromRail?.({ sessionId: sid, tmuxName: row.tmuxName }); break;
    case 'close': if (sid) w.vtCloseSession?.(sid); break;
    case 'rename': if (sid) startRename(); break;
    case 'openOnMac': if (sid) w.vtOpenSessionOnMac?.(sid); break;
    case 'screens': if (sid) w.vtShowClients?.(sid); break;
    case 'share': case 'shareSettings': if (row.tmuxName) w.vtShareOpen?.(row.tmuxName, row.share ?? null); break;
    case 'unshare': if (row.tmuxName) w.vtShareUnshare?.(row.tmuxName); break;
    case 'agentPerms': w.vtAgentPerms?.(); break;
    case 'kill': w.vtConfirmAndKillSession?.(row.tmuxName, sid); break;
    case 'splitRight': w.vtSplitOpenSession?.('right', { sessionId: sid, tmuxName: row.tmuxName }); break;
    case 'splitDown': w.vtSplitOpenSession?.('bottom', { sessionId: sid, tmuxName: row.tmuxName }); break;
    case 'moveUp': move?.(-1); break;
    case 'moveDown': move?.(1); break;
    case 'openParent': if (row.lineage?.parent) w.vtOpenSessionFromRail?.({ sessionId: null, tmuxName: row.lineage.parent }); break;
    // 리디자인 6단계(시안 O15-C) — 보고를 알림 창 대신 「작업 기록」으로 연다: 부모 세션 기록 왼쪽, 오른쪽에 이 위임 세션(보고 포함).
    // 부모가 사라졌으면 이 세션 자신의 기록을 연다.
    case 'report': if (row.lineage) void openReport(row, w); break;
    case 'closeDelegation': if (row.lineage) void closeDelegation(row.lineage.delegation, w); break;
  }
}

async function openReport(row: { tmuxName?: string | null; name?: string; lineage?: Lineage | null }, w: any): Promise<void> {
  const lin = row.lineage;
  if (!lin) return;
  try {
    const { openWorkLog } = await import('../history/session-record.js');
    openWorkLog({
      tmuxName: lin.parent || row.tmuxName || '',
      select: { kind: 'delegate', tmuxName: row.tmuxName, delegation: lin.delegation, name: row.name, summary: lin.summary },
    });
  } catch {
    void alertDialog(lin.summary, { title: t('{name}의 보고', { name: row.name || '' }) });
  }
  void w;
}

async function closeDelegation(id: string, w: any): Promise<void> {
  try {
    // 쿠키만 싣는 요청이라 vtFetch를 못 쓴다 — 주소·헤더는 그대로, 마감만 붙인다.
    const res = await timedFetch(`/api/delegations/${encodeURIComponent(id)}/close-by-user`, { method: 'POST', credentials: 'same-origin' });
    if (!res.ok) throw new Error(String(res.status));
    w.showToast?.(t('위임을 닫았습니다. 세션은 남아 있습니다.'));
  } catch {
    w.showToast?.(t('위임을 닫지 못했습니다. 다시 시도하세요.'));
  }
}

// ── 아이콘 선택 ────────────────────────────────────────────────────────────

/** 둘째 줄 앞 아이콘 이름. 메인 워크트리 = 본선(git-commit), 딸린 워크트리 = 가지(git-branch). */
export function worktreeIconName(row: Pick<SessionRow, 'isMainWorktree'>): 'git-commit' | 'git-branch' {
  return row.isMainWorktree ? 'git-commit' : 'git-branch';
}

export const ORIGIN_ICON: Record<Exclude<Origin, 'local'>, string> = {
  peer: 'laptop',
  external: 'square-terminal',
};

/** 변경량 표기. "+142 −38"(줄 수가 있으면) · 한쪽이 0이면 그쪽은 생략("+14", "−3") ·
 * 둘 다 0(이름만 바뀌거나 바이너리)이면 "파일 3". 없으면 null. RailRow·Fleet이 공용한다. */
export function diffLabel(row: Pick<SessionRow, 'changed' | 'diffFiles'>): string | null {
  const c = row.changed;
  const add = c && c.add > 0 ? `+${c.add}` : '';
  const del = c && c.del > 0 ? `−${c.del}` : '';
  if (add || del) return [add, del].filter(Boolean).join(' ');
  const files = row.diffFiles != null && row.diffFiles > 0 ? row.diffFiles : (c && c.files > 0 ? c.files : 0);
  return files > 0 ? t('파일 {n}', { n: files }) : null;
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
