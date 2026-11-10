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
import solid from 'vite-plugin-solid';

const isVoice = !!process.env.VOICE_BUILD;

// 빌드 로그에 core 청크의 모듈별 기여도 상위를 찍는다(정보용, 빌드를 실패시키지 않는다).
// 초기 페이로드 상한 논의 때 소스맵 추정이 틀렸던 적이 있어(청크가 이어 붙은 맵이라 파일별 수치가
// 거칠다) Rollup이 직접 주는 `chunk.modules[id].renderedLength`(공백 제거 전, 트리 셰이킹 뒤)를 쓴다.
// 새 큰 모듈이 core로 조용히 들어오면 여기서 먼저 눈에 띈다.
function reportCoreModules(top = 15) {
  return {
    name: 'fsh-report-core-modules',
    apply: 'build',
    generateBundle(_o, bundle) {
      const core = bundle['core.js'];
      if (!core || core.type !== 'chunk') return;
      const rows = Object.entries(core.modules)
        .map(([id, m]) => [m.renderedLength, id.split('?')[0].replace(/\\/g, '/').replace(/^.*\/(?=frontend\/|node_modules\/)/, '')])
        .sort((a, b) => b[0] - a[0]);
      const total = rows.reduce((s, r) => s + r[0], 0);
      console.log(`\ncore.js 모듈별 기여도(렌더 바이트, 공백 제거 전) — 합계 ${total.toLocaleString()} B, 모듈 ${rows.length}개`);
      for (const [n, id] of rows.slice(0, top)) console.log(`  ${String(n).padStart(8)}  ${id}`);
    },
  };
}

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

// N18 — `vite build --mode test`(tests/helpers/solid-env.js가 부른다)로만
// 켜지는 세 번째 산출물. .ts/.tsx는 tests/helpers/vm-esm.js가 직접 못 읽으므로
// (TS/JSX 변환 필요), 검사 대상을 모은 배럴 frontend/tests/fixtures/test-entry.ts를
// 실제로 컴파일해 그 산출물을 jsdom에 로드한다. 별도 outDir(dist-test)라
// app/voice 산출물과 절대 안 섞인다.
// 모듈 -> 청크 이름. 규칙은 위에서부터 처음 걸리는 것이 이긴다(null이면 Rolldown이 알아서 나눈다).
function chunkOf(id) {
    const p = id.split('?')[0].replace(/\\/g, '/');
    if (p.endsWith('/frontend/js/history/api.js')) return 'shell';
    if (p.includes('/frontend/js/history/')) return 'history';
    // 음성 상태 UI는 인증 후 조회할 때 로딩한다. 초기 core 크기를 늘리지 않는다.
    if (/\/frontend\/js\/panels\/subagent-(viewer|poll)\.js$/.test(p)) return 'subagents';
    if (p.endsWith('/frontend/js/voice/status-panel.js')) return 'voice-status';
    // 음성 입력 대상 고정(3KB) — 상태 패널(voice-status)과 설정 > 음성(settings)만 쓴다. 이름이 없으면 두 지연 청크가
    // 함께 쓴다는 이유로 포괄 규칙이 core로 보낸다(리디자인 9단계에 찾았다). voice.js 번들은 별도 빌드라 제 사본을 갖는다.
    if (p.endsWith('/frontend/js/voice/target-pin.js')) return 'voice-status';
    // 터치 제스처는 터치 기기에만 로딩한다. 상태/키 인코더는 core 하나를 공유한다.
    if (/\/frontend\/js\/term\/(?:touch(?:-select)?|keybar-ui)\.js$/.test(p)) return 'touch';
    // pane 작업 정보 구독은 main.js가 지연 로딩한다. 초기 부팅에 필요하지 않다.
    if (p.endsWith('/frontend/js/ui/session-context.js')) return 'shell';
    if (p.endsWith('/frontend/js/term/tab-worktree.js')) return 'shell';
    if (p.includes('/node_modules/solid-js/') || p.includes('/frontend/js/shell/')) return 'shell';
    // 코드 뷰어 본문(CodeMirror 6, 읽기 전용 — 파일 개편 단계 2). panels/viewer/file.js가 파일을 열 때 import()한다.
    // panels 규칙보다 먼저 와야 한다(같은 폴더). 이름이 없으면 Rolldown이 해시 붙은 청크를 따로 만들어 고정 이름
    // 계약(ADR-26)이 깨진다.
    // 언어 팩은 언어마다 청크를 나눈다 — 한 청크로 묶으면 editor.js가 1MB(gzip 289KB)가 되고, 파이썬 파일 하나를
    // 열어도 자바·고·마크다운 파서까지 받는다. editor.js가 언어를 import()로 부르므로 연 언어만 받는다.
    // 다른 언어를 정적으로 끌어오는 팩(html → js·css, markdown → html)은 그 청크를 함께 받는다.
    if (/\/node_modules\/@(?:codemirror\/lang-javascript|lezer\/javascript)\//.test(p)) return 'editor-js';
    if (/\/node_modules\/@(?:codemirror\/lang-css|lezer\/css)\//.test(p)) return 'editor-css';
    if (/\/node_modules\/@(?:codemirror\/lang-html|lezer\/html)\//.test(p)) return 'editor-html';
    if (/\/node_modules\/@(?:codemirror\/lang-markdown|lezer\/markdown)\//.test(p)) return 'editor-md';
    if (/\/node_modules\/@(?:codemirror\/lang-python|lezer\/python)\//.test(p)) return 'editor-py';
    if (/\/node_modules\/@(?:codemirror\/lang-java|lezer\/java)\//.test(p)) return 'editor-java';
    if (/\/node_modules\/@(?:codemirror\/lang-go|lezer\/go)\//.test(p)) return 'editor-go';
    if (/\/node_modules\/@(?:codemirror\/lang-yaml|lezer\/yaml)\//.test(p)) return 'editor-yaml';
    if (/\/node_modules\/@(?:codemirror\/lang-json|lezer\/json)\//.test(p)) return 'editor-json';
    if (/\/node_modules\/@codemirror\/lang-sql\//.test(p)) return 'editor-sql';
    if (p.includes('/node_modules/@codemirror/legacy-modes/')) return 'editor-legacy';   // shell·kotlin·swift
    // 나머지(view·state·language·search·lezer 런타임)와 뷰어 모듈은 `editor` 하나.
    if (/\/frontend\/js\/panels\/viewer\/editor(?:-text)?\.js$/.test(p)) return 'editor';
    if (/\/node_modules\/(?:@codemirror|@lezer|@marijn|style-mod|w3c-keyname|crelt)\//.test(p)) return 'editor';
    // 파일 트리(Solid + TS, 파일 개편 단계 5) — panels/viewer/tree.js(`dir.show`)가 패널을 열 때 import()한다.
    // panels 규칙보다 먼저 올 필요는 없지만(폴더가 다르다) 이름이 없으면 포괄 규칙이 core로 보내 초기 페이로드가 는다.
    if (p.includes('/frontend/js/files/')) return 'files';
    // 마크다운 미리보기(파일 개편 단계 7) — marked + DOMPurify와 그 둘을 붙이는 markdown-lib.js만. panels/viewer/file.js가
    // 마크다운을 「렌더」로 열 때 import()한다. 정화 규칙(markdown.js)은 라이브러리를 import하지 않으므로 panels에 둔다 —
    // 여기로 보내면 panels가 이 청크를 정적으로 끌어와 파일을 열 때마다 라이브러리를 받게 된다.
    if (/\/node_modules\/(?:marked|dompurify)\//.test(p)) return 'preview';
    if (p.endsWith('/frontend/js/panels/viewer/markdown-lib.js')) return 'preview';
    // 진짜로 "열 때" 받는 화면들. 여기 한 줄 추가하는 것만으로 지연
    // 청크가 되고, 그 모듈이 무엇을 import하든 core는 따라오지 않는다.
    if (p.includes('/frontend/js/panels/viewer/')) return 'panels';
    if (p.endsWith('/frontend/js/lib/difflex.js')) return 'panels';  // 뷰어 전용 UMD
    if (p.includes('/frontend/js/panels/files/')) return 'panels';
    if (p.includes('/frontend/js/panels/ports/')) return 'panels';
    if (p.endsWith('/frontend/js/queue.js')) return 'panels';
    // ── `settings` 청크 ────────────────────────────────────────────
    // dock 화면들과 **한 청크에 묶지 않는다.** 묶으면 「큐」탭 한 번
    // 누르는 데 설정 7개 섹션·MCP·스니펫·사용량까지 같이 받는다 —
    // 실측으로 드러난 문제다: panels.js가 79KB에서 119KB가 되자
    // 큐 패널이 클릭 후 제때 안 떠서 실브라우저 스모크가 잡아냈다.
    // 열리는 경로가 다르면(dock 탭 / 설정·MCP 팝업) 청크도 나눈다.
    //
    // `panels/settings.js`·`panels/settings/**`만 해당한다.
    // **`core/settings.js`(설정 스토어 싱글톤)는 아니다** — 아래 포괄
    // 규칙이 core로 보낸다.
    if (p.endsWith('/frontend/js/panels/settings.js')) return 'settings';
    if (p.includes('/frontend/js/panels/settings/')) return 'settings';
    if (p.endsWith('/frontend/js/panels/mcp.js')) return 'settings';
    if (p.endsWith('/frontend/js/panels/agent-config.js')) return 'settings';
    // dock MCP 버튼(shell/Dock.tsx)이 지연 import하는 열기 로직 —
    // panels/mcp.js와 항상 같이 쓰이니 같은 청크(mcp-open.js 머리말 참고).
    if (p.endsWith('/frontend/js/panels/mcp-open.js')) return 'settings';
    if (p.endsWith('/frontend/js/snippets.js')) return 'settings';
    // 사용량 화면. **`panels/usage-badge.js`는 아니다** — rail 배지
    // 폴링은 상시 동작이라 core에 남는다(endsWith라 안 걸린다).
    if (p.endsWith('/frontend/js/panels/usage.js')) return 'settings';
    // 설정 > 에이전트 프로필 섹션(23KB) — agent-config.js가 섹션을 열 때만 지연 import한다.
    // 규칙이 없으면 포괄 규칙이 core로 보내 상시 로드에 실린다.
    if (p.endsWith('/frontend/js/panels/agent-profiles.js')) return 'settings';
    // 스니펫 순수 함수 — snippets.js(settings 청크)만 쓴다.
    if (p.endsWith('/frontend/js/snippets-util.js')) return 'settings';
    // 시스템 패널 — panels/system-lazy.js(core 스텁)가 열 때만 지연 import한다.
    if (p.endsWith('/frontend/js/panels/system.js')) return 'panels';
    // 계정 선택기 메뉴·기본 계정 변경(accounts-ui)과 줄 글(accounts-text) — 상태 막대 칩(shell)·설정 「계정」(settings)·「+ 새 세션」 메뉴의
    // 「이번만 다른 계정으로」(core → import())만 쓴다. 규칙이 없으면 포괄 규칙이 core로 보내 초기 페이로드가 는다.
    if (p.endsWith('/frontend/js/term/accounts-ui.js')) return 'shell';
    if (p.endsWith('/frontend/js/term/accounts-text.js')) return 'shell';
    // 숫자 스피너 — shell(Solid) 청크와 settings 청크만 쓴다. 둘 다 core를 거치지 않고 shell을 먼저 받는다.
    if (p.endsWith('/frontend/js/ui/number-spinner.js')) return 'shell';
    // 모달 뼈대(panels/panel.js)와 확인·입력·알림 대화상자 본체(core/dialog.js) — 부팅에 필요 없다.
    // 부팅 쪽은 core/dialog-lazy.js(스텁)와 term/guide.js의 import()만 쓴다. 이 둘을 정적으로 import하는
    // 화면 청크(panels·settings·share·history·screens)는 panel 청크를 함께 받는다(core를 거치지 않는다).
    if (p.endsWith('/frontend/js/panels/panel.js')) return 'panel';
    if (p.endsWith('/frontend/js/core/dialog.js')) return 'panel';
    // 패널 전용 아이콘(리디자인 5단계) — 패널 화면 청크가 모두 panel.js를 받으므로 같은 청크에 둔다.
    if (p.endsWith('/frontend/js/panels/panel-icons.js')) return 'panel';
    // 사용 가이드 본문 — term/guide.js(core 스텁)가 열 때만 import()한다.
    if (p.endsWith('/frontend/js/term/guide-view.js')) return 'panel';
    // 업로드 전송·대기열·취소 본체(10KB) — term/upload-lite.js(core)가 첫 업로드 때와 유휴 시간에 import()한다.
    // 드롭 판별·창 기본 동작 차단·window 브리지 스텁은 lite에 남는다. panels/files는 브리지로만 부르므로 정적 의존이 없다.
    if (p.endsWith('/frontend/js/term/upload.js')) return 'upload';
    // <select> 드롭다운 본체(12KB) — ui/select-lazy.js(core 스텁)가 select가 나타날 때와 유휴 시간에 지연 import한다.
    // screens에 합치지 않는다: select 하나 때문에 화면 모듈까지 받게 된다.
    if (p.endsWith('/frontend/js/ui/select.js')) return 'select';
    // 공용 툴팁 본체(5KB) — ui/tooltip-lazy.js(core 스텁)가 첫 hover·focus 때와 유휴 시간에 import()한다(리디자인 9단계).
    if (p.endsWith('/frontend/js/ui/tooltip.js')) return 'tooltip';
    // 칸 경계를 끄는 동안의 크기 캡션(3KB) — layout/panes.js가 첫 드래그 때 import()한다(리디자인 9단계).
    if (p.endsWith('/frontend/js/layout/resize-overlay.js')) return 'resize-overlay';
    // 에이전트 브랜드 로고 path(18KB) — 초기 페이로드 상한 때문에 뗐다.
    // ui/icons.js가 부팅 직후 지연 import하고, 오기 전엔 선 기호로 그린다.
    if (p.endsWith('/frontend/js/ui/brand-icons.js')) return 'brand';
    // 작업대(활동 막대·사이드바) 전용 아이콘 — shell 청크(Workbench.tsx)만 정적 import한다.
    // 규칙이 없으면 포괄 규칙이 core로 보내 초기 페이로드가 는다.
    if (p.endsWith('/frontend/js/ui/icons-shell.js')) return 'shell';
    // 세션 미리보기를 터미널 색으로 그리는 해석기(리디자인 6단계) — 팔레트(shell)와 빈 칸 세션 선택(screens)만 쓴다.
    if (p.endsWith('/frontend/js/lib/ansi-spans.js')) return 'shell';
    // 「연결된 화면」 모달 + 클라이언트 목록 — HUD 칩·레일 메뉴가 열 때만 쓴다.
    // layout/connected-screens-lazy.js가 지연 import하고, 설정 > 음성 섹션도
    // clients.js를 정적 import한다(settings -> screens 단방향, core는 안 따라온다).
    if (p.endsWith('/frontend/js/layout/connected-screens.js')) return 'screens';
    if (p.endsWith('/frontend/js/layout/clients.js')) return 'screens';
    // 에이전트 세션 공유 — 공유 대화상자·승인 모달·권한 패널. js/share-lazy.js(core)가
    // 이벤트 수신과 import() 만 한다. 청크 이름이 없으면 포괄 규칙이 core 로 보내 상시 로드가 늘어난다.
    if (p.includes('/frontend/js/share/')) return 'share';
    // 통합 승인함(모달·권한·감사)도 같은 청크 — share-lazy.js 가 이벤트 수신과 import() 만 한다.
    if (p.includes('/frontend/js/authz/')) return 'share';
    // 빈 칸 클릭 시 열리는 세션 선택 시트(layout/panes.js가 지연 import).
    if (p.endsWith('/frontend/js/layout/pane-picker.js')) return 'screens';
    // 모바일 세션 시트의 목록 렌더링 전부(picker.js가 시트를 열 때 지연 import)와 그 안의 뷰 순서 편집.
    if (p.endsWith('/frontend/js/ui/workspace-details.js')) return 'screens';
    if (p.endsWith('/frontend/js/layout/session-order.js')) return 'screens';
    // 뷰 탭 우클릭 메뉴(layout/tabbar.js가 contextmenu 때 지연 import).
    if (p.endsWith('/frontend/js/layout/tab-menu.js')) return 'screens';
    // 포괄 규칙 — 위에 안 걸린 앱 모듈 전부. main.js(entry)는 제외해야
    // 한다(entry를 청크에 배정하면 Rollup이 app.js를 빈 껍데기로 만든다).
    if (p.includes('/frontend/js/') && !p.endsWith('/frontend/js/main.js')) return 'core';
    return null;
}
// 설정 파일은 번들러가 따옴표를 바꿀 수 있어 두 가지 모두 받는다. 규칙을 추가하면 이름이 자동으로 그룹이 된다.
const CHUNK_NAMES = [...new Set([...chunkOf.toString().matchAll(/return\s+['"]([a-z-]+)['"]/g)].map((m) => m[1]))];

export default defineConfig(({ mode }) => {
  const isTest = mode === 'test';

  return {
    // N17: solid()는 .tsx/.jsx만 변환한다(기존 9천 줄의 classic .js는 그대로 —
    // vite-plugin-solid의 기본 include가 확장자로만 걸린다). lib 모드·2회 분리
    // 빌드·해시 없는 파일명은 그대로 유지된다(ADR-26).
    plugins: [tailwindcss(), solid(), ...(isTest ? [] : [reportCoreModules()])],
    // 식별자는 줄이지 않는다 — 산출물에 모듈 스코프 이름(`sessions`, `activeId`…)이
    // 남아야 build-output.test.js가 청크 간 싱글톤 복제를 잡는다.
    build: {
      lib: isTest ? {
        entry: 'frontend/tests/fixtures/test-entry.ts',
        formats: ['es'],
        fileName: () => 'test-entry.js',
      } : isVoice ? {
        entry: 'frontend/js/voice/index.js',
        formats: ['es'],
        fileName: () => 'voice.js',   // 해시 없음 — sw.js PRECACHE 계약 보호
      } : {
        entry: 'frontend/js/main.js',
        formats: ['es'],
        fileName: () => 'app.js',     // 해시 없음 — sw.js PRECACHE 계약 보호
        cssFileName: 'app',           // → app.css
      },
      outDir: isTest ? 'frontend/dist-test' : 'frontend/dist',
      // voice 빌드가 app 빌드 산출물을 지우면 안 된다 — package.json의 build
      // 스크립트가 항상 app을 먼저(emptyOutDir로 정리) 돌리고, voice는 이어서
      // 같은 디렉토리에 추가하는 순서를 전제한다. test 빌드는 아예 다른
      // outDir이라 매번 비워도 안전하다.
      emptyOutDir: isTest || !isVoice,
      // 2026-09-29 — minify를 켰다. 끈 채로는 초기 페이로드가 300KiB 상한에
      // 110B 남기고 닿아 기능 하나 못 넣는 상태였다. 원본은 소스맵으로 읽는다.
      // test 빌드는 산출물 문자열을 검사하는 테스트가 있어 그대로 둔다.
      // 식별자를 줄이지 않는다(mangle:false) — 산출물에 모듈 스코프 이름(`sessions`, `activeId`…)이 남아야
      // build-output.test.js가 청크 간 싱글톤 복제를 잡는다. vite 8의 기본 minify는 이름까지 바꾼다.
      // build.minify는 끈다 — lib 모드에서는 build.minify 객체의 세부 옵션이 먹지 않았다(c05a174가 공백 제거
      // 플러그인을 지운 뒤 들여쓰기·`// frontend/js/...` 주석이 산출물에 그대로 남은 회귀). 줄이기는 아래
      // rolldownOptions.output.minify가 맡는다.
      minify: false,
      sourcemap: !isTest,
      // 제3자 고지 — 줄이기(minify)가 `@license` 주석을 지우므로(marked·DOMPurify 등), 번들에 든 의존 패키지마다
      // 이름·버전·라이선스 식별자·LICENSE 본문을 한 파일로 낸다(Vite `build.license`). dist/에 있으므로 릴리스
      // tarball에 함께 실린다(release.yml이 있는지 확인한다). 주석을 산출물에 남기는 방법(legalComments)은 초기
      // 페이로드(app.js + core.js ≤ 307,200B)를 늘리므로 쓰지 않았다. voice 빌드는 같은 outDir에 이어 쓰므로 이름을
      // 따로 둔다(덮어쓰지 않게). DOMPurify(MPL-2.0 OR Apache-2.0)는 Apache-2.0으로 쓰고, 그 패키지의 LICENSE가 그 본문이다.
      license: isTest ? false : { fileName: isVoice ? 'THIRD_PARTY_NOTICES.voice.md' : 'THIRD_PARTY_NOTICES.md' },
      rolldownOptions: {
        // 벤더(xterm 등)는 아직 ES 모듈로 import하지 않는다 — index.html의 classic
        // <script>가 로드하고 sw.js가 개별 프리캐시한다(F4에서 core/vendor.js가
        // window.* 를 re-export하며 이 경계가 명시화된다). 지금은 external로 지정할
        // 대상이 없다 — main.js가 실제로 import하는 건 legacy 앱 스크립트뿐이다.
        external: [],
        // includeDependenciesRecursively:false 의 요구 조건(Rolldown이 안내). main.js는 export가 없는 진입점이라 무해하다.
        preserveEntrySignatures: isVoice || isTest ? 'strict' : false,
        output: {
          // 공백·주석 제거(2026-11-10 복구, 초기 페이로드 346,035 → 287,512 B)에 문법 축약(compress)을 더했다(같은 날,
          // 305,789 → 293,932 B). 이름 줄이기(mangle)는 끈다: 산출물에 모듈 스코프 이름(`sessions`, `activeId`…)이 남아야
          // build-output.test.js(실제 프로덕션 빌드를 본다)가 청크 간 싱글톤 복제를 잡고, 스택·소스맵도 읽힌다. compress 의 부작용 옵션은 명시적으로 끈다: dropConsole·dropDebugger 는 false,
          // keepNames 는 켜서 `Function.name`·`Class.name` 을 지킨다. 부수효과 제거(treeshake.*)는 기본값(보수적)을 쓴다.
          // 문자열 따옴표는 백틱으로 바뀔 수 있다(codegen) — 테스트 정규식이 이를 받는다. test 빌드는 그대로 둔다.
          minify: isTest ? false : { mangle: false, compress: { dropConsole: false, dropDebugger: false, keepNames: { function: true, class: true } }, codegen: { removeWhitespace: true } },
          // N34/N35/ADR-26 — 지연 청크에도 **해시를 붙이지 않는다.** ADR-1의
          // "산출물 이름 고정" 계약이 entry뿐 아니라 청크에도 그대로 적용된다:
          // sw.js가 `/static/dist/`를 network-first로 잡아 캐시하는데, 이름이
          // 매 빌드 바뀌면 옛 청크가 캐시에 무한히 쌓이고 오프라인 복구도
          // 어긋난다. `[name]`은 아래 manualChunks가 반환한 이름 그대로 쓴다
          // (해시가 안 붙는다는 점은 entry와 동일, 청크가 둘 이상이라 정적
          // 문자열 하나로는 못 박을 수 없을 뿐).
          chunkFileNames: '[name].js',
          // ⚠ manualChunks가 없으면 안 된다(실측): 동적 import가 split point를
          // 만드는 순간 Rollup은 **entry와 청크가 공유하는 모듈 전부**를 청크로
          // 끌어올린다. Hud.tsx가 core/api·core/store·core/dom·term/e2e를 같이
          // 쓰므로 그 판정에 앱 그래프 거의 전부가 걸려서, app.js가 21바이트
          // 스텁이 되고 shell.js가 273KB, 게다가 두 번째 청크(shell2.js)까지
          // 생겨 고정 이름이 충돌했다.
          //
          // ── 2.1.6: 화이트리스트에서 **블랙리스트로 뒤집었다** ────────────────
          // 그전까지는 "지연시킬 것만 이름을 주고 나머지는 null(=Rollup 자동
          // 판정)"이었는데, 그 구조에는 **조용한 함정**이 있었다: 지연 청크가
          // 공유 모듈 하나(core/store·layout/store·term/session·theme…)를 새로
          // import하는 순간 그 모듈이 청크로 승격되고, 거기 딸린 그래프 전부가
          // 따라간다. 그러면 entry가 청크를 *정적으로* import하게 되어 지연이
          // 지연이 아니게 되는데, **빌드는 성공하고 크기 게이트도 통과한다**
          // (app.js는 오히려 작아지니까). 실측: snippets.js 하나를 지연시키자
          // app.js가 0.04KB 스텁이 되고 panels.js가 215KB로 부풀었다.
          // 그래서 이 함정을 `shell` 청크는 window 브리지로 우회하고 있었고
          // (Rail.tsx가 core/store를 직접 import하지 못한다), 새 화면을 지연
          // 청크로 뺄 때마다 같은 우회를 반복해야 했다.
          //
          // 이제 규칙은 하나다: **지연 대상만 열거하고, frontend/js 아래 나머지
          // 전부를 `core`로 못박는다.** 명시적으로 배정된 모듈은 다른 청크로
          // 끌려갈 수 없으므로, 지연 청크가 무엇을 import하든 승격이 구조적으로
          // 일어나지 않는다. 결과로 지연 모듈은 **평범한 정적 import를 그대로
          // 쓴다** — window 브리지도, 의존성 주입 배관도 필요 없다.
          //
          // 산출물 구성:
          // - `app.js`  — main.js(부팅 배선)만. 수십 KB.
          // - `core.js` — 상시 필요한 전부(스토어·PTY·레이아웃·터미널·테마).
          //               app.js가 정적 import하므로 **초기 페이로드**다.
          // - `shell.js`    — solid-js 런타임 + js/shell/(HUD·레일·독). bootApp() 직후.
          // - `panels.js`   — dock 탭이 여는 화면(뷰어·소스컨트롤·파일·포트·큐).
          // - `settings.js` — 설정 패널과 MCP 팝업이 여는 화면(설정 7개 섹션·스니펫·사용량).
          // panels와 settings를 나눈 이유는 아래 해당 규칙의 주석에 있다.
          //
          // ⚠ 그래서 크기 게이트는 app.js 하나가 아니라 **app.js + core.js 합계**를
          // 본다(frontend/tests/build-output.test.js). 상한의 뜻은 원래부터
          // "초기 페이로드 상한"(300KiB; 2026-11-04~11-10 한때 350KiB)이었고, 청크를 쪼갰다고 그 뜻이 느슨해지면 안 된다.
          //
          // ⚠ voice 빌드(별도 Rollup 실행, 같은 outDir)에는 적용하지 않는다 —
          // 적용하면 voice 그래프에서도 core.js가 나와 app 빌드의 core.js를
          // **덮어쓴다**(실측: 201KB → 10KB로 clobber). voice는 예나 지금이나
          // 단일 산출물이라 청크가 필요 없다.
          // vite 8(Rolldown)은 manualChunks를 더 이상 그대로 따르지 않는다 — 규칙이 잡은 모듈의 **의존 모듈까지**
          // 끌고 들어가 `panel`이 `history`에 먹히는 식으로 경계가 뒤섞였다(실측). 이름마다 그룹을 두고
          // includeDependenciesRecursively:false 로 "내가 고른 모듈만" 넣는다. 나머지는 위 chunkOf의 포괄 규칙이 core로 보낸다.
          strictExecutionOrder: isVoice || isTest ? undefined : true,
          codeSplitting: isVoice || isTest ? undefined : {
            includeDependenciesRecursively: false,
            groups: CHUNK_NAMES.map((name) => ({ name, test: (id) => chunkOf(id) === name })),
          },
        },
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
  };
});
