// 앱 부팅 오케스트레이션 + 온보딩(빈 상태) 화면. F4에서 terminal.js(구
// :1614-1759)에서 분리 — 계획서(§4)는 이 boot 시퀀스가 "tmux 패널 코드 사이에
// 끼어 있다"고 지적했는데, 실제로 뽑아보니 tmux-panel.js/session.js/workspace.js
// 사이 의존이 얽혀 있어 결국 이 파일이 그 세 모듈을 조립하는 최상위가 됐다.
// 계획서가 예상한 panels/onboarding.js로의 흡수(L4)는 이번 F4 범위가 아니다.
import { addSession, createSession } from './session.js';
import { attachTmux } from './tmux-panel.js';
import { restoreWorkspace, reconcileMissingTmuxSessions } from './workspace.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { getSession } from '../core/store.js';

export function showOnboarding() {
  const el = document.createElement('div');
  el.id = 'onboarding';
  el.className = 'vt-onboarding';
  el.innerHTML = `
    <div class="vt-ob-icon"><i class="icon-mic"></i></div>
    <h2>FarShell</h2>
    <p>음성으로 터미널을 조작하세요.<br>tmux 세션을 만들거나, 새 터미널을 시작할 수 있습니다.</p>
    <div id="ob-sessions" class="vt-ob-sessions" hidden></div>
    <div class="vt-ob-actions">
      <button class="vt-btn-primary" onclick="document.getElementById('onboarding').remove();createTmuxSession()">tmux 세션 시작</button>
      <button class="vt-btn-secondary" onclick="document.getElementById('onboarding').remove();createSession()">일반 터미널</button>
    </div>
    <p class="vt-ob-hint">맥북에서 Ctrl+Shift+V로 음성 입력 (voice daemon 실행 시)</p>
  `;
  document.body.appendChild(el);
  renderOnboardingSessions();
}

// U1: 탭 0개(온보딩) 화면이 살아있는 tmux 세션을 모르는 채로 "새 세션" 버튼만
// 보여주던 문제 — showTmuxSessions()의 목록 로직(buildTmuxRow와 동일한 배지/문구)을
// 온보딩 안에도 그려서, 새로 만들지 않고 기존 세션으로 바로 들어갈 수 있게 한다.
//
// L1(U1 보강, Orca 패턴): fetch가 끝나야 목록이 나타나면 그 사이 빈 칸이었다가
// 갑자기 목록이 생겨서 아래 버튼들이 밀려 내려간다(레이아웃 점프). 직전 목록을
// localStorage에 캐시해뒀다가 즉시(비활성 상태로) 그리고, 실제 fetch 결과가
// 오면 그걸로 다시 그려 갱신 + 캐시 반영한다 — 첫 실행(캐시 없음)만 빈 칸에서
// 시작하고 그 이후로는 항상 레이아웃이 안정적이다.
const OB_SESSIONS_CACHE_KEY = 'vt_ob_sessions_cache';
function _drawOnboardingSessions(list, tmuxList, interactive) {
  if (!Array.isArray(tmuxList) || tmuxList.length === 0) {
    list.hidden = true;
    list.innerHTML = '';
    return;
  }
  list.hidden = false;
  list.classList.toggle('vt-ob-sessions-pending', !interactive);
  list.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'vt-ob-sessions-title';
  title.textContent = `살아있는 tmux 세션 ${tmuxList.length}개`;
  list.appendChild(title);
  for (const s of tmuxList) {
    const row = document.createElement('div');
    row.className = 'vt-ob-row';
    const openInWeb = !!s.web_session_id;
    const badge = openInWeb ? '🟢' : (s.attached > 0 ? '🖥️' : '💤');
    const statusText = openInWeb ? '웹에 열림' : (s.attached > 0 ? '데스크톱 attach' : '잠듦');
    const label = document.createElement('span');
    label.className = 'vt-ob-row-label';
    const cmd = s.command ? ` · ${s.command}` : '';
    label.textContent = `${badge} ${s.name}  (${s.windows}win · ${statusText}${cmd})`;
    if (interactive) {
      label.title = '이 세션 열기';
      label.onclick = async () => {
        document.getElementById('onboarding')?.remove();
        await attachTmux(s.name);
      };
    } else {
      label.title = '연결 확인 중...';
    }
    row.appendChild(label);
    list.appendChild(row);
  }
}

async function renderOnboardingSessions() {
  const list = document.getElementById('ob-sessions');
  if (!list) return; // 그 사이 온보딩이 닫혔으면 조용히 무시

  // 1) 캐시가 있으면 비활성 상태로 즉시 그린다 — fetch 대기 중에도 레이아웃이
  //    미리 자리를 잡아서, 실제 목록이 도착했을 때 아래 버튼이 밀리지 않는다.
  try {
    const cached = JSON.parse(localStorage.getItem(OB_SESSIONS_CACHE_KEY) || 'null');
    if (Array.isArray(cached) && cached.length > 0) _drawOnboardingSessions(list, cached, false);
  } catch (_) { /* 캐시 파싱 실패 시 빈 칸에서 시작 */ }

  let tmuxList = [];
  try {
    const res = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    tmuxList = await res.json();
  } catch (_) { /* 서버 오류 시 목록 없이 버튼만 — 아래에서 캐시본도 정리 */ }
  if (!document.getElementById('ob-sessions')) return; // fetch 중 닫혔을 수 있음

  // 2) 실제 결과로 다시 그리고(클릭 가능) 캐시 갱신 — 세션이 사라졌으면 캐시도 비운다.
  _drawOnboardingSessions(list, tmuxList, true);
  try {
    if (Array.isArray(tmuxList) && tmuxList.length > 0) {
      localStorage.setItem(OB_SESSIONS_CACHE_KEY, JSON.stringify(tmuxList));
    } else {
      localStorage.removeItem(OB_SESSIONS_CACHE_KEY);
    }
  } catch (_) { /* localStorage 실패 무시 */ }
}

// 시작 시: URL hash에 #tmux=<name>이 있으면 해당 세션 우선 attach (handoff 링크)
//         → localStorage 워크스페이스 복원 (Phase 8 G7)
//         → 기존 웹 세션 복원 → tmux 세션 자동 attach → 온보딩
//
// F4에서 이 블록은 더 이상 모듈 평가 시점에 자동 실행되지 않는다(구 terminal.js에서는
// classic script 로드 순서상 이 코드가 실행될 즈음 ui/toast.js가 이미 로드돼 있었지만,
// 이제 term/*.js는 전부 main.js의 정적 import — legacy classic script(toast.js 포함)
// 보다 먼저 평가된다). 그대로 즉시 실행하면 showToast가 아직 없는 채로 restoreWorkspace
// 등이 그걸 부를 수 있다(레이스). main.js가 toast.js 로드 완료를 기다린 뒤 bootApp()을
// 명시적으로 호출한다.
export async function bootApp() {
  try {
    // 0. handoff 링크 (#tmux=<name>) 처리
    const hashParams = new URLSearchParams(location.hash.slice(1));
    const targetTmux = hashParams.get('tmux');
    if (targetTmux) {
      await attachTmux(targetTmux);
      return;
    }

    // 0.5. localStorage 워크스페이스 복원 (Phase 8 G7)
    if (await restoreWorkspace()) {
      // 복원된 스냅샷은 "마지막 저장 시점" 기준이라 그 이후 다른 곳에서 생긴
      // tmux 세션을 놓칠 수 있다 — 서버 목록과 대조해 누락분만 보충.
      await reconcileMissingTmuxSessions();
      return;
    }

    // 1. 기존 웹 세션 복원
    const res = await apiFetch(`${API_BASE}/api/sessions`);
    const existing = await res.json();
    if (existing.length > 0) {
      for (const s of existing) {
        addSession(s.id, s.name || s.id);
        // ⚠ 없으면 openSessionOnMac()이 진짜 tmux 세션도 "tmux 아님"으로 오판한다.
        if (s.tmux_name) {
          const rec = getSession(s.id);
          if (rec) rec.tmuxName = s.tmux_name;
        }
      }
      return;
    }

    // 2. tmux 세션이 있으면 전부 자동 attach (저장된 탭 순서가 없는 상태이므로
    //    이름 알파벳/숫자 순으로 정렬 — 예전엔 tmuxList[0] 하나만 열고 끝냈다)
    const tmuxRes = await apiFetch(`${API_BASE}/api/tmux/sessions`);
    const tmuxList = await tmuxRes.json();
    if (tmuxList.length > 0) {
      const sorted = [...tmuxList].sort((a, b) => a.name.localeCompare(b.name));
      for (const s of sorted) {
        await attachTmux(s.name);
      }
      return;
    }

    // 3. 아무것도 없으면 온보딩 표시
    showOnboarding();
  } catch (e) {
    createSession();
  }
}
