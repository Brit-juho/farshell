// 도구 패널(리디자인 5단계 「정돈」)이 새로 쓰는 아이콘 — ui/icons.js에 없는 것만 둔다.
//
// icons.js는 core 청크라 path 하나만 늘어도 초기 페이로드(상한 358,400B)가 는다. 패널 화면 청크(panels·settings)는
// 모두 panels/panel.js(`panel` 청크)를 이미 정적으로 받으므로, 이 파일도 같은 `panel` 청크에 둔다(vite.config.js
// `chunkOf`). ui/icons-shell.js(shell 청크)를 빌려 쓰면 폰에서 패널 하나를 열 때 작업대 청크까지 받게 된다.
//
// path는 Lucide(ISC)의 24x24 stroke 좌표를 그대로 옮겼다. 고정 데이터라 sanitize 불필요.
import { icon } from '../ui/icons.js';

const PANEL_ICONS = {
  minus: '<path d="M5 12h14"/>',
  eye: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  'external-link': '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
  'file-text': '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
};

/** icon()과 같은 모양의 SVG 문자열. 이 파일에 없으면 ui/icons.js에서 찾는다. */
export function pIcon(name, size, strokeWidth) {
  const body = Object.prototype.hasOwnProperty.call(PANEL_ICONS, name) ? PANEL_ICONS[name] : null;
  if (!body) return icon(name, size, strokeWidth);
  const s = size || 16;
  const sw = strokeWidth || 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 24 24" `
    + `fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" focusable="false">${body}</svg>`;
}

/** 아이콘 하나짜리 행/머리 버튼(.vt-icon-btn.sm). 이름은 aria-label과 툴팁(data-tip)이 맡는다. */
export function pIconBtn(name, label, { cls = '', size = 14 } = {}) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `vt-icon-btn sm ${cls}`.trim();
  b.setAttribute('aria-label', label);
  b.dataset.tip = label;
  b.innerHTML = pIcon(name, size);
  return b;
}
