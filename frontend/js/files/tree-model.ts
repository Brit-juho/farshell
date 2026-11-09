// 파일 트리의 순수 로직 — DOM도 fetch도 모른다(파일 개편 단계 5, 계획 2.4·3.5·4.4·4.5).
//
// 트리는 **평탄화한 행 목록**이다. 펼친 폴더의 목록(서버 `/api/fs/tree` 한 페이지 이상)을 깊이 우선으로 펴서
// 행 배열 하나를 만들고, 화면은 그 배열의 보이는 구간만 DOM으로 그린다(가상 스크롤, `windowRange`).
// 예전 트리(panels/viewer/tree.js)는 행마다 DOM과 리스너를 만들고, 변경 알림이 오면 펼친 폴더 전부를 다시 받아
// 트리 전체를 새로 그렸다 — 폴더 100개를 펼친 상태에서 루트 변경 1번에 목록 101회, 폰 조건 656ms.
//
// 상태의 키는 `(hostId, path)`다(계획 3.5) — 같은 절대경로가 다른 맥에도 있을 수 있다. `keyOf`가 그 키를 만든다.
// 행 객체는 키가 같고 내용이 같으면 **같은 객체를 다시 쓴다**(`reuseRows`). Solid의 `<For>`가 객체 동일성으로
// DOM을 재사용하므로, 폴더 하나가 바뀌면 그 폴더의 바뀐 행만 다시 그려진다.

export interface Entry {
  name: string;
  dir: boolean;
  size: number;
  /** 파일만 — 수정 시각(ms). 폴더는 서버가 stat 하지 않는다. */
  mtime?: number;
}

/** `/api/fs/tree` 응답(필요한 필드만). */
export interface Listing {
  path?: string;
  entries: (Entry & { children?: Listing })[];
  next?: string | null;
  total?: number;
  capped?: boolean;
  truncated?: boolean;
}

/** 폴더 하나의 받아 둔 상태. */
export interface DirState {
  entries: Entry[];
  /** 다음 페이지 커서. null이면 다 받았다. */
  next: string | null;
  /** 서버가 센 보이는 항목 수(스캔 상한까지). */
  total: number;
  /** 서버 스캔 상한(5만)에 닿았다. */
  capped: boolean;
  /** 받은 페이지 수 — 변경 알림 때 같은 수만큼 다시 받는다. */
  pages: number;
  loading: boolean;
  /** 다음 페이지를 받는 중. */
  loadingMore: boolean;
  error: string | null;
}

export type RowKind = 'dir' | 'file' | 'more' | 'loading' | 'error' | 'empty' | 'capped';

export interface Row {
  /** `(host, path)` 키. 안내 행은 부모 키 + 접미사. */
  key: string;
  kind: RowKind;
  /** 항목의 절대경로. 안내 행은 부모 폴더 경로. */
  path: string;
  name: string;
  depth: number;
  open: boolean;
  size: number;
  /** 바뀜 표시 — 파일은 'dot', 안쪽에 바뀐 것이 있는 폴더는 'ring'. */
  changed: '' | 'dot' | 'ring';
  /** 필터에 맞은 이름 구간 [시작, 끝). */
  match: [number, number] | null;
  /** aria-posinset / aria-setsize(항목 행만). */
  pos: number;
  setSize: number;
  /** 「더 불러오기」·오류 행의 수치·문구. */
  loaded: number;
  total: number;
  detail: string;
}

export const LOCAL_HOST = 'local';

export function keyOf(host: string, path: string): string {
  return `${host}\n${path}`;
}

export function joinPath(dir: string, name: string): string {
  return (dir === '/' ? '' : dir.replace(/\/+$/, '')) + '/' + name;
}

export function parentOf(path: string): string {
  const p = path.replace(/\/+$/, '');
  const i = p.lastIndexOf('/');
  return i <= 0 ? '/' : p.slice(0, i);
}

export function isUnder(path: string, dir: string): boolean {
  const base = dir === '/' ? '/' : dir.replace(/\/+$/, '') + '/';
  return path !== dir && path.startsWith(base);
}

/** 감시 알림의 루트 기준 상대 디렉토리("" = 루트) → 절대경로. 위로 벗어나는 값은 버린다. */
export function relToAbs(root: string, rel: string): string | null {
  if (typeof rel !== 'string') return null;
  if (!rel) return root;
  const parts = rel.split('/');
  if (parts.some((p) => !p || p === '.' || p === '..')) return null;
  return joinPath(root, parts.join('/'));
}

// OS가 만드는 메타데이터 파일만 목록에서 뺀다(예전 트리와 같다 — 사용자가 만든 dot 파일은 남긴다).
const OS_NOISE = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini', '.localized']);
export function denoise(entries: Entry[]): Entry[] {
  return entries.filter((e) => !OS_NOISE.has(e.name));
}

/** 서버 목록 한 페이지 → 우리 모양(children·잡음 제거). */
export function toEntries(listing: Listing): Entry[] {
  return denoise((listing.entries || []).map((e) => {
    const out: Entry = { name: String(e.name), dir: !!e.dir, size: Number(e.size) || 0 };
    if (typeof e.mtime === 'number') out.mtime = e.mtime;
    return out;
  }));
}

export function emptyDir(): DirState {
  return { entries: [], next: null, total: 0, capped: false, pages: 0, loading: false, loadingMore: false, error: null };
}

/** 서버 첫 페이지 → DirState(이전 상태의 다른 필드는 버린다). */
export function dirFromListing(listing: Listing): DirState {
  const entries = toEntries(listing);
  return {
    entries,
    next: listing.next || null,
    total: typeof listing.total === 'number' ? listing.total : entries.length,
    capped: !!listing.capped,
    pages: 1,
    loading: false,
    loadingMore: false,
    error: null,
  };
}

/** 다음 페이지를 이어 붙인다. 커서 경합(폴더가 그 사이 바뀜)으로 겹친 이름은 한 번만 둔다. */
export function appendPage(d: DirState, listing: Listing): DirState {
  const seen = new Set(d.entries.map((e) => e.name));
  const more = toEntries(listing).filter((e) => !seen.has(e.name));
  return {
    ...d,
    entries: d.entries.concat(more),
    next: listing.next || null,
    total: typeof listing.total === 'number' ? listing.total : d.total,
    capped: !!listing.capped,
    pages: d.pages + 1,
    loadingMore: false,
    error: null,
  };
}

/**
 * 미리 받은 하위 목록(`children`, depth 1·2)을 경로 → 첫 페이지로 펼친다. 결과에는 `listing` 자신도 들어 있다.
 */
export function flattenPrefetch(path: string, listing: Listing, out: Map<string, Listing> = new Map()): Map<string, Listing> {
  out.set(path, listing);
  for (const e of listing.entries || []) {
    if (e.dir && e.children) flattenPrefetch(joinPath(path, e.name), e.children, out);
  }
  return out;
}

/** 같은 폴더의 옛 목록과 새 목록을 비교해 **바뀐 이름**(새로 생김 · 크기 · 수정 시각)을 돌려준다. 지워진 것은 빼다. */
export function changedNames(prev: Entry[], next: Entry[]): string[] {
  const old = new Map(prev.map((e) => [e.name, e]));
  const out: string[] = [];
  for (const e of next) {
    const o = old.get(e.name);
    if (!o) { out.push(e.name); continue; }
    if (o.dir !== e.dir) { out.push(e.name); continue; }
    if (!e.dir && (o.size !== e.size || (o.mtime ?? null) !== (e.mtime ?? null))) out.push(e.name);
  }
  return out;
}

/** 새 목록에서 사라진 하위 폴더 이름(그 밑에 받아 둔 상태를 치울 때). */
export function removedDirs(prev: Entry[], next: Entry[]): string[] {
  const now = new Set(next.filter((e) => e.dir).map((e) => e.name));
  return prev.filter((e) => e.dir && !now.has(e.name)).map((e) => e.name);
}

// --- 이름 필터 ------------------------------------------------------------------------------

export type NameMatcher = (name: string) => [number, number] | null;

/**
 * 이름 필터. 글자만 있으면 대소문자 무시 부분 일치(맞은 구간을 강조한다). `*`·`?`가 있으면 이름 전체에 대한 glob
 * (`*.tsx`, `test_*`). 빈 문자열이면 null — 필터를 끈다.
 */
export function compileFilter(q: string): NameMatcher | null {
  const s = (q || '').trim();
  if (!s) return null;
  if (/[*?]/.test(s)) {
    const rx = new RegExp('^' + s.split('').map((c) => (c === '*' ? '.*' : c === '?' ? '.'
      : c.replace(/[.+^${}()|[\]\\]/g, '\\$&'))).join('') + '$', 'i');
    return (name) => (rx.test(name) ? [0, name.length] : null);
  }
  const needle = s.toLowerCase();
  return (name) => {
    const i = name.toLowerCase().indexOf(needle);
    return i < 0 ? null : [i, i + needle.length];
  };
}

// --- 평탄화 ---------------------------------------------------------------------------------

export interface FlattenInput {
  host: string;
  root: string;
  dirs: Map<string, DirState>;
  /** 펼친 폴더의 키(`keyOf`). */
  expanded: Set<string>;
  /** 마지막으로 연 뒤 바뀐 **파일**의 키. */
  changed: Set<string>;
  /** 안이 바뀌었지만 아직 받지 않은 폴더의 키(접힌 채 바뀜). */
  changedDirs: Set<string>;
  filter?: NameMatcher | null;
  /**
   * 폴더별 행 조각 캐시(필터가 없을 때만 쓴다). 넘기면 바뀌지 않은 폴더의 조각과 행 객체를 그대로 다시 쓴다 —
   * 폴더 하나를 펼치거나 다시 받으면 그 폴더와 조상의 조각만 새로 만든다.
   */
  cache?: FlattenCache;
  /** 바뀜 표시(`changed`·`changedDirs`)가 바뀔 때마다 오르는 수. 캐시가 이 값으로 표시를 다시 계산할지 정한다. */
  marks?: number;
  /**
   * 폴더 키 → 그 폴더의 **바로 아래** 폴더 펼침이 바뀔 때마다 오르는 수. 주면 캐시가 그대로인 폴더의 항목을 다시
   * 훑지 않는다(펼친 폴더 수만큼만 일한다). 없으면 매번 항목을 훑어 펼침을 확인한다.
   */
  expandVer?: (dirKey: string) => number;
}

/** `flatten`의 폴더별 조각 캐시. 화면 하나가 하나를 들고 있는다. */
export class FlattenCache {
  segs = new Map<string, Seg>();
  marks = -1;
  root = '';
}

interface Seg {
  state: DirState;
  depth: number;
  /** 만들 때의 `expandVer` 값(없으면 -1). */
  ever: number;
  /** 펼친 하위 폴더의 경로(순서대로). */
  kidPaths: string[];
  /** 펼친 하위 폴더 조각(순서대로) — 이것들이 모두 그대로면 이 조각도 그대로다. */
  kids: Row[][];
  /** 이 폴더 자신의 행(하위 조각 제외) — 다시 만들 때 같은 행 객체를 찾는 데 쓴다. */
  own: Row[];
  rows: Row[];
}

const NO_ROWS: Row[] = [];

export interface Flat {
  rows: Row[];
  /** 필터에 맞은 항목 수(필터가 없으면 -1). */
  matches: number;
}

function changedAncestors(input: FlattenInput): Set<string> {
  // 바뀐 파일·폴더의 모든 조상 폴더 키 — 「안쪽에 바뀐 것이 있다」 빈 원을 그릴 자리.
  const out = new Set<string>();
  const prefix = input.host + '\n';
  const add = (k: string, self: boolean) => {
    if (!k.startsWith(prefix)) return;
    let p = k.slice(prefix.length);
    if (self) out.add(k);
    while (p !== '/' && p !== input.root && isUnder(p, input.root)) {
      p = parentOf(p);
      out.add(keyOf(input.host, p));
    }
  };
  for (const k of input.changed) add(k, false);
  for (const k of input.changedDirs) add(k, true);
  return out;
}

function makeRow(partial: Partial<Row> & Pick<Row, 'key' | 'kind' | 'path' | 'name' | 'depth'>): Row {
  return {
    open: false, size: 0, changed: '', match: null, pos: 0, setSize: 0, loaded: 0, total: 0, detail: '',
    ...partial,
  };
}

/**
 * 받아 둔 상태 → 보일 행 목록.
 *
 * 필터가 켜져 있으면 **받아 둔 폴더 전부**를 내려가 이름이 맞는 항목과 그 조상만 남기고, 조상은 펼친 것처럼 그린다
 * (필터는 받아 둔 이름만 본다 — 디스크 전체 검색은 팔레트 `/`가 맡는다). 안내 행(불러오는 중·더 불러오기 등)은
 * 필터 중에는 그리지 않는다.
 */
export function flatten(input: FlattenInput): Flat {
  if (input.cache && !input.filter) return { rows: flattenCached(input, input.cache), matches: -1 };
  const { host, root, dirs, expanded, changed, filter } = input;
  const rows: Row[] = [];
  const ring = changedAncestors(input);
  let matches = filter ? 0 : -1;

  // 필터: 폴더 키 → 그 아래에 맞는 것이 있는가(메모).
  const hasMatch = new Map<string, boolean>();
  const subtreeMatches = (path: string): boolean => {
    const k = keyOf(host, path);
    const memo = hasMatch.get(k);
    if (memo !== undefined) return memo;
    hasMatch.set(k, false);   // 순환 방어(심링크는 서버가 따라가지 않지만)
    const d = dirs.get(k);
    let any = false;
    if (d && filter) {
      for (const e of d.entries) {
        if (filter(e.name) || (e.dir && subtreeMatches(joinPath(path, e.name)))) { any = true; break; }
      }
    }
    hasMatch.set(k, any);
    return any;
  };

  const walk = (path: string, depth: number) => {
    const dk = keyOf(host, path);
    const d = dirs.get(dk);
    if (!d) return;
    if (!filter) {
      if (d.loading && !d.entries.length) {
        rows.push(makeRow({ key: dk + '\n#loading', kind: 'loading', path, name: '', depth }));
        return;
      }
      if (d.error && !d.entries.length) {
        rows.push(makeRow({ key: dk + '\n#error', kind: 'error', path, name: '', depth, detail: d.error }));
        return;
      }
      if (!d.entries.length) {
        rows.push(makeRow({ key: dk + '\n#empty', kind: 'empty', path, name: '', depth }));
        return;
      }
    }
    const setSize = Math.max(d.total, d.entries.length);
    d.entries.forEach((e, i) => {
      const p = joinPath(path, e.name);
      const k = keyOf(host, p);
      let m: [number, number] | null = null;
      let showChildren = false;
      if (filter) {
        m = filter(e.name);
        const below = e.dir && subtreeMatches(p);
        if (!m && !below) return;
        if (m) matches += 1;
        showChildren = below;
      } else {
        showChildren = e.dir && expanded.has(k);
      }
      rows.push(makeRow({
        key: k, kind: e.dir ? 'dir' : 'file', path: p, name: e.name, depth,
        open: e.dir && (filter ? showChildren : expanded.has(k)),
        size: e.size,
        // 펼친 폴더도 빈 원을 그린다(시안 F01 — frontend · js · shell 모두 원, 바뀐 파일만 점).
        changed: e.dir ? (ring.has(k) ? 'ring' : '') : (changed.has(k) ? 'dot' : ''),
        match: m, pos: i + 1, setSize,
      }));
      if (showChildren) walk(p, depth + 1);
    });
    if (filter) return;
    if (d.next) {
      rows.push(makeRow({
        key: dk + '\n#more', kind: 'more', path, name: '', depth,
        loaded: d.entries.length, total: d.total, open: d.loadingMore,
      }));
    } else if (d.capped) {
      rows.push(makeRow({ key: dk + '\n#capped', kind: 'capped', path, name: '', depth, loaded: d.entries.length }));
    }
    if (d.error && d.entries.length) {
      rows.push(makeRow({ key: dk + '\n#error', kind: 'error', path, name: '', depth, detail: d.error }));
    }
  };
  walk(root, 0);
  return { rows, matches };
}

function noteRows(dk: string, path: string, depth: number, d: DirState): Row[] {
  if (d.loading && !d.entries.length) return [makeRow({ key: dk + '\n#loading', kind: 'loading', path, name: '', depth })];
  if (d.error && !d.entries.length) return [makeRow({ key: dk + '\n#error', kind: 'error', path, name: '', depth, detail: d.error })];
  if (!d.entries.length) return [makeRow({ key: dk + '\n#empty', kind: 'empty', path, name: '', depth })];
  const out: Row[] = [];
  if (d.next) {
    out.push(makeRow({ key: dk + '\n#more', kind: 'more', path, name: '', depth, loaded: d.entries.length, total: d.total, open: d.loadingMore }));
  } else if (d.capped) {
    out.push(makeRow({ key: dk + '\n#capped', kind: 'capped', path, name: '', depth, loaded: d.entries.length }));
  }
  if (d.error) out.push(makeRow({ key: dk + '\n#error', kind: 'error', path, name: '', depth, detail: d.error }));
  return out;
}

/** 필터 없는 평탄화 — 폴더별 조각을 캐시해 바뀐 폴더와 그 조상만 다시 만든다(`flatten`과 같은 결과). */
function flattenCached(input: FlattenInput, cache: FlattenCache): Row[] {
  const { host, root, dirs, expanded, changed } = input;
  const marks = input.marks ?? -2;
  if (cache.marks !== marks || cache.root !== root || marks === -2) {
    cache.segs.clear();
    cache.marks = marks;
    cache.root = root;
  }
  const ring = changedAncestors(input);
  const live = new Set<string>();

  const segment = (path: string, depth: number): Row[] => {
    const dk = keyOf(host, path);
    const d = dirs.get(dk);
    if (!d) return NO_ROWS;
    live.add(dk);
    const c = cache.segs.get(dk);
    const ever = input.expandVer ? input.expandVer(dk) : -1;
    // 빠른 길: 이 폴더의 목록과 바로 아래 펼침이 그대로면 항목을 훑지 않고, 펼친 하위 폴더만 내려가 본다.
    if (c && ever >= 0 && c.ever === ever && c.state === d && c.depth === depth) {
      let same = true;
      for (let i = 0; i < c.kidPaths.length; i += 1) {
        if (segment(c.kidPaths[i], depth + 1) !== c.kids[i]) same = false;
      }
      if (same) return c.rows;
    }
    const kids: Row[][] = [];
    const kidList: string[] = [];
    const kidPaths: (string | null)[] = [];
    if (d.entries.length) {
      for (const e of d.entries) {
        if (!e.dir) { kidPaths.push(null); continue; }
        const p = joinPath(path, e.name);
        if (expanded.has(keyOf(host, p))) { kids.push(segment(p, depth + 1)); kidPaths.push(p); kidList.push(p); }
        else kidPaths.push(null);
      }
    }
    if (c && c.state === d && c.depth === depth && c.kids.length === kids.length && c.kids.every((k, i) => k === kids[i])) {
      c.ever = ever;
      c.kidPaths = kidList;
      return c.rows;
    }
    const prevOwn = new Map<string, Row>();
    if (c) for (const r of c.own) prevOwn.set(r.key, r);
    const keep = (r: Row): Row => {
      const o = prevOwn.get(r.key);
      return o && sameRow(o, r) ? o : r;
    };
    const own: Row[] = [];
    const rows: Row[] = [];
    if (d.entries.length) {
      const setSize = Math.max(d.total, d.entries.length);
      let ki = 0;
      d.entries.forEach((e, i) => {
        const p = joinPath(path, e.name);
        const k = keyOf(host, p);
        const open = kidPaths[i] != null;
        const r = keep(makeRow({
          key: k, kind: e.dir ? 'dir' : 'file', path: p, name: e.name, depth, open, size: e.size,
          changed: e.dir ? (ring.has(k) ? 'ring' : '') : (changed.has(k) ? 'dot' : ''),
          pos: i + 1, setSize,
        }));
        own.push(r);
        rows.push(r);
        if (open) { const kr = kids[ki++]; for (let j = 0; j < kr.length; j += 1) rows.push(kr[j]); }
      });
    }
    for (const n of noteRows(dk, path, depth, d)) { const r = keep(n); own.push(r); rows.push(r); }
    cache.segs.set(dk, { state: d, depth, ever, kidPaths: kidList, kids, own, rows });
    return rows;
  };
  const out = segment(root, 0);
  // 더는 보이지 않는 폴더의 조각은 버린다(접은 폴더를 다시 펴면 새로 만든다 — 행 객체는 다시 쓰지 못한다).
  if (cache.segs.size > live.size) for (const k of Array.from(cache.segs.keys())) if (!live.has(k)) cache.segs.delete(k);
  return out;
}

const ROW_FIELDS: (keyof Row)[] = ['kind', 'path', 'name', 'depth', 'open', 'size', 'changed', 'pos', 'setSize', 'loaded', 'total', 'detail'];

function sameRow(a: Row, b: Row): boolean {
  for (const f of ROW_FIELDS) if (a[f] !== b[f]) return false;
  const am = a.match, bm = b.match;
  if (am === bm) return true;
  return !!am && !!bm && am[0] === bm[0] && am[1] === bm[1];
}

/**
 * 경로 키 비교 갱신 — 새 행 중 키와 내용이 이전과 같은 것은 **이전 객체**로 바꿔 돌려준다. 돌려준 맵을 다음
 * 호출의 `prev`로 넘긴다. 바뀐 행 수도 돌려준다(측정·시험용).
 */
export function reuseRows(prev: Map<string, Row>, rows: Row[]): { rows: Row[]; byKey: Map<string, Row>; changed: number } {
  const byKey = new Map<string, Row>();
  let changedCount = 0;
  const out = rows.map((r) => {
    const old = prev.get(r.key);
    const keep = old === r || (old && sameRow(old, r)) ? old! : r;
    if (keep !== old) changedCount += 1;
    byKey.set(r.key, keep);
    return keep;
  });
  return { rows: out, byKey, changed: changedCount };
}

// --- 가상 스크롤 ----------------------------------------------------------------------------

/** 이만큼은 화면 위아래로 더 그린다(빠르게 스크롤할 때 빈 칸이 비치지 않게). */
export const OVERSCAN = 10;

/** 보이는 행 구간 [start, end). */
export function windowRange(scrollTop: number, viewport: number, rowH: number, count: number, overscan = OVERSCAN):
  { start: number; end: number } {
  if (count <= 0 || rowH <= 0) return { start: 0, end: 0 };
  const first = Math.floor(Math.max(0, scrollTop) / rowH);
  const visible = Math.ceil(Math.max(0, viewport) / rowH) + 1;
  const start = Math.max(0, Math.min(count - 1, first) - overscan);
  const end = Math.min(count, first + visible + overscan);
  return { start, end: Math.max(start, end) };
}

/** 행 `index`가 보이도록 할 새 scrollTop(이미 보이면 그대로). */
export function scrollToReveal(index: number, scrollTop: number, viewport: number, rowH: number): number {
  const top = index * rowH;
  const bottom = top + rowH;
  if (top < scrollTop) return top;
  if (bottom > scrollTop + viewport) return Math.max(0, bottom - viewport);
  return scrollTop;
}

/** 행 높이 — 폰(터치) 44, 촘촘 22, 기본 28(시안 F01 · 사용자 선택 4 「가」). */
export function rowHeight(dense: boolean, mobile: boolean): number {
  if (mobile) return 44;
  return dense ? 22 : 28;
}

// --- 키보드 ---------------------------------------------------------------------------------

export type TreeAction =
  | { type: 'focus'; index: number }
  | { type: 'expand'; index: number }
  | { type: 'collapse'; index: number }
  | { type: 'activate'; index: number }
  | { type: 'insert'; index: number }
  | { type: 'menu'; index: number }
  | { type: 'filter' }
  | { type: 'none' };

/** 초점을 받을 수 있는 행(안내 행 중 「더 불러오기」만 받는다). */
export function focusable(r: Row | undefined): boolean {
  return !!r && (r.kind === 'dir' || r.kind === 'file' || r.kind === 'more');
}

function stepFocusable(rows: Row[], from: number, dir: 1 | -1): number {
  for (let i = from + dir; i >= 0 && i < rows.length; i += dir) if (focusable(rows[i])) return i;
  return from;
}

export interface KeyInput {
  key: string;
  altKey?: boolean;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
}

/**
 * WAI-ARIA 트리 키보드 규칙 — ↑↓ 이동, → 펼치기 / 첫 자식, ← 접기 / 부모, Home·End, Enter·Space 열기,
 * ⌥Enter 경로 삽입, ⇧F10·메뉴 키 행 메뉴, `/` 필터. 글자 입력(이름 앞부분으로 이동)은 `typeahead`가 따로 본다.
 */
export function treeKey(rows: Row[], index: number, ev: KeyInput): TreeAction {
  if (!rows.length) return ev.key === '/' ? { type: 'filter' } : { type: 'none' };
  const i = index < 0 || index >= rows.length ? -1 : index;
  const cur = i >= 0 ? rows[i] : undefined;
  const first = () => stepFocusable(rows, -1, 1);
  switch (ev.key) {
    case 'ArrowDown': return { type: 'focus', index: i < 0 ? first() : stepFocusable(rows, i, 1) };
    case 'ArrowUp': return { type: 'focus', index: i < 0 ? first() : stepFocusable(rows, i, -1) };
    case 'Home': return { type: 'focus', index: first() };
    case 'End': return { type: 'focus', index: stepFocusable(rows, rows.length, -1) };
    case 'ArrowRight':
      if (!cur || cur.kind !== 'dir') return { type: 'none' };
      if (!cur.open) return { type: 'expand', index: i };
      return rows[i + 1] && rows[i + 1].depth > cur.depth && focusable(rows[i + 1]) ? { type: 'focus', index: i + 1 } : { type: 'none' };
    case 'ArrowLeft': {
      if (!cur) return { type: 'none' };
      if (cur.kind === 'dir' && cur.open) return { type: 'collapse', index: i };
      const parent = cur.kind === 'more' ? cur.path : parentOf(cur.path);
      for (let j = i - 1; j >= 0; j -= 1) if (rows[j].kind === 'dir' && rows[j].path === parent) return { type: 'focus', index: j };
      return { type: 'none' };
    }
    case 'Enter':
      if (!cur) return { type: 'none' };
      if (ev.altKey) return cur.kind === 'more' ? { type: 'none' } : { type: 'insert', index: i };
      return { type: 'activate', index: i };
    case ' ':
      return cur ? { type: 'activate', index: i } : { type: 'none' };
    case 'ContextMenu':
      return cur && cur.kind !== 'more' ? { type: 'menu', index: i } : { type: 'none' };
    case 'F10':
      return ev.shiftKey && cur && cur.kind !== 'more' ? { type: 'menu', index: i } : { type: 'none' };
    case '/':
      return { type: 'filter' };
    default:
      return { type: 'none' };
  }
}

/** 글자 입력으로 이동 — `from` 다음 행부터 돌아가며 이름이 `prefix`로 시작하는 첫 항목. 없으면 -1. */
export function typeahead(rows: Row[], from: number, prefix: string): number {
  const p = prefix.toLowerCase();
  if (!p || !rows.length) return -1;
  // 같은 글자를 반복하면 다음 일치로, 새 글자가 붙으면 지금 행부터 다시 본다.
  const startAt = p.length > 1 ? Math.max(0, from) : from + 1;
  for (let n = 0; n < rows.length; n += 1) {
    const i = (startAt + n) % rows.length;
    const r = rows[i];
    if ((r.kind === 'dir' || r.kind === 'file') && r.name.toLowerCase().startsWith(p)) return i;
  }
  return -1;
}

/** 크기 표기(예전 트리 `_fmtSize`와 같은 단위). */
export function fmtSize(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)}K`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)}M`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)}G`;
}
