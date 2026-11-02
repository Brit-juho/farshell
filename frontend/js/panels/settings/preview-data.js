// 설정 화면 오른쪽 「미리보기」 칸의 순수 계산 — DOM·스토어를 모른다(node:test에서 값만 검증한다).
// 그리는 쪽은 panels/settings/preview.js.

// 고정폭 글꼴의 한 글자 폭은 글자 크기의 약 0.6배다(JetBrains Mono 0.6, IBM Plex Mono 0.6).
// 미리보기가 알려 주는 「한 줄에 약 N자」는 실제 터미널 폭이 아니라 이 칸 폭 기준의 가늠값이다.
export const MONO_ADVANCE = 0.6;

export function estimateCols(widthPx, fontPx) {
  const w = Number(widthPx), f = Number(fontPx);
  if (!(w > 0) || !(f > 0)) return 0;
  return Math.floor(w / (f * MONO_ADVANCE));
}

// 커서 모양 → 미리보기 CSS 클래스. 스키마 값(block/underline/bar) 밖의 값은 block으로 본다.
export function cursorClass(style) {
  return style === 'underline' || style === 'bar' ? style : 'block';
}

// 미리보기 터미널에 찍는 예시 화면. 사용자의 실제 출력이 아니라 고정된 샘플이다.
// 토큰의 이름은 xterm 테마 키(green·yellow·brightBlack)이며, 그리는 쪽이 색을 입힌다.
export const SAMPLE_LINES = [
  [{ text: '❯ ', color: 'yellow' }, { text: 'git status -s' }],
  [{ text: ' M ', color: 'red' }, { text: 'frontend/js/shell/Rail.tsx' }],
  [{ text: '?? ', color: 'green' }, { text: 'notes.md' }],
  [{ text: '❯ ', color: 'yellow' }, { text: 'npm test' }],
  [{ text: '✓ ', color: 'green' }, { text: '42 passed ' }, { text: '(1.8s)', color: 'brightBlack' }],
  [{ text: '❯ ', color: 'yellow' }, { cursor: true }],
];

// 스킨 이름은 theme.js의 vtSkins() 밖이면 farshell로 되돌린다(theme.js applyVtSkin과 같은 규칙).
export function resolveSkin(skin, skins) {
  return Array.isArray(skins) && skins.includes(skin) ? skin : 'farshell';
}
