// 앱 부팅 오케스트레이션 + 온보딩(빈 상태) 화면. F4에서 terminal.js(구
// :1614-1759)에서 분리 — 계획서(§4)는 이 boot 시퀀스가 "tmux 패널 코드 사이에
// 끼어 있다"고 지적했는데, 실제로 뽑아보니 tmux-panel.js/session.js/workspace.js
// 사이 의존이 얽혀 있어 결국 이 파일이 그 세 모듈을 조립하는 최상위가 됐다.
// 계획서가 예상한 panels/onboarding.js로의 흡수(L4)는 이번 F4 범위가 아니다.
import { waitForAuthentication, requireAuthentication } from '../core/auth-state.js';
import { attachTmux } from './session.js';
import { attachLayoutSessions } from './workspace.js';
import { restoreLayout, loadLayoutSnapshot, snapshotSessionInfos, pruneClosedByLive, recoverLostPanes } from '../layout/persist.js';
import { onWorkspaceEvent } from '../core/workspace-ws.js';
import { isLayoutEmpty } from '../layout/store.js';
import { load as loadSettings } from '../core/settings.js';
import { apiFetch } from '../core/api.js';
import { hostKey } from '../core/host-route.js';
import { API_BASE } from '../core/env.js';
import { allSessions } from '../core/store.js';
import { icon } from '../ui/icons.js';
import { displayComboHtml } from '../core/keymap.js';
import { tmuxStatus, tmuxStatusDot } from '../ui/session-badge.js';
import { showToast } from '../ui/toast.js';
import { t } from '../core/i18n.js';

export function showOnboarding() {
  const el = document.createElement('div');
  el.id = 'onboarding';
  el.className = 'vt-onboarding';
  el.innerHTML = `
<div class="vt-ob-icon">${icon('mic', 22)}</div>
<h2>FarShell</h2>
<p>${t('음성으로 터미널을 조작하세요.')}<br>${t('tmux 세션을 만들거나, 새 터미널을 시작할 수 있습니다.')}</p>
<div id="ob-sessions" class="vt-ob-sessions" hidden></div>
<div class="vt-ob-actions">
<button class="vt-btn solid lg" onclick="document.getElementById('onboarding').remove();createTmuxSession()">${t('tmux 세션 시작')}</button>
<button class="vt-btn lg" onclick="document.getElementById('onboarding').remove();createSession()">${t('일반 터미널')}</button>
</div>
<p class="vt-ob-hint">${t('맥북에서 {combo}로 음성 입력 (voice daemon 실행 시)', { combo: displayComboHtml('Ctrl+Shift+M') })}</p>
  `;
  document.body.appendChild(el);
  renderOnboardingSessions();
}

// U1: 탭 0개(온보딩) 화면이 살아있는 tmux 세션을 모르는 채로 "새 세션" 버튼만
// 보여주던 문제 — showTmuxSessions()의 목록 로직(buildTmuxRow와 동일한 배지/문구)을
// 온보딩 안에도 그려서, 새로 만들지 않고 기존 세션으로 바로 들어갈 수 있게 한다.
//
// L1(U1 보강): fetch가 끝나야 목록이 나타나면 그 사이 빈 칸이었다가
// 갑자기 목록이 생겨서 아래 버튼들이 밀려 내려간다(레이아웃 점프). 직전 목록을
// localStorage에 캐시해뒀다가 즉시(비활성 상태로) 그리고, 실제 fetch 결과가
// 오면 그걸로 다시 그려 갱신 + 캐시 반영한다 — 첫 실행(캐시 없음)만 빈 칸에서
// 시작하고 그 이후로는 항상 레이아웃이 안정적이다.
const OB_SESSIONS_CACHE_KEY = hostKey('vt_ob_sessions_cache');
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
  title.textContent = t('살아있는 tmux 세션 {n}개', { n: tmuxList.length });
  list.appendChild(title);
  for (const s of tmuxList) {
    const row = document.createElement('div');
    row.className = 'vt-ob-row';
    const { text: statusText } = tmuxStatus(s);
    const label = document.createElement('span');
    label.className = 'vt-ob-row-label';
    label.style.cssText = 'display:flex;align-items:center;';
    const cmd = s.command ? ` · ${s.command}` : '';
    label.append(tmuxStatusDot(s), document.createTextNode(`${s.name}  (${s.windows}win · ${statusText}${cmd})`));
    if (interactive) {
      label.title = t('이 세션 열기');
      label.onclick = async () => {
        document.getElementById('onboarding')?.remove();
        await attachTmux(s.name);
      };
    } else {
      label.title = t('연결 확인 중...');
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

// 시작 시: 저장된 레이아웃 복원 → #tmux=<name> 핸드오프 → 뷰가 없으면 온보딩.
//
// F4에서 이 블록은 더 이상 모듈 평가 시점에 자동 실행되지 않는다(구 terminal.js에서는
// classic script 로드 순서상 이 코드가 실행될 즈음 ui/toast.js가 이미 로드돼 있었지만,
// 이제 term/*.js는 전부 main.js의 정적 import — legacy classic script(toast.js 포함)
// 보다 먼저 평가된다). 그대로 즉시 실행하면 showToast가 아직 없는 채로 복원 경로가
// 그걸 부를 수 있다(레이스). main.js가 toast.js 로드 완료를 기다린 뒤 bootApp()을
// 명시적으로 호출한다.
// 떠나는 문서의 부팅은 fetch가 중단되며 예외로 끝난다. 그건 실패가 아니다.
let _leaving = false;
let _bootRetryCount = 0;
window.addEventListener('pagehide', () => { _leaving = true; });

export async function bootApp() {
  if (_leaving || !await waitForAuthentication()) return;
  // S2: 설정은 캐시로 이미 동기 적용돼 있고(core/settings.js 모듈 평가 시점),
  // 여기서는 서버 값으로 맞추기만 한다 — 다른 기기에서 바꾼 설정이 이 시점에
  // 반영된다. 실패해도 부팅을 막지 않는다(캐시 값으로 그대로 동작).
  loadSettings().catch(() => {});
  try {
    // 2026-10-03(세션·뷰 규칙 2단계) — 저장된 레이아웃만 복원한다(불변식 3).
    // 이 브라우저 탭의 저장본, 없으면 가장 최근 저장본의 사본(새 탭)을 고르고,
    // 그 **열린 칸**에 있던 세션만 attach한 뒤 트리를 적용한다. 서버의 tmux
    // 세션을 전부 붙이던 경로(옛 1·2번, reconcileMissingTmuxSessions)는 없앴다 —
    // 사용자가 닫은 세션을 새로고침이 다시 열었다. 열리지 않은 세션은 레일·
    // 온보딩 목록에서 연다.
    const snap = await loadLayoutSnapshot();
    if (snap) await attachLayoutSessions(snapshotSessionInfos(snap));
    // 레이아웃이 없어도 반드시 한 번 부른다 — 저장 가드가 여기서 열린다.
    // #tmux= 핸드오프도 이 뒤에 처리해 저장이 꺼지지 않게 한다(예전엔 핸드오프가
    // 레이아웃 복원을 건너뛰어 그 탭은 다시 저장되지 않았다).
    await restoreLayout(snap);
    // 닫은 묶음 기억 정리 — 연결(재연결 포함)마다 서버가 현재 tmux 목록을 한 번 밀어 주고, 이후
    // 바뀔 때마다 또 온다. 이미 종료된 세션이 든 항목을 거기서 비운다(session-actions 재열기와 짝).
    try {
      onWorkspaceEvent('tmux_sessions', (msg) => {
        pruneClosedByLive(msg && msg.sessions);
        // 복원 때 못 찾았던 세션이 목록에 돌아오면 그 칸에 다시 붙인다(빈 뷰로 굳지 않게).
        recoverLostPanes(msg && msg.sessions, (name) => attachTmux(name, { place: 'none' })).catch(() => {});
      });
    } catch (_) { /* 정리는 부가 기능 */ }

    // handoff 링크(#tmux=<name>) — 이미 열려 있으면 그 칸으로, 아니면 새 단독 뷰.
    const targetTmux = new URLSearchParams(location.hash.slice(1)).get('tmux');
    if (targetTmux) await attachTmux(targetTmux);

    // 뷰가 하나도 없으면 온보딩. **지금 이 시점에** 없을 때만 — fetch를 기다리는
    // 사이 사용자가 이미 「새 세션」을 눌렀을 수 있다(z-index:500이라 세션을 가린다).
    if (isLayoutEmpty() && Object.keys(allSessions()).length === 0) showOnboarding();
    if (_bootRetryCount >= 3) showToast(t('저장된 화면을 다시 연결했습니다'), 'success', { key: 'boot-restore' });
    _bootRetryCount = 0;
  } catch (e) {
    if (e.status === 401 && !_leaving) {
      requireAuthentication();
      if (await waitForAuthentication() && !_leaving) return bootApp();
      return;
    }
    if (e.retryable && !_leaving) {
      // 실패한 attach 뒤 restoreLayout을 실행하면 저장된 tmux 연결이 빈 칸으로
      // 덮인다. 서버가 돌아올 때까지 원본 스냅샷을 유지하고 부팅을 다시 시도한다.
      const delay = Math.min(5000, 250 * 2 ** Math.min(_bootRetryCount++, 5));
      if (_bootRetryCount === 3) showToast(t('저장된 화면에 다시 연결하는 중입니다'), 'warn',
                                           { key: 'boot-restore', duration: 0 });
      await new Promise((resolve) => setTimeout(resolve, delay));
      if (!_leaving) return bootApp();
      return;
    }
    // ADR-45 — 여기서 세션을 만들면 안 된다. 예전엔 createSession()이었고, 부팅 중
    // 새로고침·터널 끊김만으로 서버에 요청하지 않은 셸이 하나씩 생겼다(e2e 온보딩
    // 간헐 실패의 원인). 세션은 온보딩 버튼으로 사용자가 만든다.
    if (_leaving) return;
    console.error('[FarShell boot]', e);
    if (Object.keys(allSessions()).length === 0) showOnboarding();
    showToast(t('서버에서 세션 목록을 받지 못했습니다 — 새로고침해 보세요'), 'warn');
  }
}
