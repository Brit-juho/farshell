// 작업 완료 알림 WebSocket — F4에서 voice.js에서 분리. `/ws-notify`로 오는
// task_complete(TTS blob 또는 요약 텍스트)·clipboard_push(맥 클립보드 동기화)를
// 처리한다. `copyToClipboard`/`showToast`는 아직 classic script(term/clipboard.js·
// ui/toast.js)가 소유해 bare identifier로 읽는다.
import { get as setting } from '../core/settings.js';
import { playAudioBlob } from './tts.js';

const _vToken = new URLSearchParams(location.search).get('token') || '';
const _vTokenQ = _vToken ? `?token=${_vToken}` : '';
const WS_NOTIFY = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws-notify${_vTokenQ}`;

let notifyWs = null;
let pendingMeta = null;
let _notifyRetries = 0;
let _notifyStableTimer = null;

export function connectNotify() {
  // C2: 상한 도달 후 영구 포기하지 않는다(모바일 장시간 세션의 flap에도 알림 유지).
  // [회귀 fb827a6] 단, onopen에서 _notifyRetries를 '즉시' 0으로 리셋하면 서버가 accept
  // 직후 닫는 half-open flap에서 지수 백오프가 자라지 못해 2초마다 영구 재연결한다
  // (터미널 WS와 동일한 메모리 스톰). 3초 이상 안정적으로 열린 뒤에만 리셋한다.
  notifyWs = new WebSocket(WS_NOTIFY);

  notifyWs.onopen = () => {
    clearTimeout(_notifyStableTimer);
    _notifyStableTimer = setTimeout(() => { _notifyRetries = 0; }, 3000);
  };

  notifyWs.onmessage = (e) => {
    // 메시지 처리 중 어떤 예외(잘못된 JSON, Notification 생성자 throw 등)가 나도
    // notify 채널 전체가 죽지 않도록 방어한다.
    try {
      if (typeof e.data === 'string') {
        const data = JSON.parse(e.data);
        console.log('[NOTIFY] received:', data.type, data.summary?.slice(0, 60));
        if (data.type === 'task_complete') {
          pendingMeta = data;
          showNotification(data.summary, data.session_id);
          // 탭 파비콘 '완료' 뱃지 — 탭 재포커스 시 favicon.js가 자동 해제
          if (window.VTFavicon) VTFavicon.set('done');
        } else if (data.type === 'clipboard_push' && data.text) {
          // 맥북(서버) 쪽 clipboard_daemon.py가 감지한 시스템 클립보드 변경 —
          // copyToClipboard는 term/clipboard.js가 전역(window 브리지)으로 노출.
          // "⋯ → 설정 → 드래그 시 자동 복사"를 꺼도 이 경로(fsh clip)만 안 막히면
          // 여전히 자동으로 클립보드가 덮어써지므로 같은 설정을 공유한다.
          const autoSyncOff = !setting('mouse.autocopyOnSelect');
          if (!autoSyncOff && typeof copyToClipboard === 'function') {
            copyToClipboard(data.text).then((ok) => {
              if (ok && typeof showToast === 'function') showToast('클립보드 동기화됨');
            });
          }
        }
      } else if (e.data instanceof Blob) {
        console.log('[NOTIFY] audio blob:', e.data.size, 'bytes');
        if (e.data.size > 0) {
          playAudioBlob(e.data);
        }
        pendingMeta = null;
      }
    } catch (err) {
      console.warn('[NOTIFY] 메시지 처리 오류(무시):', err);
    }
  };

  notifyWs.onclose = (ev) => {
    clearTimeout(_notifyStableTimer);
    // 인증 실패(4001)는 재시도해도 동일 → 재연결 중단.
    if (ev && ev.code === 4001) { console.warn('[NOTIFY] 인증 실패 — 재연결 중단'); return; }
    // [M2] 지수 백오프 재연결 (무한 — 지수는 5로 clamp해 오버플로 방지)
    _notifyRetries++;
    const delay = Math.min(1000 * Math.pow(2, Math.min(_notifyRetries, 5)), 30000);
    setTimeout(connectNotify, delay);
  };

  notifyWs.onerror = () => {
    notifyWs.close();
  };
}

function showNotification(summary, sessionId) {
  // 요약 텍스트 (최대 100자). summary가 없을 수도 있으므로 문자열로 정규화.
  const text = summary == null ? '' : String(summary);
  const short = text.length > 100 ? text.slice(0, 100) + '...' : text;
  // D6: 예전엔 이 함수가 #vt-toasts 컨테이너를 거치지 않고 body에 .vt-toast를
  // 직접 붙인 뒤 인라인 style로 페이드아웃했다 — ui/toast.js(F5 통합본)와
  // 별개의 세 번째 토스트 구현이었다. showToast는 voice 번들에서도 bare 전역으로
  // 이미 쓰고 있으므로(clipboard_push 분기 참고) 그대로 재사용한다.
  showToast(`[${sessionId}] ${short}`, 'success', { duration: 5000 });

  // 백그라운드 브라우저 알림 (화면 밖에서도 보이게)
  showBrowserNotification('FarShell — 작업 완료', short);
}

// ⚠ Android Chrome 등 모바일 브라우저는 `new Notification()` 생성자를 금지하고
// (TypeError: Failed to construct 'Notification': Illegal constructor)
// ServiceWorkerRegistration.showNotification()만 허용한다. 예전엔 권한 수락 직후
// 첫 task_complete에서 이 생성자가 던지면 notify 처리 전체가 깨졌다(=수락하면 멈춤).
// SW 경로를 우선 쓰고, 모든 경로를 try/catch로 감싸 어떤 플랫폼에서도 예외가 새지 않게 한다.
function showBrowserNotification(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready
        .then((reg) => reg.showNotification(title, { body }))
        .catch(() => { try { new Notification(title, { body }); } catch (_) {} });
      return;
    }
    new Notification(title, { body });
  } catch (_) { /* 알림 생성 실패는 무시 — 화면 토스트로 이미 알렸다 */ }
}

// 알림 WebSocket 연결
connectNotify();

// 알림 권한은 여기서 미리 요청하지 않는다 — 브라우저는 권한을 한 번만 물어보므로,
// 사용자가 "푸시 알림"을 켜기도 전에 아무 탭에서나 먼저 소진시키면 나중에 진짜로
// 켜려 할 때 다시 물어볼 방법이 없다. ⋯ 메뉴 "푸시 알림" 토글(pushui.js → togglePush()
// → VTPush.subscribe())에서만 요청한다. showBrowserNotification()은 권한 미승인 시
// 조용히 스킵하므로 여기서 미리 받아둘 필요가 없다.

// --- PWA Service Worker 등록 ---
// (P5) 등록은 push/swreg.js 로 옮겼다. 이 파일은 음성 미설치 환경에서 아예 로드되지
// 않으므로(agent/status.js가 capabilities를 보고 결정), 여기 두면 SW가 등록조차 안 됐다.
// → PWA 오프라인 캐시도, Web Push도 음성 설치 여부에 인질로 잡혀 있었다.
