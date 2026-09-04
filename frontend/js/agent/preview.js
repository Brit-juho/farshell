// 라이브 프리뷰 그리드 뷰 — F4에서 grid.js에서 분리(Phase 7 #7-3 원본).
// "지금 뭐가 떠 있고 뭘 하고 있는지 한눈에" 보여주는 카드 그리드 자체
// (열고닫기·카드 렌더·세션당 프리뷰 WebSocket)를 담당한다. "일하는 중이냐
// 표시"는 status.js, "무슨 아이콘을 보여줄까"는 badges.js가 갖고 있다.
import { apiFetch } from '../core/api.js';
import { API_BASE, WS_BASE, _tokenQuery } from '../core/env.js';
import { getSession } from '../core/store.js';
import { switchTo, addSession } from '../term/session.js';
import { registerAction } from '../core/dom.js';
import { _applyCardAgent } from './badges.js';
import { _applyActiveHighlights, agent_status_active_cache } from './status.js';

// ANSI→HTML 변환 + 빈 줄/긴 패딩 정리는 순수 로직이라 ansilex.js로 분리했다
// (keyseq.js/difflex.js와 같은 이유 — Node 테스트에서 재사용하기 위해서다).
// ansiToHtml은 그리드 카드 라이브 프리뷰의 유일한 XSS 방어선이다: 서버가 보낸
// tmux capture-pane 원문을 innerHTML로 꽂기 때문에(아래 refreshCard), 반드시
// 여기서 HTML escape를 거쳐야 한다. VTAnsiLex는 UMD(window 전역)라 그대로 읽는다.
const ansiToHtml = VTAnsiLex.ansiToHtml;
const _trimBlankLines = VTAnsiLex.trimBlankLines;

// search.js가 Esc로 그리드를 닫을 때 bare `gridViewEnabled`를 읽는다 — 아직
// classic script라 window 브리지가 필요하다(재할당마다 동기화).
export let gridViewEnabled = false;
window.gridViewEnabled = gridViewEnabled;

export async function toggleGridView() {
  gridViewEnabled = !gridViewEnabled;
  window.gridViewEnabled = gridViewEnabled;
  const grid = document.getElementById('grid-view');
  const term = document.getElementById('terminal-container');
  const btn = document.getElementById('grid-toggle');
  if (gridViewEnabled) {
    grid.style.display = 'block';
    term.style.display = 'none';
    btn.classList.add('active');     // D2: CSS class로 활성 상태 관리
    await refreshGrid();
    // Phase 9 #1: setInterval(refreshGrid, 2000) 제거 — 카드별 ws push가 갱신 담당.
  } else {
    grid.style.display = 'none';
    term.style.display = '';
    btn.classList.remove('active');  // D2: 비활성 시 class 제거
    // 모든 preview ws 닫기
    for (const ws of Object.values(_previewWs)) { try { ws.close(); } catch (_) {} }
    Object.keys(_previewWs).forEach(k => delete _previewWs[k]);
  }
}
// Phase 9 #1: 카드별 preview ws — 변화 시에만 갱신.
const _previewWs = {};
export function ensurePreviewWs(sessName) {
  if (_previewWs[sessName]) return;
  const ws = new WebSocket(`${WS_BASE}/ws-preview/${encodeURIComponent(sessName)}${_tokenQuery}`);
  _previewWs[sessName] = ws;
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.type !== 'preview' || !msg.content) return;
    const cards = document.getElementById('grid-cards');
    const card = cards && cards.querySelector(`[data-name="${CSS.escape(sessName)}"]`);
    if (!card) return;
    const pre = card.querySelector('.card-preview');
    if (pre) { pre.innerHTML = ansiToHtml(_trimBlankLines(msg.content)); pre.scrollTop = pre.scrollHeight; }
  };
  // 30초 keepalive — 끊김 방지.
  // ⚠ 닫힌 WebSocket에 send()는 예외를 던지지 않고 조용히 버린다(스펙: CLOSING/CLOSED).
  // 따라서 catch로만 정리하면 인터벌이 영구히 남아 죽은 소켓을 붙잡는다(메모리 누수).
  // onclose에서 명시적으로 clearInterval하고, 매 tick도 readyState로 방어한다.
  const ka = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) { clearInterval(ka); return; }
    try { ws.send('ping'); } catch (_) { clearInterval(ka); }
  }, 30000);
  ws.onclose = () => { clearInterval(ka); delete _previewWs[sessName]; };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

export async function refreshGrid() {
  try {
    // 세션 목록 + 에이전트 배지(어떤 CLI가 그 pane에 떠 있는지)를 함께 가져온다.
    // agents는 /ws-agent 최초 접속 시 한 번(snapshot)만 오므로, 그리드를 열 때마다
    // 여기서도 새로 물어봐야 배지가 stale해지지 않는다.
    const [sessRes, agentsRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/tmux/sessions`),
      apiFetch(`${API_BASE}/api/agents`).catch(() => null),
    ]);
    const tmuxSessions = await sessRes.json();
    const agents = agentsRes ? await agentsRes.json().catch(() => ({})) : {};
    const cards = document.getElementById('grid-cards');

    // D3: 빈 상태 — 세션 없을 때 안내 메시지
    if (tmuxSessions.length === 0) {
      cards.innerHTML = `<div class="vt-grid-empty">
        <div class="big">⊞</div>
        <div style="font-size:14px;margin-bottom:8px">실행 중인 tmux 세션이 없습니다</div>
        <div style="font-size:12px;">터미널에서 <code>tmux new -A -s dev</code> 실행 후 새로고침</div>
      </div>`;
      return;
    }
    const existingNames = new Set(Array.from(cards.children).map(c => c.dataset.name));
    const incomingNames = new Set(tmuxSessions.map(s => s.name));
    // 사라진 세션 카드 제거
    for (const card of Array.from(cards.children)) {
      if (!incomingNames.has(card.dataset.name)) card.remove();
    }
    // 카드 생성/갱신
    // D8: 카드 생성은 순차, 프리뷰 fetch는 Promise.all로 병렬화
    for (const sess of tmuxSessions) {
      let card = cards.querySelector(`[data-name="${CSS.escape(sess.name)}"]`);
      if (!card) {
        card = document.createElement('div');
        card.dataset.name = sess.name;
        card.className = 'vt-card';
        // 세션 이름은 tmux가 주는 임의 문자열이라 innerHTML 보간이 아니라
        // textContent로 넣는다 (`<`가 든 이름이 마크업으로 해석되지 않도록).
        card.innerHTML = `
          <div class="card-head">
            <span class="card-agent"></span>
            <span class="card-title"></span>
            <span class="card-cmd"></span>
          </div>
          <pre class="card-preview"><span style="opacity:.5;font-style:italic;font-size:var(--fs-3xs);">로딩 중...</span></pre>
        `;
        card.querySelector('.card-title').textContent = sess.name;
        card.onclick = () => {
          // Codex: toggleGridView 내부에서 gridViewEnabled를 반전시키므로
          // 그리드가 열려있을 때만 toggle 호출해야 닫힌다.
          if (gridViewEnabled) toggleGridView();
          // "완료" 강조는 확인했다는 뜻이니 클릭하면 지운다.
          card.classList.remove('done');
          if (sess.web_session_id && getSession(sess.web_session_id)) {
            switchTo(sess.web_session_id);
          } else {
            attachTmuxSession(sess.name);
          }
        };
        cards.appendChild(card);
      }
      card.querySelector('.card-cmd').textContent = sess.command || '';
      // cwd는 dataset에 저장해둔다 — agent_event(pre/stop)가 cwd로만 오므로
      // "어느 카드가 지금 작업 중인지"를 여기 저장된 값과 매칭해서 찾는다.
      card.dataset.cwd = sess.cwd || '';
      // 이미 탭으로 열려 있으면(전환 vs attach — 클릭 결과가 달라진다) 왼쪽에 표시.
      const isOpenTab = !!(sess.web_session_id && getSession(sess.web_session_id));
      card.classList.toggle('open-tab', isOpenTab);
      card.title = isOpenTab ? '이미 탭으로 열려 있음 — 클릭하면 그 탭으로 전환' : '클릭하면 이 세션에 접속';
      _applyCardAgent(card, agents[sess.name]);
    }

    // Phase 9 #1: 폴링 fetch 제거 — 카드별 ws push가 즉시 첫 콘텐츠 + 변화 push.
    for (const sess of tmuxSessions) ensurePreviewWs(sess.name);
    // 사라진 세션의 ws는 닫기
    for (const name of Object.keys(_previewWs)) {
      if (!incomingNames.has(name)) {
        try { _previewWs[name].close(); } catch (_) {}
        delete _previewWs[name];
      }
    }

    // 스냅샷에 이미 작업 중인 세션이 있으면(그리드를 늦게 열었을 수 있음) 바로 반영.
    _applyActiveHighlights(agent_status_active_cache);
  } catch (e) { console.warn('grid refresh fail', e); }
}

export async function attachTmuxSession(name) {
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/attach`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.id) {
      if (!getSession(data.id)) {
        addSession(data.id, data.name || `tmux:${name}`);
        const s = getSession(data.id);
        if (s) s.tmuxName = name;
      }
      switchTo(data.id);
    }
  } catch (e) { console.warn('attach fail', e); }
}

// Phase 9 #2: agents 폴링 제거 — `/ws-agent` push가 모두 대체.
// P6: 예전엔 탭이 다시 보이면 setInterval(refreshGrid, 1000)을 영구히 돌렸다 —
// 프리뷰/에이전트 배지는 이미 ws-preview/ws-agent push가 갱신을 맡고 있어
// 1초 폴링과 내용이 겹쳤다. 실제로 폴링이 필요한 건 "탭이 백그라운드였던 동안
// 놓쳤을 수도 있는 갱신"을 한 번 따라잡는 것뿐이므로(외부에서 tmux 세션을
// 새로 만들거나 죽인 경우는 push 채널이 없어 이 캐치업이 유일한 감지 수단),
// 반복 폴링 대신 1회성 refreshGrid() 호출로 바꿨다.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && gridViewEnabled) refreshGrid();
});

window.toggleGridView = toggleGridView;

// F3(c): data-action 위임용 등록.
registerAction('grid.toggle', () => toggleGridView());
