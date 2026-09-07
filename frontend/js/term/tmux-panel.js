// tmux 세션 관리 패널 (깨우기 / 완전 종료) + tmux 세션 생성/attach.
// F4에서 terminal.js(구 :1466-1612)에서 분리. 계획서(§4)는 이 내용이 나중에
// L4에서 panels/picker.js로 흡수될 것으로 봤지만, 그건 별도 단계(레일 UI 개편)의
// 일이라 F4에서는 일단 독립 파일로만 뽑았다 — 계획과 다른 점은 파일명뿐, 책임
// 경계는 그대로다.
import { getSession } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { addSession, switchTo, removeSession } from './session.js';
import { saveWorkspace } from './workspace.js';
import { registerAction } from '../core/dom.js';
import { icon } from '../ui/icons.js';
import { tmuxStatus, tmuxStatusDot } from '../ui/session-badge.js';

export async function showTmuxSessions() {
  // 토글: 이미 열려 있으면 닫기
  let menu = document.getElementById('tmux-menu');
  if (menu) { menu.remove(); return; }

  menu = document.createElement('div');
  menu.id = 'tmux-menu';
  menu.className = 'vt-menu';
  document.body.appendChild(menu);
  await renderTmuxMenu(menu);

  setTimeout(() => {
    document.addEventListener('click', function _close(e) {
      if (!document.body.contains(menu)) { document.removeEventListener('click', _close); return; }
      if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', _close); }
    });
  }, 100);
}

// 패널 내용을 다시 그린다 (kill 후 목록 갱신에도 재사용)
async function renderTmuxMenu(menu) {
  menu.innerHTML = '';
  let tmuxList = [];
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    tmuxList = await res.json();
  } catch (_) { /* 서버 오류 시 빈 목록 */ }

  if (!Array.isArray(tmuxList) || tmuxList.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'vt-menu-item';
    empty.style.cssText = 'opacity:.6;cursor:default;';
    empty.textContent = '실행 중인 tmux 세션 없음';
    menu.appendChild(empty);
  } else {
    for (const s of tmuxList) menu.appendChild(buildTmuxRow(menu, s));
  }

  const sep = document.createElement('div');
  sep.className = 'vt-menu-sep';
  menu.appendChild(sep);
  const newItem = document.createElement('div');
  newItem.className = 'vt-menu-item new';
  newItem.textContent = '+ 새 tmux 세션';
  newItem.onclick = async () => { menu.remove(); await createTmuxSession(); };
  menu.appendChild(newItem);
}

// 세션 한 줄: [상태점·이름 → 깨우기/전환]  [완전 종료(2단계 확인)]
function buildTmuxRow(menu, s) {
  const row = document.createElement('div');
  row.className = 'vt-menu-item';
  row.style.cssText = 'display:flex;align-items:center;gap:8px;';

  const openInWeb = !!s.web_session_id;
  const { text: statusText } = tmuxStatus(s);

  const label = document.createElement('span');
  label.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;';
  const cmd = s.command ? ` · ${s.command}` : '';
  label.append(tmuxStatusDot(s), document.createTextNode(`${s.name}  (${s.windows}win · ${statusText}${cmd})`));
  label.title = openInWeb ? '이 탭으로 전환' : '깨워서 열기 (attach)';
  label.onclick = async () => { menu.remove(); await attachTmux(s.name); };
  row.appendChild(label);

  // 완전 종료 — 2단계 인라인 확인 (실수 방지, 네이티브 dialog 미사용)
  const kill = document.createElement('button');
  const reset = () => {
    kill.innerHTML = icon('trash-2', 14); kill.style.color = 'var(--sub)';
  };
  kill.title = '완전 종료 (tmux 세션 kill — 되돌릴 수 없음)';
  kill.setAttribute('aria-label', `${s.name} 완전 종료`);
  kill.style.cssText = 'flex-shrink:0;background:transparent;border:none;cursor:pointer;padding:2px 6px;border-radius:5px;display:inline-flex;align-items:center;';
  reset();
  let armed = false, armTimer = null;
  kill.onclick = async (e) => {
    e.stopPropagation();
    if (!armed) {
      armed = true;
      kill.textContent = '종료?'; kill.style.color = 'var(--err)'; kill.style.fontSize = '11px';
      armTimer = setTimeout(() => { armed = false; reset(); }, 3000);
      return;
    }
    clearTimeout(armTimer);
    kill.textContent = '…';
    await killTmuxSession(s.name, s.web_session_id);
    if (document.body.contains(menu)) await renderTmuxMenu(menu);
  };
  row.appendChild(kill);
  return row;
}

// tmux 세션 완전 종료. 웹에 열린 탭이 있으면 먼저 정리해 무한 재연결을 막는다.
async function killTmuxSession(name, webSessionId) {
  // 서버 kill이 웹 PTY까지 destroy하므로, 열린 탭을 그대로 두면 WS가 끊긴 뒤
  // 재연결 루프에 빠진다. 클라이언트 탭을 먼저 정리(= detach)한 뒤 kill한다.
  if (webSessionId && getSession(webSessionId)) {
    await removeSession(webSessionId);
  }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/kill/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) { showToast(`완전 종료 실패: ${name} (${res.status})`); return false; }
    showToast(`완전 종료됨: ${name}`);
    return true;
  } catch (_) {
    showToast(`완전 종료 오류: ${name}`);
    return false;
  }
}

export async function attachTmux(tmuxName) {
  const res = await apiFetch(`${API_BASE}/api/tmux/attach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: tmuxName }),
  });
  // 서버가 세션 없음(404) 등을 돌려주면 data.id가 없다. 그대로 addSession(undefined)하면
  // /ws/undefined로 무한 재연결하는 유령 탭이 생기므로 여기서 차단한다.
  if (!res.ok) { showToast(`세션 열기 실패: ${tmuxName} (${res.status})`); return; }
  const data = await res.json();
  if (!data.id) { showToast(`세션 열기 실패: ${tmuxName}`); return; }
  // 이미 웹에 열려 있으면 해당 탭으로 전환
  if (getSession(data.id)) {
    switchTo(data.id);
  } else {
    addSession(data.id, data.name || data.id);
    // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
    const s = getSession(data.id);
    if (s) s.tmuxName = data.tmux_session || tmuxName;
    // L8에서 발견: addSession() 안의 saveWorkspace()는 tmuxName이 붙기 **전에**
    // 돌기 때문에, 마지막으로 붙인 세션은 스냅샷에 tmux_name:null로 남아
    // 있었다(그 뒤에 또 다른 세션이 붙어 다시 저장되지 않는 한). 그러면 다음
    // 부팅에서 그 탭은 "순수 PTY"로 복원돼 tmuxName이 영영 안 채워지고,
    // openSessionOnMac()·레이아웃 복원이 그 세션을 tmux로 못 알아본다.
    saveWorkspace();
  }
}

// export — showAddMenu(session.js)의 "tmux 세션" 메뉴 항목이 부른다.
export async function createTmuxSession() {
  // "맥에서도 열기" 토글이 켜져 있으면 서버가 osascript로 iTerm 창도 함께 연다.
  const autoMac = document.getElementById('auto-mac-checkbox')?.checked;
  const res = await apiFetch(`${API_BASE}/api/tmux/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(autoMac ? { auto_open_on_mac: true } : {}),
  });
  if (!res.ok) { showToast(`tmux 세션 생성 실패 (${res.status})`); return; }
  const data = await res.json();
  if (!data.id) { showToast('tmux 세션 생성 실패'); return; }
  addSession(data.id, data.name || data.id);
  // ⚠ tmuxName 미설정 시 openSessionOnMac()이 "tmux 세션 아님"으로 오판한다.
  const s = getSession(data.id);
  if (s) s.tmuxName = data.tmux_session;
}

// showOnboarding()(boot.js)이 만드는 온보딩 화면의 버튼은 innerHTML 문자열
// onclick="...createTmuxSession()"이라 모듈 경계와 무관하게 window를 거쳐야 한다.
window.createTmuxSession = createTmuxSession;

// F3(c): data-action 위임용 등록.
registerAction('session.tmux-list', () => showTmuxSessions());
