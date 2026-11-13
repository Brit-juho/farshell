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
import { alertDialog, promptDialog } from '../core/dialog-lazy.js';
import { timedFetch } from '../core/api.js';
import { DEFAULT_SORT, type RepoSort, type SessionSort, type TreeSort } from './repo-tree.js';
import { normalizeShells, type BgShells } from './bg-shells.js';

// ── 행 모양 ────────────────────────────────────────────────────────────────

export type Origin = 'local' | 'peer' | 'external';
/** 에이전트에게 공유된 세션(서버 `share`). 모드는 읽기 전용(read) 또는 읽기·쓰기(readwrite). */
export interface ShareInfo { nick: string; mode: 'read' | 'readwrite'; }
export type WaitKind = 'approval' | 'choice' | 'plan' | 'question';
export type DoneBadge = 'question' | 'plan';

export interface SessionRow {
  physicalKey?: string;
  /** 세션을 만든 시각(초, tmux) — 「만든 순」 정렬. */
  created?: number | null;
  catalogSessionId?: string;
  runtimeId?: string;
  subagents?: unknown;
  /** 백그라운드 셸(서버 bg_shells.py 원본). 화면은 bg-shells.ts `normalizeShells`로 읽는다. */
  shells?: unknown;
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
  /** waiting에서 감지된 질문 원문. 행에는 안 보이고 툴팁에만 쓴다(목록에서 답하지 않는다 — 답은 터미널에서). */
  question?: string | null;
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
  /** FarShell이 새 세션을 만들 때 넣은 계정(서버 `profile` — 생성 시 기록, 셸에서 바꾼 환경은 반영 안 됨). 안 넣었거나 구버전 서버면 없다/null. */
  profile?: SessionProfile | null;
  /** 이 세션이 속한 묶음 뷰의 이름(묶음이 아니면 null). */
  viewLabel: string | null;
  viewId?: string | null;
  /** 이 브라우저 탭 말고 이 세션에 붙은 **웹** 화면 수(다른 브라우저 탭·폰). */
  otherScreens: number;
  /** 웹이 아닌 attach 수(맥 터미널 등). 구버전 서버가 값을 안 주면 0. */
  otherClients: number;
  /** 에이전트 위임으로 만든 하위 세션이면 그 계보(서버 위임 장부 기준). 아니면 없다/null. */
  lineage?: Lineage | null;
  /** 카탈로그가 정한 자리(처음 시작한 저장소·작업 트리, cd해도 그대로). 아직 기록 전·옛 서버는 null. */
  home?: SessionHome | null;
  /** 보이는 이름(별칭)이 사람이 붙인 것인가. */
  nameSource?: 'auto' | 'human' | null;
  /** 서버가 만든 도구 요약(파일 경로·프로그램 이름). working에서 도구 옆에 붙는다. */
  summary?: string | null;
  /** 마지막으로 사람이 친 지시(이 맥 화면에만). */
  lastPrompt?: string | null;
}

export interface SessionHome {
  project_id: string; workspace_id?: string; root: string; name: string; repository: boolean; kind?: string; path?: string;
  /** 목록의 저장소(CLI를 켠 곳으로 등록됨·제거 안 됨)인가, 숨겼나 — 아니면 「저장소 밖」으로 그린다. */
  registered?: boolean; hidden?: boolean;
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
  /** 보고가 온 시각(초). 아직이면 null. */
  reportedAt?: number | null;
  /** 보고가 왔는데 부모가 그 뒤로 새 턴을 시작하지 않았다(서버 판정). */
  reportUnread?: boolean;
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
    reportedAt: Number(o.reportedAt) || null,
    reportUnread: o.reportUnread === true,
  };
}

/** 가지 그래프의 하위 세션 한 줄 오른쪽: 「보고 도착」(부모가 아직 안 읽음) · 「보고 · 성공/실패」 · 「진행 중」 …. */
export function kidLabel(l: Pick<Lineage, 'state' | 'outcome' | 'reportUnread'>): { text: string; tone: 'done' | 'error' | 'muted' } {
  if (l.state === 'reported' && l.reportUnread) return { text: t('보고 도착'), tone: l.outcome === 'failed' ? 'error' : 'done' };
  if (l.state === 'reported' && l.outcome === 'failed') return { text: lineageLabel(l), tone: 'error' };
  return { text: lineageLabel(l), tone: 'muted' };
}

/** 하위 세션 한 줄 표시: 「진행 중」「보고 · 성공」「보고 · 실패」「닫힘」「부모 없음」. */
export function lineageLabel(l: Pick<Lineage, 'state' | 'outcome'>): string {
  if (l.state === 'reported') return l.outcome === 'failed' ? t('보고 · 실패') : t('보고 · 성공');
  if (l.state === 'closed') return t('닫힘');
  if (l.state === 'orphaned') return t('부모 없음');
  return t('진행 중');
}

/** 부모 행의 칩: 「하위 보고 1/2」 — 열린 하위 중 보고한 수 / 열린 하위 수. 열린 하위가 없으면 null. */
export function kidsProgress<T extends { lineage?: Lineage | null }>(kids: T[]): { reported: number; open: number } | null {
  if (kids.length === 0) return null;
  return { reported: kids.filter((k) => k.lineage?.state === 'reported').length, open: kids.length };
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
  // 예전 이름 「직접 만듦」은 칸에서 지운 말이라 설정 필터 이름도 바꿨다(세션 로직 점검 후속).
  external: t('터미널에서 만든 세션'),
};

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

/** 생성 때 고른 계정. `name`이 null이면 레지스트리에서 이름을 못 읽은 것(이름을 지어내지 않는다). */
export interface SessionProfile { id: string; name: string | null; tool: string | null; }

/** 서버 값 → 계정 표시. 모양이 맞지 않으면 표시하지 않는다(추측 표시 금지). */
export function normalizeProfile(v: unknown): SessionProfile | null {
  const o = v as { id?: unknown; name?: unknown; tool?: unknown } | null;
  if (!o || typeof o !== 'object' || typeof o.id !== 'string' || !o.id) return null;
  return { id: o.id, name: typeof o.name === 'string' && o.name ? o.name : null, tool: typeof o.tool === 'string' ? o.tool : null };
}

/** 배지·툴팁 글. 이름을 모르면 「알 수 없는 계정」. */
export function profileLabel(p: SessionProfile): string { return p.name ?? t('알 수 없는 계정'); }

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

/** 가지 그래프 세션 칸의 둘째 줄(할 말이 있을 때만). 「유휴」는 쓰지 않는다 — 아무 일도 없으면 마지막으로 사람이 친
 * 지시를 보이고, 그것도 없으면 둘째 줄이 없다. */
export interface NodeLine { tone: 'waiting' | 'working' | 'done' | 'muted'; text: string; detail: string | null }
export function nodeLine(row: Pick<SessionRow, 'status' | 'kind' | 'badge' | 'tool' | 'summary' | 'lastPrompt' | 'unseen'>): NodeLine | null {
  const s = normalizeState(row.status);
  const detail = row.summary || null;
  if (s === 'waiting') return { tone: 'waiting', text: KIND_LABEL[normalizeKind(row.kind)], detail: detail || row.tool || null };
  if (s === 'working') return { tone: 'working', text: row.tool || STATE_LABEL.working, detail };
  if (s === 'done') {
    const b = normalizeBadge(row.badge);
    const text = row.unseen ? t('끝남 · 확인 안 함') : t('끝남');
    return { tone: row.unseen ? 'done' : 'muted', text: b ? `${text} · ${BADGE_LABEL[b]}` : text, detail: null };
  }
  return row.lastPrompt ? { tone: 'muted', text: `「${row.lastPrompt}」`, detail: null } : null;
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

// 계보 접기(기기별)를 설정 키 하나에 JSON으로 묶는다. core/settings.js의 스키마 줄을 아끼려는 것이다 — 초기 번들이
// 300KiB 상한에 닿아 있고 그 파일은 주석까지 번들에 남는다. 그래서 설명은 여기에 둔다.
// (예전 정렬 설정 `rail.order`는 세션 로직 점검 단계 8에서 스키마에서도 뺐다 — 목록은 저장소 묶음·최근 활동순뿐이다.
//  저장돼 있던 값은 스키마에 없는 키라 읽히지 않는다.)
/** 기기별 설정 하나에 `{"collapsed":[하위를 접은 부모…]}`. 기본은 펼침. (예전의 `closedOpen` — 닫힌 하위 펼침 — 은 가지 그래프에
 *  그 접기가 없어 읽지 않는다. 다음에 저장할 때 빠진다.) */
export const LINEAGE_KEY = 'rail.lineage';

function parseObj(v: unknown): Record<string, unknown> {
  if (typeof v !== 'string' || !v) return {};
  try { const o = JSON.parse(v); return o && typeof o === 'object' && !Array.isArray(o) ? o : {}; } catch { return {}; }
}

export function encodeLineagePrefs(collapsed: string[]): string {
  return JSON.stringify({ collapsed: collapsed.slice(0, 200) });
}

/** 기기별 설정 하나에 `{"groupsCollapsed":[열쇠…]}` — 레일의 저장소 접힘(`repo:<저장소 경로>`)과 「종료 N」 펼침
 * (`ended:<저장소 경로>`). 키를 아끼려고 JSON 한 덩어리로 묶는다(LINEAGE_KEY와 같은 이유). 폰에서 접은 것이 맥에 새면 안
 * 되므로 기기별이다. 예전 값(byRepo·recent·recentCollapsed·runningCollapsed)은 읽지 않는다(세션 로직 점검 단계 5·7). */
export const VIEW_KEY = 'rail.view';
export const GROUPS_COLLAPSED_MAX = 200;
/** 정렬(저장소 2차, 설계 §3.7) — 같은 기기 설정 JSON에 칸을 더한다(새 설정 키를 만들지 않아 초기 번들에 영향 없음).
 * 기본은 「생성된 순」: 저장소 등록순·세션 만든 순, 입력 대기 먼저는 끔 — 최근 활동순은 목록이 계속 바뀐다. */
export interface RailView { groupsCollapsed: string[]; sort: TreeSort }
const REPO_SORTS: RepoSort[] = ['registered', 'name', 'activity'];
const SESSION_SORTS: SessionSort[] = ['created', 'name', 'activity'];
export function parseRailView(v: unknown): RailView {
  const o = parseObj(v);
  const s = o.sort && typeof o.sort === 'object' && !Array.isArray(o.sort) ? o.sort as Record<string, unknown> : {};
  return {
    groupsCollapsed: parseNameList(o.groupsCollapsed).filter(Boolean).slice(0, GROUPS_COLLAPSED_MAX),
    sort: {
      repos: REPO_SORTS.includes(s.repos as RepoSort) ? s.repos as RepoSort : DEFAULT_SORT.repos,
      sessions: SESSION_SORTS.includes(s.sessions as SessionSort) ? s.sessions as SessionSort : DEFAULT_SORT.sessions,
      waitingFirst: typeof s.waitingFirst === 'boolean' ? s.waitingFirst : DEFAULT_SORT.waitingFirst,
    },
  };
}
export function encodeRailView(v: RailView): string {
  return JSON.stringify({ groupsCollapsed: (v.groupsCollapsed || []).slice(-GROUPS_COLLAPSED_MAX), sort: v.sort || DEFAULT_SORT });
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
  kidsCollapsed: string[];
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
    kidsCollapsed: parseNameList(parseObj(get(LINEAGE_KEY)).collapsed),
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

/**
 * 「실행 중」 구역 = 「세션」 전체에서 「개입 필요」에 이미 올라간 행을 뺀 것.
 * 개입 필요 구역이 꺼져 있으면(`attention`이 비어 있다) 빼는 것이 없다.
 * 저장소 가지 그래프(repo-tree.ts)와 접힌 띠는 이 결과(개입 필요에 없는 행)를 받는다.
 */
export function splitRunning<T>(lists: RailLists<T>): T[] {
  if (lists.attention.length === 0) return lists.sessions;
  const top = new Set<T>(lists.attention);
  return lists.sessions.filter((r) => !top.has(r));
}

// ── 행 메뉴(⋯ 버튼·우클릭·폰 롱프레스가 같은 항목) ───────────────────────

export type RowMenuId =
  | 'open' | 'close' | 'rename' | 'openOnMac' | 'screens' | 'kill' | 'splitRight' | 'splitDown'
  | 'sessionAccess' | 'share' | 'shareSettings' | 'unshare' | 'agentPerms'
  | 'report' | 'closeDelegation' | 'openParent' | 'record';

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
 * hideIdle — 눈에 보이는 요약에서는 「유휴」를 쓰지 않는다(칸에서 지운 말과 같은 기준). 화면 읽기용(accessible)은 남긴다 —
 * 링 모양만으로는 상태를 읽을 수 없다.
 */
export function rowSummary(
  row: Pick<SessionRow, 'status' | 'kind' | 'otherClients' | 'origin' | 'originHost' | 'branch'> & { share?: ShareInfo | null; cwd?: string },
  opts: { hideIdle?: boolean } = {},
): string {
  const s = normalizeState(row.status);
  const parts: string[] = opts.hideIdle && s === 'idle' ? [] : [(STATE_LABEL as Record<string, string>)[s]];
  if (s === 'waiting') parts.push(KIND_LABEL[normalizeKind(row.kind)]);
  if (row.share) parts.push(shareSentence(row.share));
  if (row.otherClients > 0) parts.push(t('웹 외 {n}', { n: row.otherClients }));
  // 만든 곳은 다른 맥에서 만든 세션일 때만 — 「직접 만듦」은 대부분의 세션에 붙어 정보가 없었다(세션 로직 점검 단계 7).
  if (normalizeOrigin(row.origin) === 'peer') parts.push(originLabel('peer', row.originHost));
  if (row.cwd) parts.push(row.cwd);
  if (row.branch) parts.push(row.branch);
  return parts.join(' · ');
}

export interface RowSummaryDetails {
  status: string;
  /** 백그라운드 셸 — 하나라도 있으면(실행 중·끝남) 메뉴 요약에 구역이 생긴다. */
  shells?: BgShells;
  cwd?: string;
  project?: string;
  branch?: string;
  accessible: string;
}

export function rowSummaryDetails(row: Parameters<typeof rowSummary>[0] & { shells?: unknown }): RowSummaryDetails {
  const cwd = row.cwd || undefined;
  const shells = normalizeShells(row.shells);
  return {
    ...(shells.running.length || shells.ended.length ? { shells } : {}),
    status: rowSummary({ ...row, cwd: undefined, branch: undefined }, { hideIdle: true }),
    cwd,
    project: cwd ? cwd.replace(/\/+$/, '').split('/').pop() || cwd : undefined,
    branch: row.branch || undefined,
    accessible: rowSummary(row) + (shells.running.length ? ' · ' + t('백그라운드 셸 {n}개 실행 중', { n: shells.running.length }) : ''),
  };
}

export interface RowMenuOpts {
  /** 「오른쪽에 나란히 열기」「아래에 열기」를 넣는다. 폭 720px 이상 + 터치일 때만 true로 준다
   * (term/session-actions.js `touchSplitAvailable`). 터치 드래그를 만들지 않는 대신의 길이다. */
  split?: boolean;
  /** 에이전트 공유 항목을 넣는다. **이 맥을 보고 있을 때만** true — 승인·공유는 이 맥 전용이다(다른 맥으로 중계되지 않는다). */
  share?: boolean;
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
  items.push(...record, ...delegationItems);
  if (opts.share && row.tmuxName) {
    items.push({ id: 'sessionAccess', label: t('공유 및 권한…') });
  }
  if (row.tmuxName) items.push({ id: 'kill', label: t('종료'), detail: t('되돌릴 수 없음'), danger: true });
  return items;
}

/** 분할 화면 묶음 이름 바꾸기(행 메뉴 「묶음 이름 바꾸기」 — 레일·폰 공용). 묶음 머리가 없어진 뒤(세션 로직 점검 단계 5)
 * 남은 유일한 길이다. 이름은 뷰 라벨이라 세션 이름·브랜치와 따로 저장된다(layout/store.js setTabLabel, 앞뒤 공백 제거 1~128자).
 * 열린 묶음과 닫혀 기억된 묶음 모두 된다. 바꿨으면 true. */
export async function renameViewGroup(row: Pick<SessionRow, 'viewId' | 'viewLabel'>, w: any = window): Promise<boolean> {
  if (!row.viewId) return false;
  const v = await promptDialog(t('세션 이름과 따로 저장됩니다. 1~128자.'), {
    title: t('묶음 이름 바꾸기'), defaultValue: row.viewLabel || '', okLabel: t('저장'),
  });
  if (v === null || v === undefined) return false;
  const value = String(v).trim();
  if (!value || value === (row.viewLabel || '')) return false;
  if (value.length > 128 || !w.vtRenameTab?.(row.viewId, value)) {
    await alertDialog(t('묶음 이름을 바꾸지 못했습니다. 1~128자로 정하거나, 묶음이 아직 있는지 확인하세요.'));
    return false;
  }
  return true;
}

/** 메뉴 항목을 실행한다. 지연 청크는 세션 모듈을 정적 import 못 해 window 브리지를 받는다. */
export function runRowMenu(
  id: RowMenuId,
  row: Pick<SessionRow, 'sessionId' | 'tmuxName' | 'name'> & { share?: ShareInfo | null; cwd?: string; context?: any; lineage?: Lineage | null },
  w: any,
  startRename: () => void,
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
