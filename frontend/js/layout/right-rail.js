// L8 — 우측 레일 접기/펴기. 폭·접힘 상태는 설정 스토어(S2)에 저장해 기기 간
// 따라오게 한다.
//
// **레일 자체의 표시 여부는 여기서 결정하지 않는다** — `.needs-usage` capability
// 게이팅(agent/status.js)이 그 일을 한다. 섹션이 하나도 없으면 레일이 통째로
// 사라지고 pane이 폭을 회수한다(30-layout-shell.md §4).
import { get as setting, set as setSetting, subscribe as onSettings } from '../core/settings.js';

const RAIL = document.getElementById('vt-right-rail');
const BTN = document.getElementById('vt-rr-collapse');

function apply() {
  if (!RAIL) return;
  const collapsed = !!setting('rightRail.collapsed');
  document.body.classList.toggle('vt-rr-collapsed', collapsed);
  RAIL.classList.toggle('collapsed', collapsed);
  if (BTN) {
    BTN.textContent = collapsed ? '‹' : '›';
    BTN.title = collapsed ? '펼치기' : '접기';
    BTN.setAttribute('aria-label', collapsed ? '우측 레일 펼치기' : '우측 레일 접기');
    BTN.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

if (RAIL && BTN) {
  BTN.addEventListener('click', () => setSetting('rightRail.collapsed', !setting('rightRail.collapsed')));
  onSettings((changed) => { if (!changed || 'rightRail.collapsed' in changed) apply(); });
  apply();
}
