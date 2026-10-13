// 서버 도달 불가 배너 — "화면은 멀쩡한데 아무것도 안 되는" 상태를 없앤다.
//
// ## 왜 필요한가 (2026-09-21 실제 사고)
//
// 익명 quick tunnel은 cloudflared 프로세스가 죽으면 주소가 영영 죽는다. 그런데
// `sw.js`의 network-first 분기는 `fetch`가 **거부되면 캐시 셸을 돌려준다**:
//
//     try { const res = await fetch(req); ... }
//     catch (_) { const cached = await cache.match(req); return cached || Response.error(); }
//
// 그래서 죽은 주소로 들어가도 FarShell UI가 통째로 그려진다. 사용자에게는
// "접속은 됐는데 세션이 안 뜬다"로 보이고, 서버 로그에는 요청이 **한 줄도**
// 안 남는다(애초에 도달을 못 하니까). 양쪽 어디에서도 원인이 안 보였다.
//
// 그 캐시 fallback 자체는 옳다 — 오프라인에서 앱 껍데기가 뜨는 건 PWA의 이점이다.
// 잘못된 건 **앱이 그 사실을 모른 채 정상인 척한 것**이다. 그래서 캐시를 건드리지
// 않고, 앱이 자기 요청의 실패를 세어 직접 말하게 한다.
//
// ## 무엇을 "도달 불가"로 보는가
//
//   1. `fetch` 자체가 거부됨(TypeError) — DNS·TLS·연결 실패. 주소가 죽었거나
//      기기가 오프라인이다.
//   2. 502 / 503 / 504 — 입구(터널)는 살아 있는데 뒤의 서버가 없다.
//      `fsh restart` 중 잠깐, 또는 서버만 죽은 상태가 여기 해당한다(실측:
//      cloudflared를 살려 둔 채 백엔드를 죽이면 정확히 502가 나온다).
//
// **401/403은 도달 불가가 아니다.** 서버가 멀쩡히 답한 것이고, 그건 로그인
// 게이트(`gate.js`)가 처리할 일이다. 쿠키 만료(24h)로 401이 쏟아질 때 이 배너가
// 같이 뜨면 "서버가 죽었나"로 오진하게 된다 — 2026-09-21에 실제로 그 두 가지를
// 헷갈렸다.
//
// ## 왜 연속 실패를 세는가
//
// wifi↔LTE 전환 같은 찰나의 끊김은 보통 1~2초면 스스로 낫는다. 첫 실패에 바로
// 배너를 띄우면 그때마다 화면이 번쩍인다 — `term/conn-overlay.js`가 GRACE_MS로
// 같은 문제를 이미 해결했고, 여기서도 같은 원칙을 쓴다.

import { t } from './i18n.js';

// 연속 실패 임계치. 폴링 주기가 짧아(수 초) 2회면 몇 초 안에 뜬다.
export const FAIL_THRESHOLD = 2;

// 입구는 살아 있는데 뒤가 없다는 뜻의 상태 코드.
const GATEWAY_DOWN = new Set([502, 503, 504]);

const BANNER_ID = 'vt-unreachable-banner';

const MESSAGES = {
  // 주소 자체에 못 닿음 — 터널이 죽었거나(재시작으로 URL이 바뀜) 기기가 오프라인.
  offline: {
    title: t('서버에 연결할 수 없습니다'),
    sub: t('접속 주소가 바뀌었거나 서버가 꺼져 있습니다. 터미널에서 `fsh status`로 현재 주소를 확인하세요.'),
  },
  // 터널은 응답하는데 뒤의 서버가 없음 — 재시작 중이면 곧 저절로 돌아온다.
  gateway: {
    title: t('서버가 응답하지 않습니다'),
    sub: t('입구는 살아 있지만 서버가 내려가 있습니다. 재시작 중이라면 곧 자동으로 복구됩니다.'),
  },
};

let _fails = 0;
let _reason = null;   // 'offline' | 'gateway' | null

/** 현재 도달 불가로 판정된 상태인가. */
export function isUnreachable() {
  return _reason !== null;
}

/** 지금 화면에 띄우고 있는 사유(없으면 null) — 테스트/디버깅용. */
export function unreachableReason() {
  return _reason;
}

/**
 * 배너를 그린다. 이미 같은 사유로 떠 있으면 아무것도 안 한다.
 *
 * DOM이 없는 환경(테스트의 순수 로직 검증)에서도 안전해야 하므로 document
 * 유무를 먼저 본다 — 상태 계산과 렌더를 한 함수에 묶으면 호출부마다 가드를
 * 흩뿌리게 된다.
 */
function _render() {
  if (typeof document === 'undefined' || !document.body) return;
  const existing = document.getElementById(BANNER_ID);

  if (!_reason) {
    if (existing) existing.remove();
    return;
  }

  const msg = MESSAGES[_reason];
  if (existing) {
    if (existing.dataset.reason === _reason) return;   // 같은 상태 — 다시 그리지 않는다
    existing.dataset.reason = _reason;
    existing.querySelector('.vt-unreachable-title').textContent = msg.title;
    existing.querySelector('.vt-unreachable-sub').textContent = msg.sub;
    return;
  }

  const el = document.createElement('div');
  el.id = BANNER_ID;
  el.className = 'vt-unreachable';
  el.dataset.reason = _reason;
  // role=status + aria-live: 스크린리더에도 알린다. alert가 아니라 status인 건
  // 이게 사용자 조작을 막는 상황이 아니라 배경 상태이기 때문이다.
  el.setAttribute('role', 'status');
  el.setAttribute('aria-live', 'polite');

  const title = document.createElement('div');
  title.className = 'vt-unreachable-title';
  title.textContent = msg.title;

  const sub = document.createElement('div');
  sub.className = 'vt-unreachable-sub';
  sub.textContent = msg.sub;

  el.appendChild(title);
  el.appendChild(sub);
  document.body.appendChild(el);
}

/** 요청이 서버까지 닿아 응답을 받았다 — 상태 코드로 판정. */
export function noteResponse(status) {
  if (GATEWAY_DOWN.has(status)) {
    _fails += 1;
    if (_fails >= FAIL_THRESHOLD) { _reason = 'gateway'; _render(); }
    return;
  }
  // 4xx(401 포함)와 5xx 나머지는 **서버가 살아서 답한 것**이다 — 도달은 됐다.
  noteReachable();
}

/** `fetch`가 거부됐다 — 주소에 아예 못 닿았다. */
export function noteNetworkError() {
  _fails += 1;
  if (_fails >= FAIL_THRESHOLD) { _reason = 'offline'; _render(); }
}

/** 정상 응답 — 카운터를 리셋하고 배너를 내린다. */
export function noteReachable() {
  _fails = 0;
  if (_reason !== null) { _reason = null; _render(); }
}

/** 테스트 전용 — 모듈 스코프 상태를 초기화한다. */
export function _resetForTest() {
  _fails = 0;
  _reason = null;
  if (typeof document !== 'undefined' && document.body) {
    const el = document.getElementById(BANNER_ID);
    if (el) el.remove();
  }
}
