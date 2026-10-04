// 세션 피커(모바일 세션 관리 시트) + 맥에서 열기 + 파일 업로드 — F5에서
// classic script에서 ES 모듈로 전환.
//
// term/session.js와 순환 import 관계다 — 여기는 revealSession/removeSession/
// renameSession을 부르고, term/session.js는 탭 전환/제거 시 updateSessionPicker를
// 부른다. panels/viewer의 shell.js↔tree.js와 같은 이유로 그대로 유지한다(모든
// 참조가 함수 본문 안에서만 일어나 ES 모듈이 정상 지원).
import { activeSession, activeSessionId, allSessions, sessionDisplayName, getSession } from './core/store.js';
import { apiFetch } from './core/api.js';
import { API_BASE } from './core/env.js';
import { registerAction, getAction } from './core/dom.js';
import { revealSession, renameSession } from './term/session.js';
import { closeSession, openSessionFromRail, confirmAndKillSession } from './term/session-actions.js';
import { uploadFiles } from './term/upload.js';
import { _focusables } from './panels/panel.js';
import { icon } from './ui/icons.js';
import { promptDialog } from './core/dialog.js';

export function updateSessionPicker() {
  const picker = document.getElementById('voice-session-picker');
  if (!picker) return;
  const s = activeSession();
  const entries = Object.keys(allSessions());
  const activeId = activeSessionId();
  const position = entries.indexOf(activeId) + 1;
  // 2026-09-29(H1) — 두 줄: 윗줄 세션 이름(주인공) + ▾, 아랫줄 "기기 · 3/5".
  // 기기 이름은 layout/env-chip.js가 html[data-vt-machine]에 둔다(늦게 오면
  // 'vt:machine' 이벤트로 다시 그린다). 이름은 사용자 입력이라 textContent로만 넣는다.
  const name = document.createElement('span');
  name.className = 'vt-sp-name';
  name.textContent = s ? sessionDisplayName(activeId) : '세션';
  const row = document.createElement('span');
  row.className = 'vt-sp-row';
  row.append(name);
  row.insertAdjacentHTML('beforeend', icon('chevron-down', 14));
  const meta = document.createElement('span');
  meta.className = 'vt-sp-meta';
  const machine = document.documentElement.dataset.vtMachine || '';
  const pos = s && position > 0 ? `${position}/${entries.length}` : '';
  meta.textContent = [machine, pos].filter(Boolean).join(' · ');
  picker.replaceChildren(row, ...(meta.textContent ? [meta] : []));
  const sheet = document.getElementById('session-manager');
  if (sheet) renderSessionManager(sheet);
}

function sessionName(id) {
  return sessionDisplayName(id);
}

function openSessionManager() {
  let backdrop = document.getElementById('session-manager');
  if (backdrop) { closeSessionManager(); return; }
  backdrop = document.createElement('div');
  backdrop.id = 'session-manager';
  backdrop.className = 'vt-session-backdrop';
  backdrop.setAttribute('role', 'presentation');
  backdrop.innerHTML = '<section class="vt-session-sheet" role="dialog" aria-modal="true" aria-labelledby="session-manager-title"></section>';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeSessionManager(); });
  document.body.appendChild(backdrop);
  document.getElementById('voice-session-picker')?.setAttribute('aria-expanded', 'true');
  renderSessionManager(backdrop);
  const close = backdrop.querySelector('.vt-session-close');
  if (close) close.focus();
  // D7: 포커스 트랩 — openPanel()의 모달들과 같은 규칙(panels/panel.js 참고).
  backdrop._onKeydown = (e) => {
    if (e.key === 'Escape') { closeSessionManager(); return; }
    if (e.key !== 'Tab') return;
    const items = _focusables(backdrop);
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!backdrop.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener('keydown', backdrop._onKeydown);
}

function closeSessionManager() {
  const backdrop = document.getElementById('session-manager');
  if (!backdrop) return;
  if (backdrop._onKeydown) document.removeEventListener('keydown', backdrop._onKeydown);
  backdrop.remove();
  const picker = document.getElementById('voice-session-picker');
  if (picker) { picker.setAttribute('aria-expanded', 'false'); picker.focus(); }
}

// ADR-29 E — tmux 목록 팝업(옛 term/tmux-panel.js의 showTmuxSessions)을
// 없애면서, 그게 하던 일(열리지 않은 tmux 세션 보기 + 열기 + 종료)을 이
// 시트가 직접 흡수한다. `/api/tmux/sessions`는 이 브라우저 탭에 열리지 않은
// 것까지 포함해 내려주므로, 이미 열린 세션의 tmux 이름과 겹치는 것만 걸러내면
// "열리지 않은 것"만 남는다 — 레일의 판정과 같은 생각이다.
async function fetchTmuxSessions() {
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    const list = await res.json();
    return Array.isArray(list) ? list : [];
  } catch (_) { return []; }
}

async function renderSessionManager(backdrop) {
  const sheet = backdrop.querySelector?.('.vt-session-sheet');
  if (!sheet) return;
  // "맥에서도 열기"는 (특정 세션이 아니라) 앞으로 만들 모든 세션에 적용되는
  // 전역 설정이라 ⋯의 「설정」 그룹 쪽에 남긴다(index.html 참고).
  sheet.innerHTML = `<div class="vt-session-head"><h2 id="session-manager-title">세션 관리</h2><button class="vt-icon-btn xl vt-session-close" type="button" aria-label="세션 관리 닫기">${icon('x', 16)}</button></div><div class="vt-session-list"></div><div class="vt-session-footer"><button type="button" class="vt-session-footer-item" id="session-open-mac-btn">지금 이 세션 맥에서 열기</button></div>`;
  sheet.querySelector('.vt-session-close').onclick = closeSessionManager;
  const list = sheet.querySelector('.vt-session-list');
  const entries = Object.entries(allSessions());
  const activeIdNow = activeSessionId();
  const awakeTmuxNames = new Set();
  for (const [id, s] of entries) {
    const tmuxName = s && (s.tmuxName || s.tmux_name);
    if (tmuxName) awakeTmuxNames.add(tmuxName);
    const row = document.createElement('div');
    row.className = 'vt-session-row' + (id === activeIdNow ? ' active' : '');
    const select = document.createElement('button');
    select.type = 'button'; select.className = 'vt-name-btn vt-session-select'; select.textContent = sessionName(id);
    select.setAttribute('aria-current', id === activeIdNow ? 'true' : 'false');
    // 2026-09-21(사용자 지적) — Rail.tsx의 openRow와 같은 이유로 revealSession.
    // entries는 allSessions()라 이미 열려 있는 세션뿐이다 — "가서 보여줘"가
    // 맞는 동작이지 "배정"이 아니다.
    select.onclick = () => { revealSession(id); closeSessionManager(); };
    const rename = document.createElement('button');
    rename.type = 'button'; rename.className = 'vt-icon-btn xl vt-session-action'; rename.innerHTML = icon('pencil', 16); rename.setAttribute('aria-label', `${sessionName(id)} 이름 변경`);
    rename.onclick = async () => {
      const next = await promptDialog('새 세션 이름', { title: '이름 변경', defaultValue: sessionName(id) });
      if (next === null) return;
      if (await renameSession(id, next)) renderSessionManager(backdrop);
    };
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'vt-icon-btn xl danger vt-session-action'; close.innerHTML = icon('x', 16); close.setAttribute('aria-label', `${sessionName(id)} 닫기`);
    // 닫기 — 레일·칸 ×와 같은 함수(term/session-actions.js closeSession).
    close.onclick = async () => { await closeSession(id); if (document.body.contains(backdrop)) renderSessionManager(backdrop); };
    row.append(select, rename, close);
    // tmux 세션은 완전 종료도 여기서 — 지금까지 모바일에서 이 동작에 닿는
    // 유일한 길이 tmux 목록 팝업이었다(ADR-29 E, 그 팝업을 없앴다).
    if (tmuxName) {
      const kill = document.createElement('button');
      kill.type = 'button'; kill.className = 'vt-icon-btn xl danger vt-session-action'; kill.innerHTML = icon('trash-2', 16);
      kill.setAttribute('aria-label', `${tmuxName} 완전 종료`);
      kill.onclick = async () => {
        if (await confirmAndKillSession(tmuxName, id)) { if (document.body.contains(backdrop)) renderSessionManager(backdrop); }
      };
      row.appendChild(kill);
    }
    list.appendChild(row);
  }

  const sleeping = (await fetchTmuxSessions()).filter((s) => s && s.name && !awakeTmuxNames.has(s.name));
  for (const s of sleeping) {
    const row = document.createElement('div');
    row.className = 'vt-session-row sleeping';
    const wake = document.createElement('button');
    wake.type = 'button'; wake.className = 'vt-name-btn vt-session-select'; wake.textContent = s.name;
    wake.title = '열기';
    // 레일 클릭과 같은 규칙 — 기억된 묶음 뷰가 있으면 통째로, 없으면 새 단독 뷰.
    wake.onclick = async () => { closeSessionManager(); await openSessionFromRail({ tmuxName: s.name }); };
    const kill = document.createElement('button');
    kill.type = 'button'; kill.className = 'vt-icon-btn xl danger vt-session-action'; kill.innerHTML = icon('trash-2', 16);
    kill.setAttribute('aria-label', `${s.name} 완전 종료`);
    kill.onclick = async () => {
      if (await confirmAndKillSession(s.name, s.web_session_id || null)) { if (document.body.contains(backdrop)) renderSessionManager(backdrop); }
    };
    row.append(wake, kill);
    list.appendChild(row);
  }

  if (!entries.length && !sleeping.length) { list.innerHTML = '<p class="vt-session-empty">열려 있는 세션이 없습니다.</p>'; }

  sheet.querySelector('#session-open-mac-btn').onclick = () => { getAction('session.open-on-mac')?.(); };
}

// --- 이 세션 맥에서 열기 (tmux 세션을 iTerm에 나중에 attach) ---
// explicitSessionId: 레일 컨텍스트 메뉴처럼 "지금 보고 있지 않은 세션"을 대상으로
// 부를 때 쓴다 — activeSessionId를 그 세션으로 바꿔치기하면(예전 방식) 포커스가
// 딴 데로 튀므로, 여기서 대상만 직접 지정해 activeSession을 건드리지 않는다.
async function openSessionOnMac(explicitSessionId) {
  const s = explicitSessionId ? getSession(explicitSessionId) : activeSession();
  if (!s) { showToast('열려 있는 세션이 없습니다', 'error'); return; }
  const tmuxName = s.tmuxName || s.tmux_name;
  if (!tmuxName) { showToast('이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다', 'error'); return; }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/open-on-mac`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: tmuxName }),
    });
    const data = await res.json();
    if (data.skipped) showToast(`'${tmuxName}'은 이미 맥에서 열려 있습니다`, 'success');
    else if (data.ok) showToast(`맥 iTerm에 '${tmuxName}' 열림`, 'success');
    else showToast('맥에서 열기 실패: ' + (data.error || ''), 'error');
  } catch (e) {
    showToast('맥에서 열기 실패: ' + e.message, 'error');
  }
}

// --- 파일 업로드 ---
// index.html의 <input type="file" onchange="uploadFile(this)">가 직접 부른다 —
// 인라인 핸들러라 window 브리지가 필요하다(F3(c)에서도 data-action으로 못 옮긴
// 것과 같은 종류: change 이벤트 위임은 F3(c)의 클릭 위임 범위 밖이었다).
//
// R6 — 알맹이는 term/upload.js 하나다. 드롭(터미널 칸·파일 패널)과 이 input이
// **같은 코드**를 타야 "여러 개를 올렸을 때 경로가 어떻게 들어가는가" 같은
// 규칙이 진입점마다 갈라지지 않는다. 여기는 input을 비우는 일만 더 한다 —
// 같은 파일을 연달아 두 번 고를 수 있어야 하므로.
async function uploadFile(input) {
  await uploadFiles(input.files, { insertPath: true });
  input.value = '';
}
window.uploadFile = uploadFile;

// F3(c): data-action 위임용 등록. (인자 없이 호출 — 대상은 항상 activeSession.)
registerAction('session.manager', () => openSessionManager());
registerAction('session.open-on-mac', () => openSessionOnMac());
// Rail.tsx가 특정 세션을 지정해 부르는 자리 — 정적 import 불가(레일 지연 청크
// 제약, session-actions.js 머리말과 같은 이유)라 window로 브리지한다.
window.vtOpenSessionOnMac = openSessionOnMac;

document.addEventListener('vt:machine', () => updateSessionPicker());
