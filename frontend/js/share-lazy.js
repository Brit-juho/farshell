// 에이전트 세션 공유 — 상시 로드되는 얇은 진입점(queue-lazy.js 와 같은 구조).
// 대화상자·승인 모달·권한 패널은 `share` 청크(vite.config.js)에 있고, 여기는 이벤트 수신과
// import() 몇 줄뿐이다. 승인은 이 맥에서만 된다(중계 허용 목록에 없다) — 다른 맥을 보는 화면은 듣지 않는다.
import { onWorkspaceEvent } from './core/workspace-ws.js';
import { isRelayed } from './core/host-route.js';

let mod = null;
const load = () => (mod || (mod = import('./share/entry.js')));
const warn = (e) => console.error('[FarShell share]', e);

// 대기 요청을 쿠키만으로 받는다(토큰·Authorization 이 실리면 서버가 사람 요청으로 치지 않는다 — share/share-api.js).
function sync() {
  fetch(`${window.API_BASE || ''}/api/agent/share/requests`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      const list = d && Array.isArray(d.requests) ? d.requests : null;
      if (!list) return;
      if (list.length || mod) load().then((m) => m.setPending(list)).catch(warn);
    })
    .catch(() => { /* 다음 연결에서 다시 맞춘다 */ });
}

window.vtShareOpen = (name, share) => load().then((m) => m.openShareDialog(name, share)).catch(warn);
window.vtShareUnshare = (name) => load().then((m) => m.unshareSession(name)).catch(warn);

if (!isRelayed()) {
  onWorkspaceEvent('agent_share_request', (msg) => { if (msg && msg.request) load().then((m) => m.addRequest(msg.request)).catch(warn); });
  onWorkspaceEvent('agent_share_request_resolved', (msg) => { if (mod && msg) mod.then((m) => m.resolveRequest(msg.id)).catch(warn); });
  // 부팅·재연결 때 놓친 요청을 맞춘다(ws 가 열릴 때마다).
  onWorkspaceEvent('ws_open', sync);
}
