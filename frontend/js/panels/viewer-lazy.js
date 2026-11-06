// ADR-26/N35 — 파일 렌더러(panels/viewer/*)를 지연 청크로 뺀다. **이 파일만
// 앱 본체(app.js)에 남는다** — 나머지는 vite.config.js의 manualChunks가
// `panels.js`(고정 이름 지연 청크)로 보낸다. highlight.js(127KB)까지 딸려
// 오므로 파일을 실제로 열기 전에는 내려받지 않는다.
//
// N35 §6에서 모달 코드 뷰어(viewer/shell.js·tree.js)가 사라졌다. 지금 이
// 청크가 여는 화면은 둘뿐이다: **뷰어 페인**(파일 하나 — layout/panes.js가
// renderFile을 부른다)과 **dock 소스컨트롤 탭**(scm.js).
//
// 소비처는 정적 import를 쓰면 안 된다 — 정적 import는 그 모듈을 참조하는
// 쪽의 청크에 도로 끌어들인다(N34 커밋에서 core/*가 복제됐던 함정).
import { registerAction, getAction } from '../core/dom.js';
import { register as registerKey } from '../core/keymap.js';
import { get as getSetting } from '../core/settings.js';
import { openViewerPane } from '../layout/store.js';
import { relayHost } from '../core/host-route.js';

let _loading = null;

/** 파일 렌더러 + 최근 파일 목록 + 변경 감시. 전부 같은 청크라 요청은 한 번이다. */
export function loadViewer() {
  if (!_loading) {
    _loading = import('./viewer/file.js')
      .then(async (file) => ({
        ...file,
        ...(await import('./viewer/recent.js')),
        ...(await import('../shell/fs-watch.js')),
      }));
  }
  return _loading;
}

/** 파일을 **페인**으로 연다. 표면 설정과 무관하다 — 「새 pane에 열기」·「페인으로」 버튼처럼
 * 정말로 pane을 원하는 자리가 쓴다(ADR-44). */
// opts.line·col은 그 줄로 이동(터미널 링크 `path:줄:열`). 줄 이동은 렌더러가 맡는다 — 이 함수가 먼저 등록한
// then이 페인 렌더(layout/panes.js의 loadViewer().then)보다 먼저 돈다.
export function openFileInPane(path, opts = {}) {
  if (!path) return;
  // 온보딩(세션 0개 안내)은 position:fixed + z-index:500이라 페인 위를 덮는다.
  // 이제 볼 것이 생겼으므로 치운다 — term/session.js가 세션을 만들 때 하는 것과
  // 같은 처리다(같은 화면을 두 조건이 공유한다).
  document.getElementById('onboarding')?.remove();
  loadViewer().then((v) => {
    v._pushRecent(path);
    if (opts.line) v.queueJump(path, opts.line, opts.col);
  }).catch(() => {});
  openViewerPane(path);
}

/**
 * 파일 열기의 기본 진입점. `viewer.surface`에 따라 오버레이(sheet/float) 또는 페인으로 연다.
 * opts: `line`·`col`(그 줄로 이동), `host`(파일이 있는 호스트 — 터미널 링크는 그 세션의 호스트, 계획 3.5).
 * 지금 화면과 다른 호스트의 파일은 늘 오버레이로 연다(페인은 호스트를 기억하지 않는다). 최근 파일 목록에도 넣지
 * 않는다 — 그 목록은 경로만 기억해서 다음에 이 호스트의 같은 경로를 열게 된다.
 */
export function openFile(path, opts = {}) {
  if (!path) return;
  const cross = !!opts.host && opts.host !== 'local' && opts.host !== relayHost();
  if (!cross && getSetting('viewer.surface') === 'pane') { openFileInPane(path, opts); return; }
  document.getElementById('onboarding')?.remove();
  if (!cross) loadViewer().then((v) => v._pushRecent(path)).catch(() => {});
  import('./viewer/overlay.js')
    .then((m) => m.openFileOverlay(path, opts))
    .catch((e) => {
      // 지연 청크를 못 받았을 때 클릭이 아무 일도 안 한 것처럼 보이면 안 된다.
      console.error('[FarShell viewer]', e);
      if (!cross) openViewerPane(path);
    });
}

// 「파일」 진입점(레일 ⋯ · 키맵 Ctrl+Shift+E · 팔레트 명령)은 이제 **팔레트**를
// 연다: 파일을 고르는 자리가 팔레트로 옮겨갔기 때문이다(§6). 팔레트의 파일
// 검색 모드 자체는 60 §3의 몫이라, 2.1.0에서는 팔레트 기본 화면의 「최근 파일」
// 섹션이 그 역할을 한다 — 그 전까지 트리로만 도달하던 파일은 잠시 최근
// 목록으로만 열린다(사용자와 합의된 범위).
registerAction('viewer.show', () => {
  const open = getAction('quickopen.open');
  if (typeof open === 'function') open();
});

// 2026-09-23(2.1 D6) — 'viewer' 키맵 액션(기본 Ctrl+Shift+E)에 실행 핸들러가
// 아예 없었다(core/keymap.js의 ACTIONS에는 있었지만 아무도 register하지
// 않았다 — 누른다고 아무 일도 안 일어나는 죽은 단축키). 위 viewer.show와
// 같은 동작이므로 그대로 위임한다.
registerKey('viewer', () => {
  const open = getAction('viewer.show');
  if (typeof open === 'function') open();
});

// dock 「소스컨트롤」 탭(40 §3)도 같은 청크에 있다 — git.js·diff.js를 그대로
// 쓰므로 따로 뺄 이유가 없다. scm.js는 자기 액션을 등록하지 않으므로 여기가
// 유일한 진입점이다.
//
// 재진입 가드가 필요한 이유: dock은 "활성 탭의 패널이 DOM에 없으면 연다"는
// effect로 동작하는데, 청크가 도착하기 전에 그 effect가 한 번 더 돌면
// showScm()이 두 번 불려 두 번째 호출이 **토글로 닫아버린다**.
let _scmOpening = false;
registerAction('scm.show', () => {
  if (_scmOpening) return;
  _scmOpening = true;
  import('./viewer/scm.js')
    .then((m) => m.showScm())
    .catch((e) => console.error('[FarShell scm]', e))
    .finally(() => { _scmOpening = false; });
});

// dock 「파일」 탭(디렉토리 트리, N35 번복 — 2026-09-22) — scm.show와 같은
// 재진입 가드 이유.
let _dirOpening = false;
registerAction('dir.show', () => {
  if (_dirOpening) return;
  _dirOpening = true;
  import('./viewer/tree.js')
    .then((m) => m.showTree())
    .catch((e) => console.error('[FarShell dir]', e))
    .finally(() => { _dirOpening = false; });
});

// window 브리지 — 이 저장소의 관행대로(core/store.js·term/resize.js 하단)
// "밖에서 부를 수 있어야 하는" 함수 하나를 전역에 둔다. 인자를 받는 동작이라
// registerAction(무인자 계약)에는 안 맞고, 실브라우저 스모크와 향후 팔레트
// 파일 모드(60 §3)가 같은 경로를 쓴다.
window.openFileInPane = openFileInPane;
window.openFile = openFile;
