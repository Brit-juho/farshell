// 세션 WebSocket 수명주기 (초기 연결 + 자동 재연결 통합) + term.onData 배선 +
// 리사이즈 리스너 배선. F4에서 addSession(구 terminal.js :863-1017 부근)에서 분리.
//
// [회귀 fb827a6] 재연결 상한(retries>=15)을 없애 무한 재시도로 바꾸면서, onopen에서
// retries를 '즉시' 0으로 리셋하는 로직을 그대로 뒀다. 서버는 세션이 없으면
// `accept()` 직후 code 4004로 닫는데(=half-open flap), 이때 onopen이 먼저 발화해
// retries가 0으로 리셋된다 → 지수 백오프가 절대 자라지 못하고 2초마다 영구 재연결.
// localStorage 워크스페이스가 복원한 죽은 세션 탭들이 서버 재시작 후 이 스톰에 빠지면
// 매 2초 소켓 생성 + scrollback 재주입 + 접근성 DOM 재도색으로 Chrome 메모리가 폭증한다.
//
// 수정: (1) 4001/4004 같은 '재시도해도 동일'한 코드는 재연결하지 않고 중단.
//       (2) 연결이 STABLE_MS 이상 안정적으로 유지된 뒤에만 백오프 카운터를 리셋.
import { getSession } from '../core/store.js';
import { WS_BASE } from '../core/env.js';
import { E2E_ENABLED, wrapE2E, _wsQuery } from './e2e.js';
import { fitAndResize } from './resize.js';
import { updateConnStatus, _setConnOverlayDetail } from './conn-overlay.js';
import { applyStickyMod } from './keybar.js';

const TERMINAL_CLOSE_CODES = new Set([4001, 4004]);
const STABLE_MS = 3000;

// addSession에서 term.open() 직후 호출. onResize 함수를 반환한다 —
// removeSession이 window.removeEventListener로 해제할 수 있어야 하기 때문.
export function startSessionSocket(id, term) {
  // WebSocket URL 구성 — E2E 활성 시 ?e2e=1 (토큰 있으면 ?token=...&e2e=1)
  const _wsPath = `/ws/${id}${_wsQuery()}`;
  let _retries = 0;
  let _stableTimer = null;

  function connectTerminalWs() {
    if (!getSession(id)) return;
    const sock = new WebSocket(`${WS_BASE}${_wsPath}`);
    sock.binaryType = 'arraybuffer';
    getSession(id).ws = sock;

    sock.onopen = () => {
      updateConnStatus(id, true);
      // 재연결이었다면(첫 연결이 아니면) 서버가 scrollback(최대 256KB)을 통째로 재전송한다.
      // reset 없이 write하면 이전 출력이 중복 누적되므로 비운 뒤 깨끗하게 repaint한다.
      // R1: "[재연결됨]" 상태 문구는 예전엔 term.write()로 찍어 스크롤백에 영구히
      // 남았다(ttyd·wetty처럼 오버레이로 분리) — updateConnStatus가 이미 오버레이를
      // 지워 "연결됨"을 보여주므로 별도 텍스트 없이 reset만 한다.
      if (_retries > 0) { term.reset(); }
      // 새(재)연결된 PTY는 크기를 모르므로 캐시를 비워 첫 fitAndResize가 반드시 보내게 한다.
      // _lastFitW/H도 같이 비워야 한다 — 컨테이너 픽셀 크기가 그대로면 fitAndResize의
      // fit() 자체를 건너뛰어 sendResize까지 도달 못 하고 새 PTY에 크기를 못 알린다.
      const s = getSession(id);
      s._lastCols = s._lastRows = null;
      s._lastFitW = s._lastFitH = null;
      if (!E2E_ENABLED) fitAndResize(id);
      // STABLE_MS 이상 열려 있어야 백오프를 리셋 — 즉시 리셋하면 accept 직후 닫히는
      // flap에서 지수가 자라지 못해 무한 재연결 스톰이 된다.
      clearTimeout(_stableTimer);
      _stableTimer = setTimeout(() => { _retries = 0; }, STABLE_MS);
    };

    // Phase 8 G2: 서버 ping 응답 (heartbeat pong)
    sock.addEventListener('message', (e) => {
      if (typeof e.data !== 'string') return;
      try {
        const msg = JSON.parse(e.data);
        if (msg && msg.type === 'ping') sock.send(JSON.stringify({ type: 'pong' }));
      } catch (_) { /* binary or non-JSON */ }
    });

    // R2: 서버 pause_read는 WS 송신 큐(_on_data) 크기만 본다 — 네트워크로는
    // 다 나갔는데 xterm.js 렌더링(특히 저사양 모바일)이 못 따라가는 상황은
    // 못 잡는다. ttyd의 writeData() 패턴처럼 xterm write() 완료 콜백으로 실제
    // 렌더링 진행 상황을 재고, 밀리면 별도 render_pause/resume 신호를 보낸다
    // (서버는 이걸 큐 기반 pause와 독립된 requester로 취급 — 둘 다 풀려야 재개).
    let _pendingWrites = 0;
    let _renderPaused = false;
    const RENDER_PAUSE_HIGH = 8;
    const RENDER_PAUSE_LOW = 2;
    // wrapE2E 가 핸드셰이크 후 handle 을 넘김. E2E 비활성이면 즉시 실행.
    wrapE2E(sock,
      (handle) => {
        getSession(id).wsHandle = handle;
        if (E2E_ENABLED) fitAndResize(id);
      },
      (bytes) => {
        _pendingWrites++;
        if (_pendingWrites > RENDER_PAUSE_HIGH && !_renderPaused) {
          _renderPaused = true;
          try { sock.send(JSON.stringify({ type: 'render_pause' })); } catch (_) {}
        }
        term.write(bytes, () => {
          _pendingWrites--;
          if (_pendingWrites < RENDER_PAUSE_LOW && _renderPaused) {
            _renderPaused = false;
            try { sock.send(JSON.stringify({ type: 'render_resume' })); } catch (_) {}
          }
        });
      }
    );

    sock.onclose = (ev) => {
      clearTimeout(_stableTimer);
      // ⚠ 탭을 사용자가 직접 닫은 경우(removeSession이 세션을 이미 제거)에도
      // 이 close 이벤트가 큐잉돼 나중에 실행된다. 예전엔 이 체크보다 먼저
      // updateConnStatus(id, false)를 불러 "서버 연결 끊김" 전체 화면 오버레이가
      // 잠깐이라도 무조건 떴다 — 의도적으로 닫은 건데 마치 네트워크가 끊긴 것처럼
      // 보였다. 탭 닫힘 여부를 먼저 확인해 그 경우엔 아예 아무 것도 안 한다.
      const s = getSession(id);
      if (!s) return;                                       // 탭 닫힘 → 중단
      updateConnStatus(id, false);
      const code = ev && ev.code;
      if (TERMINAL_CLOSE_CODES.has(code)) {                  // 영구 실패 → 재연결 안 함
        const why = code === 4001 ? '인증 실패' : '세션이 서버에 없음(종료됨)';
        _setConnOverlayDetail(id, `재연결 중단 — ${why}. 탭을 닫고 새로 여세요.`);
        return;
      }
      _retries++;
      // M9: 첫 재시도는 기존에 1000ms 밑변으로 시작해 최소 2초를 기다렸다 —
      // wifi↔LTE 전환 같은 순간적 끊김조차 늘 2초+ 재연결 지연을 강제해,
      // updateConnStatus의 오버레이 유예(CONN_OVERLAY_GRACE_MS=1.5초)가
      // 사실상 항상 만료되고 오버레이가 뜬 뒤였다. 밑변을 250ms로 낮춰
      // 첫 재시도(500ms)가 유예 시간 안에 들어오게 했다 — 지속되는 장애에는
      // 여전히 지수적으로 늘어나 30초 상한까지 백오프하므로 폭풍 재발 위험은 없음.
      // Math.pow(2, retries)는 지수가 커지면 오버플로하므로 지수를 7로 clamp.
      const delay = Math.min(250 * Math.pow(2, Math.min(_retries, 7)), 30000);
      _setConnOverlayDetail(id, `재연결 시도 중... (${_retries}회)`);
      // O1: "끊기"를 누른 상태면 사용자가 멈춘 것 — 다음 타이머를 새로 걸지 않는다.
      // "다시 연결"을 누르면 _reconnectNow()가 이 delay를 기다리지 않고 즉시 재시도한다.
      if (!s._reconnectStopped) {
        s.reconnTimer = setTimeout(connectTerminalWs, delay);
      }
    };

    sock.onerror = () => { try { sock.close(); } catch (_) {} };
  }

  getSession(id)._reconnectNow = connectTerminalWs;
  connectTerminalWs();

  term.onData((data) => {
    const handle = getSession(id)?.wsHandle;
    if (handle && handle.readyState === WebSocket.OPEN) {
      // keybar의 sticky Ctrl이 armed면 소프트 키보드로 친 문자에 Ctrl 조합 적용.
      handle.send(new TextEncoder().encode(applyStickyMod(data)));
    }
  });

  // 리사이즈 디바운스 — 모바일 키보드가 뜨고/닫히거나 viewport가 흔들리면 resize가
  // 연속으로 쏟아진다. 매 이벤트마다 fit+sendResize하면 PTY가 SIGWINCH 폭탄을 맞아
  // TUI가 계속 전체 재도색(대량 출력)을 하고, 입력 중 메모리가 급증한다. 120ms로 합친다.
  let _resizeTimer = null;
  const onResize = () => {
    clearTimeout(_resizeTimer);
    _resizeTimer = setTimeout(() => fitAndResize(id), 120);
  };
  window.addEventListener('resize', onResize);
  // 모바일: visualViewport resize (키보드 나타날 때)
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', onResize);
  }
  return onResize;
}
