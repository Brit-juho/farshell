// 재연결 오버레이 UI. F4에서 terminal.js(구 :1358-1464)에서 분리.
import { getSession, activeSessionId } from '../core/store.js';
import { icon } from '../ui/icons.js';

// M9: wifi↔LTE 전환 같은 순간적 망 전환은 보통 1~2초면 스스로 재연결된다.
// 예전엔 첫 onclose에서 곧바로 전체 화면 오버레이를 띄워, 그런 찰나의 끊김도
// 매번 화면을 덮었다 사라지길 반복해 거슬렸다. Mosh는 이런 순간 끊김을 아예
// 티 안 나게 처리하는 게 원칙 — 여기서도 GRACE_MS 안에 재연결되면 오버레이
// 자체를 띄우지 않고 조용히 넘어간다. 작은 상태 pill(#conn-status)은 즉시
// 갱신 — 방해되지 않는 수준이라 굳이 늦출 이유가 없다.
const CONN_OVERLAY_GRACE_MS = 1500;
let _connOverlayTimer = null;
let _connOverlayTickTimer = null;

function _fmtElapsed(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60), s = total % 60;
  return m > 0 ? `${m}분 ${s}초 경과` : `${s}초 경과`;
}

// O1: 오버레이가 떠 있는 동안 1초마다 경과 시간을 갱신 — Wave의
// connstatusoverlay.tsx가 "얼마나 기다렸는지" 실시간으로 보여주는 것과 같은 이유.
// 그래야 "재연결 시도 중..."만 보고 언제까지 기다려야 할지 감이 안 오는 문제가 풀린다.
function _tickConnOverlay(id) {
  const overlay = document.getElementById('conn-overlay');
  if (!overlay) { clearInterval(_connOverlayTickTimer); _connOverlayTickTimer = null; return; }
  const s = getSession(id);
  const el = overlay.querySelector('.vt-ov-elapsed');
  if (el && s && s._disconnectedAt) el.textContent = _fmtElapsed(Date.now() - s._disconnectedAt);
}

// O1: "끊기" — 무한 지수 백오프 재시도를 사용자가 직접 멈출 수 있게 한다(예: 서버가
// 한동안 안 뜰 걸 아는 상황에서 계속 재시도 소리/배터리 낭비를 막고 싶을 때).
// 다시 누르면(라벨이 "다시 연결"로 바뀜) 즉시(백오프 지연 없이) 재연결을 시도한다.
export function _toggleReconnectStop(id) {
  const s = getSession(id);
  const overlay = document.getElementById('conn-overlay');
  if (!s || !overlay) return;
  const sub = overlay.querySelector('.vt-ov-sub');
  const btn = overlay.querySelector('.vt-ov-stop-btn');
  if (!s._reconnectStopped) {
    s._reconnectStopped = true;
    if (s.reconnTimer) { clearTimeout(s.reconnTimer); s.reconnTimer = null; }
    overlay.classList.add('stopped');
    if (sub) sub.textContent = '자동 재연결이 중단되었습니다.';
    if (btn) btn.textContent = '다시 연결';
  } else {
    s._reconnectStopped = false;
    overlay.classList.remove('stopped');
    if (sub) sub.textContent = '자동 재연결 시도 중...';
    if (btn) btn.textContent = '끊기';
    if (typeof s._reconnectNow === 'function') s._reconnectNow();
  }
}

function _removeConnOverlay() {
  const overlay = document.getElementById('conn-overlay');
  if (overlay) overlay.remove();
  if (_connOverlayTickTimer) { clearInterval(_connOverlayTickTimer); _connOverlayTickTimer = null; }
}

export function updateConnStatus(id, connected) {
  const el = document.getElementById('conn-status');
  let overlay = document.getElementById('conn-overlay');
  if (!connected && id === activeSessionId()) {
    el.textContent = '서버 연결 끊김';
    el.className = 'disconnected';
    const s0 = getSession(id);
    if (s0 && !s0._disconnectedAt) s0._disconnectedAt = Date.now();
    if (!overlay && !_connOverlayTimer) {
      _connOverlayTimer = setTimeout(() => {
        _connOverlayTimer = null;
        // 유예 시간 동안 이미 재연결됐으면(다른 분기가 정리했으면) 아무 것도 안 함.
        const s = getSession(id);
        if (document.getElementById('conn-overlay')) return;
        if (!s || (s.ws && s.ws.readyState === WebSocket.OPEN)) return;
        const ov = document.createElement('div');
        ov.id = 'conn-overlay';
        ov.className = 'vt-overlay';
        ov.setAttribute('role', 'status');
        ov.setAttribute('aria-live', 'polite');
        ov.innerHTML = `
          <div class="vt-ov-icon">${icon('wifi-off', 36)}</div>
          <div class="vt-ov-title">서버 연결 끊김</div>
          <div class="vt-ov-sub">자동 재연결 시도 중...</div>
          <div class="vt-ov-elapsed"></div>
          <button type="button" class="vt-ov-stop-btn">끊기</button>
        `;
        document.body.appendChild(ov);
        ov.querySelector('.vt-ov-stop-btn').addEventListener('click', () => _toggleReconnectStop(id));
        _tickConnOverlay(id);
        clearInterval(_connOverlayTickTimer);
        _connOverlayTickTimer = setInterval(() => _tickConnOverlay(id), 1000);
      }, CONN_OVERLAY_GRACE_MS);
    }
  } else {
    el.className = '';
    if (_connOverlayTimer) { clearTimeout(_connOverlayTimer); _connOverlayTimer = null; }
    _removeConnOverlay();
    const s = getSession(id);
    if (s) { s._disconnectedAt = null; s._reconnectStopped = false; }
  }
}

// R1: 재연결 시도 횟수/중단 사유 같은 상태 디테일은 예전엔 term.write()로 터미널
// 스크롤백에 직접 찍어 영구히 남았다. updateConnStatus()가 이미 띄워둔 오버레이의
// 서브텍스트만 갱신 — 실제 세션 출력과 분리된다(ttyd·wetty와 같은 패턴).
export function _setConnOverlayDetail(id, text) {
  if (id !== activeSessionId()) return;
  const overlay = document.getElementById('conn-overlay');
  const sub = overlay && overlay.querySelector('.vt-ov-sub');
  if (sub) sub.textContent = text;
}
