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

const LEGACY_APP_SCRIPTS = [
  '/static/js/ui/toast.js',   // showToast 단일 구현 — 다른 스크립트가 호출하므로 먼저
  '/static/js/theme.js',
  '/static/js/terminal.js',
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
  for (const src of LEGACY_APP_SCRIPTS) {
    await loadClassicScript(src);
  }
}

boot().catch((error) => {
  console.error('[FarShell bootstrap]', error);
  document.documentElement.dataset.appBootFailed = 'true';
});
