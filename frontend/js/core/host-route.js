// 호스트 전환 — 「지금 이 화면 전체가 어느 맥인가」와, 그에 따라 서버 호출을
// 어디로 보낼지.
//
// 2026-09-28(사용자 요청) — 레일 스위처로 M5를 고르면 세션만이 아니라 탭·포트·
// 파일·첨부·설정까지 **전부** M5 것이 되어야 한다. 브라우저는 여전히 로그인한
// 서버(M1)에만 붙고, M1이 M5로 중계한다(server/routes/host_relay.py). 그래서
// 모든 호출이 지나는 곳(core/api.js의 vtFetch·apiFetch·vtUrl, 각 WebSocket)에서
// 경로만 바꾼다:
//     /api/tmux/sessions  →  /api/hosts/m5/relay/api/tmux/sessions
//     /ws/<id>            →  /ws/hosts/m5/relay/ws/<id>
//
// 전환은 **같은 주소에서 새로고침**이다(사용자 결정). 화면 안에서 모듈마다 상태를
// 비우는 방식은 이전 호스트의 늦은 응답이 섞일 틈이 많다 — 새로고침하면 앱이
// 처음부터 M5 기준으로 뜬다. 선택은 localStorage에 둬서 다시 열어도 유지된다.
//
// **로컬에 남는 것** — 이 브라우저·이 로그인에 붙은 것들이다: 로그인·기기·OTP,
// 호스트 목록과 peer 관리(중계를 중계하지 않는다), 푸시·알림 소켓, 클립보드,
// 음성, 기기 설정. 나머지는 전부 선택한 맥으로 간다. 거기서 막을 것(공유 링크·
// 포트 노출 같은 승격 기능)은 상대 서버가 허용 목록으로 거절한다
// (server/relay_policy.py) — 여기 목록은 "보내면 안 되는 것"이 아니라 "이 기기
// 것"의 목록이다.

export const LOCAL_HOST = 'local';
const STORE_KEY = 'vt.relayHost';
const ID_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;

const LOCAL_HTTP = [
  /^\/api\/auth(\/|$)/,
  /^\/api\/devices(\/|$)/,
  /^\/api\/device-settings(\/|$)/,
  /^\/api\/hosts(\/|$)/,
  /^\/api\/peer(\/|$)/,
  /^\/api\/push(\/|$)/,
  /^\/api\/notify(\/|$)/,
  /^\/api\/clipboard(\/|$)/,
  /^\/voice(\/|$)/,
];
const RELAY_WS = [/^\/ws\/[^/]+$/, /^\/ws-agent$/, /^\/ws-workspace$/, /^\/ws-preview\/[^/]+$/, /^\/ws-fs-watch$/];

function _read() {
  try {
    const v = localStorage.getItem(STORE_KEY) || '';
    return ID_RE.test(v) && v !== LOCAL_HOST ? v : LOCAL_HOST;
  } catch (_) { return LOCAL_HOST; }
}

// 부팅 때 한 번 읽고 고정한다 — 실행 중에 바뀌면 절반은 M1, 절반은 M5에 붙는다.
// 바꾸는 길은 switchHost(새로고침)뿐이다.
const _host = _read();

/** 이 화면이 보고 있는 맥. 로그인한 서버면 'local'. */
export function relayHost() { return _host; }
export function isRelayed() { return _host !== LOCAL_HOST; }

function _split(path) {
  const i = path.indexOf('?');
  return i < 0 ? [path, ''] : [path.slice(0, i), path.slice(i)];
}

/** HTTP 경로(`/api/...`, 쿼리 포함 가능)를 지금 호스트로 보낼 경로로. */
export function relayPath(path) {
  if (_host === LOCAL_HOST || typeof path !== 'string' || !path.startsWith('/')) return path;
  const [p] = _split(path);
  if (LOCAL_HTTP.some((rx) => rx.test(p))) return path;
  if (!p.startsWith('/api/')) return path;
  return `/api/hosts/${encodeURIComponent(_host)}/relay${path}`;
}

/** WebSocket 경로(`/ws/<id>`, `/ws-agent` …, 쿼리 포함 가능)를 지금 호스트로. */
export function relayWsPath(path) {
  if (_host === LOCAL_HOST) return path;
  const [p] = _split(path);
  if (!RELAY_WS.some((rx) => rx.test(p))) return path;
  return `/ws/hosts/${encodeURIComponent(_host)}/relay${path}`;
}

/** 절대 URL(`${API_BASE}/api/...`)용 — apiFetch 호출부가 이 모양으로 부른다. */
export function relayUrl(url) {
  const base = window.API_BASE || '';
  if (_host === LOCAL_HOST || typeof url !== 'string' || !url.startsWith(base)) return url;
  return base + relayPath(url.slice(base.length));
}

/** localStorage 키를 호스트별로 가른다. M1의 탭 배치·캐시가 M5 화면에 새지 않게. */
export function hostKey(key) {
  return _host === LOCAL_HOST ? key : `${key}@${_host}`;
}

/** 다른 맥으로 전환 — 저장하고 같은 주소에서 새로고침. */
export function switchHost(id) {
  const next = id && ID_RE.test(id) ? id : LOCAL_HOST;
  if (next === _host) return;
  try {
    if (next === LOCAL_HOST) localStorage.removeItem(STORE_KEY);
    else localStorage.setItem(STORE_KEY, next);
  } catch (_) {
    if (typeof window.showToast === 'function') {
      window.showToast('이 브라우저에 선택을 저장할 수 없어 전환하지 못했습니다', 'error');
    }
    return;
  }
  location.reload();
}

// 전환해 둔 맥이 지워졌거나(`fsh host rm`) 권한이 회수됐거나 구버전이면, 앱이
// 통째로 빈 화면이 된다. 부팅 직후 한 번 확인하고, 그런 경우엔 이유를 알린 뒤
// 이 맥으로 돌아온다. **꺼져 있는 것(502)은 되돌리지 않는다** — 잠깐 꺼진 맥을
// 고른 상태는 사용자의 의도이고, 켜지면 그대로 이어져야 한다.
const NOTICE_KEY = 'vt.relayNotice';
export async function verifyRelayHost() {
  try {
    const msg = sessionStorage.getItem(NOTICE_KEY);
    if (msg) {
      sessionStorage.removeItem(NOTICE_KEY);
      setTimeout(() => window.showToast?.(msg, 'warn'), 800);
    }
  } catch (_) { /* sessionStorage 불가 — 안내만 못 한다 */ }
  if (_host === LOCAL_HOST) return;
  let res;
  try {
    res = await fetch(`${window.API_BASE}${relayPath('/api/capabilities')}`, { credentials: 'include' });
  } catch (_) { return; }   // 네트워크 문제 — 되돌리지 않는다
  if (res.ok || res.status === 502 || res.status === 401) return;
  let reason = '';
  try { reason = (await res.json()).reason || ''; } catch (_) { /* 본문 없음 */ }
  try { sessionStorage.setItem(NOTICE_KEY, `${_host}로 전환할 수 없어 이 맥으로 돌아왔습니다${reason ? ` — ${reason}` : ''}`); } catch (_) {}
  switchHost(LOCAL_HOST);
}

window.vtRelayHost = relayHost;
window.vtSwitchHost = switchHost;
