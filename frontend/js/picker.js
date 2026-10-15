// 세션 피커(모바일 상단 세션 이름 → 「세션」 탭 전환) + 맥에서 열기 + 파일 업로드 — F5에서
// classic script에서 ES 모듈로 전환.
//
// term/session.js는 탭 전환/제거 시 updateSessionPicker를 부른다. 세션 목록·관리는 「세션」 탭
// (shell/Fleet.tsx)이 맡는다 — 예전 상단 시트(layout/session-sheet.js)는 거기로 합쳤다.
import { activeSession, activeSessionId, sessionDisplayName, getSession } from './core/store.js';
import { openSessionIds } from './layout/compact.js';
import { onLayoutChange } from './layout/store.js';
import { apiFetch } from './core/api.js';
import { API_BASE } from './core/env.js';
import { registerAction } from './core/dom.js';
import { uploadFiles } from './term/upload.js';
import { t } from './core/i18n.js';

export function updateSessionPicker() {
  const picker = document.getElementById('voice-session-picker');
  if (!picker) return;
  const s = activeSession();
  // 화면 순서(뷰 탭 → 칸) — 스와이프·키 이동과 같은 목록(layout/compact.js).
  const entries = openSessionIds();
  const activeId = activeSessionId();
  const position = entries.indexOf(activeId) + 1;
  // 2026-09-29(H1) — 두 줄: 윗줄 세션 이름(주인공), 아랫줄 "기기 · 3/5".
  // 기기 이름은 layout/env-chip.js가 html[data-vt-machine]에 둔다(늦게 오면
  // 'vt:machine' 이벤트로 다시 그린다). 이름은 사용자 입력이라 textContent로만 넣는다.
  const name = document.createElement('span');
  name.className = 'vt-sp-name';
  name.textContent = s ? sessionDisplayName(activeId) : t('세션');
  const row = document.createElement('span');
  row.className = 'vt-sp-row';
  row.append(name);
  const meta = document.createElement('span');
  meta.className = 'vt-sp-meta';
  const machine = document.documentElement.dataset.vtMachine || '';
  const pos = s && position > 0 ? `${position}/${entries.length}` : '';
  meta.textContent = [machine, pos].filter(Boolean).join(' · ');
  picker.replaceChildren(row, ...(meta.textContent ? [meta] : []));
}

// 순서·n/m의 출처가 레이아웃이므로 레이아웃이 바뀌면(부팅 복원, 칸 드롭, 뷰 재정렬)
// 라벨도 같이 다시 그린다 — 전환 경로만 갱신하면 복원 직후 n/m이 비어 있었다.
onLayoutChange((_tree, _pane, kind) => { if (kind !== 'ratio') updateSessionPicker(); });

// --- 이 세션 맥에서 열기 (tmux 세션을 iTerm에 나중에 attach) ---
// explicitSessionId: 레일 컨텍스트 메뉴처럼 "지금 보고 있지 않은 세션"을 대상으로
// 부를 때 쓴다 — activeSessionId를 그 세션으로 바꿔치기하면(예전 방식) 포커스가
// 딴 데로 튀므로, 여기서 대상만 직접 지정해 activeSession을 건드리지 않는다.
async function openSessionOnMac(explicitSessionId) {
  const s = explicitSessionId ? getSession(explicitSessionId) : activeSession();
  if (!s) { showToast(t('열려 있는 세션이 없습니다'), 'error'); return; }
  const tmuxName = s.tmuxName || s.tmux_name;
  if (!tmuxName) { showToast(t('이 세션은 tmux 세션이 아니라 맥에서 열 수 없습니다'), 'error'); return; }
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/open-on-mac`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: tmuxName }),
    });
    const data = await res.json();
    if (data.skipped) showToast(t("'{name}'은 이미 맥에서 열려 있습니다", { name: tmuxName }), 'success');
    else if (data.ok) showToast(t("맥 터미널에 '{name}' 열림", { name: tmuxName }), 'success');
    else showToast(t('맥에서 열기 실패: {error}', { error: data.error || '' }), 'error');
  } catch (e) {
    showToast(t('맥에서 열기 실패: {error}', { error: e.message }), 'error');
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
// 상단 세션 이름 — 팝업이 아니라 하단 「세션」 탭 전환이다(layout/mobile-nav.js가 받는다).
registerAction('session.manager', () => document.dispatchEvent(new CustomEvent('vt:sessions-toggle')));
registerAction('session.open-on-mac', () => openSessionOnMac());
// Rail.tsx가 특정 세션을 지정해 부르는 자리 — 정적 import 불가(레일 지연 청크
// 제약, session-actions.js 머리말과 같은 이유)라 window로 브리지한다.
window.vtOpenSessionOnMac = openSessionOnMac;

document.addEventListener('vt:machine', () => updateSessionPicker());
