// 접힌 세션 띠(48px)의 칸에 마우스를 올리거나 포커스했을 때 뜨는 **상세 카드**의 내용 구성. DOM도 fetch도 모른다.
//
// 값은 전부 이미 클라이언트에 있는 것(세션 행 `SessionRow`, 호스트 목록, 하위 세션 행)만 쓴다 — 올리는 순간 네트워크 요청은 없다.
// 비어 있는 값은 줄 자체를 만들지 않는다(「정보 갱신 대기」 같은 빈 문구 없음).
//
// 구성 순서(위 → 아래):  머리(마크 · 이름 · 경과) · 상태 칩(+ 입력 대기 종류) · 활동 · 위치 · 서브에이전트 · 메타.
// 높이가 정해져 있어 넘치면 **중요도 낮은 쪽부터** 자른다: 메타 → 하위 세션 줄 → 내부 에이전트 줄(위치는 자르지 않는다).
//
// 도구 이름(`tool`)은 호출마다 바뀌어 깜빡이므로(NativeChildren의 「도구 이름을 안 그린다」와 같은 이유) 이 함수는 **받은 값 그대로**만
// 쓴다. 카드 컴포넌트(RailHoverCard.tsx)가 열릴 때 한 번 찍은 값과 상태가 바뀔 때만 갱신한 값을 넘긴다.
import { t } from '../core/i18n.js';
import { STATE_LABEL, normalizeState } from '../design/state-classes.js';
import { normalizeSubagents, nativeStateLabel, sameLabelOrdinals } from './native-subagents.js';
import { reasonLabel } from './host-data.js';
import {
  KIND_LABEL, ORIGIN_ICON, diffLabel, formatRelativeTime, kidsProgress, lineageLabel, normalizeKind, normalizeOrigin,
  originLabel, shareSentence, stateKeyword, type SessionRow,
} from './rail-data.js';

export type HcState = 'idle' | 'working' | 'waiting' | 'done';
/** 줄 왼쪽 열에 그리는 상태 모양. `failed`는 내부 에이전트의 실패, `unseen`은 읽지 않음 점. */
export type HcGlyph = HcState | 'failed' | 'unseen';

export interface HcRow {
  /** 안정된 키(렌더 키·테스트용). */
  key: string;
  /** 왼쪽 14px 열의 아이콘 이름(shellIcon). `glyph`가 있으면 그쪽이 이긴다. */
  icon?: string;
  glyph?: HcGlyph;
  /** 값 열의 글. 길면 CSS가 말줄임한다(경로는 여기서 이미 가운데를 줄였다). */
  text: string;
  mono?: boolean;
  muted?: boolean;
  /** 흐리게(상태 확인 필요한 서브에이전트). 글로도 말한다 — extra. */
  dim?: boolean;
  /** 말줄임 없이 보여 줄 전체 값(마우스를 올려 읽는 title). */
  title?: string;
  /** 값 뒤에 붙는 작은 표기(「워크트리」). */
  tag?: string;
  /** 오른쪽 열(경과·보고 상태·응답 속도). */
  extra?: string;
  /** 오른쪽 열의 +N −N. */
  diff?: { add: number; del: number };
}

export type HcSectionId = 'activity' | 'location' | 'subagents' | 'meta';
export interface HcSection { id: HcSectionId; rows: HcRow[]; }

export interface HcModel {
  name: string;
  agent: string | null;
  state: HcState;
  stateText: string;
  /** 입력 대기의 종류(승인 요청·선택 요청·계획 확인·질문), working·done의 보조 표시(질문 있음·계획 있음). */
  note: string | null;
  since: string;
  sections: HcSection[];
}

export interface HcHost { label: string; online: boolean; latencyMs?: number | null; reason?: string | null }

export interface HcOptions {
  now?: number;
  /** 도구 이름 — 호출부가 정한 값 그대로 쓴다(위 머리말). working일 때만 그린다. */
  tool?: string | null;
  /** 이 세션이 부모인 위임 하위 세션 행(진행 중·보고됨). */
  kids?: SessionRow[];
  /** 원격 호스트를 보고 있으면 그 호스트. 로컬이면 null. */
  host?: HcHost | null;
  /** 「공유 안 함」을 말해도 되는 세션인가(이 맥 + 공유할 수 있는 이름). */
  shareable?: boolean;
  /** 머리·상태 칩을 뺀 본문 줄 수 상한. */
  maxLines?: number;
}

export const NATIVE_MAX = 5;
export const KIDS_MAX = 3;
/** 본문 줄 상한 — 카드 최대 높이 380px(CSS)에서 머리 24 + 칩 22 + 위아래 여백 20 + 구역 사이 8×3을 빼고 줄 높이 20으로 나눈 값. */
export const BODY_MAX_LINES = 14;
export const PATH_MAX_CHARS = 34;

/** 홈 디렉터리를 `~`로. 클라이언트는 홈 경로를 모르므로 `/Users/<이름>` · `/home/<이름>` · `/root` 모양을 홈으로 본다. */
export function tildePath(p: string): string {
  const m = /^(?:\/Users\/[^/]+|\/home\/[^/]+|\/root)(?=\/|$)/.exec(p);
  return m ? '~' + p.slice(m[0].length) : p;
}

/**
 * 경로 가운데 말줄임 — 첫 조각(`~` 또는 루트)과 **마지막 3 → 2 → 1 조각**을 보이게 한다: `~/…/tools/farshell/frontend`.
 * 마지막 조각 하나도 안 들어가면 그 조각의 앞을 줄인다(`~/…ong-directory-name`).
 */
export function shortenPath(path: string, max = PATH_MAX_CHARS): string {
  const trimmed = path.length > 1 ? path.replace(/\/+$/, '') || '/' : path;
  const p = tildePath(trimmed);
  if (p.length <= max) return p;
  const parts = p.split('/');
  const head = parts[0];            // `~` 또는 ''(절대 경로의 루트)
  const rest = parts.slice(1);
  for (let k = Math.min(3, rest.length - 1); k >= 1; k--) {
    const s = `${head}/…/${rest.slice(-k).join('/')}`;
    if (s.length <= max) return s;
  }
  const last = rest[rest.length - 1] ?? p;
  const room = Math.max(1, max - head.length - 2);
  return `${head}/…${last.slice(-room)}`;
}

const NATIVE_RANK: Record<string, number> = { working: 0, idle: 1, failed: 2, done: 3 };
const KID_RANK: Record<HcState, number> = { waiting: 0, working: 1, done: 2, idle: 3 };

function nativeRows(row: SessionRow, now: number, cap: number): HcRow[] {
  const sub = normalizeSubagents(row.subagents);
  const items: any[] = sub.items;
  if (!items.length && !sub.limited) return [];
  const working = items.filter((r) => r.state === 'working').length;
  const head = sub.limited
    ? t('내부 에이전트 · 표시 한도 도달')
    : working > 0 ? t('내부 에이전트 {n}개 · 작업 중 {m}', { n: items.length, m: working }) : t('내부 에이전트 {n}개', { n: items.length });
  const rows: HcRow[] = [{ key: 'native', icon: 'bot', text: head }];
  // 같은 이름은 시작 순 #N — 전체 목록에서 한 번 매겨(NativeChildren과 같은 규칙) 카드가 자른 뒤에도 번호가 같다.
  const ord = sameLabelOrdinals(items);
  const ranked = items
    .map((r, i) => ({ r, i }))
    .sort((a, b) => (NATIVE_RANK[a.r.state] - NATIVE_RANK[b.r.state]) || ((a.r.started_at ?? Infinity) - (b.r.started_at ?? Infinity)) || (a.i - b.i))
    .map((x) => x.r);
  for (const r of ranked.slice(0, cap)) {
    const name = ord.has(r.id) ? `${r.label} #${ord.get(r.id)}` : r.label;
    rows.push({
      key: 'native:' + r.id, glyph: r.state === 'failed' ? 'failed' : (r.state as HcState), text: name,
      extra: r.stale ? t('상태 확인 필요') : formatRelativeTime(r.started_at, now) || undefined,
      dim: r.stale, title: `${name} · ${nativeStateLabel(r.state)}`,
    });
  }
  if (ranked.length > cap) rows.push({ key: 'native-more', text: t('외 {n}개', { n: ranked.length - cap }), muted: true });
  return rows;
}

function kidRows(kids: SessionRow[], cap: number): HcRow[] {
  const open = kids.filter((k) => k.lineage && (k.lineage.state === 'running' || k.lineage.state === 'reported'));
  const progress = kidsProgress(open);
  if (!progress) return [];
  const rows: HcRow[] = [{
    key: 'kids', icon: 'corner-down-right',
    text: t('하위 세션 {total} · 보고 {r}/{total}', { total: progress.open, r: progress.reported }),
  }];
  const ranked = open
    .map((k, i) => ({ k, i }))
    .sort((a, b) => (KID_RANK[normalizeState(a.k.status) as HcState] - KID_RANK[normalizeState(b.k.status) as HcState]) || (a.i - b.i))
    .map((x) => x.k);
  for (const k of ranked.slice(0, cap)) {
    rows.push({
      key: 'kid:' + (k.tmuxName || k.name), glyph: normalizeState(k.status) as HcState, text: k.name,
      extra: k.lineage ? lineageLabel(k.lineage) : undefined, title: k.lineage?.task || undefined,
    });
  }
  if (ranked.length > cap) rows.push({ key: 'kids-more', text: t('외 {n}개', { n: ranked.length - cap }), muted: true });
  return rows;
}

function locationRows(row: SessionRow, host: HcHost | null | undefined): HcRow[] {
  const rows: HcRow[] = [];
  if (row.cwd) {
    rows.push({ key: 'dir', icon: 'folder', text: shortenPath(row.cwd), mono: true, title: row.locationTip || row.cwd });
  }
  const branch = row.branch || '';
  if (branch) {
    const c = row.changed;
    const hasLines = !!c && (c.add > 0 || c.del > 0);
    rows.push({
      key: 'branch', icon: 'git-branch', text: branch, mono: true, title: branch,
      tag: row.workspaceKind === 'WT' ? t('워크트리') : undefined,
      diff: hasLines ? { add: c!.add, del: c!.del } : undefined,
      // 줄 수를 모르면 「파일 3」(diffLabel) — 줄 수가 있으면 위 diff가 이 값을 대신한다.
      extra: hasLines ? undefined : diffLabel(row) || undefined,
    });
  }
  if (host) {
    const ms = host.latencyMs != null && host.latencyMs > 0 ? ` · ${host.latencyMs}ms` : '';
    rows.push({
      key: 'host', icon: host.online ? 'server' : 'wifi-off', text: host.label, title: host.label,
      extra: host.online ? t('응답 중') + ms : reasonLabel(host.reason),
    });
  }
  return rows;
}

function metaRows(row: SessionRow, shareable: boolean): HcRow[] {
  const rows: HcRow[] = [];
  const screens = [
    row.otherScreens > 0 ? t('다른 화면 {n}', { n: row.otherScreens }) : '',
    row.otherClients > 0 ? t('터미널 {n}', { n: row.otherClients }) : '',
  ].filter(Boolean).join(' · ');
  if (screens) rows.push({ key: 'screens', icon: 'monitor-smartphone', text: screens });
  if (row.share) {
    rows.push({ key: 'share', icon: row.share.mode === 'readwrite' ? 'share-write' : 'share-read', text: shareSentence(row.share) });
  }
  const origin = normalizeOrigin(row.origin);
  if (origin !== 'local') rows.push({ key: 'origin', icon: ORIGIN_ICON[origin], text: originLabel(origin, row.originHost) });
  if (row.unseen && normalizeState(row.status) !== 'idle') rows.push({ key: 'unseen', glyph: 'unseen', text: t('아직 확인 안 함') });
  if (!row.share && shareable) rows.push({ key: 'unshared', icon: 'lock', text: t('공유 안 함'), muted: true });
  return rows;
}

const lineCount = (sections: HcSection[]) => sections.reduce((n, s) => n + s.rows.length, 0);

/** 카드 내용. 자르는 순서와 규칙은 위 머리말. 반환한 구역에는 빈 구역이 없다. */
export function buildHoverCard(row: SessionRow, opts: HcOptions = {}): HcModel {
  const now = opts.now ?? Date.now();
  const max = opts.maxLines ?? BODY_MAX_LINES;
  const state = normalizeState(row.status) as HcState;
  const kw = stateKeyword(row);
  const note = state === 'waiting' ? KIND_LABEL[normalizeKind(row.kind)] : kw.badge;

  const activity: HcRow[] = state === 'working' && opts.tool
    ? [{ key: 'tool', icon: 'terminal', text: opts.tool, mono: true, title: t('도구: {tool}', { tool: opts.tool }) }]
    : [];
  const location = locationRows(row, opts.host);
  const meta = metaRows(row, !!opts.shareable);
  // 위임 하위 세션이면 부모와 맡은 일 — 서브에이전트 구역에 같이 둔다.
  const lin = row.lineage;
  const delegated: HcRow[] = [];
  if (lin && lin.state !== 'orphaned' && lin.parent) {
    delegated.push({ key: 'parent', icon: 'corner-up-left', text: t('부모: {name}', { name: lin.parent }), extra: lineageLabel(lin) });
    if (lin.task) delegated.push({ key: 'task', icon: 'file-text', text: lin.task, muted: true, title: lin.task });
  }

  let metaKeep = meta.length, kidsCap = KIDS_MAX, nativeCap = NATIVE_MAX;
  const assemble = (): HcSection[] => {
    const subagents = [...nativeRows(row, now, nativeCap), ...kidRows(opts.kids || [], kidsCap), ...delegated];
    const all: HcSection[] = [
      { id: 'activity', rows: activity }, { id: 'location', rows: location },
      { id: 'subagents', rows: subagents }, { id: 'meta', rows: meta.slice(0, metaKeep) },
    ];
    return all.filter((s) => s.rows.length > 0);
  };
  let sections = assemble();
  // 넘치면 메타 → 하위 세션 줄 → 내부 에이전트 줄 순으로(위치는 안 자른다). 더 못 줄이면 CSS max-height가 마지막 안전장치다.
  while (lineCount(sections) > max) {
    if (metaKeep > 0) metaKeep--;
    else if (kidsCap > 1) kidsCap--;
    else if (nativeCap > 1) nativeCap--;
    else break;
    sections = assemble();
  }

  return {
    name: row.name, agent: row.agent || null, state, stateText: STATE_LABEL[state], note: note || null,
    since: formatRelativeTime(row.since, now), sections,
  };
}
