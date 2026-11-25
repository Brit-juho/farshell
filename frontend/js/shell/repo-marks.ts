// 저장소 표식(2026-11) — 레일·폰 저장소 머리의 폴더 아이콘을 저장소마다 다른 아이콘·색으로 바꾼다(구분용).
//
// 정본은 **그 맥의 전역 설정** `rail.repoMarks`(core/settings.js, `/api/workspace`) — 정렬(rail.sort)과 같은 자리라
// 그 맥을 보는 모든 기기가 같은 표식을 본다. 값은 `{"marks":{"<저장소 id>":{"icon":"code","color":"blue"}}}`.
// 저장소 id는 카탈로그의 projects.id(무작위 32자 hex, 경로가 같으면 그대로 — session_catalog/db.py 형식 v4).
//
// 아이콘·색은 **정해진 목록의 이름만** 저장한다. 모르는 이름은 읽을 때 버린다(기본 폴더로 그린다) — 저장된 값이
// innerHTML·style로 흘러가지 않는다. 아이콘 path는 Lucide(ISC, lucide-static 0.460.0)의 24x24 stroke 좌표를
// 그대로 옮긴 고정 데이터다. 색은 테마마다 대비를 맞춘 터미널 색 토큰(--color-term-*)을 빌린다.
import { t } from '../core/i18n.js';
import { shellIcon } from '../ui/icons-shell.js';

export const MARKS_KEY = 'rail.repoMarks';
/** 저장소 상한(server/repo_store.py MAX_REPOS와 같은 수). 넘으면 오래 전에 정한 것부터 빠진다. */
export const MARKS_MAX = 200;

/** 고를 수 있는 아이콘. 'folder'(기본)는 icons.js에 있어 여기 path가 없다. */
const MARK_PATHS: Record<string, string> = {
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  server: '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  smartphone: '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
  cloud: '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  'book-open': '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  'flask-conical': '<path d="M10 2v7.527a2 2 0 0 1-.211.896L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.069-10.127A2 2 0 0 1 14 9.527V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>',
  briefcase: '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
  'gamepad-2': '<line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  star: '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/>',
  heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
  zap: '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
};

/** 선택판 순서. 맨 앞 'folder'가 기본(표식 없음과 같다). */
export const MARK_ICONS: string[] = ['folder', ...Object.keys(MARK_PATHS)];

/** 색 이름 → CSS 값. ''(기본)은 지금처럼 본문 색. */
export const MARK_COLORS: Record<string, string> = {
  blue: 'var(--color-term-blue)',
  cyan: 'var(--color-term-cyan)',
  green: 'var(--color-term-green)',
  amber: 'var(--color-term-amber)',
  red: 'var(--color-term-red)',
  mag: 'var(--color-term-mag)',
};
export const MARK_COLOR_NAMES: string[] = ['', ...Object.keys(MARK_COLORS)];

export interface RepoMark { icon: string; color: string }
export type RepoMarks = Record<string, RepoMark>;

// 저장소 id는 hex라 이 모양이면 충분하다. 밑줄을 빼 `__proto__`가 열쇠가 될 수 없게 한다.
const ID_RE = /^[A-Za-z0-9:.-]{1,128}$/;
const own = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);

function cleanMark(v: unknown): RepoMark | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const icon = typeof o.icon === 'string' && MARK_ICONS.includes(o.icon) ? o.icon : 'folder';
  const color = typeof o.color === 'string' && own(MARK_COLORS, o.color) ? o.color : '';
  return icon === 'folder' && !color ? null : { icon, color };
}

/** 설정 문자열 → 저장소 id별 표식. 깨진 값·모르는 이름은 버린다. */
export function parseRepoMarks(v: unknown): RepoMarks {
  const out: RepoMarks = Object.create(null);
  if (typeof v !== 'string' || !v) return out;
  let o: any;
  try { o = JSON.parse(v); } catch { return out; }
  const marks = o && typeof o === 'object' && !Array.isArray(o) ? o.marks : null;
  if (!marks || typeof marks !== 'object' || Array.isArray(marks)) return out;
  let n = 0;
  for (const id of Object.keys(marks)) {
    if (n >= MARKS_MAX) break;
    if (!ID_RE.test(id)) continue;
    const m = cleanMark(marks[id]);
    if (m) { out[id] = m; n += 1; }
  }
  return out;
}

export function encodeRepoMarks(marks: RepoMarks): string {
  const clean: Record<string, RepoMark> = Object.create(null);
  const ids = Object.keys(marks).filter((id) => ID_RE.test(id));
  // 상한을 넘으면 앞(오래 전에 정한 것)부터 뺀다 — withMark가 새로 정한 것을 끝에 붙인다.
  for (const id of ids.slice(Math.max(0, ids.length - MARKS_MAX))) {
    const m = cleanMark(marks[id]);
    if (m) clean[id] = m;
  }
  return JSON.stringify({ marks: clean });
}

/** 한 저장소의 표식을 바꾼 새 묶음. 기본(폴더·기본 색)이면 지운다. 바꾼 것은 끝으로 옮긴다. */
export function withMark(marks: RepoMarks, id: string, mark: Partial<RepoMark> | null): RepoMarks {
  const out: RepoMarks = Object.create(null);
  for (const k of Object.keys(marks)) if (k !== id) out[k] = marks[k];
  if (!ID_RE.test(id) || !mark) return out;
  const m = cleanMark({ ...(marks[id] || { icon: 'folder', color: '' }), ...mark });
  if (m) out[id] = m;
  return out;
}

export function markOf(marks: RepoMarks, id: string | null | undefined): RepoMark | null {
  return id && own(marks, id) ? marks[id] : null;
}

/** 아이콘 SVG 문자열(고정 데이터만 — 이름은 목록에서 골라 쓴다). */
export function markIconHtml(name: string, size: number, strokeWidth = 2): string {
  const body = own(MARK_PATHS, name) ? MARK_PATHS[name] : null;
  if (!body) return shellIcon('folder', size, strokeWidth);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" `
    + `fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" focusable="false">${body}</svg>`;
}

/** 머리 아이콘 칸의 style — 색이 있으면 아이콘을 그 색으로, withBg면 칸 바탕도 그 색을 옅게(레일의 22px 칸).
 * 바탕 칸이 없는 곳(폰 머리)은 withBg=false로 글자색만. */
export function markStyle(mark: RepoMark | null, withBg = true): Record<string, string> | undefined {
  const c = mark?.color && own(MARK_COLORS, mark.color) ? MARK_COLORS[mark.color] : '';
  if (!c) return undefined;
  return withBg ? { color: c, background: `color-mix(in srgb, ${c} 16%, var(--color-bg-3))` } : { color: c };
}

/** 사람이 읽는 이름(선택판 aria-label·툴팁). 언어가 바뀔 수 있어 부를 때마다 만든다. */
export function markColorLabel(name: string): string {
  const L: Record<string, string> = {
    '': t('기본 색'), blue: t('파랑'), cyan: t('청록'), green: t('초록'), amber: t('노랑'), red: t('빨강'), mag: t('보라'),
  };
  return own(L, name) ? L[name] : name;
}
export function markIconLabel(name: string): string {
  const L: Record<string, string> = {
    folder: t('폴더'), code: t('코드'), server: t('서버'), globe: t('웹'), smartphone: t('앱'), database: t('데이터베이스'),
    cloud: t('클라우드'), layers: t('라이브러리'), 'book-open': t('문서'), wrench: t('도구'), 'flask-conical': t('실험'),
    briefcase: t('업무'), 'gamepad-2': t('게임'), music: t('음악'), star: t('별'), heart: t('하트'), zap: t('번개'),
  };
  return own(L, name) ? L[name] : name;
}
