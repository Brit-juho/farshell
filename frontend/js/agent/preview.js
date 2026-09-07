// 라이브 프리뷰 카드 — F4에서 grid.js에서 분리(Phase 7 #7-3 원본), L3 4단계에서
// 전체화면 그리드 뷰(#grid-view/#grid-toggle) 자체는 폐지했다(ADR-7 — 자유
// 분할 pane 트리로 대체). "카드를 만들고 갱신하는 로직"만 남긴다 — 5단계(빈
// pane 클릭 → 썸네일 세션 선택 시트)와 팔레트(L6)가 이 카드 마크업을 그대로
// 재사용할 예정이라, 당장 컨테이너가 없어도(#grid-cards가 사라졌다) 함수
// 자체는 지우지 않았다. "일하는 중이냐 표시"는 status.js, "무슨 아이콘을
// 보여줄까"는 badges.js가 갖고 있다.
//
// /ws-preview 엔드포인트(세션당 프리뷰 WebSocket)는 서버 쪽 그대로 유지된다
// — 진짜 attach가 아니라 tmux 클라이언트 수를 늘리지 않는 유일한 수단이라
// ADR-7이 명시적으로 보존을 요구한다.
import { apiFetch } from '../core/api.js';
import { API_BASE, WS_BASE, _tokenQuery } from '../core/env.js';
import { getSession } from '../core/store.js';
import { switchTo, addSession } from '../term/session.js';
import { _applyCardAgent } from './badges.js';
import { _applyActiveHighlights, agent_status_active_cache } from './status.js';

// ANSI→HTML 변환 + 빈 줄/긴 패딩 정리는 순수 로직이라 ansilex.js로 분리했다
// (keyseq.js/difflex.js와 같은 이유 — Node 테스트에서 재사용하기 위해서다).
// ansiToHtml은 카드 라이브 프리뷰의 유일한 XSS 방어선이다: 서버가 보낸
// tmux capture-pane 원문을 innerHTML로 꽂기 때문에(아래 refreshCard), 반드시
// 여기서 HTML escape를 거쳐야 한다. VTAnsiLex는 UMD(window 전역)라 그대로 읽는다.
const ansiToHtml = VTAnsiLex.ansiToHtml;
const _trimBlankLines = VTAnsiLex.trimBlankLines;

// Phase 9 #1: 카드별 preview ws — 변화 시에만 갱신.
const _previewWs = {};
export function ensurePreviewWs(sessName) {
  if (_previewWs[sessName]) return;
  const ws = new WebSocket(`${WS_BASE}/ws-preview/${encodeURIComponent(sessName)}${_tokenQuery}`);
  _previewWs[sessName] = ws;
  ws.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
    if (msg.type !== 'preview' || !msg.content) return;
    // L6에서 발견한 버그 수정: 이 핸들러가 `#grid-cards`에만 스코프돼 있었다 —
    // 그리드 뷰가 L3 4단계에서 폐지된 뒤로 카드는 pane-picker.js(`#vt-pp-cards`)·
    // quickopen.js(팔레트 세션 목록) 두 곳에서 재사용되는데, 이 onmessage는 여전히
    // 존재하지 않는 `#grid-cards`만 찾아 항상 조용히 no-op이었다 — 즉 두 화면
    // 모두 "첫 프레임(로딩 중...)" 이후로는 라이브 갱신이 전혀 반영되지 않았다.
    // 컨테이너 id에 의존하지 않고 문서 전체에서 이름이 일치하는 카드를 찾는다
    // (같은 세션 카드가 동시에 여러 화면에 떠 있어도 전부 갱신됨).
    document.querySelectorAll(`[data-name="${CSS.escape(sessName)}"] .card-preview`).forEach((pre) => {
      pre.innerHTML = ansiToHtml(_trimBlankLines(msg.content));
      pre.scrollTop = pre.scrollHeight;
    });
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

// 카드 하나의 정적 뼈대를 만든다(제목·프리뷰 자리·클릭 핸들러). 값 채우기는
// updateSessionCard()가 별도로 한다 — refreshGrid()의 "이미 있으면 재사용,
// 값만 갱신" 관행과 5단계(layout/pane-picker.js)의 "매번 새로 만든다" 관행이
// 이 두 함수를 각자 다른 방식으로 조합해서 쓸 수 있도록 분리했다.
export function buildSessionCard(sess, onSelect) {
  const card = document.createElement('div');
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
  card.onclick = onSelect;
  return card;
}

// 매번 새로 계산해야 하는 값(명령·cwd·이미 탭인지·에이전트 배지)만 갱신한다.
export function updateSessionCard(card, sess, agentInfo) {
  card.querySelector('.card-cmd').textContent = sess.command || '';
  // cwd는 dataset에 저장해둔다 — agent_event(pre/stop)가 cwd로만 오므로
  // "어느 카드가 지금 작업 중인지"를 여기 저장된 값과 매칭해서 찾는다.
  card.dataset.cwd = sess.cwd || '';
  // 이미 탭으로 열려 있으면(전환 vs attach — 클릭 결과가 달라진다) 왼쪽에 표시.
  const isOpenTab = !!(sess.web_session_id && getSession(sess.web_session_id));
  card.classList.toggle('open-tab', isOpenTab);
  card.title = isOpenTab ? '이미 탭으로 열려 있음 — 클릭하면 그 탭으로 전환' : '클릭하면 이 세션에 접속';
  _applyCardAgent(card, agentInfo);
}

export async function refreshGrid() {
  // L3 4단계: #grid-cards 컨테이너 자체가 사라졌다(그리드 뷰 폐지) — 이 함수는
  // 그 이름의 컨테이너가 다시 생기기 전까지는 호출부가 없다(5단계는 카드
  // 빌더만 재사용하고 자기 컨테이너 `#vt-pp-cards`를 따로 쓴다,
  // layout/pane-picker.js 참고). 그래도 누군가 미리 부르더라도 조용히 아무
  // 일도 안 하게 방어한다 — 없는 엘리먼트에 innerHTML을 쓰면 즉시 TypeError로 죽는다.
  const cards = document.getElementById('grid-cards');
  if (!cards) return;
  try {
    // 세션 목록 + 에이전트 배지(어떤 CLI가 그 pane에 떠 있는지)를 함께 가져온다.
    // agents는 /ws-agent 최초 접속 시 한 번(snapshot)만 오므로, 카드를 새로
    // 그릴 때마다 여기서도 새로 물어봐야 배지가 stale해지지 않는다.
    const [sessRes, agentsRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/tmux/sessions`),
      apiFetch(`${API_BASE}/api/agents`).catch(() => null),
    ]);
    const tmuxSessions = await sessRes.json();
    const agents = agentsRes ? await agentsRes.json().catch(() => ({})) : {};

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
        card = buildSessionCard(sess, () => {
          // "완료" 강조는 확인했다는 뜻이니 클릭하면 지운다.
          card.classList.remove('done');
          if (sess.web_session_id && getSession(sess.web_session_id)) {
            switchTo(sess.web_session_id);
          } else {
            attachTmuxSession(sess.name);
          }
        });
        cards.appendChild(card);
      }
      updateSessionCard(card, sess, agents[sess.name]);
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
// (예전엔 탭 가시성이 돌아올 때 refreshGrid()로 한 번 따라잡는 visibilitychange
// 리스너가 여기 있었다 — 그리드 뷰 폐지로 카드를 상시 띄워두는 화면 자체가
// 없어져서 함께 걷어냈다. 5단계가 카드 컨테이너를 다시 연결할 때, 그 UI가
// 스스로 열리는 시점에 refreshGrid()를 부르면 되므로 여기서 전역으로 감시할
// 필요가 없다.)
