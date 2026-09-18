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

let _cleanup = null;
let _onKey = null;

function closePanel() {
  const el = document.getElementById('vt-connected-screens');
  if (el) el.remove();
  if (_cleanup) { _cleanup(); _cleanup = null; }
  if (_onKey) { document.removeEventListener('keydown', _onKey); _onKey = null; }
}

function openPanel() {
  // 토글 — HUD 칩·컨텍스트 메뉴 둘 다에서 반복 클릭될 수 있다.
  if (document.getElementById('vt-connected-screens')) { closePanel(); return; }

  const activeSess = getSession(activeSessionId());
  const tmuxName = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (!tmuxName) {
    if (typeof window.showToast === 'function') window.showToast('tmux 세션이 아닙니다');
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
  card.setAttribute('aria-label', '연결된 화면');

  const head = document.createElement('div');
  head.className = 'vt-wtd-head';
  const title = document.createElement('span');
  title.className = 'vt-wtd-title';
  title.textContent = '연결된 화면';
  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'vt-btn sm quiet vt-wtd-x';
  x.textContent = '×';
  x.setAttribute('aria-label', '닫기');
  x.addEventListener('click', closePanel);
  head.append(title, x);

  const body = document.createElement('div');
  body.className = 'vt-wtd-body';

  card.append(head, body);
  backdrop.appendChild(card);
  document.body.appendChild(backdrop);

  _cleanup = mountClients(body, tmuxName, activeSess.remote);
  _onKey = (e) => { if (e.key === 'Escape') closePanel(); };
  document.addEventListener('keydown', _onKey);
}

registerAction('clients.show', openPanel);
