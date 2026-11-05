// 에이전트 세션 공유 + 승인함 — 상시 로드되는 얇은 진입점(queue-lazy.js 와 같은 구조).
// 공유 대화상자는 `share/entry.js`, 승인함(모달·권한·감사)은 `authz/entry.js` 이고 둘 다 `share` 청크(vite.config.js)다.
// 여기는 이벤트 수신, 배지, import() 몇 줄뿐이다. 승인은 이 맥에서만 된다(중계 허용 목록에 없다) — 다른 맥을 보는 화면은 듣지 않는다.
import { onWorkspaceEvent } from './core/workspace-ws.js';
import { isRelayed } from './core/host-route.js';
import { getAction } from './core/dom.js';
import { timedFetch } from './core/api.js';

let shareMod = null;
let authzMod = null;
const loadShare = () => (shareMod || (shareMod = import('./share/entry.js')));
const loadAuthz = () => (authzMod || (authzMod = import('./authz/entry.js')));
const warn = (e) => console.error('[FarShell share]', e);

// 설정 버튼들에 붙는 대기 건수 배지(기존 rail 배지와 같은 모양). 0건이면 숨긴다.
// 넓은 화면의 설정 진입점은 메뉴바의 FarShell 메뉴 버튼이다(2026-11, 우측 dock의 ⚙를 없앴다).
const BADGE_HOSTS = ['#vt-rail-settings', '.vt-mb-menu', '.vt-mnav-btn[data-mnav="more"]'];
window.vtAuthzBadge = (n) => {
  window.vtAuthzPending = n;      // 설정 모바일 홈의 「권한」 줄이 읽는다
  for (const sel of BADGE_HOSTS) {
    const host = document.querySelector(sel);
    if (!host) continue;
    let b = host.querySelector('.vt-authz-badge');
    if (!b && n > 0) {
      b = document.createElement('span');
      b.className = 'vt-authz-badge';
      host.appendChild(b);
    }
    if (!b) continue;
    b.hidden = !(n > 0);
    b.textContent = n > 99 ? '99+' : String(n);
    host.dataset.authzPending = n > 0 ? String(n) : '';
    const base = host.dataset.authzLabel || host.getAttribute('aria-label') || '';
    host.dataset.authzLabel = base;
    host.setAttribute('aria-label', n > 0 ? `${base} (${window.vtT ? window.vtT('승인 대기 {n}건', { n }) : `${n}`})` : base);
  }
};

// 대기 요청을 쿠키만으로 받는다(토큰·Authorization 이 실리면 서버가 사람 요청으로 치지 않는다 — share/share-api.js).
function sync() {
  // 쿠키만 — 주소·헤더는 그대로 두고 마감과 도달 판정만 붙인다(core/api.js timedFetch).
  timedFetch(`${window.API_BASE || ''}/api/authz/approvals`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const list = d && Array.isArray(d.approvals) ? d.approvals : null;
      if (!list) return;
      window.vtAuthzBadge(list.length);
      if (list.length || authzMod) loadAuthz().then((m) => m.setPending(list)).catch(warn);
    })
    .catch(() => { /* 다음 연결에서 다시 맞춘다 */ });
}

window.vtSessionAccess = (name, share) => loadShare().then((m) => m.openSessionAccess(name, share)).catch(warn);
window.vtShareOpen = (name, share) => loadShare().then((m) => m.openShareDialog(name, share)).catch(warn);
window.vtShareUnshare = (name) => loadShare().then((m) => m.unshareSession(name)).catch(warn);
window.vtAgentPerms = () => { const open = getAction('permissions.show'); if (typeof open === 'function') open(); };

if (!isRelayed()) {
  // 요청이 새로 생기는 이벤트는 청크를 받아 처리하고, 처리·회수 이벤트는 청크가 이미 떠 있을 때만 쓴다.
  // 옛 이벤트(agent_share_request*)는 새 이벤트가 오기 전까지만 같은 일을 한다(authz/store.js onEvent).
  for (const type of ['authz_request', 'agent_share_request']) {
    onWorkspaceEvent(type, (msg) => { if (msg) loadAuthz().then((m) => m.onEvent({ ...msg, type })).catch(warn); });
  }
  for (const type of ['authz_resolved', 'authz_revoked', 'agent_share_request_resolved', 'lease_notice']) {
    onWorkspaceEvent(type, (msg) => { if (msg && authzMod) authzMod.then((m) => m.onEvent({ ...msg, type })).catch(warn); });
  }
  // 부팅·재연결 때 놓친 요청을 맞춘다(ws 가 열릴 때마다).
  onWorkspaceEvent('ws_open', sync);
}
