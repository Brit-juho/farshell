import { pushScope } from '../core/keyboard-scopes.js';
// ADR-29 E — 「연결된 화면」의 마지막 남은 진입점.
//
// 이 파일은 `layout/rail.js`(48px 아이콘 레일 + 사이드 플라이아웃)를 대체하는
// 게 아니라, 그 파일을 지우면서 유일하게 살아남아야 했던 조각 하나를 옮겨
// 온 것이다. 레일이 세션 목록·「연결된 화면」·설정 정적 컨텐츠를 한 플라이아웃
// 안에 다 담고 있었는데, ADR-29 B(레일 = 세션 행)·설정 패널 이식(2026-09-18
// CHANGELOG)이 그중 세션 목록과 설정을 각자 정식 화면으로 옮기면서, `rail.js`가
// 실제로 하던 일 중 남은 건 `clients.show` 액션(HUD의 "연결된 화면 N" 칩,
// 레일 세션 컨텍스트 메뉴의 "연결된 화면")뿐이었다.
//
// 창은 다른 시트들(RepoVisibility.tsx의 backdrop+card)과 같은 모양을 쓴다 —
// 레일이 사라진 지금 `#vt-rail-panel`의 `left:48px` 앵커는 더 이상 뜻이 없다.
import { activeSessionId, getSession } from '../core/store.js';
import { registerAction } from '../core/dom.js';
import { mountClients } from './clients.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';

let _cleanup = null;
let _onKey = null;
let _shownSessionId = null;

function closePanel() {
  const el = document.getElementById('vt-connected-screens');
  if (el) el.remove();
  if (_cleanup) { _cleanup(); _cleanup = null; }
  if (_onKey) { _onKey(); _onKey = null; }
  _shownSessionId = null;
}

// explicitSessionId: 레일 컨텍스트 메뉴가 "지금 안 보고 있는 세션"을 대상으로
// 부를 때 쓴다 — activeSessionId를 그 세션으로 바꿔치기하지 않는다(예전엔 그걸
// 위해 switchTo를 불렀는데, switchTo가 지금 포커싱된 pane 자리를 그 세션으로
// 덮어써 버렸다 — 사용자 보고로 발견).
export function openPanel(explicitSessionId) {
  const targetId = explicitSessionId || activeSessionId();
  // 같은 세션을 다시 누르면 닫고, 다른 행을 누르면 그 세션의 목록으로 전환한다.
  if (document.getElementById('vt-connected-screens')) {
    const same = _shownSessionId === targetId;
    closePanel();
    if (same) return;
  }

  const activeSess = getSession(targetId);
  const tmuxName = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (!tmuxName) {
    if (typeof window.showToast === 'function') window.showToast(t('tmux 세션이 아닙니다'));
    return;
  }

  const backdrop = document.createElement('div');
  backdrop.id = 'vt-connected-screens';
  backdrop.className = 'vt-viewer-backdrop vt-wtd-backdrop';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closePanel(); });

  const card = document.createElement('div');
  card.className = 'vt-viewer-card vt-wtd-card';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.setAttribute('aria-label', t('연결된 화면'));

  const head = document.createElement('div');
  head.className = 'vt-wtd-head';
  const title = document.createElement('span');
  title.className = 'vt-wtd-title';
  title.textContent = t('연결된 화면');
  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'vt-icon-btn vt-wtd-x';
  x.innerHTML = icon('x', 14);
  x.setAttribute('aria-label', t('닫기'));
  x.addEventListener('click', closePanel);
  head.append(title, x);

  const body = document.createElement('div');
  body.className = 'vt-wtd-body';

  card.append(head, body);
  backdrop.appendChild(card);
  document.body.appendChild(backdrop);

  // 리디자인 6단계(시안 O12) — 상태 막대의 「화면 N」 칩이 보이면 그 칩 위로 여는 떠 있는 면이다(뒤 막 없이, 칩 왼쪽에 맞춰).
  // 칩이 없는 자리(레일 행 메뉴 · 폰)에서 열면 예전처럼 가운데 창. 바깥을 누르면 닫히는 것은 같다.
  const chip = document.querySelector('#vt-hud [data-chip="screens"]');
  const r = chip?.getBoundingClientRect();
  if (r && r.width > 0 && window.innerWidth >= 720) {
    backdrop.classList.add('vt-screens-pop');
    card.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - Math.min(360, window.innerWidth - 16) - 8))}px`;
  }
  _shownSessionId = targetId;
  _cleanup = mountClients(body, tmuxName, activeSess.remote, targetId);
  _onKey = pushScope({ id: 'connected-screens', element: backdrop, modal: true, onKey: (e) => {
    if (e.key === 'Escape') { closePanel(); return true; }
  } });
}

registerAction('clients.show', openPanel);
// Rail.tsx가 특정 세션을 지정해 부르는 자리 — 정적 import 불가(레일 지연 청크 제약).
window.vtShowClients = openPanel;
