// 앱 진입점. 기존 classic 스크립트는 전역 상태를 공유하므로 한 번에 ESM으로 바꾸면
// 위험이 크다. 대신 로드 순서를 HTML에서 제거하고 이 모듈에 명시한다. 이후 각 기능을
// ESM으로 옮겨도 이 파일의 manifest만 바꾸면 되므로, 전역 의존성 축소의 안전한 경계가 된다.

const LEGACY_APP_SCRIPTS = [
  '/static/js/theme.js',
  '/static/js/favicon.js',
  '/static/js/keyseq.js',
  '/static/js/terminal.js',
  '/static/js/search.js',
  '/static/js/picker.js',
  '/static/js/ansilex.js',
  '/static/js/grid.js',
  '/static/js/vtapi.js',
  '/static/js/panel.js',
  '/static/js/difflex.js',
  '/static/js/viewer.js',
  '/static/js/ports.js',
  '/static/js/queue.js',
  '/static/js/swreg.js',
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
