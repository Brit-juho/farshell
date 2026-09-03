// Vite 설정 (F1, 10-frontend-restructure.md).
//
// library 모드를 쓰는 이유: 기본(앱) 모드는 index.html을 입력으로 삼아 변환하고
// 산출 파일명에 해시를 붙인다. 둘 다 우리에게 해롭다 — frontend/sw.js:12의
// PRECACHE가 파일명을 하드코딩해 오프라인 PWA를 성립시키고, index.html은 로그인
// 게이트·FOUC 방지 인라인 스크립트를 담고 있어 우리가 직접 통제해야 한다.
// lib 모드는 "단일 진입점 → 고정 이름 산출물"이라 두 문제가 동시에 사라진다.
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss()],
  build: {
    lib: {
      entry: 'frontend/js/main.js',
      formats: ['es'],
      fileName: () => 'app.js',   // 해시 없음 — sw.js PRECACHE 계약 보호
      cssFileName: 'app',         // → app.css
    },
    outDir: 'frontend/dist',
    emptyOutDir: true,
    // 디버깅 시 원본을 그대로 읽기 위해 끈다. 크기 이득보다 가치가 크다
    // (지금 앱 JS 총합이 ~9천줄이라 minify 안 해도 I2의 300KB 상한에 여유가 있다).
    minify: false,
    rollupOptions: {
      // 벤더(xterm 등)는 아직 ES 모듈로 import하지 않는다 — index.html의 classic
      // <script>가 로드하고 sw.js가 개별 프리캐시한다(F4에서 core/vendor.js가
      // window.* 를 re-export하며 이 경계가 명시화된다). 지금은 external로 지정할
      // 대상이 없다 — main.js가 실제로 import하는 건 legacy 앱 스크립트뿐이다.
      external: [],
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:7777',
      '/ws': { target: 'ws://localhost:7777', ws: true },
    },
  },
});
