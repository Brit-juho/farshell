// WebSocket 재연결 공용 조각 — 지터 백오프, 화면 복귀 신호, 연결 기한.
//
// 소켓마다 같은 박자로 다시 붙지 않게(지터) 하고, 폰이 잠들었다 깨어날 때 최대 30초짜리
// 백오프 타이머를 그대로 기다리지 않게(onWake) 한다. 소켓 상태는 각 소유자가 들고 있고
// 이 모듈은 값과 신호만 준다.
//
// ⚠ 이 모듈은 core 청크에 들어간다(초기 페이로드 300KiB 상한). 의존성을 늘리지 말 것.

/** 연결이 이 시간 안에 안 열리면 포기하고 다음 백오프로 간다(watchSocketLiveness의 timeout으로 쓴다). */
export const CONNECT_TIMEOUT_MS = 10000;
/** 이만큼 숨어 있다 돌아오면 `long`. */
export const LONG_HIDDEN_MS = 10000;
const MAX_DELAY_MS = 30000;

/**
 * "equal jitter" 백오프: c = min(30s, base·2^min(n,6)), 결과는 [c/2, c).
 * @param {number} n 직전까지 연속 실패 수(0부터)
 * @param {number} base 첫 상한(ms)
 * @param {() => number} [rand] 0 이상 1 미만(테스트용)
 */
export function retryDelay(n, base, rand = Math.random) {
  const c = Math.min(MAX_DELAY_MS, base * Math.pow(2, Math.min(Math.max(n | 0, 0), 6)));
  return c / 2 + rand() * (c / 2);
}

const _subs = new Set();
let _installed = false;
let _hiddenAt = 0;

function _emit(reason, hiddenMs) {
  if (document.hidden) return;   // 숨은 동안에는 아무것도 하지 않는다
  for (const fn of Array.from(_subs)) {
    try { fn({ reason, hiddenMs }); } catch (_) { /* 구독자 하나가 다른 구독자를 막지 않는다 */ }
  }
}

function _install() {
  if (_installed) return;
  _installed = true;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { _hiddenAt = Date.now(); return; }
    const ms = _hiddenAt ? Date.now() - _hiddenAt : 0;
    _hiddenAt = 0;
    _emit(ms >= LONG_HIDDEN_MS ? 'long' : 'short', ms);
  });
  // bfcache에서 되살아난 페이지는 소켓이 이미 죽어 있다.
  window.addEventListener('pageshow', (e) => { if (e && e.persisted) _emit('long', 0); });
  window.addEventListener('online', () => _emit('network', 0));
}

/**
 * 화면이 다시 보일 때·bfcache 복귀·`online`을 하나로 묶어 알린다.
 * `fn({reason: 'short'|'long'|'network', hiddenMs})`. 해제 함수를 돌려준다.
 */
export function onWake(fn) {
  _install();
  _subs.add(fn);
  return () => { _subs.delete(fn); };
}
