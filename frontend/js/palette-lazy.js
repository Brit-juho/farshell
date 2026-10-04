// N5/N40/N46(60-settings-palette.md §3) — 팔레트 진입점(app.js 쪽에 남는
// 스텁). 실제 렌더링은 shell/Palette.tsx(지연 청크 shell.js)가 맡는다 —
// panels/viewer-lazy.js(N35)와 같은 패턴: 액션 등록 + 실제 `import()`만
// 여기서 전담하고, 무거운 것(Solid 런타임 등)은 열 때만 받는다.
//
// 이 파일은 **정적으로** core/store.js·term/session.js·layout/store.js·
// agent/preview.js 등을 import한다 — 괜찮다: 이 파일 자체는
// `frontend/js/shell/` 아래가 아니라서 vite.config.js의 manualChunks가
// 'shell'로 묶지 않는다(app.js 엔트리 그래프에 남는다, main.js가 이미
// 이 모듈들을 정적 import하므로 인스턴스 중복도 없다). Palette.tsx(지연
// 청크) 쪽에는 이 모듈들의 참조만 deps 인자로 건네고, 그쪽에서 직접
// import하지 않는다(Hud.tsx 상단 주석과 동일한 이유, ADR-26).
import { registerAction, getAction } from './core/dom.js';
import { register as registerKey, getBinding, displayCombo, displayComboHtml, list as keymapList, invoke as keymapInvoke } from './core/keymap.js';
import { vtFetch } from './core/api.js';
import { sessionDisplayName } from './core/store.js';
import { openSessionIds } from './layout/compact.js';
import { revealSession } from './term/session.js';
import { splitActivePane, setPaneSession } from './layout/store.js';
import { loadViewer, openFile, openFileInPane } from './panels/viewer-lazy.js';
import { setVtSkin, vtSkins, vtSkinLabel } from './theme.js';
import { buildSessionCard, updateSessionCard } from './agent/preview.js';
import { showToast } from './ui/toast.js';

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
  showToast('검색 단축키가 바뀌었습니다 — 예전처럼 쓰려면 설정에서 되돌릴 수 있습니다', 'info', { duration: 6000 });
}

// N40/N46 — `Mod+F`는 이제 팔레트를 `~`(스크롤백 검색) 모드로 연다.
registerKey('search', () => { maybeShowRewireToast(); openPalette('scrollback'); });
// 예전 `Mod+F`(인페인 검색바)는 `Mod+Shift+F`로 내려갔다 — term/search-bar.js가
// 등록한 `search.toggle` 액션을 그대로 호출한다(액션 id는 안 바뀐다).
registerKey('searchInPane', () => {
  const fn = getAction('search.toggle');
  if (typeof fn === 'function') fn();
});

// 실브라우저 스모크·디버깅용 브리지 — openFileInPane(panels/viewer-lazy.js)과
// 같은 관행.
window.openPalette = openPalette;
