// WebSocket 생존 감시 — 아무 메시지도 안 오는 "열린" 소켓을 닫는다.
//
// ## 왜 필요한가 (서버가 연결은 받는데 답을 안 하던 사고)
//
// 반쯤 열린(half-open) 연결이나 멎은 서버는 onclose를 영영 부르지 않는다. 그래서
// 터미널·에이전트·워크스페이스 소켓이 전부 "연결됨"으로 보였고, 재연결 백오프와
// 연결 끊김 오버레이(term/conn-overlay.js) 어느 쪽도 돌지 않았다.
//
// 서버는 `/ws/{id}`·`/ws-agent`·`/ws-workspace`·`/ws-fs-watch`에 15초마다
// `{"type":"ping"}`을 보낸다. 그러니 조용한 터미널이라도 15초마다 무언가는 온다 —
// 45초(세 번) 동안 **아무것도** 안 왔으면 연결이 죽은 것으로 보고 닫는다.
// 닫으면 각 소켓의 기존 onclose → 백오프 → 재연결 경로가 그대로 돈다.
//
// 죽은 연결에서는 close()의 닫기 핸드셰이크도 끝나지 않을 수 있다(브라우저가
// close 이벤트를 한참 뒤에야 주거나 안 준다). 그래서 CLOSE_GRACE_MS 안에 onclose가
// 안 불리면 핸들러를 떼어 낸 뒤 **한 번만** 직접 부른다 — 나중에 진짜 close가 와도
// 핸들러가 없으니 재연결이 두 번 걸리지 않는다.
//
// 쓰는 쪽 계약: onopen에서 만들고, 메시지마다 alive(), onclose에서 stop().

/** 이만큼 아무 메시지도 없으면 닫는다(서버 ping 15초 × 3). */
export const WS_SILENCE_TIMEOUT_MS = 45000;
/** 클라이언트가 생존 감시로 닫을 때의 코드(4000번대는 앱 정의 영역). */
export const WS_HEARTBEAT_CLOSE_CODE = 4000;
export const WS_HEARTBEAT_CLOSE_REASON = 'client heartbeat timeout';
/** close() 뒤 onclose를 기다리는 시간. 넘기면 직접 부른다. */
export const CLOSE_GRACE_MS = 3000;

/**
 * @param {WebSocket | any} ws
 * @param {{ timeout?: number, grace?: number, later?: (fn: () => void, ms: number) => any, cancel?: (id: any) => void }} [opts]
 * @returns {{ alive: () => void, stop: () => void, expired: () => boolean }}
 */
export function watchSocketLiveness(ws, opts = {}) {
  const timeout = opts.timeout > 0 ? opts.timeout : WS_SILENCE_TIMEOUT_MS;
  const grace = opts.grace > 0 ? opts.grace : CLOSE_GRACE_MS;
  const later = opts.later || ((fn, ms) => setTimeout(fn, ms));
  const cancel = opts.cancel || ((id) => clearTimeout(id));

  let timer = null;
  let graceTimer = null;
  let stopped = false;
  let expired = false;

  function arm() {
    if (timer != null) cancel(timer);
    timer = later(onSilent, timeout);
  }

  function onSilent() {
    timer = null;
    if (stopped) return;
    expired = true;
    try { ws.close(WS_HEARTBEAT_CLOSE_CODE, WS_HEARTBEAT_CLOSE_REASON); } catch (_) { /* 이미 닫히는 중 */ }
    if (stopped) return;   // close()가 onclose를 동기로 불렀다(테스트 대역 등)
    graceTimer = later(() => {
      graceTimer = null;
      if (stopped) return;
      stopped = true;
      const handler = ws.onclose;
      // 떼어 낸 뒤 부른다 — 나중에 진짜 close 이벤트가 와도 재연결을 또 걸지 않게.
      ws.onclose = null;
      ws.onmessage = null;
      ws.onerror = null;
      if (typeof handler === 'function') {
        try {
          handler.call(ws, { code: WS_HEARTBEAT_CLOSE_CODE, reason: WS_HEARTBEAT_CLOSE_REASON, wasClean: false });
        } catch (_) { /* 쓰는 쪽 오류가 감시를 깨면 안 된다 */ }
      }
    }, grace);
  }

  arm();
  return {
    alive() {
      if (stopped || expired) return;
      arm();
    },
    stop() {
      stopped = true;
      if (timer != null) { cancel(timer); timer = null; }
      if (graceTimer != null) { cancel(graceTimer); graceTimer = null; }
    },
    expired: () => expired,
  };
}
