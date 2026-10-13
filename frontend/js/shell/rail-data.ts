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
import { alertDialog } from '../core/dialog.js';

// ── 행 모양 ────────────────────────────────────────────────────────────────

export type Origin = 'local' | 'peer' | 'external';
/** 에이전트에게 공유된 세션(서버 `share`). 모드는 읽기 전용(read) 또는 읽기·쓰기(readwrite). */
export interface ShareInfo { nick: string; mode: 'read' | 'readwrite'; }
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
  context?: any;
  contextStatus?: string;
  isMainWorktree?: boolean;
  worktreeId?: string | null;
  origin: Origin;
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

export interface RailPrefs {
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

/**
 * 접힌 레일(48px)은 구역 머리글이 없다 — 「개입 필요」와 「세션」을 따로 그리면 waiting 세션이
 * 두 번 보인다. 하나의 목록으로: 개입 필요 세션이 위(이름순), 나머지가 그 아래(이름순).
 * `split`은 둘 사이에 경계선을 그릴 위치(위쪽 개수, 0이면 경계선 없음).
 * 펼친 레일은 이 함수를 안 쓴다 — 거기서는 waiting이 두 구역에 모두 남는 게 규칙이다.
 */
export function compactRailRows<T>(lists: RailLists<T>): { rows: T[]; split: number } {
  const top = new Set<T>(lists.attention);
  const rest = lists.sessions.filter((r) => !top.has(r));
  return { rows: [...lists.attention, ...rest], split: rest.length > 0 ? top.size : 0 };
}

// ── 행 메뉴(⋯ 버튼·우클릭·폰 롱프레스가 같은 항목) ───────────────────────

export type RowMenuId =
  | 'open' | 'close' | 'rename' | 'openOnMac' | 'screens' | 'kill' | 'splitRight' | 'splitDown'
  | 'workspace' | 'share' | 'shareSettings' | 'unshare' | 'agentPerms'
  | 'moveUp' | 'moveDown' | 'report' | 'closeDelegation' | 'openParent';

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
  row: Pick<SessionRow, 'status' | 'kind' | 'otherClients' | 'origin' | 'branch'> & { share?: ShareInfo | null; cwd?: string },
): string {
  const s = normalizeState(row.status);
  const parts: string[] = [(STATE_LABEL as Record<string, string>)[s]];
  if (s === 'waiting') parts.push(KIND_LABEL[normalizeKind(row.kind)]);
  if (row.share) parts.push(shareSentence(row.share));
  if (row.otherClients > 0) parts.push(t('웹 외 {n}', { n: row.otherClients }));
  parts.push(ORIGIN_LABEL[normalizeOrigin(row.origin)]);
  if (row.cwd) parts.push(row.cwd);
  if (row.branch) parts.push(row.branch);
  return parts.join(' · ');
}

export interface RowMenuOpts {
  /** 「오른쪽에 나란히 열기」「아래에 열기」를 넣는다. 폭 720px 이상 + 터치일 때만 true로 준다
   * (term/session-actions.js `touchSplitAvailable`). 터치 드래그를 만들지 않는 대신의 길이다. */
  split?: boolean;
  /** 에이전트 공유 항목을 넣는다. **이 맥을 보고 있을 때만** true — 승인·공유는 이 맥 전용이다(다른 맥으로 중계되지 않는다). */
  share?: boolean;
  /** 수동 정렬의 키보드 대안(드래그를 못 쓰는 사람·터치). 최상위 행에서만, 끝이면 그 방향은 뺀다. */
  move?: { up: boolean; down: boolean };
}

/** 열린 세션이면 닫기, 아니면 열기. 이름 변경·맥에서 열기·연결된 화면은 웹 세션이 있어야 하고,
 * 종료는 tmux 세션이면 늘 있다(확인창은 vtConfirmAndKillSession). */
export function rowMenuItems(row: Pick<SessionRow, 'open' | 'tmuxName'> & { share?: ShareInfo | null; cwd?: string; lineage?: Lineage | null }, opts: RowMenuOpts = {}): RowMenuItem[] {
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
    items.push({ id: 'open', label: t('열기') }, ...split);
  }
  items.push(...delegationItems, ...move);
  if (row.cwd) items.push({ id: 'workspace', label: t('작업 위치 보기') });
  // 이름 규칙 밖(한글 등)은 공유 항목만 뺀다.
  if (opts.share && row.tmuxName) {
    if (SHARE_NAME_RE.test(row.tmuxName)) {
      if (row.share) {
        items.push(
          { id: 'shareSettings', label: t('공유 설정…') },
          { id: 'unshare', label: t('공유 해제'), detail: t('권한 모두 삭제') },
        );
      } else {
        items.push({ id: 'share', label: t('에이전트와 공유…') });
      }
    }
    items.push({ id: 'agentPerms', label: t('에이전트 권한…') });
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
    case 'workspace': void import('../ui/workspace-details.js').then(m => m.showWorkspaceDetails(row)); break;
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
    case 'report': if (row.lineage) void alertDialog(row.lineage.summary, { title: t('{name}의 보고', { name: row.name }) }); break;
    case 'closeDelegation': if (row.lineage) void closeDelegation(row.lineage.delegation, w); break;
  }
}

async function closeDelegation(id: string, w: any): Promise<void> {
  try {
    const res = await fetch(`/api/delegations/${encodeURIComponent(id)}/close-by-user`, { method: 'POST', credentials: 'same-origin' });
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
