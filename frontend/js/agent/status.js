// 에이전트 상태 추적 — F4에서 grid.js에서 분리. `/ws-agent` push 채널(연결·
// 재연결·스냅샷/이벤트 파싱)과 "이 세션이 지금 작업 중인가"를 cwd로 찾는 로직
// (Claude Code 훅이 tmux pane을 직접 알려주지 않아 cwd 매칭이 유일한 수단 —
// CLAUDE.md 라이브 프리뷰 항목 참고)을 담는다. 카드/탭에 실제로 클래스를
// 칠하는 로직(_applyActiveHighlights)도 여기 있다 — badges.js(어떤 아이콘을
// 보여줄지)와는 관심사가 다르다: 여긴 "일하는 중이냐"만 본다.
import { apiFetch } from '../core/api.js';
import { API_BASE, WS_BASE, _tokenQuery } from '../core/env.js';
import { getSession } from '../core/store.js';
import { applyAgentBadges } from './badges.js';

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
      banner.textContent = '안전 모드 — 위험 명령 차단됨';
      document.body.appendChild(banner);
    }
  }).catch(() => {});
});

// cwd로 그리드 카드를 찾는다 — agent_event/snapshot 둘 다 cwd 기준.
// Claude Code 훅은 tmux pane을 직접 알려주지 않아 cwd로만 매칭할 수 있는데,
// 같은 디렉토리에 여러 세션이 떠 있으면(둘 다 $HOME 등) cwd가 유일하지 않다 —
// 그럴 땐 아무 데나 강조하는 대신 아무 것도 안 켠다("틀리게 확신"보다 낫다).
export function _cardByCwd(cwd) {
  if (!cwd) return null;
  const cards = document.getElementById('grid-cards');
  if (!cards) return null;
  const matches = cards.querySelectorAll(`.vt-card[data-cwd="${CSS.escape(cwd)}"]`);
  return matches.length === 1 ? matches[0] : null;
}

// T6: 탭도 카드와 같은 방식(cwd 매칭)으로 작업중/완료 표시를 받는다. 카드는
// dataset.cwd를 그리드가 열려 있을 때만 채우므로(refreshGrid), 그리드를 한
// 번도 안 연 상태에서도 탭 뱃지가 동작하려면 tmux 세션명→cwd 매핑을 따로
// 들고 있어야 한다 — 그게 _tmuxCwdByName이다.
let _tmuxCwdByName = {};
export async function _refreshTmuxCwdMap() {
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    const list = await res.json();
    if (!Array.isArray(list)) return;
    const map = {};
    for (const s of list) if (s.name) map[s.name] = s.cwd || '';
    _tmuxCwdByName = map;
  } catch (_) { /* 다음 주기에 재시도 */ }
}

export function _tabByCwd(cwd) {
  if (!cwd) return null;
  const matches = [];
  document.querySelectorAll('#tabs .tab').forEach((tab) => {
    const sess = getSession(tab.dataset.sessionId);
    const tmuxName = sess && (sess.tmux_name || sess.tmuxName);
    if (tmuxName && _tmuxCwdByName[tmuxName] === cwd) matches.push(tab);
  });
  return matches.length === 1 ? matches[0] : null;
}

// /ws-agent 스냅샷(agent_snapshot)의 active 목록을 카드/탭 강조에 반영.
// 그리드를 연 시점에 이미 도구를 쓰고 있던 세션도 놓치지 않기 위함.
// preview.js의 refreshGrid가 그리드를 늦게 열었을 때 이 캐시로 즉시 반영한다
// (import한 시점 값이 아니라 매번 최신값을 봐야 하므로 함수로 노출한다).
export let agent_status_active_cache = [];
export function _applyActiveHighlights(active) {
  document.querySelectorAll('.vt-card.working').forEach(c => c.classList.remove('working'));
  document.querySelectorAll('#tabs .tab.working').forEach(t => t.classList.remove('working'));
  (active || []).forEach(a => {
    const card = _cardByCwd(a.cwd);
    if (card) card.classList.add('working');
    const tab = _tabByCwd(a.cwd);
    if (tab) tab.classList.add('working');
  });
}

// ── Agent WebSocket — Phase 9 #2: 폴링 대체용 push 채널 + #5: heartbeat/reconnect ─
let wsAgent = null;
let _wsAgentRetries = 0;
let _wsAgentStableTimer = null;
export function connectAgentWs() {
  try {
    wsAgent = new WebSocket(`${WS_BASE}/ws-agent${_tokenQuery}`);
  } catch (e) { return scheduleAgentReconnect(); }
  // [회귀 fb827a6와 동일 패턴] 3초 이상 안정적으로 열린 뒤에만 백오프 리셋 —
  // accept 직후 닫히는 flap에서 지수가 자라지 못하고 빠르게 재연결하는 것을 방지.
  wsAgent.onopen = () => {
    clearTimeout(_wsAgentStableTimer);
    _wsAgentStableTimer = setTimeout(() => { _wsAgentRetries = 0; }, 3000);
    // T6: 탭 뱃지의 cwd 매핑을 연결 시점에 한 번 채워둔다 — 그리드를 열기 전에도
    // agent_event가 도착하는 즉시 탭에 매칭될 수 있도록.
    _refreshTmuxCwdMap();
  };
  wsAgent.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.type === 'ping') {
      try { wsAgent.send(JSON.stringify({ type: 'pong' })); } catch (_) {}
    } else if (msg.type === 'agent_snapshot' || msg.type === 'agents_change') {
      if (msg.agents) applyAgentBadges(msg.agents);
      // 스냅샷에 활성 도구가 있으면 탭 파비콘을 '작업중'으로
      if (window.VTFavicon && msg.active && msg.active.length) VTFavicon.set('working');
      // 그리드를 늦게 열었을 때도(이미 작업 중이던 세션) 카드 강조가 맞도록 캐시+반영.
      agent_status_active_cache = msg.active || [];
      _applyActiveHighlights(agent_status_active_cache);
    } else if (msg.type === 'agent_event') {
      // 탭 파비콘 상태: pre(도구 시작)=작업중, stop(응답 완료)=완료.
      // post(도구 종료)는 다음 도구가 이어질 수 있어 '작업중' 유지(무시).
      // voice 미설치 환경에서도 stop 신호로 완료 뱃지가 뜬다.
      // 그리드 카드/탭도 cwd로 매칭해 같은 규칙(pre=작업중, stop=완료)을 적용한다.
      // T6: 그리드를 안 열어도 탭만 보고 승인 대기 세션을 찾을 수 있어야 하므로
      // 카드와 동일하게 탭에도 working/done class를 건다.
      if (msg.state && msg.state.tool) {
        showToast(`${msg.state.tool} 실행 중...`, 'info', { key: 'agent', duration: 2500 });
        if (window.VTFavicon) VTFavicon.set('working');
        const card = _cardByCwd(msg.state.cwd);
        if (card) { card.classList.add('working'); card.classList.remove('done'); }
        const tab = _tabByCwd(msg.state.cwd);
        if (tab) { tab.classList.add('working'); tab.classList.remove('done'); }
      } else if (msg.event === 'stop') {
        if (window.VTFavicon) VTFavicon.set('done');
        const card = _cardByCwd(msg.state && msg.state.cwd);
        if (card) { card.classList.remove('working'); card.classList.add('done'); }
        const tab = _tabByCwd(msg.state && msg.state.cwd);
        if (tab) { tab.classList.remove('working'); tab.classList.add('done'); }
      }
    }
  };
  wsAgent.onclose = () => { clearTimeout(_wsAgentStableTimer); scheduleAgentReconnect(); };
  wsAgent.onerror = () => { try { wsAgent.close(); } catch (_) {} };
}
export function scheduleAgentReconnect() {
  if (_wsAgentRetries >= 15) return;
  _wsAgentRetries++;
  const delay = Math.min(1000 * Math.pow(2, _wsAgentRetries), 30000);
  setTimeout(connectAgentWs, delay);
}

whenAuthed(connectAgentWs);
// T6: 탭 뱃지가 그리드를 열지 않아도 최신 cwd를 알도록 저빈도로 재조회
// (탭 상시 표시라 그리드 폴링(1초)만큼 자주일 필요는 없다 — 새 tmux 세션이
// 생기는 빈도에 맞춰 20초면 충분).
whenAuthed(() => {
  setInterval(() => { if (!document.hidden) _refreshTmuxCwdMap(); }, 20000);
});
