// 파일 내용 검색(파일 개편 단계 6)의 순수 로직 — 요청 주소, 결과 묶기, 강조 조각, 상태 문구.
// 팔레트 `?` 모드(Palette.tsx)와 사이드바 「파일 › 내용 검색」(files/mount.tsx)이 같은 화면(GrepView.tsx)을 쓰고,
// 그 화면이 이 함수들을 쓴다. 상태 모듈을 import하지 않는 순수 함수라 node:test에서 값만 검증한다
// (palette-data.ts·rail-data.ts와 같은 이유). 서버 계약은 API.md `GET /api/fs/grep`.
import { t } from '../core/i18n.js';

export interface GrepOptions {
  regex: boolean;
  case: boolean;
  word: boolean;
  /** 쉼표로 나눈 포함 glob(`frontend/**, docs/**`). */
  include: string;
  /** 쉼표로 나눈 제외 glob(`**\/*.test.*`). */
  exclude: string;
}

export const DEFAULT_OPTIONS: GrepOptions = { regex: false, case: false, word: false, include: '', exclude: '' };

export interface GrepHit {
  path: string;
  rel: string;
  line: number;
  col: number;
  text: string;
  ranges: [number, number][];
  cut?: number;
}

export interface GrepResponse {
  root?: string | null;
  roots?: string[];
  results: GrepHit[];
  files?: number;
  total?: number | null;
  searched?: number;
  skipped?: Partial<Record<'large' | 'binary' | 'excluded' | 'denied' | 'unreadable', number>>;
  limit?: 'results' | 'bytes' | 'output' | 'files' | null;
  truncated?: boolean;
  timeout?: boolean;
  elapsed_ms?: number;
}

export interface GrepGroup {
  path: string;
  rel: string;
  name: string;
  dir: string;
  hits: GrepHit[];
  /** 이 묶음의 첫 줄이 평탄 목록(`flat`)에서 몇 번째인가 — 키보드 선택과 묶음을 잇는다. */
  start: number;
}

/** 쉼표·줄바꿈으로 나눈다. `{a,b}` 안의 쉼표는 나누지 않는다(서버 `fs_grep.split_globs`와 같은 규칙). */
export function splitGlobs(raw: string): string[] {
  const out: string[] = [];
  let cur = '';
  let depth = 0;
  for (const ch of raw || '') {
    if (ch === '{') depth += 1;
    else if (ch === '}' && depth) depth -= 1;
    if ((ch === ',' || ch === '\n') && !depth) { out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}

/** 포함·제외 입력 → 서버 `glob` 값(제외는 `!`를 붙인다). 제외 칸에 이미 `!`를 썼으면 한 번만 붙인다. */
export function globParam(include: string, exclude: string): string {
  const inc = splitGlobs(include).filter((g) => g !== '!');
  const exc = splitGlobs(exclude).map((g) => g.replace(/^!+\s*/, '')).filter(Boolean).map((g) => `!${g}`);
  return [...inc, ...exc].join(',');
}

/** `GET /api/fs/grep` 주소. `path`가 비면 서버가 시작 루트 전부를 찾는다. */
export function grepUrl(q: string, opts: GrepOptions, path = '', sid = ''): string {
  const p = new URLSearchParams();
  p.set('q', q);
  if (path) p.set('path', path);
  p.set('mode', opts.regex ? 'regex' : 'literal');
  if (opts.case) p.set('case', '1');
  if (opts.word) p.set('word', '1');
  const glob = globParam(opts.include, opts.exclude);
  if (glob) p.set('glob', glob);
  if (sid) p.set('sid', sid);
  return `/api/fs/grep?${p.toString()}`;
}

/** 결과(서버는 파일·줄 순으로 준다)를 파일별로 묶는다. 같은 파일이 떨어져 와도 처음 나온 자리에 모은다. */
export function groupByFile(results: GrepHit[]): { groups: GrepGroup[]; flat: GrepHit[] } {
  const byPath = new Map<string, GrepGroup>();
  const groups: GrepGroup[] = [];
  for (const hit of results || []) {
    let g = byPath.get(hit.path);
    if (!g) {
      const rel = hit.rel || hit.path;
      const cut = rel.lastIndexOf('/');
      g = { path: hit.path, rel, name: cut < 0 ? rel : rel.slice(cut + 1), dir: cut < 0 ? '' : rel.slice(0, cut + 1), hits: [], start: 0 };
      byPath.set(hit.path, g);
      groups.push(g);
    }
    g.hits.push(hit);
  }
  const flat: GrepHit[] = [];
  for (const g of groups) {
    g.start = flat.length;
    for (const h of g.hits) flat.push(h);
  }
  return { groups, flat };
}

export interface Segment { text: string; hit: boolean; }

/** 줄 미리보기를 일치·비일치 조각으로. 범위는 UTF-16 [시작, 끝)이라 JS 문자열 인덱스와 같다. 겹치거나 벗어난 범위는 정리한다.
 * 앞쪽 공백은 `trimStart`면 걷어 내고 범위를 그만큼 당긴다(사이드바처럼 좁은 칸). */
export function segments(text: string, ranges: [number, number][], trimStart = false): Segment[] {
  let s = text || '';
  let shift = 0;
  if (trimStart) {
    const m = /^\s+/.exec(s);
    if (m) { shift = m[0].length; s = s.slice(shift); }
  }
  const rs = (ranges || [])
    .map(([a, b]) => [Math.max(0, a - shift), Math.min(s.length, b - shift)] as [number, number])
    .filter(([a, b]) => b > a)
    .sort((x, y) => x[0] - y[0]);
  const out: Segment[] = [];
  let pos = 0;
  for (const [a0, b] of rs) {
    const a = Math.max(a0, pos);
    if (b <= a) continue;
    if (a > pos) out.push({ text: s.slice(pos, a), hit: false });
    out.push({ text: s.slice(a, b), hit: true });
    pos = b;
  }
  if (pos < s.length || !out.length) out.push({ text: s.slice(pos), hit: false });
  return out;
}

/** 열 위치(뷰어에 넘길 1부터 센 열, UTF-16). 서버 `col`이 이미 그 값이다 — 없으면 첫 범위 + 잘린 앞부분. */
export function hitColumn(hit: GrepHit): number {
  if (hit.col) return hit.col;
  const first = hit.ranges?.[0]?.[0] ?? 0;
  return (hit.cut || 0) + first + 1;
}

export function fmtSeconds(ms: number): string {
  return t('{n}초', { n: (Math.max(0, ms) / 1000).toFixed(1) });
}

/** 결과 위 진행 줄의 끝난 뒤 문구 — 「결과 8 · 파일 4 · 0.8초」. 상한·기한이면 그 사실을 먼저 말한다. */
export function summary(d: GrepResponse): { text: string; tone: 'ok' | 'warn' } {
  const n = d.results?.length || 0;
  const files = d.files ?? groupByFile(d.results || []).groups.length;
  const secs = fmtSeconds(d.elapsed_ms || 0);
  if (d.timeout) {
    return { tone: 'warn', text: t('10초 안에 끝나지 않아 멈췄습니다 · 파일 {searched} / {total}개 · 결과 {n}', { searched: d.searched ?? 0, total: d.total ?? 0, n }) };
  }
  if (d.limit === 'results' || d.limit === 'output') {
    return { tone: 'warn', text: t('결과 {n}개에서 멈췄습니다 · {secs}', { n, secs }) };
  }
  if (d.limit === 'bytes' || d.limit === 'files') {
    return { tone: 'warn', text: t('읽기 한도에 닿아 멈췄습니다 · 파일 {searched} / {total}개 · 결과 {n}', { searched: d.searched ?? 0, total: d.total ?? 0, n }) };
  }
  if (!n) return { tone: 'ok', text: t('파일 {total}개 검색 · {secs}', { total: d.total ?? 0, secs }) };
  return { tone: 'ok', text: t('결과 {n} · 파일 {files} · {secs}', { n, files, secs }) };
}

/** 「건너뜀: 2 MB 넘는 파일 3 · 바이너리 12 · 제외 패턴 41」. 건너뛴 것이 없으면 빈 문자열. 거부 경로 수는 말하지 않는다(이름조차 알리지 않는 것과 같은 이유). */
export function skippedLine(d: GrepResponse): string {
  const s = d.skipped || {};
  const parts: string[] = [];
  if (s.large) parts.push(t('2 MB 넘는 파일 {n}', { n: s.large }));
  if (s.binary) parts.push(t('바이너리 {n}', { n: s.binary }));
  if (s.excluded) parts.push(t('제외 패턴 {n}', { n: s.excluded }));
  if (s.unreadable) parts.push(t('읽을 수 없음 {n}', { n: s.unreadable }));
  return parts.length ? t('건너뜀: {list}', { list: parts.join(' · ') }) : '';
}

/** 탭마다 다른 검색 자리 키(서버 `sid`). 같은 탭의 새 검색이 이전 검색을 취소하는 단위다. */
export function newSid(): string {
  const rnd = Math.random().toString(36).slice(2, 10);
  return `g${Date.now().toString(36)}${rnd}`.slice(0, 32);
}
