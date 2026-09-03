// 앱 진입점 (F1 — 구 js/bootstrap.js를 대체). Vite가 이 파일 하나를 lib 모드로
// 번들해 frontend/dist/app.js + app.css를 만든다 (vite.config.js 참고).
//
// F2에서 부수효과가 적은 잎(leaf) 모듈부터 진짜 ES import로 옮기기 시작했다.
// 이 정적 import들은 아래 boot()가 시작되기(=legacy classic script가 한 줄이라도
// 로드되기) 전에 전부 평가된다 — 그래서 이들이 window에 심어두는 값
// (API_BASE·vtFetch·VTAnsiLex 등)은 legacy 스크립트 어디서 읽어도 항상 이미
// 준비돼 있다. 아직 안 옮긴 legacy 스크립트는 classic script 최상위 스코프를
// 계속 공유하며, 이 파일의 LEGACY_APP_SCRIPTS 배열이 그 로드 순서를 관리한다.
import '../../styles/main.css';

import './core/env.js';
import './core/api.js';
import './core/store.js';
import './core/dom.js';
import './lib/ansilex.js';
import './lib/difflex.js';
import './lib/keyseq.js';
import './panels/panel.js';
import './ui/favicon.js';
import './ui/moreMenu.js';
import './push/swreg.js';

// F4 — terminal.js(2023줄)를 term/ 아래 15개 모듈로 분할. 서로 실제 import/export로
// 엮여 있어 나열 순서 자체는 중요하지 않다(ES 모듈 정적 import는 의존 그래프 순으로
// 평가되고 중복 평가되지 않는다) — 그래도 읽기 편하도록 의존 방향(leaf → 조립부)
// 순서로 나열한다. term/boot.js는 자동 실행 대신 bootApp()을 export한다(아래 참고).
import './term/e2e.js';
import './term/clipboard.js';
import './term/resize.js';
import './term/touch.js';
import './term/links.js';
import './term/selection.js';
import './term/xterm-setup.js';
import './term/tab-dom.js';
import './term/workspace.js';
import './term/conn-overlay.js';
import './term/keybar.js';
import './term/ws.js';
import './term/tmux-panel.js';
import './term/session.js';
import './term/guide.js';
import { bootApp } from './term/boot.js';

const LEGACY_APP_SCRIPTS = [
  '/static/js/ui/toast.js',   // showToast 단일 구현 — 다른 스크립트가 호출하므로 먼저
  '/static/js/theme.js',
  '/static/js/search.js',
  '/static/js/picker.js',
  '/static/js/grid.js',
  '/static/js/viewer.js',
  '/static/js/ports.js',
  '/static/js/queue.js',
  '/static/js/snippets.js',
  '/static/js/quickopen.js',
  '/static/js/pushui.js',
];

function loadClassicScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.async = false; // manifest 순서를 보장한다.
    script.onload = resolve;
    script.onerror = () => reject(new Error(`앱 스크립트를 불러오지 못했습니다: ${src}`));
    document.head.appendChild(script);
  });
}

async function boot() {
  // toast.js가 로드되기 전에 bootApp()(구 terminal.js 부팅 IIFE)이 showToast를
  // 부르면 ReferenceError다 — F4 전에는 classic script 로드 순서가 이 순서를
  // 자연히 보장했지만, term/*.js가 정적 import로 옮겨가며 없어진 보장이라
  // 명시적으로 기다린다.
  await loadClassicScript(LEGACY_APP_SCRIPTS[0]);
  bootApp();
  for (const src of LEGACY_APP_SCRIPTS.slice(1)) {
    await loadClassicScript(src);
  }
}

boot().catch((error) => {
  console.error('[FarShell bootstrap]', error);
  document.documentElement.dataset.appBootFailed = 'true';
});
