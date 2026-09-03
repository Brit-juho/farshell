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

// F4에서 Codex가 지적한 잔여 버그 수정: vite dev 서버가 /api·/ws만 프록시해서
// index.html이 참조하는 /static/*(서버가 frontend/ 를 마운트하는 경로 — vendor
// 스크립트·아이콘·manifest·frontend/dist/의 빌드 산출물)가 vite 자체에서는 404였다
// (vite root가 저장소 루트라 /static이라는 실제 디렉토리가 없다). 그래서 /static도
// 같은 백엔드로 프록시를 추가했는데, 그것만으로는 부족했다 — 실측해보니
// server/main.py의 OriginGuardMiddleware가 모든 HTTP/WS 요청의 Origin이 자기
// 자신(Host)과 일치하는지 확인해 403(cross_origin)으로 막는다. 브라우저는 항상
// 실제 페이지 origin(http://localhost:5173)을 Origin 헤더에 넣어 보내는데, 이건
// 백엔드 자신의 origin(http://localhost:7777)과 다르므로 프록시를 거쳐도 그대로
// 막힌다(실측: 원본 Origin 그대로 포워딩 시 /api/capabilities가 403). 백엔드
// 쪽에서 이 방어를 풀면(VT_ALLOWED_ORIGINS) 이 CSRF 방어의 운영 의미가 없어지고
// 매번 서버 설정도 따로 요구하므로, 대신 프록시가 나가는 요청의 Origin을 백엔드
// 자신의 origin으로 다시 써서(changeOrigin이 바꾸는 Host와 짝을 맞춰) 백엔드
// 입장에서 "자기 자신에게서 온 요청"으로 보이게 한다 — 이 재작성은 vite dev
// 프록시 계층에서만 일어나므로 백엔드의 실제 운영 정책은 전혀 느슨해지지 않는다.
const BACKEND_ORIGIN = 'http://localhost:7777';
function sameOriginProxy(target, extra = {}) {
  return {
    target,
    changeOrigin: true,
    ...extra,
    configure(proxy) {
      proxy.on('proxyReq', (proxyReq) => proxyReq.setHeader('origin', BACKEND_ORIGIN));
      proxy.on('proxyReqWs', (proxyReq) => proxyReq.setHeader('origin', BACKEND_ORIGIN));
    },
  };
}

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
      '/api': sameOriginProxy(BACKEND_ORIGIN),
      '/ws': sameOriginProxy('ws://localhost:7777', { ws: true }),
      '/static': sameOriginProxy(BACKEND_ORIGIN),
    },
  },
});
