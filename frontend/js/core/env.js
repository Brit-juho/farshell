// 환경 상수 — F2에서 terminal.js 최상단(구 :1-197 부근)에서 분리.
// F3(a)에서 VT_TOKEN·쿠키 교환·인증 상태를 마저 이관했다 (구 terminal.js:1-206).
//
// 아직 classic script인 terminal.js/grid.js/picker.js/vtapi.js/queue.js/viewer.js/
// ports.js가 이 값들을 bare identifier(API_BASE, isMac 등)로 그대로 참조하므로,
// window에도 반드시 노출한다 — 비엄격 모드 전역 스코프에서는 window 프로퍼티가
// 곧 bare identifier로 풀리므로 이 브리지만으로 기존 소비처를 전혀 안 건드려도 된다.

export const WS_BASE = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
export const API_BASE = `${location.protocol}//${location.host}`;
export const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
// OS별 안내 문구 표시용. 실제 키 처리는 이미 ctrlKey||metaKey로 두 OS 모두
// 받아들이므로 동작에는 영향 없다.
export const isMac = /Mac|iPod|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

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
// 인증 토큰 — F3(a)에서 이관. VT_TOKEN은 불변값이 아니다: 쿠키 교환
// (_exchangeTokenForCookie)이 비동기로 끝나면 ''로 재할당된다. 이 모듈
// 안에서는 `export let`이 살아있는 바인딩이라 import한 쪽(core/api.js)이
// 항상 최신값을 보지만, 아직 classic script인 terminal.js/grid.js는
// import를 못 쓰므로 재할당마다 window에도 함께 반영해 동기화한다.
const _urlParams = new URLSearchParams(location.search);
export let VT_TOKEN = _urlParams.get('token') || '';
export let _tokenQuery = VT_TOKEN ? `?token=${VT_TOKEN}` : '';
window.VT_TOKEN = VT_TOKEN;
window._tokenQuery = _tokenQuery;

// Phase 9 #8: URL의 토큰을 HttpOnly cookie로 1회 교환 후 URL에서 제거.
// 이후 fetch는 credentials:'include'로 cookie 자동 전송, ws는 same-origin이라 자동.
// '/api/auth'는 상대 경로라 apiFetch(아래 core/api.js) 대상이 아니다 — 이 교환
// 자체가 인증 상태를 만드는 부트스트랩이므로 원시 fetch를 그대로 쓴다.
(async function _exchangeTokenForCookie() {
  if (!VT_TOKEN) return;
  try {
    const r = await fetch('/api/auth', {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token: VT_TOKEN}),
    });
    if (r.ok) {
      // URL에서 토큰 제거 — 로그/공유/history 노출 차단
      _urlParams.delete('token');
      const newSearch = _urlParams.toString();
      history.replaceState({}, '', location.pathname + (newSearch ? '?' + newSearch : '') + location.hash);
      // 이후 ws/fetch는 cookie로 자동 인증되므로 query 파라미터 비우기
      VT_TOKEN = '';
      _tokenQuery = '';
      window.VT_TOKEN = VT_TOKEN;
      window._tokenQuery = _tokenQuery;
    }
  } catch (e) { /* 실패 시 query 토큰 그대로 사용 (호환) */ }
})();
