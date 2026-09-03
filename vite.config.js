// Vite 설정 (F1, 10-frontend-restructure.md).
//
// library 모드를 쓰는 이유: 기본(앱) 모드는 index.html을 입력으로 삼아 변환하고
// 산출 파일명에 해시를 붙인다. 둘 다 우리에게 해롭다 — frontend/sw.js:12의
// PRECACHE가 파일명을 하드코딩해 오프라인 PWA를 성립시키고, index.html은 로그인
// 게이트·FOUC 방지 인라인 스크립트를 담고 있어 우리가 직접 통제해야 한다.
// lib 모드는 "단일 진입점 → 고정 이름 산출물"이라 두 문제가 동시에 사라진다.
//
// F4: voice/index.js(agent/status.js가 capability 확인 후 별도 <script
// type="module">로 지연 주입 — 구 voice.js의 "미설치 시 0바이트" 계약을
// 유지하려면 진짜 별도 산출물이어야 한다)를 두 번째 entry로 추가했다.
// **한 config에 entry를 2개 넣는 방식은 시도했다가 버렸다** — Rollup이
// app.js/voice.js가 공유하는 core/*.js를 자동으로 해시 붙은 별도 청크로
// 뽑아내(`dom-CWFR5-Ty.mjs` 같은) ADR-1의 "해시 없음" 계약이 깨졌고,
// manualChunks로 청크 생성을 막는 표준적인 방법이 없다(Rollup의 청크 분리는
// 멀티 엔트리 공유 모듈에 대해 기본 동작이라 끌 수 없음). 대신 **빌드 자체를
// 완전히 분리된 두 번의 Rollup 실행**으로 나눴다 — 서로의 그래프를 아예 못
// 보므로 청크 공유가 구조적으로 불가능해진다(대가: core/*.js 코드가 두
// 산출물에 각각 인라인돼 중복되지만, 몇 KB 수준이라 I2의 300KB 상한에 문제없다).
import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

const isVoice = !!process.env.VOICE_BUILD;

export default defineConfig({
  plugins: [tailwindcss()],
  build: {
    lib: isVoice ? {
      entry: 'frontend/js/voice/index.js',
      formats: ['es'],
      fileName: () => 'voice.js',   // 해시 없음 — sw.js PRECACHE 계약 보호
    } : {
      entry: 'frontend/js/main.js',
      formats: ['es'],
      fileName: () => 'app.js',     // 해시 없음 — sw.js PRECACHE 계약 보호
      cssFileName: 'app',           // → app.css
    },
    outDir: 'frontend/dist',
    // voice 빌드가 app 빌드 산출물을 지우면 안 된다 — package.json의 build
    // 스크립트가 항상 app을 먼저(emptyOutDir로 정리) 돌리고, voice는 이어서
    // 같은 디렉토리에 추가하는 순서를 전제한다.
    emptyOutDir: !isVoice,
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
