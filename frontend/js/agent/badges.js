// 에이전트 배지(탭/카드에 어떤 CLI가 떠 있는지 아이콘으로 표시) — F4에서
// grid.js에서 분리. `applyAgentBadges`는 status.js의 agent WS 스냅샷 수신
// 시(agent_snapshot/agents_change) 부르고, `_applyCardAgent`는 그 내부와
// preview.js의 updateSessionCard(카드 렌더 직후) 양쪽에서 함께 쓴다.
import { setAgentMark } from '../ui/icons.js';

// D3(20-design-system.md §4) — 이모지 대신 SVG 마크. 에이전트가 없으면(info
// 자체가 null — 감지 실패나 순수 셸) 아무것도 안 그린다. **셸이라는 사실이
// 확정된 것과 "아직 모른다"는 다르다** — 후자에 agent-shell 마크를 그리면
// 오판으로 보인다.
export function _applyCardAgent(card, info) {
  const badge = card.querySelector('.card-agent');
  if (!badge) return;
  setAgentMark(badge, info && info.agent);
  if (info && info.label) badge.setAttribute('data-tip', info.label); else badge.removeAttribute('data-tip');
}

// 브랜드 로고 청크가 늦게 오면 마지막 스냅샷으로 한 번 더 그린다(ui/icons.js).
let _lastAgents = null;
window.addEventListener('vt:brand-icons', () => { if (_lastAgents) applyAgentBadges(_lastAgents); });

export function applyAgentBadges(agents) {
  _lastAgents = agents;
  // N37 3단계 5/n — 세션 탭 줄(`#tabs .tab`)은 사라졌다. 에이전트 마크는
  // 카드에만 그린다(뷰 탭은 2026-10-04부터 마크를 그리지 않는다).
  document.querySelectorAll('.vt-card').forEach((card) => {
    _applyCardAgent(card, agents[card.dataset.name]);
  });
}
