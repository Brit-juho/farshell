// 패널 공용 fetch 래퍼 — 구 js/vtapi.js (F2에서 이관). viewer.js/ports.js/queue.js/
// snippets.js/quickopen.js/terminal.js가 각자 만들던 _api/_ptApi/_qApi 를 하나로
// 합친 것. 셋 다 규칙이 같았다: 토큰 쿼리 부착 + {error,reason} 형태의 서버
// 에러를 Error로 통일.
//
// API_BASE/_tokenQuery는 core/env.js가 window에 심어둔 값을 그때그때 읽는다 —
// _tokenQuery는 쿠키 교환 완료 시 ''로 재할당되므로 매번 최신값을 봐야 한다.

import { noteNetworkError, noteResponse, noteTimeout } from './reachability.js';
import { relayPath, relayUrl } from './host-route.js';
import { t } from './i18n.js';

// ## 요청 시간 제한 (서버가 연결은 받는데 답을 안 하던 사고)
//
// 서버 스레드 풀이 막혀 연결은 받지만 응답을 안 주던 때, fetch는 **영원히** 끝나지
// 않았다. 거부도 응답도 아니라서 도달 불가 배너(reachability.js)도 끝내 안 떴고,
// 폴러마다 매달린 요청이 쌓였다. 그래서 모든 호출에 기본 마감을 건다.
//   - GET/HEAD 15초, 그 밖(쓰기) 30초. 호출부가 `timeout`(ms, 0 = 없음)으로 바꿀 수 있다.
//   - 호출부의 `signal`도 그대로 존중한다(둘 중 먼저 오는 쪽이 끊는다).
//   - vtFetch는 본문 읽기(res.json())까지 같은 마감 안에서 끝나야 한다.
//   - 마감이 지나면 `.timeout = true`, `.status = 0`인 Error를 던지고 도달 실패로 센다.
// 의도적으로 오래 걸리는 호출(음성 인식 업로드, 모델 적재, 커밋 훅, 워크트리 생성)은
// 호출부에서 `timeout`을 넉넉히 준다.
export const GET_TIMEOUT_MS = 15000;
export const WRITE_TIMEOUT_MS = 30000;
// 의도적으로 긴 호출의 마감. 음성 업로드는 서버가 받은 자리에서 인식까지 끝내고
// 답한다(voice/chunk-upload.js에도 같은 값이 있다 — 그 모듈은 api.js를 import하지 않는
// 순수 모듈이라서다). 모델 적재는 처음 한 번 디스크에서 수 GB를 읽는다.
export const VOICE_UPLOAD_TIMEOUT_MS = 120000;
export const STT_PRELOAD_TIMEOUT_MS = 300000;

/** 마감 초과 오류. 화면은 e.message를 그대로 보여 주는 곳이 많아 사람 말로 둔다. */
export function timeoutError() {
  const e = new Error(t('서버가 응답하지 않습니다'));
  e.name = 'TimeoutError';
  e.timeout = true;
  e.status = 0;
  return e;
}

export function isTimeoutError(e) {
  return Boolean(e && e.timeout === true);
}

function _budget(opts) {
  if (opts && opts.timeout != null) {
    const n = Number(opts.timeout);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  const method = String((opts && opts.method) || 'GET').toUpperCase();
  return method === 'GET' || method === 'HEAD' ? GET_TIMEOUT_MS : WRITE_TIMEOUT_MS;
}

// 요청 하나의 마감. fetch에 넘길 opts(signal 합성, timeout 키 제거)와,
// 아무 Promise나 마감과 경주시키는 race()를 돌려준다. race()를 쓰는 이유: abort를
// 무시하는 fetch 구현(테스트 목, 일부 임베디드 브라우저)에서도 마감이 지켜져야 한다.
function _deadline(opts) {
  const ms = _budget(opts);
  const outer = opts && opts.signal;
  const ctl = typeof AbortController === 'function' ? new AbortController() : null;
  let timer = null;
  let rejectExpired = null;
  const expired = new Promise((_, rej) => { rejectExpired = rej; });
  expired.catch(() => {});
  const onOuterAbort = () => { if (ctl) ctl.abort(outer.reason); };
  if (outer && ctl) {
    if (outer.aborted) ctl.abort(outer.reason);
    else outer.addEventListener('abort', onOuterAbort, { once: true });
  }
  if (ms) {
    timer = setTimeout(() => {
      timer = null;
      rejectExpired(timeoutError());
      if (ctl) ctl.abort();
    }, ms);
  }
  const fetchOpts = { ...(opts || {}) };
  delete fetchOpts.timeout;
  if (ctl) fetchOpts.signal = ctl.signal;
  return {
    fetchOpts,
    callerAborted: () => Boolean(outer && outer.aborted),
    race: (p) => (ms ? Promise.race([p, expired]) : p),
    done() {
      if (timer != null) { clearTimeout(timer); timer = null; }
      if (outer && ctl) outer.removeEventListener('abort', onOuterAbort);
    },
  };
}

// 거부 사유를 도달 판정에 반영한다. 호출부가 스스로 끊은 것(signal)은 서버 탓이 아니다.
function _noteFailure(dl, e) {
  if (isTimeoutError(e)) noteTimeout();
  else if (!dl.callerAborted()) noteNetworkError();
}

export async function vtFetch(path, opts) {
  // 호스트 전환 중이면 선택한 맥으로(core/host-route.js). 로컬이면 그대로.
  path = relayPath(path);
  const tokenQuery = window._tokenQuery || '';
  const sep = path.includes('?') ? '&' : '?';
  // 도달 여부를 여기서 센다 — 앱의 거의 모든 서버 호출이 이 한 곳을 지난다.
  // 호출부는 아무것도 안 바뀐다(예외/반환값 동일): 실패를 기록만 하고 그대로 던진다.
  const dl = _deadline(opts);
  let res;
  try {
    res = await dl.race(fetch(`${window.API_BASE}${path}${sep}${tokenQuery.replace(/^[?&]/, '')}`, dl.fetchOpts));
  } catch (e) {
    dl.done();
    _noteFailure(dl, e);
    throw e;
  }
  noteResponse(res.status);
  let data = null;
  try {
    data = await dl.race(res.json());
  } catch (e) {
    // 본문이 JSON이 아닌 건 예전처럼 null로 넘어간다. 본문이 마감 안에 안 오는 건 실패다.
    if (isTimeoutError(e)) { dl.done(); noteTimeout(); throw e; }
    data = null;
  }
  dl.done();
  if (!res.ok) {
    const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
    e.status = res.status; e.data = data;
    throw e;
  }
  return data;
}

/**
 * `<img src>`처럼 **브라우저가 직접 부르는 URL**을 만든다. fetch가 아니라서
 * Authorization 헤더를 붙일 방법이 없으므로, vtFetch와 같은 토큰 쿼리를 URL에
 * 실어야 한다(WebSocket URL을 만드는 `_wsQuery`와 같은 사정).
 */
export function vtUrl(path) {
  path = relayPath(path);
  const tokenQuery = (window._tokenQuery || '').replace(/^[?&]/, '');
  if (!tokenQuery) return `${window.API_BASE}${path}`;
  return `${window.API_BASE}${path}${path.includes('?') ? '&' : '?'}${tokenQuery}`;
}

// F3(a) 신설 — window.fetch 몽키패치를 대체하는 명시 래퍼. 구 terminal.js:200-206의
// 동작을 그대로 옮긴 것: url이 API_BASE로 시작할 때만 Authorization 헤더를 붙이고,
// 나머지는 raw fetch와 100% 동일하게 동작한다(res.ok/res.json() 등 호출부를 안 건드림).
// vtFetch와 달리 파싱된 JSON이 아니라 Response를 그대로 반환한다 — 기존 fetch(...)
// 호출부를 1:1로 치환하기 위한 설계.
export function apiFetch(url, opts = {}) {
  url = relayUrl(url);
  const token = window.VT_TOKEN;
  if (token && typeof url === 'string' && url.startsWith(window.API_BASE)) {
    opts = { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } };
  }
  // vtFetch와 같은 이유로 도달 여부를 기록한다. 단 **Response를 그대로 돌려주는
  // 계약은 건드리지 않는다** — 호출부가 res.ok/res.json()을 직접 보는 코드라서
  // 여기서 던지거나 감싸면 25곳 넘는 호출부가 조용히 깨진다.
  //
  // 마감도 vtFetch와 같다. 응답 헤더가 온 뒤에도 타이머는 마감까지 그대로 둔다 —
  // 마감에 abort가 걸리면 아직 덜 읽힌 본문 읽기도 함께 끊긴다(이미 다 읽었으면
  // 아무 일도 없다). 본문은 호출부가 읽으므로 이 함수가 끝을 알 수 없어서다.
  return timedFetch(url, opts);
}

/**
 * 주소·헤더를 **하나도 바꾸지 않는** fetch — 마감과 도달 판정만 붙인다.
 *
 * 쿠키만 실어야 하는 호출(승인 대기·위임 닫기 — 토큰이 실리면 서버가 사람 요청으로
 * 치지 않는다), 호스트 중계를 타면 안 되는 호출(화면 언어 — 이 화면을 내준 서버의
 * 설정이다), 상대 경로를 그대로 쓰는 호출(기록 카탈로그)이 vtFetch/apiFetch를 못 쓰고
 * 맨 fetch를 불러 왔다. 그 바람에 마감도 도달 판정도 없었다. 동작은 맨 fetch와 같고
 * Response를 그대로 돌려준다(apiFetch와 같은 계약, 본문 읽기 마감도 같다).
 */
export function timedFetch(url, opts) {
  const dl = _deadline(opts);
  return dl.race(fetch(url, dl.fetchOpts)).then(
    (res) => { noteResponse(res.status); return res; },
    (e) => { dl.done(); _noteFailure(dl, e); throw e; },
  );
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
window.vtTimedFetch = timedFetch;
