// 패널 공용 fetch 래퍼 — 구 js/vtapi.js (F2에서 이관). viewer.js/ports.js/queue.js/
// snippets.js/quickopen.js/terminal.js가 각자 만들던 _api/_ptApi/_qApi 를 하나로
// 합친 것. 셋 다 규칙이 같았다: 토큰 쿼리 부착 + {error,reason} 형태의 서버
// 에러를 Error로 통일.
//
// API_BASE/_tokenQuery는 core/env.js가 window에 심어둔 값을 그때그때 읽는다 —
// _tokenQuery는 쿠키 교환 완료 시 ''로 재할당되므로 매번 최신값을 봐야 한다.

export async function vtFetch(path, opts) {
  const tokenQuery = window._tokenQuery || '';
  const sep = path.includes('?') ? '&' : '?';
  const res = await fetch(`${window.API_BASE}${path}${sep}${tokenQuery.replace(/^[?&]/, '')}`, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
    e.status = res.status; e.data = data;
    throw e;
  }
  return data;
}

// F3(a) 신설 — window.fetch 몽키패치를 대체하는 명시 래퍼. 구 terminal.js:200-206의
// 동작을 그대로 옮긴 것: url이 API_BASE로 시작할 때만 Authorization 헤더를 붙이고,
// 나머지는 raw fetch와 100% 동일하게 동작한다(res.ok/res.json() 등 호출부를 안 건드림).
// vtFetch와 달리 파싱된 JSON이 아니라 Response를 그대로 반환한다 — 기존 fetch(...)
// 호출부를 1:1로 치환하기 위한 설계.
export function apiFetch(url, opts = {}) {
  const token = window.VT_TOKEN;
  if (token && typeof url === 'string' && url.startsWith(window.API_BASE)) {
    opts = { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } };
  }
  return fetch(url, opts);
}

// 서버가 준 문자열은 절대 innerHTML 로 넣지 않는다 — textContent 경유로 이스케이프.
export function vtEsc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

window.vtFetch = vtFetch;
window.vtEsc = vtEsc;
window.apiFetch = apiFetch;
