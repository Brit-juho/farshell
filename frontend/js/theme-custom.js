// N14 — 가져온 테마(7번째 스킨)를 **적용**하는 쪽. 추론·파싱은
// theme-import.js가 하고 지연 로드된다 — 부팅 경로가 알아야 하는 것은
// "저장된 토큰 맵을 CSS 변수로 쓴다"뿐이라 그 코드를 번들 본체에 둘 이유가 없다.
//
// 저장 위치가 둘인 이유: 설정 스토어(`theme.imported`)는 기기 간 동기화가
// 되고, localStorage 사본은 **페인트 전에** 읽을 수 있다(설정 스토어는 서버
// 왕복이라 늦다). index.html의 부팅 인라인 스크립트가 그 사본을 읽어 FOUC를
// 막는다 — 스킨 이름만 복원하고 색은 나중에 오면, 가져온 테마가 매번 기본
// 팔레트로 한 번 번쩍인다.

export const IMPORTED_SKIN = 'imported';
export const LS_KEY = 'vt-theme-imported';
export const STYLE_ID = 'vt-imported-theme';

/** localStorage 사본 읽기. 없거나 깨졌으면 null. */
export function loadImportedSkin() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (!v || typeof v !== 'object' || !v.tokens) return null;
    return v;
  } catch (_) {
    return null;
  }
}

export function saveImportedSkin(skin) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(skin)); } catch (_) { /* 무시 */ }
}

export function clearImportedSkin() {
  try { localStorage.removeItem(LS_KEY); } catch (_) { /* 무시 */ }
  document.getElementById(STYLE_ID)?.remove();
}

/**
 * 토큰 맵을 `html[data-skin="imported"]` 규칙으로 문서에 심는다.
 * 인라인 style 속성이 아니라 style 엘리먼트를 쓰는 이유: 다른 스킨으로
 * 돌아갔을 때 규칙이 **자동으로 안 먹어야** 하기 때문이다(선택자가 안 맞으면
 * 끝 — 지울 필요조차 없다).
 */
export function applyImportedTokens(tokens) {
  if (!tokens || typeof tokens !== 'object') return;
  const body = Object.entries(tokens)
    .filter(([k, v]) => /^--color-[a-z0-9-]+$/.test(k) && typeof v === 'string' && v.length < 64)
    .map(([k, v]) => `${k}:${v};`)
    .join('');
  if (!body) return;
  let el = document.getElementById(STYLE_ID);
  if (!el) {
    el = document.createElement('style');
    el.id = STYLE_ID;
    document.head.appendChild(el);
  }
  el.textContent = `html[data-skin="${IMPORTED_SKIN}"]{${body}}`;
}

/** 부팅 시 1회 — 저장된 테마가 있으면 그 규칙을 심어 둔다. 지금 활성 스킨이
 *  imported가 아니어도 심어 둔다(전환 순간에 색이 비어 보이지 않게). */
export function bootImportedTheme() {
  const skin = loadImportedSkin();
  if (skin) applyImportedTokens(skin.tokens);
  return skin;
}
