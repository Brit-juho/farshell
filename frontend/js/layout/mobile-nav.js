// N38(70-mobile.md §1/§2) — 모바일 골격: 세션 홈(탭) · 하단 내비
// 52px(세션|터미널|변경|파일|설정).
// 파일 개편 단계 5(2026-11, 사용자 선택 1 「나」) — 「첨부」 탭 자리를 「파일」 탭(폴더 아이콘)으로 바꿨다. 탭을 누르면
// 안쪽 맨 위 「파일 | 첨부」 전환이 보이고 파일(트리, `vt-dir`)이 기본이다. 첨부(`vt-files`)는 예전 화면 그대로다.
// 마지막에 본 쪽은 `ui.mobile.filesSegment`(기기 설정)가 기억한다. 올리는 중 배지(`#vt-mnav-badge-files`)는
// 파일 탭에 그대로 붙고, 전환의 「첨부」 쪽에도 같은 수를 적는다. 「변경」 탭에는 전환이 없다. compact.js(L3
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
import { createPoll } from '../core/poll.js';
import { getAction, registerAction } from '../core/dom.js';
import { t } from '../core/i18n.js';
import { getSession, activeSessionId } from '../core/store.js';
import { allStatuses, onStatusChange } from '../agent/state.js';
import { pruneBlankViews } from './store.js';
import { saveLayoutNow } from './persist.js';

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
  'vt-dir': 'files',
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
      getAction,
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
    // 폰에는 뷰 탭 바가 없어 칸 ×가 남긴 빈 뷰를 치울 길이 없다 — 세션 탭을 열 때 정리한다.
    if (_mq.matches && pruneBlankViews() > 0) saveLayoutNow();
    _fleetSlot.hidden = false;
    _ensureFleetMounted();
  } else {
    _fleetSlot.hidden = true;
  }
  // ui.mobile.home(60-settings-palette.md §1 — device 스코프): 다음에 이
  // 기기에서 열 때의 시작 탭. 큐/변경/설정은 모달일 뿐이라 저장 대상이 아니다.
  settingsSet('ui.mobile.home', tab);
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

// ── 「파일」 탭의 「파일 | 첨부」 전환 ─────────────────────────────────────────
// 두 화면은 각자의 패널(`vt-dir` 트리 · `vt-files` 첨부)이다. 전환은 compact의 탭 화면으로 열린 그 패널 머리
// 바로 아래에 끼워 넣는다 — 패널 렌더러 둘은 이 전환을 모른다(데스크톱 사이드바에는 따로 있는 활동이라 필요 없다).
export const FILES_SEGMENTS = [
  { id: 'tree', panel: 'vt-dir', action: 'dir.show', label: () => t('파일') },
  { id: 'attach', panel: 'vt-files', action: 'files.show', label: () => t('첨부') },
];

/** 지금 저장된 전환 값(모르는 값이면 트리). */
export function filesSegment() {
  return settingsGet('ui.mobile.filesSegment') === 'attach' ? 'attach' : 'tree';
}

function _segmentOf(panelId) {
  return FILES_SEGMENTS.find((s) => s.panel === panelId) || null;
}

function _uploadCount() {
  const jobs = (typeof window.vtUploadJobs === 'function') ? window.vtUploadJobs() : [];
  return jobs.filter((j) => j.phase === 'queued' || j.phase === 'uploading' || j.phase === 'saving').length;
}

/** 패널 머리 아래에 전환을 끼운다. 이미 있으면 상태만 맞춘다. */
export function mountFilesSegment(panelEl) {
  const seg = _segmentOf(panelEl && (panelEl._vtPanelId || panelEl.id));
  const card = panelEl && panelEl.querySelector('.vt-viewer-card');
  if (!seg || !card) return null;
  let bar = card.querySelector(':scope > .vt-mfiles-seg');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'vt-mfiles-seg';
    bar.setAttribute('role', 'tablist');
    bar.setAttribute('aria-label', t('파일 | 첨부'));
    for (const s of FILES_SEGMENTS) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'vt-mfiles-seg-btn';
      b.dataset.seg = s.id;
      b.setAttribute('role', 'tab');
      const label = document.createElement('span');
      label.textContent = s.label();
      b.appendChild(label);
      if (s.id === 'attach') {
        const badge = document.createElement('span');
        badge.className = 'vt-mfiles-seg-badge';
        badge.hidden = true;
        b.appendChild(badge);
      }
      b.addEventListener('click', () => switchFilesSegment(s.id));
      bar.appendChild(b);
    }
    const head = card.querySelector(':scope > .vt-viewer-head');
    if (head) head.after(bar); else card.prepend(bar);
  }
  for (const b of bar.querySelectorAll('.vt-mfiles-seg-btn')) {
    const on = b.dataset.seg === seg.id;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  }
  _paintSegmentBadge(bar);
  return bar;
}

function _paintSegmentBadge(bar) {
  const badge = bar && bar.querySelector('.vt-mfiles-seg-badge');
  if (!badge) return;
  const n = _uploadCount();
  badge.hidden = n <= 0;
  badge.textContent = n <= 0 ? '' : n > 99 ? '99+' : String(n);
}

/** 전환 — 기억하고, 지금 패널을 닫고 다른 쪽을 연다. */
export function switchFilesSegment(id) {
  const next = FILES_SEGMENTS.find((s) => s.id === id);
  if (!next) return;
  settingsSet('ui.mobile.filesSegment', next.id);
  if (document.getElementById(next.panel)) return;   // 이미 그 쪽이다
  for (const s of FILES_SEGMENTS) {
    if (s.panel === next.panel) continue;
    const el = document.getElementById(s.panel);
    if (el && typeof window.closePanel === 'function') window.closePanel(el.id, { history: 'replace' });
  }
  const fn = getAction(next.action);
  if (typeof fn === 'function') fn();
}

/** 「파일」 탭 — 마지막에 본 쪽을 연다. */
export function openFilesTab() {
  const seg = FILES_SEGMENTS.find((s) => s.id === filesSegment()) || FILES_SEGMENTS[0];
  const fn = getAction(seg.action);
  if (typeof fn === 'function') fn();
}

// 「파일」 탭 배지 — 진행 중인 업로드 개수. term/upload.js(지연 로드가 아니다 —
// 파일 input이 최상위 정적 마크업이라 부팅 시 항상 로드됨)의 `vtUploadJobs`
// 브리지를 읽는다. fetch가 필요 없는 로컬 상태라 폴링보다 `vt:upload-progress`
// 이벤트(업로드 진행마다 발생)에 얹는 쪽이 더 정확하고 더 싸다.
function _refreshFilesBadge() {
  const badge = document.getElementById('vt-mnav-badge-files');
  if (!badge) return;
  const n = _uploadCount();
  if (n > 0) { badge.hidden = false; badge.textContent = n > 99 ? '99+' : String(n); }
  else badge.hidden = true;
  for (const bar of document.querySelectorAll('.vt-mfiles-seg')) _paintSegmentBadge(bar);
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
  } catch (e) {
    badge.hidden = true;
    throw e;   // 폴러(core/poll.js)가 간격을 늘린다
  }
}

// 폴러를 통해서만 불린다 — 이전 조회가 안 끝났으면 건너뛰고, 실패하면 간격을 늘린다.
// 숨은 탭에서는 쉰다(core/poll.js).
function _refreshBadges() {
  if (!_mq.matches) return undefined;
  _refreshFilesBadge();
  return _refreshChangesBadge();
}

// 상단 세션 이름 — 세션 탭과 터미널을 오간다(탭 전환). 다른 탭(변경·첨부·설정)이 떠 있으면 세션 탭으로.
function _toggleSessionsTab() {
  if (!_mq.matches) return;
  _closeMobilePanels();
  setTab(_tab === 'fleet' ? 'terminal' : 'fleet');
}

registerAction('mfiles.show', openFilesTab);

function _init() {
  if (!_wired) return;
  document.addEventListener('vt:sessions-toggle', _toggleSessionsTab);

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

  // 세션 탭이 떠 있는 동안 헤더 이름이 눌린 상태로 보이게 한다.
  document.addEventListener('vt:mobile-tab', (e) => {
    document.getElementById('voice-session-picker')?.setAttribute('aria-pressed', String(e.detail?.tab === 'fleet'));
  });

  document.addEventListener('vt:panel-open', (e) => {
    const tab = PANEL_TAB[e.detail?.id];
    if (_mq.matches && tab) _paintNav(tab);
    // 「파일」 탭의 두 화면은 compact 탭 화면으로 열릴 때 전환을 단다(데스크톱 사이드바·칸에는 달지 않는다).
    if (e.detail?.mobileRoute && _segmentOf(e.detail.id)) {
      const el = document.getElementById(e.detail.id);
      if (el) mountFilesSegment(el);
    }
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

  createPoll(_refreshBadges, { interval: BADGE_POLL_MS, immediate: true }).start();
}

_init();
