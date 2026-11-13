// WebSocket 재연결 공용 조각 — 지터 백오프, 화면 복귀 신호, 연결 기한, 생존 탐침.
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

// --- 생존 탐침 -------------------------------------------------------------------
//
// 폰 브라우저는 백그라운드에서 소켓을 close 이벤트 없이 죽일 수 있다(미확인 가정). 그러면 돌아와도
// "열려 있는" 소켓이 45초 워치독이 닫을 때까지 말이 없다. 복귀(long)·네트워크 복귀 때 서버에
// `{"type":"probe","id":n}`을 보내 기한 안에 `probe_ack` **또는 아무 프레임**이 오지 않으면 죽은 소켓으로
// 보고 닫은 뒤 바로 다시 붙는다(서버: resilience/wsutil.py Prober).
//
// 옛 서버는 모르는 type을 무시한다 — 소켓이 열릴 때 탐침을 한 번 보내 5초 안에 ack가 와야 그 소켓을
// `capable`로 친다. capable이 아니면 절대 닫지 않는다(지금 동작 유지: 45초 워치독).

export const PROBE_CAPABLE_MS = 5000;
export const PROBE_LONG_MS = 2500;
export const PROBE_NETWORK_MS = 3000;
const STAGGER_MS = 250;

/**
 * 소켓 하나(연결 하나)당 하나. 소유자는 onopen에서 만들어 `start()`, 프레임마다 `frame(msg|null)`,
 * onclose에서 `stop()`을 부른다. 복귀 때 열려 있으면 `check(reason)`.
 * `onDead()`: 죽은 소켓을 닫은 뒤(옛 onclose 핸들러가 정리까지 마친 뒤) 0-250ms 엇갈려 불린다 —
 * 여러 소켓이 한꺼번에 다시 붙어 스크롤백 재생이 몰리는 것을 피한다.
 * @param {WebSocket | any} ws
 * @param {{ onDead?: () => void, later?: Function, cancel?: Function, rand?: () => number }} [opts]
 */
export function startProbe(ws, opts = {}) {
  const later = opts.later || ((fn, ms) => setTimeout(fn, ms));
  const cancel = opts.cancel || ((id) => clearTimeout(id));
  const rand = opts.rand || Math.random;
  let seq = 0;
  let pending = 0;     // 답을 기다리는 탐침 id(0 = 없음)
  let lastId = 0;
  let mode = '';       // 'cap' | 'live'
  let capable = false;
  let stopped = false;
  let timer = null;

  function clear() {
    if (timer != null) { cancel(timer); timer = null; }
    pending = 0;
  }

  function send(m, ms) {
    if (stopped || pending || ws.readyState !== 1) return;
    mode = m;
    pending = lastId = ++seq;
    try { ws.send(JSON.stringify({ type: 'probe', id: pending })); } catch (_) { pending = 0; return; }
    timer = later(expire, ms);
  }

  function expire() {
    timer = null;
    pending = 0;
    if (stopped || mode === 'cap') return;   // 옛 서버 — 탐침 판정을 쓰지 않는다
    stopped = true;
    const handler = ws.onclose;
    // 떼어 낸 뒤 직접 부른다 — 죽은 소켓의 늦은 close가 재연결을 또 걸지 않게(ws-watchdog.js와 같은 방식).
    ws.onclose = null; ws.onmessage = null; ws.onerror = null;
    try { ws.close(4000, 'probe timeout'); } catch (_) { /* 이미 닫히는 중 */ }
    if (typeof handler === 'function') {
      try { handler.call(ws, { code: 4000, reason: 'probe timeout', wasClean: false }); } catch (_) { /* 소유자 오류가 막지 않게 */ }
    }
    if (opts.onDead) later(opts.onDead, rand() * STAGGER_MS);
  }

  return {
    /** 열린 직후 한 번: 서버가 탐침을 아는지(capable) 확인한다. */
    start() { send('cap', PROBE_CAPABLE_MS); },
    /** 프레임이 왔다(바이너리면 msg=null). 아무 프레임이나 "살아 있다"는 증거다. */
    frame(msg) {
      if (stopped) return;
      if (msg && msg.type === 'probe_ack' && msg.id === lastId && mode === 'cap') capable = true;
      clear();
    },
    /** 화면·네트워크 복귀 때. short이거나 capable이 아니면 아무것도 안 한다. */
    check(reason) {
      if (!capable || reason === 'short') return;
      send('live', reason === 'network' ? PROBE_NETWORK_MS : PROBE_LONG_MS);
    },
    capable: () => capable,
    stop() { stopped = true; clear(); },
  };
}
