// 공용 폴링 도우미 — 화면마다 `setInterval(() => { if (!document.hidden) refresh(); }, ms)`를
// 손으로 짜던 것을 하나로 모은다.
//
// ## 왜 필요한가 (서버가 연결은 받는데 답을 안 하던 사고)
//
// 서버 스레드 풀이 막혀 연결은 받지만 응답을 안 주던 때, 폴러들은 tick마다 **이전
// 요청이 끝났는지 보지 않고** 새 요청을 쌓았다. 몇 분 만에 수천 건이 매달렸고, 서버가
// 살아난 뒤에도 그 더미를 먼저 처리하느라 한참 더 느렸다. 여기서 세 가지를 지킨다.
//
//   1. 진행 중이면 건너뛴다 — 다음 실행은 이전 실행이 끝난 뒤에야 예약된다.
//   2. 실패(거부·시간 초과)하면 간격을 늘린다 — interval * 2^n, maxInterval에서 멈춘다.
//      한 번 성공하면 원래 간격으로 돌아온다.
//   3. 탭이 숨어 있으면 쉬고, 다시 보이면 기다리지 않고 한 번 바로 따라잡는다
//      (panels/panel.js의 setPanelPoll이 원래 하던 동작).
//
// `fn`은 Promise를 돌려줘야 1·2가 의미가 있다. 동기 함수면 곧바로 끝난 것으로 본다.
// 오류를 안에서 삼키는 함수는 늘 "성공"이라 2가 걸리지 않는다 — 실패를 알리고
// 싶으면 던지거나 거부된 Promise를 돌려준다.

/** 기본 상한 — 아무리 실패해도 1분에 한 번은 다시 본다. */
export const DEFAULT_MAX_INTERVAL = 60000;

/** 이만큼 성공한 갱신이 없으면 "갱신 지연"으로 본다(간격의 두 배가 더 길면 그쪽). */
export const STALE_AFTER_MS = 90000;

/**
 * 마지막 성공(lastOkAt, ms)이 오래됐는가. 아직 한 번도 성공 못 했으면(0) 시작
 * 시각(since)부터 센다 — 첫 로드가 매달린 경우도 잡아야 한다.
 * @param {number} lastOkAt
 * @param {number} interval
 * @param {number} now
 * @param {number} [since]
 */
export function isStale(lastOkAt, interval, now, since = 0) {
  const base = lastOkAt || since;
  if (!base) return false;
  return now - base > Math.max(2 * interval, STALE_AFTER_MS);
}

/**
 * @typedef {object} PollOptions
 * @property {number} interval                 기본 간격(ms)
 * @property {number} [maxInterval]            실패 시 늘어나는 간격의 상한(ms, 기본 60000)
 * @property {boolean} [pauseWhenHidden]       document.hidden이면 쉬기(기본 true)
 * @property {boolean} [immediate]             start() 즉시 한 번 실행(기본 false)
 * @property {(ok: boolean, err?: unknown) => void} [onSettled]  실행 하나가 끝날 때마다
 * @property {Document | {hidden: boolean, addEventListener: Function, removeEventListener: Function}} [doc]  테스트용
 * @property {(fn: () => void, ms: number) => any} [later]   테스트용(기본 setTimeout)
 * @property {(id: any) => void} [cancel]                    테스트용(기본 clearTimeout)
 */

/**
 * @typedef {object} Poll
 * @property {() => void} start   시작(이미 돌고 있으면 무시)
 * @property {() => void} stop    멈춤. 진행 중인 실행의 결과는 다음 예약을 만들지 않는다
 * @property {() => void} kick    지금 한 번 실행. 진행 중이면 끝난 뒤 한 번 더 실행한다
 * @property {() => {running: boolean, pending: boolean, failures: number, lastOkAt: number}} state
 */

/**
 * @param {() => unknown} fn
 * @param {PollOptions} opts
 * @returns {Poll}
 */
export function createPoll(fn, opts) {
  const interval = Math.max(1, Number(opts && opts.interval) || 0);
  const maxInterval = Math.max(interval, Number(opts && opts.maxInterval) || DEFAULT_MAX_INTERVAL);
  const pauseWhenHidden = !opts || opts.pauseWhenHidden !== false;
  const immediate = Boolean(opts && opts.immediate);
  const onSettled = opts && opts.onSettled;
  const doc = (opts && opts.doc) || (typeof document !== 'undefined' ? document : null);
  const later = (opts && opts.later) || ((f, ms) => setTimeout(f, ms));
  const cancel = (opts && opts.cancel) || ((id) => clearTimeout(id));

  let running = false;     // start ~ stop 사이
  let pending = false;     // fn 실행 중
  let dirty = false;       // 실행 중에 kick이 들어왔다 — 끝나면 한 번 더
  let pausedHidden = false; // 숨은 동안 tick을 건너뛰었다 — 보이면 따라잡는다
  let failures = 0;
  let lastOkAt = 0;
  let timer = null;
  let gen = 0;             // stop/start 경계 — 이전 세대의 실행 결과는 무시

  const hidden = () => pauseWhenHidden && Boolean(doc && doc.hidden);
  const delay = () => Math.min(maxInterval, interval * Math.pow(2, failures));

  function schedule(ms) {
    if (timer != null) cancel(timer);
    timer = later(() => { timer = null; tick(); }, ms);
  }

  function tick() {
    if (!running) return;
    if (hidden()) { pausedHidden = true; return; }   // 보일 때까지 예약하지 않는다
    run();
  }

  function run() {
    if (!running) return;
    if (pending) { dirty = true; return; }
    if (timer != null) { cancel(timer); timer = null; }
    pending = true;
    const myGen = gen;
    let p;
    try { p = Promise.resolve(fn()); } catch (e) { p = Promise.reject(e); }
    p.then(
      () => settle(myGen, true),
      (e) => settle(myGen, false, e),
    );
  }

  function settle(myGen, ok, err) {
    if (myGen !== gen) return;      // stop() 이후에 끝난 실행
    pending = false;
    if (ok) { failures = 0; lastOkAt = Date.now(); }
    else failures += 1;
    if (onSettled) { try { onSettled(ok, err); } catch (_) { /* 표시 갱신 실패가 폴링을 멈추면 안 된다 */ } }
    if (!running) return;
    if (dirty) { dirty = false; run(); return; }
    schedule(delay());
  }

  function onVisibility() {
    if (!running || hidden()) return;
    // 숨기 전에 이미 실행 중이었다면 그 결과가 곧 온다 — 겹쳐 쏘지 않는다.
    pausedHidden = false;
    if (!pending) run();
  }

  return {
    start() {
      if (running) return;
      running = true;
      gen += 1;
      pending = false; dirty = false; pausedHidden = false;
      if (pauseWhenHidden && doc && doc.addEventListener) doc.addEventListener('visibilitychange', onVisibility);
      if (immediate) tick();
      else schedule(interval);
    },
    stop() {
      if (!running) return;
      running = false;
      gen += 1;
      pending = false; dirty = false; pausedHidden = false;
      if (timer != null) { cancel(timer); timer = null; }
      if (pauseWhenHidden && doc && doc.removeEventListener) doc.removeEventListener('visibilitychange', onVisibility);
    },
    kick() {
      if (!running) return;
      if (hidden()) { pausedHidden = true; return; }
      run();
    },
    state() {
      return { running, pending, failures, lastOkAt };
    },
  };
}
