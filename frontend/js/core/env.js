// 환경 상수 — F2에서 terminal.js 최상단(구 :1-197 부근)에서 분리.
// F3(a)에서 VT_TOKEN·쿠키 교환·인증 상태를 마저 이관했다 (구 terminal.js:1-206).
//
// 아직 classic script인 terminal.js/grid.js/picker.js/vtapi.js/queue.js/viewer.js/
// ports.js가 이 값들을 bare identifier(API_BASE, isMac 등)로 그대로 참조하므로,
// window에도 반드시 노출한다 — 비엄격 모드 전역 스코프에서는 window 프로퍼티가
// 곧 bare identifier로 풀리므로 이 브리지만으로 기존 소비처를 전혀 안 건드려도 된다.

export const WS_BASE = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
export const API_BASE = `${location.protocol}//${location.host}`;
import { isMobileDevice } from './device.js';
export const isMobile = isMobileDevice();
document.documentElement.classList.toggle('vt-mobile-device', isMobile);
// 키보드 플랫폼과 안내 표시는 같은 platform helper를 사용한다.
import { isMacKeyboard } from './platform.js';
export const isMac = isMacKeyboard;

// 물리 키보드가 없는 터치 기기 판정 — keybar 노출, M5 롱프레스 선택 기본값 등
// 여러 곳에서 같은 기준을 쓴다.
export function _isCoarsePointer() {
  try { return !!(window.matchMedia && window.matchMedia('(pointer:coarse)').matches); } catch (_) { return false; }
}

window.WS_BASE = WS_BASE;
window.API_BASE = API_BASE;
window.isMobile = isMobile;
window.isMac = isMac;
window._isCoarsePointer = _isCoarsePointer;

// ─────────────────────────────────────────────────────────────
// 인증 토큰 — URL 의 `?token=` 은 더 이상 쓰지 않는다. 예전에는 이 값을 쿠키로 바꾸거나 모든 요청에 붙였지만,
// URL 에 실린 공용 토큰은 히스토리·로그·공유로 새고 서버도 받지 않게 되었다. 링크에 `?token=` 이 있으면 주소창에서
// 지우기만 하고 값은 쓰지 않는다 — 인증이 필요하면 평소처럼 로그인 화면이 뜬다(비밀번호, 또는 `fsh mobile` 의 1회용 링크).
// `VT_TOKEN`·`_tokenQuery` 는 아직 import 하는 모듈이 있어 export 만 남기고 항상 빈 값이다.
const _urlParams = new URLSearchParams(location.search);
export let VT_TOKEN = '';
export let _tokenQuery = '';
window.VT_TOKEN = VT_TOKEN;
window._tokenQuery = _tokenQuery;

if (_urlParams.has('token')) {
  try {
    _urlParams.delete('token');
    const newSearch = _urlParams.toString();
    history.replaceState({}, '', location.pathname + (newSearch ? '?' + newSearch : '') + location.hash);
  } catch (_) { /* 주소창 정리 실패는 무시 — 값은 어디에도 쓰이지 않는다 */ }
}
