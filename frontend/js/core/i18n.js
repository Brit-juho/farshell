// 모듈용 번역 진입점. 실제 구현은 classic script js/i18n-boot.js(로그인 화면이 모듈보다
// 먼저 돌아서 그쪽이 원본이다). 여기는 그 전역을 감싸고, 전역이 없는 환경(node 테스트,
// 별도 빌드)에서는 원문을 그대로 돌려준다 — 키가 한국어 원문이라 그게 곧 ko 화면이다.
const fill = (s, vars) => (vars ? s.replace(/\{(\w+)\}/g, (m, k) => (Object.hasOwn(vars, k) ? String(vars[k]) : m)) : s);

export function lang() {
  return typeof window !== 'undefined' && typeof window.vtLang === 'function' ? window.vtLang() : 'ko';
}

export function t(src, vars) {
  if (typeof window !== 'undefined' && typeof window.vtT === 'function') return window.vtT(src, vars);
  return fill(src, vars);
}

export function applyI18n(root) {
  if (typeof window !== 'undefined' && typeof window.vtApplyI18n === 'function') window.vtApplyI18n(root);
}
