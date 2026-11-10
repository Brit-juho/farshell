// N5/N40/N46(60-settings-palette.md §3) — 팔레트 진입점(app.js 쪽에 남는
// 스텁). 실제 렌더링은 shell/Palette.tsx(지연 청크 shell.js)가 맡는다 —
// panels/viewer-lazy.js(N35)와 같은 패턴: 액션 등록 + 실제 `import()`만
// 여기서 전담하고, 무거운 것(Solid 런타임 등)은 열 때만 받는다.
//
// 이 파일은 **정적으로** core/store.js·term/session.js·layout/store.js·
// agent/preview.js 등을 import한다 — 괜찮다: 이 파일 자체는
// `frontend/js/shell/` 아래가 아니라서 vite.config.mjs의 manualChunks가
// 'shell'로 묶지 않는다(app.js 엔트리 그래프에 남는다, main.js가 이미
// 이 모듈들을 정적 import하므로 인스턴스 중복도 없다). Palette.tsx(지연
// 청크) 쪽에는 이 모듈들의 참조만 deps 인자로 건네고, 그쪽에서 직접
// import하지 않는다(Hud.tsx 상단 주석과 동일한 이유, ADR-26).
import { registerAction, getAction } from './core/dom.js';
import { register as registerKey, getBinding, displayCombo, displayComboHtml, list as keymapList, invoke as keymapInvoke } from './core/keymap.js';
import { vtFetch } from './core/api.js';
import { sessionDisplayName } from './core/store.js';
import { openSessionIds } from './layout/compact.js';
import { revealSession, createTmuxSession } from './term/session.js';
import { loadAccounts, openAccountMenu } from './term/accounts.js';
import { splitActivePane, setPaneSession } from './layout/store.js';
import { loadViewer, openFile, openFileInPane } from './panels/viewer-lazy.js';
import { setVtSkin, vtSkins, vtSkinLabel } from './theme.js';
import { buildSessionCard, updateSessionCard } from './agent/preview.js';
import { showToast } from './ui/toast.js';
import { relayHost } from './core/host-route.js';
import { isMacKeyboard } from './core/platform.js';
import { t } from './core/i18n.js';

// 이름의 출처는 세션 레코드다(core/store.js의 sessionDisplayName) — 예전엔
// 탭 DOM의 텍스트를 읽었는데, 그 DOM은 10 §4 3단계에서 사라진다.
function listSessions() {
  return openSessionIds().map((id) => ({ id, name: sessionDisplayName(id).trim() }));
}

// capability 게이팅 — agent/status.js가 /api/capabilities 응답에 따라
// `.needs-*` 엘리먼트에 직접 style.display='none'을 건다(F4). 그 결과를
// 재활용한다(quickopen.js의 _gateOk와 동일).
function gateOk(gate) {
  if (!gate) return true;
  const el = document.querySelector(`.needs-${gate}`);
  return !el || el.style.display !== 'none';
}

const deps = {
  vtFetch,
  getAction,
  gateOk,
  listSessions,
  getSession,
  // 2026-09-21(사용자 지적) — 팔레트가 여는 대상은 전부 **이미 열려 있는**
  // 세션(웹 세션 목록·원격 스크롤백 결과)이다. switchTo를 그대로 넘기면
  // Rail.tsx가 겪은 것과 같은 증상이 난다(그룹의 짝을 활성 pane에서 밀어냄).
  // Palette.tsx 쪽 프로퍼티 이름(`switchTo`)은 그대로 두고 구현만 바꾼다 —
  // 타입·호출부를 다시 손댈 이유가 없다.
  switchTo: revealSession,
  loadViewer,
  openFile,
  openFileInPane,
  splitActivePane,
  setPaneSession,
  buildSessionCard,
  updateSessionCard,
  setVtSkin,
  vtSkins,
  vtSkinLabel,
  listKeymapActions: keymapList,
  invokeKeymapAction: keymapInvoke,
  displayCombo,
  displayComboHtml,
  showToast,
  host: relayHost(),
  openSidebarSearch: (q, opts) => openContentSearch({ q, opts }),
  // 계정(핫스왑 H1) — 「<이름> 계정으로 새 세션」·「기본 계정 바꾸기」 줄의 재료와 동작.
  loadAccounts,
  newSessionWithAccount: (profile) => createTmuxSession({ profile }),
  openAccountMenu,
};

let _api = null;
let _loading = false;

function ensureMounted() {
  if (_api || _loading) return;
  _loading = true;
  const root = document.createElement('div');
  root.id = 'vt-palette-root';
  document.body.appendChild(root);
  import('./shell/Palette.tsx')
    .then(({ mountPalette }) => { _api = mountPalette(root, deps); })
    .catch((e) => console.error('[FarShell palette]', e))
    .finally(() => { _loading = false; });
}

// 첫 호출은 청크가 도착하기 전일 수 있다 — 도착할 때까지 짧게 재시도한다
// (viewer-lazy.js의 scm.show 재진입 가드와 같은 문제, 다른 해법: 여기는
// "아직 안 열렸다"가 아니라 "아직 준비 안 됐다"이므로 큐잉 대신 폴링으로
// 충분하다 — 사용자가 Mod+K를 연타해도 열리는 순간은 한 번뿐이다).
function openPalette(initialMode) {
  ensureMounted();
  const tryOpen = () => {
    if (_api) { _api.open(initialMode); return; }
    setTimeout(tryOpen, 20);
  };
  tryOpen();
}

// F3(c)/L6: data-action 위임 + Mod+K. 액션 id는 quickopen.js 시절과 동일 —
// index.html의 버튼·rail "⋯더보기"·키맵이 그대로 이 구현을 탄다.
registerAction('quickopen.open', () => openPalette());
registerKey('palette', () => openPalette());

// R7 — `Mod+F` 재배선 근육기억 완화: 이 세션에서 처음 누른 순간 1회 토스트.
const REWIRE_TOAST_KEY = 'vt.palette.searchRewireToastShown';
function maybeShowRewireToast() {
  try {
    if (localStorage.getItem(REWIRE_TOAST_KEY)) return;
    localStorage.setItem(REWIRE_TOAST_KEY, '1');
  } catch (_) {
    // localStorage 불가 환경(시크릿 모드 등) — 매번 뜨더라도 안내가 아예
    // 없는 것보다 낫다, 조용히 넘어가지 않는다.
  }
  showToast(t('검색 단축키가 바뀌었습니다 — 예전처럼 쓰려면 설정에서 되돌릴 수 있습니다'), 'info', { duration: 6000 });
}

// N40/N46 — `Mod+F`는 이제 팔레트를 `~`(스크롤백 검색) 모드로 연다.
registerKey('search', () => { maybeShowRewireToast(); openPalette('scrollback'); });
// 예전 `Mod+F`(인페인 검색바)는 `searchInPane`으로 내려갔다(지금 기본 Alt+Shift+F) — term/search-bar.js가
// 등록한 `search.toggle` 액션을 그대로 호출한다(액션 id는 안 바뀐다).
registerKey('searchInPane', () => {
  const fn = getAction('search.toggle');
  if (typeof fn === 'function') fn();
});

// ── 파일 내용 검색(파일 개편 단계 6) ─────────────────────────────────────────
// `contentSearch`(mac ⌘⇧F)는 사이드바 「파일 › 내용 검색」을 연다(시안 F06-C, 사용자 선택 2 「가」). 좁은 화면(폰)은
// 사이드바 대신 팔레트 `?` 모드. 사이드바의 파일 트리는 files 청크(files/mount.tsx)가 그리므로 여기서는 요청만 남긴다:
// 이미 떠 있으면 `vt:files-search` 이벤트로, 아니면 `window.__vtFilesSearchPending`에 두고 `dir.show`로 연다 — 트리가
// 붙으면서 그 요청을 꺼내 쓴다. `dir.show`는 토글이라 이미 열린 패널에는 부르지 않는다.
function openContentSearch(detail = {}) {
  if (window.matchMedia?.('(max-width:719px)').matches) { openPalette('grep'); return; }
  const req = { focus: true, ...detail };
  const panel = document.getElementById('vt-dir');
  if (panel && panel.isConnected && panel.querySelector('.vt-fs-side')) {
    window.dispatchEvent(new CustomEvent('vt:files-search', { detail: req }));
    return;
  }
  window.__vtFilesSearchPending = req;
  const show = getAction('dir.show');
  if (typeof show === 'function') show();
  else openPalette('grep');
}

// 터미널 내 검색이 ⌘⇧F에서 ⌥⇧F로 옮겨 간 것을 mac에서 처음 ⌘⇧F를 누를 때 한 번 알린다(R7과 같은 근육기억 완화).
const MOVE_TOAST_KEY = 'vt.palette.contentSearchMoveToastShown';
function maybeShowMoveToast() {
  const pane = getBinding('searchInPane');
  if (!pane || !pane.isDefault || !isMacKeyboard) return;
  try {
    if (localStorage.getItem(MOVE_TOAST_KEY)) return;
    localStorage.setItem(MOVE_TOAST_KEY, '1');
  } catch (_) { /* 안내가 매번 뜨더라도 없는 것보다 낫다 */ }
  showToast(t('{key}은 이제 파일 내용 검색입니다 — 터미널 내 검색은 {pane}', { key: displayCombo('Mod+Shift+F'), pane: displayCombo(pane.combo) }), 'info', { duration: 6000 });
}

registerKey('contentSearch', () => { maybeShowMoveToast(); openContentSearch(); });
registerAction('files.search', () => openContentSearch());
// 사이드바 「내용 검색」 칸 옆에 적는 단축키(사용자가 바꿨으면 바꾼 조합). files 청크는 keymap을 import하지 않는다.
window.vtContentSearchHint = () => {
  const b = getBinding('contentSearch');
  return b && !b.unavailable ? displayCombo(b.combo) : '';
};

// 실브라우저 스모크·디버깅용 브리지 — openFileInPane(panels/viewer-lazy.js)과
// 같은 관행.
window.openPalette = openPalette;
