// 인라인 SVG 아이콘 — D5: Lucide 웹폰트(vendor/lucide.min.css 64KB +
// lucide.woff2 221KB, 도합 285KB)로 아이콘 11개만 쓰자고 폰트 전체를 실어
// 오던 것과, 플랫폼마다 렌더가 갈리는 이모지(🟢🖥️💤🗑🛡🔧✅✓✎)를 여기 하나로
// 통일한다. 실제 쓰는 아이콘이 20개 안팎이라 별도 빌드 스텝/트리셰이킹 없이
// 이 map 하나로 충분하다 — 새 아이콘이 필요하면 여기 한 줄만 추가한다.
// path는 Lucide(ISC 라이선스, 기존에 이미 폰트로 번들하던 것과 같은 세트)의
// 24x24 stroke 좌표를 그대로 옮겼다.
const PATHS = {
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  terminal: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  'square-terminal': '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="m7 9 3 3-3 3"/><path d="M12 15h5"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  'clipboard-copy': '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M8 4H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-2"/><path d="M16 4h2a2 2 0 0 1 2 2v4"/><path d="M21 14H11"/><path d="m15 10-4 4 4 4"/>',
  'file-up': '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M4.5 22H18a2 2 0 0 0 2-2V7l-5-5H6a2 2 0 0 0-2 2v3.5"/><path d="M12 12v6"/><path d="m9 15 3-3 3 3"/>',
  'monitor-smartphone': '<path d="M18 8V4H6v4"/><path d="M14 20H2v-8h12"/><rect width="8" height="14" x="14" y="10" rx="2"/>',
  palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
  'layout-grid': '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  'wifi-off': '<path d="M12 20h.01"/><path d="M8.5 16.429a5 5 0 0 1 7 0"/><path d="M5 12.859a10 10 0 0 1 5.17-2.69"/><path d="M19 12.859a10 10 0 0 0-2.007-1.523"/><path d="M2 8.82a15 15 0 0 1 4.177-2.643"/><path d="M22 8.82a15 15 0 0 0-11.288-3.764"/><path d="m2 2 20 20"/>',
  'trash-2': '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  pencil: '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
};

// icon(name, size?) → SVG 마크업 문자열. innerHTML/템플릿 리터럴에 그대로
// 꽂아 쓴다 — path 데이터가 사용자 입력과 무관한 고정 맵이라 별도 sanitize가
// 필요 없다. stroke-width는 2로 고정(기존 코드가 2/2.5를 혼용했다).
export function icon(name, size) {
  const body = PATHS[name];
  if (!body) {
    console.warn('[icons] 등록되지 않은 아이콘:', name);
    return '';
  }
  const s = size || 16;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 24 24" `
    + `fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" `
    + `aria-hidden="true" focusable="false">${body}</svg>`;
}
