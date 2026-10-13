// 부팅 때 저장된 레이아웃의 세션을 다시 붙인다. F4에서 terminal.js에서 분리.
// 2026-10-03 — 옛 세션 목록 스냅샷(vt-workspace-v1, saveWorkspace/
// restoreWorkspace)은 없앴다: 세션 목록·이동 순서의 출처는 이제 레이아웃 하나다
// (layout/persist.js). session.js와 순환 import지만 호출 시점에만 쓴다.
import { getSession } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { screenId } from '../core/screen-id.js';
import { addSession } from './session.js';
import { attachRemoteSession } from './remote.js';
import { t } from '../core/i18n.js';

// 복원 요청은 인증/연결 실패와 확인된 세션 부재(404)를 구분한다.
async function restoreRequest(url, options, missing = false) {
  const res = await apiFetch(url, options).catch((e) => { e.retryable = true; throw e; });
  if (!res.ok && !(missing && res.status === 404 && (await res.json())?.error === 'tmux session not found')) {
    const e = new Error(`restore ${res.status}`);
    e.status = res.status;
    e.retryable = res.status >= 500 || res.status === 429;
    throw e;
  }
  return res;
}


// 2026-10-03(세션·뷰 규칙 2단계) — 부팅은 **저장된 레이아웃의 열린 칸에 있던
// 세션만** attach한다(layout/persist.js snapshotSessionInfos). 예전의
// restoreWorkspace(이 파일의 세션 목록 스냅샷 전부)와 reconcileMissingTmuxSessions
// (서버의 tmux 세션 중 없는 것 전부)는 사용자가 닫은 세션까지 새로고침마다 다시
// 열었다(불변식 3 위반) — 둘 다 없앴다. 자리는 붙이지 않는다(place:'none') —
// 이어서 layout/persist.js restoreLayout이 트리로 놓는다.
export async function attachLayoutSessions(infos) {
  let failed = 0;
  let opened = 0;
  const seen = new Set();
  let live = null;
  for (const info of infos || []) {
    if (!info) continue;
    const host = info.host || 'local';
    const key = info.tmux ? `${host}\n${info.tmux}` : `id\n${info.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    try {
      let id = null;
      if (host !== 'local') {
        // 원격 호스트가 꺼져 있으면 실패가 정상 — 배치는 unreachable 칸이 지킨다(C3).
        if (info.tmux) id = await attachRemoteSession(host, info.tmux, { place: 'none' });
        if (!id) continue;
      } else if (info.tmux) {
        const res = await restoreRequest(`${API_BASE}/api/tmux/attach`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: info.tmux, screen: screenId() }),
        }, true);
        const data = res.ok ? await res.json() : null;
        id = data && data.id;
        if (res.ok && !id) throw new Error('invalid attach response');
        if (id && !getSession(id)) addSession(id, data.name || info.tmux, { place: 'none', tmuxName: info.tmux });
      } else if (info.id) {
        // 비-tmux(순수 PTY) 칸 — 서버가 이 브라우저 탭의 그 PTY를 아직 들고 있을 때만.
        if (!live) {
          live = new Map();
          const r = await restoreRequest(`${API_BASE}/api/sessions?screen=${screenId()}`);
          for (const x of await r.json()) live.set(x.id, x);
        }
        const x = live.get(info.id);
        if (x) { id = x.id; if (!getSession(id)) addSession(id, x.name || id, { place: 'none', ...(x.tmux_name ? { tmuxName: x.tmux_name } : {}) }); }
      }
      if (id) opened++; else failed++;
    } catch (e) {
      if (host === 'local' || e.retryable) throw e;
      failed++;
    }
  }
  if (failed > 0) showToast(t('세션 {n}개를 열지 못했습니다 (이미 종료됨)', { n: failed }));
  return opened;
}
