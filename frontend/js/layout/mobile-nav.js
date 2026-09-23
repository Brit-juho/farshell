// N38(70-mobile.md §1/§2) — 모바일 골격: 세션 홈(탭) · 하단 내비
// 52px(세션|터미널|변경|첨부|설정). compact.js(L3
// 2단계)와 같은 위치(layout/)에 두지만 역할은 다르다 — compact.js는 "지금이
// compact인가"와 "leaf를 어떤 순서로 스와이프하나"만 안다(트리는 안 건드림).
// 이 파일은 그 위에 얹히는 **탭 골격**(플릿/터미널/변경/첨부/설정) 하나를
// 새로 갖는다 — 기존 1페인 스와이프·keybar 동작은 그대로 둔 채, 플릿 탭일
// 때만 `#vt-fleet-slot`을 터미널 위에 오버레이한다(§1 지시: "그 위에 하단
// 내비 구조를 얹는다").
// 2026-09-23 — 「큐」 탭을 뺐다(실사용 적음, 빠른 열기 `:`에서 여전히 연다).
// 「작업」은 「세션」으로 이름을 바꿨다 — 세션 이동·이름 변경·재우기·완전
// 종료를 전부 이 탭(shell/Fleet.tsx)이 맡으므로 헤더의 세션 이동 UI는
// 없앴다(70-mobile.md). 「더보기」는 「설정」이 됐다 — 예전 홈(파일/포트/
// 사용량 바로가기)은 설정 화면 자체의 첫 화면(panels/settings.js)이 대신한다.
//
// 이 파일은 main.js가 다른 layout/* 모듈처럼 **정적 import**한다(지연 청크
// 아님) — 그래서 core/settings.js·core/store.js·agent/state.js·term/session.js를
// window 브리지 없이 그대로 import해도 된다(Rail.tsx/Fleet.tsx가 지연
// 청크라 못 하는 것과 다른 점). Fleet.tsx 자체만 동적 import로 지연 로드한다
// (ADR-26 — solid-js 런타임을 app.js에 정적으로 넣으면 300KiB 상한을 넘는다).
import { get as settingsGet, set as settingsSet } from '../core/settings.js';
import { vtFetch } from '../core/api.js';
import { getSession, activeSessionId } from '../core/store.js';
import { allStatuses, onStatusChange } from '../agent/state.js';

const BADGE_POLL_MS = 15000;

const _bottomEl = document.getElementById('vt-mnav-bottom');
const _fleetSlot = document.getElementById('vt-fleet-slot');

// 이 둘 중 하나라도 없으면(예: 테스트 하네스가 index.html 일부만 로드) 이
// 모듈은 조용히 아무 것도 하지 않는다 — 다른 layout/* 모듈들의 관행과 같다.
const _wired = Boolean(_bottomEl && _fleetSlot);

// compact(<720px)에서만 실제로 켠다 — 데스크톱에서 이 파일이 초기화 시점에
// 곧장 Fleet 지연 청크를 내려받으면(기본 탭이 'fleet'이므로) 아무도 안 볼
// 화면을 위해 solid-js 런타임을 낭비하게 된다. isCompactMode()(pointer:coarse
// 겸용, compact.js)가 아니라 폭만 보는 이유는 CSS 쪽 가시성 문턱(위 legacy.css
// N38 블록, `@media (max-width:719px)`)과 정확히 맞춰야 하기 때문이다 — 배지
// 폴링·Fleet 마운트가 "화면에 보이지도 않는데 도는" 상태와 "보이는데 안 도는"
// 상태 둘 다 피한다.
const _mq = window.matchMedia('(max-width:719px)');

let _tab = null;
let _baseTab = null;
let _fleetMountPromise = null;

// 2026-09-23 — 'vt-queue' 항목을 뺐다: 큐 탭 자체가 하단 내비에서 없어져서,
// 팔레트로 큐 패널을 열어도 칠할 하단 내비 탭이 더는 없다(칠하지 않는 게
// 맞다 — 없는 탭을 억지로 켜면 다른 탭이 눌린 것처럼 보인다).
// 'vt-files'는 'more'(설정 홈의 도구 바로가기)가 아니라 전용 「첨부」 탭이 됐다.
const PANEL_TAB = {
  'vt-dock-scm': 'changes',
  'vt-settings': 'more',
  'vt-files': 'files',
  'vt-ports': 'more',
  'vt-usage': 'more',
};

function _paintNav(tab) {
  _tab = tab;
  document.documentElement.dataset.mobileTab = tab;
  for (const btn of _bottomEl.querySelectorAll('[data-mnav]')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.mnav === tab));
  }
  document.dispatchEvent(new CustomEvent('vt:mobile-tab', { detail: { tab } }));
}

function _closeMobilePanels() {
  for (const el of document.querySelectorAll('.vt-viewer-backdrop.vt-mobile-route')) {
    if (typeof window.closePanel === 'function') window.closePanel(el.id, { history: 'replace' });
    else el.remove();
  }
}

function _ensureFleetMounted() {
  if (_fleetMountPromise || !_fleetSlot || !_mq.matches) return;
  _fleetMountPromise = import('../shell/Fleet.tsx')
    .then(({ mountFleet }) => mountFleet(_fleetSlot, {
      vtFetch,
      onOpenTerminal: () => setTab('terminal'),
    }))
    .catch((e) => { console.error('[FarShell fleet]', e); _fleetMountPromise = null; });
}

// 「플릿」·「터미널」 전환 — 하단 내비의 두 "진짜" 탭(§1: 나머지 셋은 이미
// 있는 모달 액션을 그대로 여는 트리거일 뿐이다). #terminal-container는 절대
// 숨기지 않는다(styles/layers/legacy.css N38 주석 — WebGL 컨텍스트 보존 +
// SIGWINCH 0건 계약, 대신 #vt-fleet-slot을 그 위에 덮는다).
export function setTab(tab) {
  if (!_wired || (tab !== 'fleet' && tab !== 'terminal')) return;
  _baseTab = tab;
  _paintNav(tab);
  if (tab === 'fleet') {
    _fleetSlot.hidden = false;
    _ensureFleetMounted();
  } else {
    _fleetSlot.hidden = true;
  }
  // ui.mobile.home(60-settings-palette.md §1 — device 스코프): 다음에 이
  // 기기에서 열 때의 시작 탭. 큐/변경/설정은 모달일 뿐이라 저장 대상이 아니다.
  settingsSet('ui.mobile.home', tab);
}

export function currentTab() {
  return _tab;
}

function _refreshWaitingCount() {
  const el = document.getElementById('vt-mnav-badge-waiting');
  if (!el) return;
  let n = 0;
  for (const s of allStatuses().values()) if (s === 'waiting') n += 1;
  if (n > 0) { el.hidden = false; el.textContent = n > 99 ? '99+' : String(n); }
  else el.hidden = true;
}

// ── 하단 내비 배지 — Dock.tsx(N35 §6)의 배지 폴링과 **같은 소스·같은 응답**
// (`/api/git/status`)을 재사용한다. Dock.tsx를 직접 import할 수 없는
// 이유(지연 청크 정적 import 금지)는 같지만, 이 파일은 지연 청크가 아니므로
// 굳이 window 브리지를 거칠 필요 없이 같은 호출을 반복한다(Dock.tsx 자신의
// 주석에도 있는 관행 — "응답 두 개, 15초"). -->

// 「첨부」 배지 — 진행 중인 업로드 개수. term/upload.js(지연 로드가 아니다 —
// 파일 input이 최상위 정적 마크업이라 부팅 시 항상 로드됨)의 `vtUploadJobs`
// 브리지를 읽는다. fetch가 필요 없는 로컬 상태라 폴링보다 `vt:upload-progress`
// 이벤트(업로드 진행마다 발생)에 얹는 쪽이 더 정확하고 더 싸다.
function _refreshFilesBadge() {
  const badge = document.getElementById('vt-mnav-badge-files');
  if (!badge) return;
  const jobs = (typeof window.vtUploadJobs === 'function') ? window.vtUploadJobs() : [];
  const n = jobs.filter((j) => j.phase === 'queued' || j.phase === 'uploading' || j.phase === 'saving').length;
  if (n > 0) { badge.hidden = false; badge.textContent = n > 99 ? '99+' : String(n); }
  else badge.hidden = true;
}

// 「변경」 배지 — 지금 보고 있는 세션(활성 탭)의 cwd 기준 git diff 파일 수.
// Rail.tsx/Fleet.tsx는 세션 전체를 훑지만, 하단 내비 배지는 "지금 이 화면"
// 기준이 자연스럽다(다른 세션의 diff는 플릿 탭에서 각 행의 배지로 이미 보인다).
async function _refreshChangesBadge() {
  const badge = document.getElementById('vt-mnav-badge-changes');
  if (!badge) return;
  try {
    const s = getSession(activeSessionId());
    const tmuxName = s && (s.tmuxName || s.tmux_name);
    if (!tmuxName) { badge.hidden = true; return; }
    const list = await vtFetch('/api/tmux/sessions');
    const info = (list || []).find((t) => t.name === tmuxName);
    if (!info || !info.cwd) { badge.hidden = true; return; }
    const data = await vtFetch(`/api/git/status?repo=${encodeURIComponent(info.cwd)}`);
    const n = data && data.repo ? (data.files || []).length : 0;
    if (n > 0) { badge.hidden = false; badge.textContent = n > 99 ? '99+' : String(n); }
    else badge.hidden = true;
  } catch (_) { badge.hidden = true; }
}

function _refreshBadges() {
  if (!_mq.matches || document.hidden) return;
  _refreshFilesBadge();
  _refreshChangesBadge();
}

function _init() {
  if (!_wired) return;

  for (const btn of _bottomEl.querySelectorAll('[data-mnav]')) {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.mnav;
      _closeMobilePanels();
      if (tab === 'fleet' || tab === 'terminal') setTab(tab);
      else {
        _fleetSlot.hidden = true;
        _paintNav(tab);
      }
    });
  }

  document.addEventListener('vt:panel-open', (e) => {
    const tab = PANEL_TAB[e.detail?.id];
    if (_mq.matches && tab) _paintNav(tab);
  });
  document.addEventListener('vt:panel-close', (e) => {
    const tab = PANEL_TAB[e.detail?.id];
    if (_mq.matches && tab && _tab === tab) _paintNav(_baseTab || 'fleet');
  });

  const initial = settingsGet('ui.mobile.home') === 'terminal' ? 'terminal' : 'fleet';
  setTab(initial);
  // 처음엔 wide였다가 리사이즈/회전으로 compact가 되는 경우 — 그 시점에야
  // 플릿 탭이면 청크를 내려받는다(이미 fleet 탭이면 setTab 재호출 없이도
  // 마운트만 하면 된다).
  _mq.addEventListener('change', () => { if (_tab === 'fleet') _ensureFleetMounted(); });

  onStatusChange(_refreshWaitingCount);
  _refreshWaitingCount();

  // 업로드 배지는 폴링이 아니라 진행 이벤트에 얹는다 — 로컬 상태라 fetch가
  // 필요 없고, 15초를 기다리지 않고 즉시 갱신된다.
  document.addEventListener('vt:upload-progress', _refreshFilesBadge);
  _refreshFilesBadge();

  _refreshBadges();
  setInterval(_refreshBadges, BADGE_POLL_MS);
}

_init();
