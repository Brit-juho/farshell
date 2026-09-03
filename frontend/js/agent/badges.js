// 에이전트 배지(탭/카드에 어떤 CLI가 떠 있는지 아이콘으로 표시) — F4에서
// grid.js에서 분리. `applyAgentBadges`는 status.js의 agent WS 스냅샷 수신
// 시(agent_snapshot/agents_change) 부르고, `_applyCardAgent`는 그 내부와
// preview.js의 refreshGrid(카드 렌더 직후) 양쪽에서 함께 쓴다.
import { getSession } from '../core/store.js';

export function _applyCardAgent(card, info) {
  const badge = card.querySelector('.card-agent');
  if (!badge) return;
  badge.textContent = (info && info.icon) ? info.icon : '';
  if (info && info.label) badge.title = info.label; else badge.removeAttribute('title');
}

export function applyAgentBadges(agents) {
  document.querySelectorAll('.tab').forEach((tab) => {
    const sid = tab.dataset.sessionId;
    const sess = getSession(sid);
    const badge = tab.querySelector('.tab-agent');
    if (!sess || !badge) return;
    const tmuxName = sess.tmux_name || sess.tmuxName;
    const info = tmuxName && agents[tmuxName];
    badge.textContent = (info && info.icon) ? info.icon : '';
    if (info && info.label) badge.title = info.label;
  });
  document.querySelectorAll('.vt-card').forEach((card) => {
    _applyCardAgent(card, agents[card.dataset.name]);
  });
}
