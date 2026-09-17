// N38(70-mobile.md §1/§2) — 모바일 골격: 상단 24px(호스트 칩 + 대기 수) ·
// 플릿 홈(탭) · 하단 내비 52px(플릿|터미널|변경|큐|설정). compact.js(L3
// 2단계)와 같은 위치(layout/)에 두지만 역할은 다르다 — compact.js는 "지금이
// compact인가"와 "leaf를 어떤 순서로 스와이프하나"만 안다(트리는 안 건드림).
// 이 파일은 그 위에 얹히는 **탭 골격**(플릿/터미널/변경/큐/설정) 하나를
// 새로 갖는다 — 기존 1페인 스와이프·keybar 동작은 그대로 둔 채, 플릿 탭일
// 때만 `#vt-fleet-slot`을 터미널 위에 오버레이한다(§1 지시: "그 위에 하단
// 내비 구조를 얹는다").
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

const _topEl = document.getElementById('vt-mnav-top');
const _bottomEl = document.getElementById('vt-mnav-bottom');
const _fleetSlot = document.getElementById('vt-fleet-slot');

// 이 셋이 하나라도 없으면(예: 테스트 하네스가 index.html 일부만 로드) 이
// 모듈은 조용히 아무 것도 하지 않는다 — 다른 layout/* 모듈들의 관행과 같다.
const _wired = Boolean(_topEl && _bottomEl && _fleetSlot);

// compact(<720px)에서만 실제로 켠다 — 데스크톱에서 이 파일이 초기화 시점에
// 곧장 Fleet 지연 청크를 내려받으면(기본 탭이 'fleet'이므로) 아무도 안 볼
// 화면을 위해 solid-js 런타임을 낭비하게 된다. isCompactMode()(pointer:coarse
// 겸용, compact.js)가 아니라 폭만 보는 이유는 CSS 쪽 가시성 문턱(위 legacy.css
// N38 블록, `@media (max-width:719px)`)과 정확히 맞춰야 하기 때문이다 — 배지
// 폴링·Fleet 마운트가 "화면에 보이지도 않는데 도는" 상태와 "보이는데 안 도는"
// 상태 둘 다 피한다.
const _mq = window.matchMedia('(max-width:719px)');

let _tab = null;
let _fleetMountPromise = null;

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
  _tab = tab;
  document.documentElement.dataset.mobileTab = tab;
  for (const btn of _bottomEl.querySelectorAll('[data-mnav]')) {
    btn.setAttribute('aria-pressed', String(btn.dataset.mnav === tab));
  }
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

// ── 상단 바 — 호스트 칩 + 대기 수 ────────────────────────────────────────
function _refreshWaitingCount() {
  const el = document.getElementById('vt-mnav-waiting');
  if (!el) return;
  let n = 0;
  for (const s of allStatuses().values()) if (s === 'waiting') n += 1;
  if (n > 0) { el.hidden = false; el.textContent = `대기 ${n}`; }
  else el.hidden = true;
  _syncTopBar();
}

// 2.1.6 — 이 칩은 헤더(#topbar)가 이미 같은 문자열("farshell / 호스트명")을
// 보여주고 있어서, 폰에서 화면 맨 위 62px이 **같은 말을 두 번** 하고 있었다.
// 386px 폭에서 그건 세션 행 두 개만큼의 자리다.
//
// 그래서 헤더가 그 문구를 실제로 그리고 있으면 칩은 비운다. 헤더가 없거나
// (레이아웃이 바뀌어) 안 보이면 칩이 다시 자기 역할을 한다 — 어느 쪽이든
// "호스트 이름이 화면에서 사라지는" 일은 없다.
//
// `대기 N`은 헤더에 없는 정보라 그대로 둔다. 상단 바 자체는 보여줄 게 하나도
// 없을 때만 숨는다(_syncTopBar).
function _headerShowsHost() {
  const brand = document.querySelector('#topbar .vt-workspace-name');
  if (!brand) return false;
  return !!(brand.textContent || '').trim() && brand.getClientRects().length > 0;
}

function _syncTopBar() {
  const bar = document.getElementById('vt-mnav-top');
  const chip = document.getElementById('vt-mnav-host-chip');
  const waiting = document.getElementById('vt-mnav-waiting');
  if (!bar) return;
  const hasChip = !!chip && !!(chip.textContent || '').trim();
  const hasWaiting = !!waiting && !waiting.hidden;
  bar.hidden = !hasChip && !hasWaiting;
}

async function _loadHostChip() {
  const el = document.getElementById('vt-mnav-host-chip');
  if (!el) return;
  if (_headerShowsHost()) { el.textContent = ''; _syncTopBar(); return; }
  let text = 'farshell';
  try {
    const c = await vtFetch('/api/capabilities');
    if (c && c.hostname) text = `farshell / ${c.hostname}`;
  } catch (_) { /* 이름을 못 받아도 'farshell'은 보여준다 */ }
  // **await 뒤에 다시 본다.** 헤더 칩은 지연 청크가 채우므로, 이 fetch가
  // 도는 동안 헤더가 준비되는 일이 실제로 일어난다 — 그때 아래 MutationObserver가
  // 칩을 비우고 나서 이 응답이 도착해 **지운 자리에 다시 쓰는** 경쟁이 됐다
  // (실브라우저에서 중복이 그대로 남는 걸로 재현). 쓰기 직전이 유일하게
  // 안전한 판단 시점이다.
  el.textContent = _headerShowsHost() ? '' : text;
  _syncTopBar();
}

// ── 하단 내비 배지 — Dock.tsx(N35 §6)의 배지 폴링과 **같은 소스·같은 응답**
// (`/api/queue`·`/api/git/status`)을 재사용한다. Dock.tsx를 직접 import할 수
// 없는 이유(지연 청크 정적 import 금지)는 같지만, 이 파일은 지연 청크가
// 아니므로 굳이 window 브리지를 거칠 필요 없이 같은 호출을 반복한다
// (Dock.tsx 자신의 주석에도 있는 관행 — "응답 두 개, 15초"). -->
async function _refreshQueueBadge() {
  const badge = document.getElementById('vt-mnav-badge-queue');
  if (!badge) return;
  try {
    const data = await vtFetch('/api/queue');
    const n = ((data && data.items) || []).length;
    if (n > 0) { badge.hidden = false; badge.textContent = n > 99 ? '99+' : String(n); }
    else badge.hidden = true;
  } catch (_) { badge.hidden = true; }
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
  _refreshQueueBadge();
  _refreshChangesBadge();
}

function _init() {
  if (!_wired) return;

  for (const btn of _bottomEl.querySelectorAll('[data-mnav]')) {
    btn.addEventListener('click', () => setTab(btn.dataset.mnav));
  }

  const initial = settingsGet('ui.mobile.home') === 'terminal' ? 'terminal' : 'fleet';
  setTab(initial);
  // 처음엔 wide였다가 리사이즈/회전으로 compact가 되는 경우 — 그 시점에야
  // 플릿 탭이면 청크를 내려받는다(이미 fleet 탭이면 setTab 재호출 없이도
  // 마운트만 하면 된다).
  _mq.addEventListener('change', () => { if (_tab === 'fleet') _ensureFleetMounted(); });

  _loadHostChip();
  // 헤더의 워크스페이스 칩은 지연 청크(shell/HeaderExtras.tsx)가 나중에
  // 채운다 — _loadHostChip()이 먼저 돌면 "헤더에 아직 없다"고 판단해 중복
  // 문구를 그대로 넣는다. 슬롯이 채워지는 순간 다시 판단한다.
  const slot = document.getElementById('vt-workspace-chip-slot');
  if (slot) {
    const mo = new MutationObserver(() => {
      if (!_headerShowsHost()) return;
      const chip = document.getElementById('vt-mnav-host-chip');
      if (chip) chip.textContent = '';
      _syncTopBar();
      mo.disconnect();
    });
    mo.observe(slot, { childList: true, subtree: true });
  }
  onStatusChange(_refreshWaitingCount);
  _refreshWaitingCount();

  _refreshBadges();
  setInterval(_refreshBadges, BADGE_POLL_MS);
}

_init();
