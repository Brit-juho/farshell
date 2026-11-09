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
