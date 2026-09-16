// 앱 진입점 (F1 — 구 js/bootstrap.js를 대체). Vite가 이 파일 하나를 lib 모드로
// 번들해 frontend/dist/app.js + app.css를 만든다 (vite.config.js 참고).
//
// F2~F5에 걸쳐 모든 프런트엔드 자바스크립트를 여기 정적 import 그래프로
// 옮겼다(F5에서 마지막 9개 classic script 전환 완료). 아직 window.* 브리지가
// 남아 있는 값들은 (1) voice.js — capability 확인 후 별도로 독립 빌드·로드되는
// 완전히 분리된 번들이라 진짜 import가 불가능한 경우(voice/recording.js 상단
// 주석 참고), (2) index.html의 인라인 onchange="uploadFile(this)" 같은 HTML
// 속성 핸들러, (3) clearWorkspace처럼 콘솔 디버깅용으로 의도적으로 남긴 것뿐이다.
import '../../styles/main.css';

import './core/env.js';
import './core/api.js';
import './core/store.js';
// N34 — HUD(지연 청크)에 넘겨줄 것들. 위 부수효과 import와 같은 모듈 인스턴스라
// 여기서 이름으로 한 번 더 가져와도 중복 평가는 없다(ES 모듈 캐시).
import { vtFetch } from './core/api.js';
import { getSession, activeSessionId } from './core/store.js';
import { getAction } from './core/dom.js';
import { E2E_ENABLED } from './term/e2e.js';
import './core/settings.js';  // S2 — 설정 스토어(모듈 평가 시점에 캐시+마이그레이션 동기 적용)
import './core/keymap.js';    // S3 — 키맵 레지스트리(각 모듈이 register()로 액션을 붙인다)
import './core/dom.js';
import './ui/toast.js';   // F5 — showToast/dismissToast. 다른 모듈이 참조하므로 먼저.
import './theme.js';      // F5 — classic script에서 전환. term/xterm-setup.js가 이걸 import한다.
import './lib/ansilex.js';
import './lib/difflex.js';
import './lib/keyseq.js';
import './panels/panel.js';
import './panels/settings-lazy.js';  // S4 — 설정 화면(Mod+, / rail ⚙)
import './panels/usage-badge.js';     // U2 — 사용량 게이지(rail 「사용량」 + 우측 레일)
import './layout/right-rail.js';// L8 — 우측 레일 접기/펴기
import './ui/favicon.js';
import './ui/settings-toggles.js';  // E2 — rail 설정 패널 체크박스 ↔ 설정 스토어
import './push/swreg.js';

// F4 — terminal.js(2023줄)를 term/ 아래 15개 모듈로 분할. 서로 실제 import/export로
// 엮여 있어 나열 순서 자체는 중요하지 않다(ES 모듈 정적 import는 의존 그래프 순으로
// 평가되고 중복 평가되지 않는다) — 그래도 읽기 편하도록 의존 방향(leaf → 조립부)
// 순서로 나열한다. term/boot.js는 자동 실행 대신 bootApp()을 export한다(아래 참고).
import './term/e2e.js';
import './term/clipboard.js';
import './term/resize.js';
import './layout/panes.js';   // L3 1단계 — 분할 pane 렌더러. session.js의 switchTo()가 이걸 거친다.
import './term/touch.js';
import './term/links.js';
import './layout/tabbar.js';      // 10 §4 2단계 — 워크트리 탭 바(탭마다 pane 트리)
import './term/tab-worktree.js';  // 10 §4 1단계 — 탭 이름을 워크트리 단위로
import './term/remote.js';   // N7/N39 3단계 — 원격 세션 attach(window 브리지 등록)
import './term/selection.js';
import './term/xterm-setup.js';
import './term/workspace.js';
import './term/conn-overlay.js';
import './term/keybar.js';
import './term/ws.js';
import './term/settings-apply.js';  // S2 — 설정 변경을 살아있는 xterm에 즉시 반영
import './term/keymap-actions.js'; // S3 — 분할·rail 토글 등 남은 액션 배선 + wire()
import './term/tmux-panel.js';
import './term/session.js';
import './term/guide.js';
// N38(70-mobile.md §1/§2) — 모바일 골격(상단 바·하단 내비) + 플릿 홈 지연
// 마운트. term/session.js(switchTo) 이후 — 직접 참조는 안 하지만(window
// 브리지로 대신 받는 shell/Fleet.tsx 쪽), 같은 부팅 단계에 두는 게 자연스럽다.
import './layout/mobile-nav.js';
import { bootApp } from './term/boot.js';
// F4 — grid.js(382줄)를 agent/{badges,status,preview}.js 3개로 분할.
// badges(어떤 아이콘) → status(일하는 중인지, badges를 소비) → preview(그리드
// 뷰 자체, status/badges를 소비) 순서로 의존한다.
import './agent/badges.js';
import './agent/status.js';
// A5 — 서버가 판정한 4상태의 프런트 단일 소스(state)와 그걸 화면에 칠하는
// 유일한 곳(paint). status.js가 WS 메시지를 state로 넣고, paint가 구독해 탭·
// pane 헤더·파비콘을 갱신한다.
import './agent/state.js';
import './agent/paint.js';
import './agent/preview.js';
// F4 — viewer.js(1230줄)를 panels/viewer/ 아래 6개로 분할. **ADR-26/N35로
// 지연 청크(shell.js)에 넘겼다** — 여섯 파일을 여기서 직접 import하면(정적
// import) 그래프 전체가 app.js에 도로 박힌다(실측: 336KB로 300KiB 상한
// 초과, N34 커밋 기록). panels/viewer-lazy.js 하나만 남기고, 그 파일이
// `viewer.show` 액션 등록 + 실제 `import()`를 전담한다.
import './panels/viewer-lazy.js';
// F5 — 나머지 classic script 9개(theme/toast는 위에서 이미 처리)를 마저 ES
// 모듈로 전환. picker.js↔term/session.js는 순환 import(picker.js 상단 주석).
import './term/search-bar.js';
import './picker.js';
import './panels/ports-lazy.js';
import './panels/files-lazy.js';
import './queue-lazy.js';
import './snippets-lazy.js';
// N5/N40/N46 — quickopen.js(vanilla)는 shell/Palette.tsx(지연 청크)로
// 대체됐다. palette-lazy.js는 panels/viewer-lazy.js와 같은 스텁 패턴이라
// term/session.js·agent/preview.js 뒤에 둔다(그 값들을 deps로 담아간다).
import './palette-lazy.js';
import './pushui.js';
// L4 — 좌측 rail. queue.js/ports.js(data-action 대상)·agent/preview.js(세션
// 카드)·term/session.js 뒤에 둔다 — 전부 rail.js가 값으로 소비한다.
import './layout/rail.js';

// LEGACY_APP_SCRIPTS(classic <script> 순차 로더)는 F5에서 제거했다 — 위 정적
// import가 전부 대체했고, main.js가 실행되는 시점엔 이미 모든 모듈이 평가돼
// 있으므로(ES 모듈은 정적 import 그래프를 먼저 전부 해석한 뒤 진입 모듈 본문을
// 실행한다) bootApp()을 바로 불러도 안전하다 — classic script 시절엔 toast.js가
// "먼저 로드"됨을 명시적으로 기다려야 했지만 이제 그 문제 자체가 없다.
try {
  bootApp();
  // N34 §7 — 상태바 HUD(Solid). **동적 import**인 이유는 ADR-26의 번들 게이트다:
  // solid-js 런타임(~32KB)을 app.js에 정적으로 넣으면 300KiB 상한을 바로 넘긴다
  // (실측 303,733 → 336,179B). 상한을 올리는 건 금지돼 있으므로, 문서가 지정한
  // 대로 **고정 이름 지연 청크**(vite.config.js chunkFileNames → shell.js)로
  // 분리한다 — 해시가 붙으면 sw.js 캐시 계약이 깨지므로 이름은 반드시 고정.
  //
  // 부팅이 끝난 뒤에 마운트한다: HUD가 첫 렌더에서 활성 세션의 tmux 이름을 읽어
  // 「연결된 화면」을 조회하는데, bootApp() 전에는 세션이 없다.
  //
  // core/*는 **인자로 넘긴다**(Hud.tsx 상단 주석): 지연 청크가 그것들을 직접
  // import하면 Rollup이 청크 안에 복제해 넣어서, 앱의 액션 레지스트리와 HUD가
  // 보는 레지스트리가 다른 객체가 된다(칩을 눌러도 아무 일도 안 일어남).
  const activeTmuxName = () => {
    const s = getSession(activeSessionId());
    return (s && (s.tmuxName || s.tmux_name)) || '';
  };

  const hudRoot = document.getElementById('vt-hud');
  if (hudRoot) {
    import('./shell/Hud.tsx')
      .then(({ mountHud }) => mountHud(hudRoot, { vtFetch, activeTmuxName, getAction, e2eEnabled: E2E_ENABLED }))
      .catch((e) => console.error('[FarShell HUD]', e)); // HUD는 부가 정보 — 실패해도 앱은 계속
  }

  // N37 §4 — 워크스페이스 칩 + 연결된 화면. 같은 지연 청크(shell.js)를 공유하므로
  // HUD와 별도 import()를 또 부르지 않는다(모듈 캐시로 중복 다운로드는 안
  // 생기지만, 굳이 두 번 요청할 이유가 없다).
  const chipRoot = document.getElementById('vt-workspace-chip-slot');
  const screensRoot = document.getElementById('vt-screens-slot');
  if (chipRoot || screensRoot) {
    import('./shell/HeaderExtras.tsx')
      .then(({ mountWorkspaceChip, mountScreensButton }) => {
        const deps = { vtFetch, activeTmuxName, getAction };
        if (chipRoot) mountWorkspaceChip(chipRoot, deps);
        if (screensRoot) mountScreensButton(screensRoot, deps);
      })
      .catch((e) => console.error('[FarShell header]', e));
  }

  // N36 §5 — 워크트리 레일. 같은 지연 청크(shell.js)를 공유한다.
  const railRoot = document.getElementById('vt-wgrail-slot');
  if (railRoot) {
    import('./shell/Rail.tsx')
      .then(({ mountRail }) => mountRail(railRoot, { vtFetch, getAction }))
      .catch((e) => console.error('[FarShell rail]', e));
  }
  // N35 §6 — dock. 같은 지연 청크(shell.js)를 공유한다. 레일 뒤에 마운트하는
  // 이유는 dock이 부팅 시 활성 탭의 패널 렌더러(queue.js 등)를 곧바로 부르기
  // 때문이다 — 그 액션들이 registerAction으로 등록돼 있어야 한다(정적 import가
  // 위에서 이미 끝났으므로 이 시점엔 전부 등록돼 있다).
  const dockRoot = document.getElementById('vt-dock-slot');
  if (dockRoot) {
    import('./shell/Dock.tsx')
      .then(({ mountDock }) => mountDock(dockRoot, { vtFetch, getAction }))
      .catch((e) => console.error('[FarShell dock]', e));
  }

  // 부팅 완료 표시. `appBootFailed`(아래)와 짝이다 — 지금까지 실패만 표시하고
  // 성공은 표시하지 않아서, 밖에서는 "아직 부팅 중"과 "부팅 끝"을 구분할 수 없었다.
  //
  // 필요해진 계기: 실브라우저 스모크(E1)가 rail 버튼을 클릭했는데 아무 일도
  // 안 일어났다. `#vt-rail`은 index.html의 **정적 마크업**이라 그게 보인다고
  // 배선이 끝난 게 아니다 — 요소는 있고 리스너는 아직 없는 창이 존재한다.
  // sleep으로 덮으면 느려지고 CI에서 플레이키해진다. 앱이 직접 말하는 게 맞다.
  document.documentElement.dataset.appBooted = 'true';
} catch (error) {
  console.error('[FarShell bootstrap]', error);
  document.documentElement.dataset.appBootFailed = 'true';
}
