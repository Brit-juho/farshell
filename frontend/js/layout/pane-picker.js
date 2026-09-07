// L3 5단계 — 빈 pane 클릭 → 썸네일 기반 세션 선택 시트. ADR-7이 예고한
// "프리뷰 카드는 썸네일로 축소돼 세션 시트·팔레트·빈 pane 선택 화면에서
// 재사용된다"의 첫 소비처 — 4단계에서 agent/preview.js에 남겨둔 카드 빌더
// (buildSessionCard/updateSessionCard/ensurePreviewWs/attachTmuxSession)를
// 그대로 가져다 쓴다. "그리드 뷰의 전체화면만 지우고 카드 렌더링은 남긴다"가
// 바로 이 순간을 위한 결정이었다.
//
// 목록에 두 갈래가 있다:
//  1. tmux 세션(`/api/tmux/sessions`) — 이미 탭으로 열려 있든 아니든 전부,
//     그리드가 하던 것과 동일하게 라이브 프리뷰(ws-preview)를 붙인다.
//  2. tmux가 아닌 일반 터미널로 이미 열려 있는 세션 — 서버에 이름으로 다시
//     조회할 방법이 없어(tmux처럼 세션명이 없다) 라이브 프리뷰는 못 붙이지만,
//     "이 화면에 배정"은 여전히 가능해야 한다.
// 그 위에 "+ 새 세션"을 고정 배치한다 — 지금까지의 placeholder 버튼과 같은
// 동작(setActivePane 후 createSession)을 그대로 옮긴 것뿐이다.
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { allSessions, getSession } from '../core/store.js';
import { openPanel, closePanel } from '../panels/panel.js';
import { switchTo, createSession } from '../term/session.js';
import { buildSessionCard, updateSessionCard, ensurePreviewWs, attachTmuxSession } from '../agent/preview.js';
import { setActivePane } from './store.js';
import { icon } from '../ui/icons.js';

const PANEL_ID = 'vt-pane-pick';

function _sessionLabel(id) {
  const s = getSession(id);
  return s?.tabEl?.querySelector('.tab-name')?.textContent || id.slice(0, 8);
}

export function openPanePicker(paneId) {
  const panel = openPanel({
    id: PANEL_ID,
    ariaLabel: '이 화면에 표시할 세션 선택',
    extraClass: 'vt-pane-pick',
    headHTML: `<div class="vt-vw-title">이 화면에 표시할 세션</div>`,
    bodyId: 'vt-pp-body',
    bodyHTML: `
      <button type="button" class="vt-pp-new">${icon('plus', 15)}새 세션</button>
      <div class="vt-card-grid" id="vt-pp-cards"><div class="vt-vw-loading">불러오는 중…</div></div>
    `,
  });
  if (!panel) return; // 토글 — 이미 열려 있어서 닫기만 했다

  panel.body.querySelector('.vt-pp-new').addEventListener('click', () => {
    setActivePane(paneId);
    createSession();
    closePanel(PANEL_ID);
  });

  _renderCandidates(paneId, panel.body.querySelector('#vt-pp-cards'));
}

async function _renderCandidates(paneId, cardsEl) {
  let tmuxSessions = [];
  let agents = {};
  try {
    const [sessRes, agentsRes] = await Promise.all([
      apiFetch(`${API_BASE}/api/tmux/sessions`),
      apiFetch(`${API_BASE}/api/agents`).catch(() => null),
    ]);
    tmuxSessions = await sessRes.json();
    agents = agentsRes ? await agentsRes.json().catch(() => ({})) : {};
  } catch (_) {
    // 목록을 못 가져와도 "+ 새 세션"은 여전히 동작해야 하므로 여기서는
    // 삼키고 아래에서 "가져올 게 없다"로 처리한다.
  }
  // fetch가 도는 사이 시트가 닫혔을 수 있다(빠른 연타 등) — 더 진행하지 않는다.
  if (!document.getElementById(PANEL_ID)) return;

  const tmuxNamesOpen = new Set(tmuxSessions.map(s => s.name));
  cardsEl.replaceChildren();

  for (const sess of tmuxSessions) {
    const card = buildSessionCard(sess, () => {
      setActivePane(paneId);
      // 이미 탭으로 열려 있으면 그 탭으로 전환(로컬 정보만으로 충분, API
      // 호출 불필요) — refreshGrid()의 카드 클릭과 동일한 분기.
      if (sess.web_session_id && getSession(sess.web_session_id)) {
        switchTo(sess.web_session_id);
      } else {
        attachTmuxSession(sess.name);
      }
      closePanel(PANEL_ID);
    });
    updateSessionCard(card, sess, agents[sess.name]);
    cardsEl.appendChild(card);
    ensurePreviewWs(sess.name);
  }

  // tmux 목록엔 없는(일반 터미널) 열린 세션 — 라이브 프리뷰는 못 붙이지만
  // "이 화면에 배정"은 지원해야 한다.
  for (const [id, s] of Object.entries(allSessions())) {
    const tmuxName = s.tmuxName || s.tmux_name;
    if (tmuxName && tmuxNamesOpen.has(tmuxName)) continue; // 위에서 이미 다뤘다
    const card = document.createElement('div');
    card.className = 'vt-card open-tab';
    card.innerHTML = `<div class="card-head"><span class="card-title"></span></div>
      <div class="vt-pp-plain-hint">일반 터미널 · 미리보기 없음</div>`;
    card.querySelector('.card-title').textContent = _sessionLabel(id);
    card.title = '클릭하면 이 화면에 표시';
    card.onclick = () => {
      setActivePane(paneId);
      switchTo(id);
      closePanel(PANEL_ID);
    };
    cardsEl.appendChild(card);
  }

  if (!cardsEl.children.length) {
    cardsEl.innerHTML = '<div class="vt-vw-empty">배정할 수 있는 세션이 없습니다. 위에서 새로 만드세요.</div>';
  }
}
