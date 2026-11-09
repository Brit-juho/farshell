// 작업대(활동 막대·사이드바)가 새로 쓰는 아이콘 — ui/icons.js에 없는 것만 둔다.
//
// icons.js는 core 청크라 path 하나만 늘어도 초기 페이로드(상한 358,400B)가 는다. 작업대는
// 지연 청크(shell)에서만 그리므로 아이콘도 같은 청크에 둔다(vite.config.js `chunkOf` —
// brand-icons.js가 `brand` 청크로 빠진 것과 같은 방식). 여기 없는 이름은 icons.js로 넘긴다.
//
// path는 Lucide(ISC)의 24x24 stroke 좌표를 그대로 옮겼다. 고정 데이터라 sanitize 불필요.
import { icon } from './icons.js';

export const SHELL_ICONS = {
  // lucide box — MCP · 스킬 관리(옛 dock은 core 예산 때문에 `link`를 빌려 썼다).
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  // lucide server — 제목 막대 호스트 전환에서 원격 맥(로컬은 icons.js의 laptop).
  server: '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
  // ── 파일 내용 검색(파일 개편 단계 6, 시안 F06) — 팔레트 `?` 모드와 사이드바 「내용 검색」 ──
  'case-sensitive': '<path d="m3 15 4-8 4 8"/><path d="M4 13h6"/><circle cx="18" cy="12" r="3"/><path d="M21 9v6"/>',
  'whole-word': '<circle cx="7" cy="12" r="3"/><path d="M10 9v6"/><circle cx="17" cy="12" r="3"/><path d="M14 7v8"/><path d="M22 17v1c0 .5-.5 1-1 1H3c-.5 0-1-.5-1-1v-1"/>',
  regex: '<path d="M17 3v10"/><path d="m12.67 5.5 8.66 5"/><path d="m12.67 10.5 8.66-5"/><path d="M9 17a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-2z"/>',
  'file-code': '<path d="M10 12.5 8 15l2 2.5"/><path d="m14 12.5 2 2.5-2 2.5"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/>',
  'file-text': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  'circle-x': '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
  'chevrons-down-up': '<path d="m7 20 5-5 5 5"/><path d="m7 4 5 5 5-5"/>',
  'list-tree': '<path d="M21 12h-8"/><path d="M21 6H8"/><path d="M21 18h-8"/><path d="M3 6v4c0 1.1.9 2 2 2h3"/><path d="M3 10v6c0 1.1.9 2 2 2h3"/>',
};

/** icon()과 같은 모양의 SVG 문자열. 이 파일에 없으면 ui/icons.js에서 찾는다. */
export function shellIcon(name, size, strokeWidth) {
  const body = Object.prototype.hasOwnProperty.call(SHELL_ICONS, name) ? SHELL_ICONS[name] : null;
  if (!body) return icon(name, size, strokeWidth);
  const s = size || 16;
  const sw = strokeWidth || 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 24 24" `
    + `fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" focusable="false">${body}</svg>`;
}
