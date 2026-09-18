// 세션 카드 — F4에서 grid.js에서 분리(Phase 7 #7-3 원본), L3 4단계에서
// 전체화면 그리드 뷰(#grid-view/#grid-toggle) 자체는 폐지했다(ADR-7 — 자유
// 분할 pane 트리로 대체). "카드를 만들고 갱신하는 로직"만 남긴다 — pane-picker.js
// (빈 pane → 세션 선택 시트)와 팔레트(shell/Palette.tsx)가 이 카드 마크업을
// 그대로 재사용한다.
//
// 2.1 D5 — 세션당 `/ws-preview` 소켓을 열어 tmux 화면을 실시간으로 그리던
// 라이브 미리보기를 걷어냈다. 카드 3벌(팔레트·pane-picker·구 rail 세션
// 목록)이 화면에 동시에 떠 있으면 세션마다 소켓이 3배로 늘고, 실제로 보여준
// 값은 "지금 이 세션이 무슨 명령을 어디서 돌리고 있나"보다 덜 유용했다(스크롤
// 위치·색·굵기까지 그대로 옮기는데 정작 고르는 데 필요한 건 command/cwd
// 한 줄이다). 이제 그 자리에 cwd 한 줄만 정적으로 채운다 — 소켓이 필요 없다.
// `/ws-preview` **서버 엔드포인트 자체는 그대로 둔다**(ADR-7) — fsh CLI 등
// 다른 소비처가 남아 있을 수 있고, 엔드포인트 삭제는 이 정리의 범위 밖이다.
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getSession } from '../core/store.js';
import { switchTo, addSession } from '../term/session.js';
import { _applyCardAgent } from './badges.js';

// 카드 하나의 정적 뼈대를 만든다(제목·메타 자리·클릭 핸들러). 값 채우기는
// updateSessionCard()가 별도로 한다 — pane-picker.js의 "매번 새로 만든다"와
// Palette.tsx의 "이미 있으면 재사용, 값만 갱신" 관행이 이 두 함수를 각자
// 다른 방식으로 조합해서 쓸 수 있도록 분리했다.
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
    <div class="card-meta"></div>
  `;
  card.querySelector('.card-title').textContent = sess.name;
  card.onclick = onSelect;
  return card;
}

// 매번 새로 계산해야 하는 값(명령·cwd·이미 탭인지·에이전트 배지)만 갱신한다.
export function updateSessionCard(card, sess, agentInfo) {
  card.querySelector('.card-cmd').textContent = sess.command || '';
  // cwd는 dataset에도 저장해둔다 — agent_event(pre/stop)가 cwd로만 오므로
  // "어느 카드가 지금 작업 중인지"를 여기 저장된 값과 매칭해서 찾는다.
  card.dataset.cwd = sess.cwd || '';
  // textContent로 넣는다 — cwd는 사용자의 실제 파일 경로라 임의 문자를
  // 포함할 수 있다(card-title과 같은 이유, innerHTML 금지).
  card.querySelector('.card-meta').textContent = sess.cwd || '';
  // 이미 탭으로 열려 있으면(전환 vs attach — 클릭 결과가 달라진다) 왼쪽에 표시.
  const isOpenTab = !!(sess.web_session_id && getSession(sess.web_session_id));
  card.classList.toggle('open-tab', isOpenTab);
  card.setAttribute('data-tip', isOpenTab ? '이미 탭으로 열려 있음 — 클릭하면 그 탭으로 전환' : '클릭하면 이 세션에 접속');
  _applyCardAgent(card, agentInfo);
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
        addSession(data.id, data.name || name);
        const s = getSession(data.id);
        if (s) s.tmuxName = name;
      }
      switchTo(data.id);
    }
  } catch (e) { console.warn('attach fail', e); }
}
