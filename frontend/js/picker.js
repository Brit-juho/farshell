import { pushScope } from './core/keyboard-scopes.js';
// 세션 피커(모바일 세션 관리 시트) + 맥에서 열기 + 파일 업로드 — F5에서
// classic script에서 ES 모듈로 전환.
//
// term/session.js는 탭 전환/제거 시 updateSessionPicker를 부른다(이 파일 → session.js 방향의
// 정적 import는 목록 렌더링과 함께 layout/session-sheet.js로 옮겨 순환이 없어졌다).
// 시트의 목록 렌더링 전부는 지연 청크(`screens`)다 — 상시 코드(core.js)는 열기/닫기·배선만 갖는다.
import { activeSession, activeSessionId, sessionDisplayName, getSession } from './core/store.js';
import { openSessionIds } from './layout/compact.js';
import { onLayoutChange, pruneBlankViews } from './layout/store.js';
import { isCompactMode } from './layout/compact.js';
import { saveLayoutNow } from './layout/persist.js';
import { apiFetch } from './core/api.js';
import { API_BASE } from './core/env.js';
import { registerAction, getAction } from './core/dom.js';
import { uploadFiles } from './term/upload.js';
import { _focusables } from './panels/panel.js';
import { icon } from './ui/icons.js';
import { onWorkspaceEvent } from './core/workspace-ws.js';

export function updateSessionPicker() {
  const picker = document.getElementById('voice-session-picker');
  if (!picker) return;
  const s = activeSession();
  // 화면 순서(뷰 탭 → 칸) — 스와이프·키 이동과 같은 목록(layout/compact.js).
  const entries = openSessionIds();
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

// 순서·n/m의 출처가 레이아웃이므로 레이아웃이 바뀌면(부팅 복원, 칸 드롭, 뷰 재정렬)
// 라벨도 같이 다시 그린다 — 전환 경로만 갱신하면 복원 직후 n/m이 비어 있었다.
onLayoutChange((_tree, _pane, kind) => { if (kind !== 'ratio') updateSessionPicker(); });

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
  // 청크가 올 때까지의 틀 — 내용은 layout/session-sheet.js가 채운다.
  backdrop.innerHTML = '<section class="vt-session-sheet" role="dialog" aria-modal="true" aria-labelledby="session-manager-title" aria-busy="true"><div class="vt-session-head"><h2 id="session-manager-title">세션 관리</h2></div><p class="vt-session-loading">불러오는 중</p></section>';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) closeSessionManager(); });
  // 모바일: 시트를 열 때 비어 있는 단독 뷰(「빈 화면」)를 정리한다 — 폰에는 뷰 탭 바가 없어
  // 칸 ×가 남긴 빈 뷰를 치울 길이 없다. 데스크톱은 「칸 ×는 빈 칸으로 남김」 규칙 그대로.
  if (isCompactMode() && pruneBlankViews() > 0) saveLayoutNow();
  document.body.appendChild(backdrop);
  backdrop._offSessions = onWorkspaceEvent('tmux_sessions', (msg) => {
    if (!Array.isArray(msg.sessions)) return;
    backdrop._snapshot = msg.sessions;
    if (backdrop.isConnected) renderSessionManager(backdrop);
  });
  document.getElementById('voice-session-picker')?.setAttribute('aria-expanded', 'true');
  renderSessionManager(backdrop);
  // D7: 포커스 트랩 — openPanel()의 모달들과 같은 규칙(panels/panel.js 참고).
  backdrop._onKeydown = (e) => {
    if (document.activeElement?.closest('.vt-dialog')) return;
    if (e.key === 'Escape') { closeSessionManager(); return true; }
    if (e.key !== 'Tab') return;
    const items = _focusables(backdrop);
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!backdrop.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  };
  backdrop._disposeKeys = pushScope({ id: 'session-manager', element: backdrop, modal: true, onKey: backdrop._onKeydown });
}

function closeSessionManager() {
  const backdrop = document.getElementById('session-manager');
  if (!backdrop) return;
  backdrop._disposeKeys?.();
  backdrop._offSessions?.();
  backdrop.remove();
  const picker = document.getElementById('voice-session-picker');
  if (picker) { picker.setAttribute('aria-expanded', 'false'); picker.focus(); }
}

// 목록 렌더링은 layout/session-sheet.js(지연 청크). 열린 시트를 다시 그리라는 요청은 모두 여기로 온다.
// 한 번 받은 뒤에는 모듈을 쥐고 **동기로** 부른다 — 편집 모드의 다시 그리기가 마이크로태스크 늦으면
// 방금 닫은 뷰의 안내 문구 같은 일회성 상태가 낡은 목록에 먼저 소비된다.
let _sheet = null;
function renderSessionManager(backdrop) {
  const run = (m) => m.renderSessionSheet(backdrop, {
    close: closeSessionManager,
    refresh: () => { if (backdrop.isConnected) renderSessionManager(backdrop); },
  });
  if (_sheet) return run(_sheet);
  return import('./layout/session-sheet.js')
    .then((m) => { _sheet = m; return run(m); })
    .catch((e) => {
      console.error('[FarShell sheet]', e);
      const l = backdrop.querySelector('.vt-session-loading');
      if (l) l.textContent = '목록을 불러오지 못했습니다';
    });
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
    else if (data.ok) showToast(`맥 터미널에 '${tmuxName}' 열림`, 'success');
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
