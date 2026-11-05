// 에이전트 상태 추적 — F4에서 grid.js에서 분리. `/ws-agent` push 채널(연결·
// 재연결·스냅샷/이벤트 파싱)과 "이 세션이 지금 작업 중인가"를 cwd로 찾는 로직
// (Claude Code 훅이 tmux pane을 직접 알려주지 않아 cwd 매칭이 유일한 수단 —
// CLAUDE.md 라이브 프리뷰 항목 참고)을 담는다. 카드/탭에 실제로 클래스를
// 칠하는 로직(_applyActiveHighlights)도 여기 있다 — badges.js(어떤 아이콘을
// 보여줄지)와는 관심사가 다르다: 여긴 "일하는 중이냐"만 본다.
import { apiFetch } from '../core/api.js';
import { API_BASE, WS_BASE, _tokenQuery } from '../core/env.js';
import { relayWsPath } from '../core/host-route.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
import { getSession } from '../core/store.js';
import { applyAgentBadges } from './badges.js';
import { applySnapshot, applyEvent, applyAgentNames } from './state.js';
import { showToast } from '../ui/toast.js';
import { t } from '../core/i18n.js';
import { createPoll } from '../core/poll.js';
import { watchSocketLiveness } from '../core/ws-watchdog.js';

// 로그인 게이트가 실제로 걷히기 전까지 기다린다 — index.html의 hideGate()가
// window.__vtAuthed=true + 'vt:authed' 이벤트로 신호를 준다. 이게 없으면 이
// 파일의 인증 필요 호출(capabilities/safe-mode/ws-agent)이 로그인 폼이 떠 있는
// 동안(=사용자가 비밀번호를 입력하는 시간만큼) 계속 401/403으로 재시도한다.
export function whenAuthed(cb) {
  if (window.__vtAuthed) { cb(); return; }
  document.addEventListener('vt:authed', cb, { once: true });
}

// 음성 기능 설치 여부 확인 → 미설치 시 voice UI 숨김
whenAuthed(() => (async () => {
  try {
    const res = await apiFetch(`${API_BASE}/api/capabilities`);
    const caps = await res.json();
    import('../voice/status-panel.js').then(({ mountVoiceStatus }) => mountVoiceStatus())
      .catch((e) => console.warn('[voice] 상태 패널 로드 실패', e));
    // P2: 열람 가능한 루트가 없으면 코드 뷰어 진입점을 숨긴다.
    // (voice와 달리 return보다 먼저 처리해야 음성 미설치 환경에서도 게이팅이 걸린다)
    if (!caps.fs) {
      document.querySelectorAll('.needs-fs').forEach(el => el.style.display = 'none');
    }
    // P3: lsof 없는 환경이면 포트 대시보드 진입점을 숨긴다.
    if (!caps.ports) {
      document.querySelectorAll('.needs-ports').forEach(el => el.style.display = 'none');
    }
    // P5: 서버에 pywebpush 가 없으면 푸시 토글을 숨긴다.
    // (secure context / iOS PWA 여부는 클라이언트 사정이라 pushui.js가 따로 안내한다)
    if (!caps.push) {
      document.querySelectorAll('.needs-push').forEach(el => el.style.display = 'none');
    }
    // U1: 이제 capabilities가 usage를 돌려준다 — `{available, provider, profiles}`.
    // L4가 예고한 대로 이 파일은 안 고치고도 자동으로 나타나야 했는데, 값이
    // 객체라 truthy가 되면 **소스가 없어도 항목이 뜬다**. `available`을 봐야 한다.
    // N41: 한도형(usage)·누적형(usage_counter)은 독립 소스다 — clauth 없이
    // 로컬 LLM만 쓰는 사용자도 있으므로 **셋 중 하나만 있어도** 사용량 탭은 뜬다.
    // 96번 계획서: Codex(usage_codex)도 같은 원칙의 독립 소스로 추가.
    const usageAvailable = (caps.usage && caps.usage.available)
      || (caps.usage_counter && caps.usage_counter.available)
      || (caps.usage_codex && caps.usage_codex.available);
    if (!usageAvailable) {
      document.querySelectorAll('.needs-usage').forEach(el => el.style.display = 'none');
      // L8: 우측 레일은 body padding으로 자리를 만든다 — 엘리먼트를 숨기는
      // 것만으로는 그 패딩이 안 사라져 오른쪽에 빈 300px가 남는다.
      document.body.classList.add('no-right-rail');
    }
    if (!caps.voice) {
      // (#voice-bar 조회가 있었으나 index.html 에 그런 id 가 없어 항상 null 이었다 — F0에서 제거)
      const ms = document.getElementById('mic-status');
      if (ms) ms.style.display = 'none';
      // 음성 전용/이어폰 메뉴 항목은 voice.js 함수에 의존 → 미설치 시 숨김
      document.querySelectorAll('.needs-voice').forEach(el => el.style.display = 'none');
      return; // voice.js 로드 안 함
    }
  } catch (e) {
    // 서버 통신 실패 시 기본 표시
  }
  // F4: voice.js(classic script)가 voice/ 아래 4개 ES 모듈 + voice/index.js
  // 진입점으로 쪼개졌다. capability 게이팅 패턴(설치 안 됐으면 아예 안 부른다)은
  // 그대로 유지한다. `import()` 동적 임포트 대신 여전히 <script> 태그 주입을
  // 쓰는 이유는 vite.config.js에 적어뒀다 — 요약하면 동적 임포트는 Rollup이
  // 해시 붙은 공유 청크를 만들어 ADR-1(해시 없음)을 깬다. voice/index.js는
  // 별도 lib entry(frontend/dist/voice.js)로 독립 빌드되므로, 그 산출물을
  // type="module" 스크립트로 지연 주입해야 한다. 계획서는 이 트리거를
  // main.js로 옮기라고 했지만, capabilities 응답을 이미 여기서 들고 있어(다시
  // 묻지 않고) 같은 위치에서 바로 이어가는 쪽이 더 단순하다고 판단했다
  // (계획서 대비 편차로 문서에 기록).
  const s = document.createElement('script');
  s.type = 'module';
  s.src = '/static/dist/voice.js';
  // capability는 true인데 산출물(voice.js)이 없거나 깨진 경우(빌드 누락 등,
  // 2026-09-18 실사고) — 지금까지는 콘솔 404만 남고 버튼은 눌리는 채로
  // 방치돼 "눌러도 반응 없음"으로만 보였다. .needs-voice를 그대로 두면
  // 사용자가 계속 죽은 버튼을 누르게 되므로 여기서 끈다.
  s.onerror = () => {
    showToast(t('음성 모듈을 불러오지 못했습니다 — 서버를 다시 빌드/재시작해 보세요.'), 'error');
    document.querySelectorAll('.needs-voice').forEach((el) => { el.disabled = true; });
  };
  document.body.appendChild(s);
})());

// ── 안전 모드 표시 ───────────────────────────────────────────────
whenAuthed(() => {
  apiFetch(`${API_BASE}/api/safe-mode`).then(r => r.json()).then(data => {
    if (data.enabled) {
      const banner = document.createElement('div');
      banner.className = 'vt-banner';
      // D5/D7: 이모지(🛡) 제거 — 배경이 이미 --err(빨강)이라 색으로 심각도가
      // 드러나므로 점이 따로 필요 없다. aria-live로 스크린리더에도 알린다.
      banner.setAttribute('role', 'status');
      banner.setAttribute('aria-live', 'polite');
      banner.textContent = t('안전 모드 — 위험 명령 차단됨');
      document.body.appendChild(banner);
    }
  }).catch(() => {});
});

// cwd로 세션 카드를 찾는다 — agent_event/snapshot 둘 다 cwd 기준.
// Claude Code 훅은 tmux pane을 직접 알려주지 않아 cwd로만 매칭할 수 있는데,
// 같은 디렉토리에 여러 세션이 떠 있으면(둘 다 $HOME 등) cwd가 유일하지 않다 —
// 그럴 땐 아무 데나 강조하는 대신 아무 것도 안 켠다("틀리게 확신"보다 낫다).
//
// L4에서 발견한 버그 수정: `#grid-cards`에만 스코프돼 있었다 — 그 컨테이너는
// L3 4단계에서 그리드 뷰 폐지와 함께 사라졌는데(ADR-7), 이 함수는 계속
// 존재하지 않는 `#grid-cards`만 찾아 항상 조용히 null을 반환했다. 즉 카드
// 쪽 working/done 강조는 그리드 폐지 이후 완전히 죽어 있었다. agent/preview.js의
// ensurePreviewWs onmessage 스코프 버그(L6에서 발견·수정)와 같은 종류 —
// 컨테이너 id에 의존하지 않고 문서 전체에서 찾도록 일반화한다. 이제
// quickopen.js·layout/pane-picker.js·layout/rail.js의 세션 카드가 전부
// 이 강조를 공짜로 받는다.
function _cardByCwd(cwd) {
  if (!cwd) return null;
  const matches = document.querySelectorAll(`.vt-card[data-cwd="${CSS.escape(cwd)}"]`);
  return matches.length === 1 ? matches[0] : null;
}

// T6: 카드의 작업중/완료 표시는 cwd 매칭으로 온다. 카드는 dataset.cwd를
// 목록이 열려 있을 때만 채우므로 tmux 세션명→cwd 매핑을 따로 들고 있어야
// 한다 — 그게 _tmuxCwdByName이다. (세션 탭 줄도 같은 경로를 탔지만 N37
// 3단계에서 사라졌다 — 세션 단위 상태는 A5의 agent/state.js가 소유한다.)
let _tmuxCwdByName = {};
function _setTmuxCwdMap(list) {
  if (!Array.isArray(list)) return;
  const map = {};
  for (const s of list) if (s && s.name) map[s.name] = s.cwd || '';
  _tmuxCwdByName = map;
}

// 실패는 던진다 — 폴러(core/poll.js)가 간격을 늘리고 다음 주기에 다시 본다.
async function _refreshTmuxCwdMap() {
  const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  _setTmuxCwdMap(await res.json());
}

// /ws-agent 스냅샷(agent_snapshot)의 active 목록을 카드/탭 강조에 반영.
// 카드 목록을 연 시점에 이미 도구를 쓰고 있던 세션도 놓치지 않기 위함
// (import한 시점 값이 아니라 매번 최신값을 봐야 하므로 함수로 노출한다).
export let agent_status_active_cache = [];
export function _applyActiveHighlights(active) {
  document.querySelectorAll('.vt-card.working').forEach(c => c.classList.remove('working'));
  (active || []).forEach(a => {
    const card = _cardByCwd(a.cwd);
    if (card) card.classList.add('working');
  });
}

// ── Agent WebSocket — Phase 9 #2: 폴링 대체용 push 채널 + #5: heartbeat/reconnect ─
let wsAgent = null;
let _wsAgentRetries = 0;
let _wsAgentStableTimer = null;
function connectAgentWs() {
  try {
    wsAgent = new WebSocket(`${WS_BASE}${relayWsPath(`/ws-agent${_tokenQuery}`)}`);
  } catch (e) { return scheduleAgentReconnect(); }
  // 생존 감시(core/ws-watchdog.js) — 서버 ping(15초)이 45초 동안 안 오면 닫아 재연결을 돌린다.
  let liveness = null;
  const opened = wsAgent;
  // [회귀 fb827a6와 동일 패턴] 3초 이상 안정적으로 열린 뒤에만 백오프 리셋 —
  // accept 직후 닫히는 flap에서 지수가 자라지 못하고 빠르게 재연결하는 것을 방지.
  wsAgent.onopen = () => {
    liveness?.stop();
    liveness = watchSocketLiveness(opened);
    _sendAgentVisibility();
    clearTimeout(_wsAgentStableTimer);
    _wsAgentStableTimer = setTimeout(() => { _wsAgentRetries = 0; }, 3000);
    // T6: 탭 뱃지의 cwd 매핑을 연결 시점에 한 번 채워둔다 — 그리드를 열기 전에도
    // agent_event가 도착하는 즉시 탭에 매칭될 수 있도록.
    _refreshTmuxCwdMap().catch(() => { /* 폴러가 다음 주기에 다시 본다 */ });
  };
  wsAgent.onmessage = (e) => {
    liveness?.alive();
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.type === 'ping') {
      try { wsAgent.send(JSON.stringify({ type: 'pong' })); } catch (_) {}
    } else if (msg.type === 'agent_status_sync') {
      // A6: 하트비트에 실려 오는 상태 스냅샷. TTL 만료처럼 서버 안에서만
      // 일어나는 전이를 클라이언트가 놓치지 않기 위한 것이라, 상태 스토어만
      // 갱신하고 다른 UI(에이전트 배지 등)는 건드리지 않는다.
      applySnapshot(msg.all || {});
    } else if (msg.type === 'agent_snapshot' || msg.type === 'agents_change') {
      if (msg.agents) {
        applyAgentBadges(msg.agents);
        // 레일·플릿의 에이전트 마크도 같은 값을 쓴다(예전엔 따로 4초 폴링).
        applyAgentNames(msg.agents);
      }
      // A5: 서버가 판정한 4상태를 상태 스토어에 반영한다. 재접속·새로고침
      // 시에도 done/waiting이 복원되는 근거가 이 스냅샷이다(A1이 stop에서
      // 엔트리를 안 지우게 바꾼 이유).
      if (msg.all) applySnapshot(msg.all);
      // 스냅샷에 활성 도구가 있으면 탭 파비콘을 '작업중'으로
      if (window.VTFavicon && msg.active && msg.active.length) VTFavicon.set('working');
      // 그리드를 늦게 열었을 때도(이미 작업 중이던 세션) 카드 강조가 맞도록 캐시+반영.
      agent_status_active_cache = msg.active || [];
      _applyActiveHighlights(agent_status_active_cache);
    } else if (msg.type === 'agent_event') {
      applyEvent(msg.state);
      // 탭 파비콘 상태: pre(도구 시작)=작업중, stop(응답 완료)=완료.
      // post(도구 종료)는 다음 도구가 이어질 수 있어 '작업중' 유지(무시).
      // voice 미설치 환경에서도 stop 신호로 완료 뱃지가 뜬다.
      // 세션 카드도 cwd로 매칭해 같은 규칙(pre=작업중, stop=완료)을 적용한다.
      // (세션 단위 상태의 정본은 A5의 agent/state.js다 — 여기 카드 강조는
      // cwd밖에 모르던 T6 시절 경로가 카드에만 남은 것이다.)
      // 2.2.1 — 도구마다 띄우던 "X 실행 중..." 토스트(2.5초, key:'agent')는
      // 지웠다. 에이전트가 일하는 동안 화면 한구석이 계속 깜빡였는데, 같은
      // 정보를 바로 아래 두 줄(파비콘·세션 카드 강조)과 레일의 에이전트 마크가
      // 이미 실시간으로 말하고 있었다. 토스트는 "지나가면 다시 볼 수 없는 말"에만
      // 쓴다 — 상태는 상태를 보여주는 자리가 맡는다.
      if (msg.state && msg.state.tool) {
        if (window.VTFavicon) VTFavicon.set('working');
        const card = _cardByCwd(msg.state.cwd);
        if (card) { card.classList.add('working'); card.classList.remove('done'); }
      } else if (msg.event === 'stop') {
        if (window.VTFavicon) VTFavicon.set('done');
        const card = _cardByCwd(msg.state && msg.state.cwd);
        if (card) { card.classList.remove('working'); card.classList.add('done'); }
      }
    }
  };
  // 이미 다른 소켓으로 갈아탔으면 옛 소켓의 close가 재연결을 하나 더 만들지 않게 한다.
  const sock = wsAgent;
  sock.onclose = () => {
    liveness?.stop();
    liveness = null;
    if (sock !== wsAgent) return;
    clearTimeout(_wsAgentStableTimer);
    scheduleAgentReconnect();
  };
  sock.onerror = () => { try { sock.close(); } catch (_) {} };
}
// 2026-09-30 — 서버의 에이전트 이름 감시는 보이는 화면이 없으면 쉰다(routes/
// agents.py _agent_hidden). core/workspace-ws.js와 같은 이유로 화면이 알린다.
function _sendAgentVisibility() {
  if (!wsAgent || wsAgent.readyState !== 1) return;
  try { wsAgent.send(JSON.stringify({ type: 'visibility', hidden: !!document.hidden })); } catch (_) { /* 다음 전환 때 다시 */ }
}
document.addEventListener('visibilitychange', _sendAgentVisibility);

// 2026-10-15 — 예전엔 15번 실패하면 **영원히** 멈췄다(30초 상한이라 약 6분). 폰이 잠들거나
// 서버가 그만큼 내려가 있으면 이후 상태가 새로고침 전까지 얼어붙었다. 이제 상한 간격으로
// 계속 시도하고, 화면이 다시 보이거나 네트워크가 돌아오면 기다리지 않고 바로 붙는다.
let _wsAgentTimer = null;
function scheduleAgentReconnect() {
  if (_wsAgentTimer) return;
  _wsAgentRetries = Math.min(_wsAgentRetries + 1, 15);
  const delay = Math.min(1000 * Math.pow(2, _wsAgentRetries), 30000);
  _wsAgentTimer = setTimeout(() => { _wsAgentTimer = null; connectAgentWs(); }, delay);
}
function _reconnectAgentNow() {
  if (document.hidden || !_authed) return;
  if (wsAgent && (wsAgent.readyState === 0 || wsAgent.readyState === 1)) return;
  clearTimeout(_wsAgentTimer);
  _wsAgentTimer = null;
  _wsAgentRetries = 0;
  connectAgentWs();
}
let _authed = false;
whenAuthed(() => { _authed = true; });
document.addEventListener('visibilitychange', _reconnectAgentNow);
window.addEventListener('online', _reconnectAgentNow);

whenAuthed(connectAgentWs);
// T6: 탭 뱃지가 그리드를 열지 않아도 최신 cwd를 알도록 한다. 2026-09-30부터
// 정본은 `/ws-workspace`의 `tmux_sessions` push(목록이 바뀔 때만 온다)이고,
// 20초 폴링은 그 채널이 끊긴 동안을 위한 60초 안전망으로 줄였다.
whenAuthed(() => {
  onWorkspaceEvent('tmux_sessions', (msg) => _setTmuxCwdMap(msg.sessions));
  // 이전 조회가 안 끝났으면 건너뛰고, 실패하면 간격을 늘린다. 숨은 탭에서는 쉰다.
  createPoll(_refreshTmuxCwdMap, { interval: 60000 }).start();
});
