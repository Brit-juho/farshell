// localStorage 워크스페이스(탭 목록 스냅샷) 저장/복원. F4에서 terminal.js
// (구 :1133-1272)에서 분리. Phase 8 G7.
//
// restoreWorkspace/reconcileMissingTmuxSessions는 addSession/switchTo(session.js
// 소유)를 부르고, session.js의 여러 함수(renameSession/switchTo/removeSession)는
// 반대로 saveWorkspace를 부른다 — 상호 import(순환)이지만 전부 이벤트 콜백 안에서만
// 쓰여 모듈 평가 시점엔 실행되지 않으므로 안전하다(ES 모듈 circular import는
// 라이브 바인딩이라 호출 시점에만 실제로 존재하면 된다).
import { allSessions, getSession, activeSessionId } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { addSession, switchTo } from './session.js';

const WORKSPACE_KEY = 'vt-workspace-v1';

export function saveWorkspace() {
  try {
    const tabs = Array.from(document.querySelectorAll('#tabs .tab')).map(tab => {
      const id = tab.dataset.sessionId;
      const s = getSession(id);
      const nameSpan = tab.querySelector('.tab-name');
      return {
        id,
        name: nameSpan ? nameSpan.textContent : '',
        tmux_name: s && s.tmuxName ? s.tmuxName : null,
      };
    });
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify({
      version: 1,
      active_id: activeSessionId(),
      tabs,
    }));
  } catch (e) { /* localStorage 실패 무시 */ }
}

export async function restoreWorkspace() {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (!raw) return false;
    const state = JSON.parse(raw);
    if (!state || !Array.isArray(state.tabs) || state.tabs.length === 0) return false;
    let restored = 0;
    let failed = 0;
    let firstNewId = null;
    // tmux_name이 없는(순수 PTY) 탭은 서버가 재시작되지 않은 한 session_store에
    // 그대로 살아있을 수 있다 — /api/sessions로 살아있는 id 목록을 한 번에 확인.
    // 서버 응답의 tmux_name도 함께 들고 있는다 — 스냅샷에 tmux_name이 비어
    // 있어도(과거에 저장된 스냅샷, L8에서 고친 저장 시점 문제 등) 서버가
    // 아는 값으로 채워줄 수 있다. 이게 없으면 그 탭은 "순수 PTY"로 복원돼
    // tmuxName이 계속 비어 있고, 레이아웃 복원(layout/persist.js)이 tmux
    // 이름으로 그 세션을 다시 찾지 못한다.
    let liveWebIds = new Set();
    let liveTmuxNames = new Map();
    try {
      const wsRes = await apiFetch(`${API_BASE}/api/sessions`);
      if (wsRes.ok) {
        const live = await wsRes.json();
        liveWebIds = new Set(live.map(s => s.id));
        liveTmuxNames = new Map(live.filter(s => s.tmux_name).map(s => [s.id, s.tmux_name]));
      }
    } catch (_) { /* 조회 실패 시 아래에서 전부 유실 처리됨 */ }

    for (const tab of state.tabs) {
      if (tab.tmux_name) {
        try {
          const res = await apiFetch(`${API_BASE}/api/tmux/attach`, {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ name: tab.tmux_name })
          });
          if (res.ok) {
            const data = await res.json();
            if (data.id) {
              if (getSession(data.id)) {
                // 같은 PTY 이미 있으면 skip
              } else {
                addSession(data.id, data.name || `tmux:${tab.tmux_name}`);
                const s = getSession(data.id);
                if (s) s.tmuxName = tab.tmux_name;
                if (!firstNewId) firstNewId = data.id;
              }
              restored++;
            } else { failed++; }
          } else { failed++; }
        } catch (_) { failed++; }
      } else {
        // 비-tmux(순수 PTY) 탭: 원래 조용히 버려지던 부분 — 서버가 아직
        // 그 PTY를 들고 있으면(=재시작 안 됐으면) 그대로 재연결한다.
        if (liveWebIds.has(tab.id)) {
          if (!getSession(tab.id)) {
            addSession(tab.id, tab.name || tab.id);
            if (!firstNewId) firstNewId = tab.id;
          }
          const rec = getSession(tab.id);
          if (rec && !rec.tmuxName && liveTmuxNames.has(tab.id)) rec.tmuxName = liveTmuxNames.get(tab.id);
          restored++;
        } else {
          failed++;
        }
      }
    }
    if (failed > 0) {
      showToast(`탭 ${failed}개 복원 실패 (세션이 이미 종료됨)`);
    }
    if (restored > 0 && firstNewId) {
      switchTo(firstNewId);
    }
    return restored > 0;
  } catch (e) { return false; }
}

// localStorage 워크스페이스 스냅샷은 "마지막 저장 시점"만 기억한다 — 그 이후
// 다른 탭/기기/CLI(fsh)에서 만들어진 tmux 세션은 서버엔 실제로 살아있어도
// 이 스냅샷에 없으면 restoreWorkspace()가 절대 못 찾는다. 서버의 진짜 tmux
// 목록과 대조해 빠진 것만 추가로 붙인다(활성 탭은 건드리지 않는다).
export async function reconcileMissingTmuxSessions() {
  const keepActive = activeSessionId(); // addSession()이 매번 switchTo()를 호출하므로 복원해야 함
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    const tmuxList = await res.json();
    if (!Array.isArray(tmuxList)) return;
    const sessions = allSessions();
    const known = new Set(Object.values(sessions).map(s => s.tmuxName).filter(Boolean));
    const missing = tmuxList.filter(s => !known.has(s.name));
    if (missing.length === 0) return;

    // 저장된 스냅샷에 있던 순서를 알아야 "원래 자리"에 되돌려 넣을 수 있다
    // (attach가 일시 실패해서 restoreWorkspace가 놓쳤던 세션 등). 스냅샷에
    // 없던(=저장 이후 다른 곳에서 새로 생긴) 세션은 원래 자리가 없으므로
    // 알파벳/숫자 순으로 정렬해 끝에 붙인다.
    let savedOrder = [];
    try {
      const raw = localStorage.getItem(WORKSPACE_KEY);
      const state = raw ? JSON.parse(raw) : null;
      if (state && Array.isArray(state.tabs)) savedOrder = state.tabs;
    } catch (_) { /* 파싱 실패 시 전부 새 세션 취급 */ }

    const knownFromSnapshot = missing.filter(s => savedOrder.some(t => t.tmux_name === s.name));
    const genuinelyNew = missing.filter(s => !savedOrder.some(t => t.tmux_name === s.name))
      .sort((a, b) => a.name.localeCompare(b.name));

    let added = false;
    for (const s of [...knownFromSnapshot, ...genuinelyNew]) {
      const res2 = await apiFetch(`${API_BASE}/api/tmux/attach`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ name: s.name })
      });
      if (!res2.ok) continue;
      const data = await res2.json();
      if (data.id && !getSession(data.id)) {
        // 스냅샷 순서상 이 세션 다음에 와야 할, 이미 DOM에 있는 탭을 찾아 그 앞에 삽입
        let insertBeforeId = null;
        const idx = savedOrder.findIndex(t => t.tmux_name === s.name);
        if (idx !== -1) {
          for (let i = idx + 1; i < savedOrder.length; i++) {
            const nextId = Object.keys(sessions).find(sid => sessions[sid].tmuxName === savedOrder[i].tmux_name);
            if (nextId) { insertBeforeId = nextId; break; }
          }
        }
        addSession(data.id, data.name || `tmux:${s.name}`, insertBeforeId);
        const s2 = getSession(data.id);
        if (s2) s2.tmuxName = s.name;
        added = true;
      }
    }
    if (added && keepActive && getSession(keepActive)) switchTo(keepActive);
  } catch (e) { /* 서버 통신 실패 시 조용히 무시 — 복원된 탭은 이미 정상 동작 중 */ }
}

export function clearWorkspace() {
  localStorage.removeItem(WORKSPACE_KEY);
}
window.clearWorkspace = clearWorkspace; // 콘솔에서 호출 가능
