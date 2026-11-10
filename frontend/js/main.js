// 앱 진입점 (F1 — 구 js/bootstrap.js를 대체). Vite가 이 파일 하나를 lib 모드로
// 번들해 frontend/dist/app.js + app.css를 만든다 (vite.config.js 참고).
//
// F2~F5에 걸쳐 모든 프런트엔드 자바스크립트를 여기 정적 import 그래프로
// 옮겼다(F5에서 마지막 9개 classic script 전환 완료). 아직 window.* 브리지가
// 남아 있는 값들은 (1) voice.js — capability 확인 후 별도로 독립 빌드·로드되는
// 완전히 분리된 번들이라 진짜 import가 불가능한 경우(voice/recording.js 상단
// 주석 참고), (2) (제거됨: 인라인 핸들러는 CSP가 막는다), (3) clearWorkspace처럼 콘솔 디버깅용으로 의도적으로 남긴 것뿐이다.
import '../../styles/main.css';

// 정적 HTML 문구 번역 — 다른 모듈이 DOM 문구를 읽거나 바꾸기 전에 한 번.
import { applyI18n } from './core/i18n.js';
applyI18n(document.body);

import './core/env.js';
import './core/api.js';
// 호스트 전환 — 전환해 둔 맥에 닿을 수 없는 이유(삭제·권한 회수·구버전)면 이 맥으로 되돌린다.
import { verifyRelayHost } from './core/host-route.js';
verifyRelayHost();
import './core/store.js';
// 정적 HTML의 <svg data-icon> 자리를 ui/icons.js 도형으로 채운다 — 원본은 icons.js 하나.
import { hydrateIcons } from './ui/icons.js';
hydrateIcons();
// N34 — HUD(지연 청크)에 넘겨줄 것들. 위 부수효과 import와 같은 모듈 인스턴스라
// 여기서 이름으로 한 번 더 가져와도 중복 평가는 없다(ES 모듈 캐시).
import { vtFetch } from './core/api.js';
import { getSession, activeSessionId, sessionDisplayName, subscribe as storeSubscribe } from './core/store.js';
import { onLayoutChange } from './layout/store.js';
import { branchOf, repoOf, onBranchChips } from './layout/branch-chips.js';
import { allStatuses, isUnseen, onStatusChange } from './agent/state.js';
import { copyToClipboard } from './term/clipboard.js';
import { lang } from './core/i18n.js';
import { isMac } from './core/env.js';
import { getAction, registerAction } from './core/dom.js';
import { E2E_ENABLED } from './term/e2e.js';
import { setFocusUiLoader, isFocusActive, jumpToWaiting, FOCUS_EVENT } from './layout/focus.js';  // 리디자인 7단계 — 집중 모드 스위치(지연 UI는 아래)
import { get as settingGet, set as settingSet, subscribe as settingSubscribe, firstLoad as settingsFirstLoad } from './core/settings.js';  // S2 — 설정 스토어(모듈 평가 시점에 캐시+마이그레이션 동기 적용)
import { getBinding, displayCombo } from './core/keymap.js';    // S3 — 키맵 레지스트리(각 모듈이 register()로 액션을 붙인다)
import './core/dom.js';
import { showToast } from './ui/toast.js';   // F5 — showToast/dismissToast. 다른 모듈이 참조하므로 먼저.
import { prefetchUpload } from './term/upload-lite.js';  // 업로드 본체는 upload 청크 — 유휴 시간에 미리 받는다
import { prefetchDialogs } from './core/dialog-lazy.js';  // 확인·입력·알림 대화상자 본체는 panel 청크
import { watchSelects } from './ui/select-lazy.js';  // 기본 <select> 팝업 대신 FarShell 드롭다운(모든 select에 자동) — 본체는 select 청크
import { initTooltips } from './ui/tooltip-lazy.js';  // 2026-09-18 — 네이티브 title 대체(data-tip). 본체는 첫 사용 때(리디자인 9단계)
import './theme.js';      // F5 — classic script에서 전환. term/xterm-setup.js가 이걸 import한다.
import './lib/ansilex.js';
import './lib/keyseq.js';
import './core/app-session-lazy.js';  // 로그아웃·캐시 지우기 액션(본체는 shell 청크)
import './panels/settings-lazy.js';  // S4 — 설정 화면(Mod+, / rail ⚙)
import './panels/usage-badge.js';     // U2 — 사용량 게이지(rail 「사용량」 + 우측 레일)
import './ui/favicon.js';
import './push/swreg.js';

// F4 — terminal.js(2023줄)를 term/ 아래 15개 모듈로 분할. 서로 실제 import/export로
// 엮여 있어 나열 순서 자체는 중요하지 않다(ES 모듈 정적 import는 의존 그래프 순으로
// 평가되고 중복 평가되지 않는다) — 그래도 읽기 편하도록 의존 방향(leaf → 조립부)
// 순서로 나열한다. term/boot.js는 자동 실행 대신 bootApp()을 export한다(아래 참고).
import './term/e2e.js';
import './term/clipboard.js';
import './term/resize.js';
import './layout/panes.js';   // L3 1단계 — 분할 pane 렌더러. session.js의 switchTo()가 이걸 거친다.
// term/touch.js는 부수효과가 없는 함수 모듈이고 xterm-setup.js가 터치 기기에서만 import()한다 — 여기서 정적 import하지 않는다.
import './term/links.js';
import './layout/view-actions.js';  // 뷰(탭마다 pane 트리)의 window 브리지 — 탭 줄 UI는 없다
import './layout/menubar-slots.js'; // 720px 이상: #topbar의 남은 버튼을 제목 막대로 옮긴다
import('./term/tab-worktree.js');  // 현재 작업 정보 조회는 셸 UI와 함께 지연 로딩
import './term/remote.js';   // N7/N39 3단계 — 원격 세션 attach(window 브리지 등록)
import './term/selection.js';
import './term/xterm-setup.js';
import './term/workspace.js';
import './term/conn-overlay.js';
import './term/keybar.js';
import './term/ws.js';
import './term/settings-apply.js';  // S2 — 설정 변경을 살아있는 xterm에 즉시 반영
import './term/keymap-actions.js'; // S3 — 분할·rail 토글 등 남은 액션 배선 + wire()
import './term/session.js';
import './term/guide.js';
// N38(70-mobile.md §1/§2) — 모바일 골격(상단 바·하단 내비) + 플릿 홈 지연
// 마운트. term/session.js(switchTo) 이후 — 직접 참조는 안 하지만(window
// 브리지로 대신 받는 shell/Fleet.tsx 쪽), 같은 부팅 단계에 두는 게 자연스럽다.
import './layout/mobile-nav.js';
// 리디자인 8단계 — 폰 수동 집중 모드(상단바 버튼 · 개입 알약 · 위 가장자리 끌어내리기). focus.toggle 액션의 compact 몫.
import './layout/mobile-focus.js';
// 폰 파일 편집(시안 M-F5) — 편집 중엔 아래 탭을 숨기고 키보드 위에 Tab·화살표·Esc·저장 줄을 띄운다.
import './layout/mobile-edit.js';
import './layout/env-chip.js'; // N45 — 모바일 헤더 환경 칩
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
import './panels/system-lazy.js';   // 시스템(CPU·RAM·네트워크) — HUD 칩과 dock 탭이 연다
import './panels/files-lazy.js';
import './queue-lazy.js';
import './snippets-lazy.js';
import './share-lazy.js';   // 에이전트 세션 공유 — 본체는 `share` 청크
// N5/N40/N46 — quickopen.js(vanilla)는 shell/Palette.tsx(지연 청크)로
// 대체됐다. palette-lazy.js는 panels/viewer-lazy.js와 같은 스텁 패턴이라
// term/session.js·agent/preview.js 뒤에 둔다(그 값들을 deps로 담아간다).
import './palette-lazy.js';
import './pushui.js';
// ADR-29 E — 48px 아이콘 레일 자체(layout/rail.js)는 지웠다: 데스크톱
// 레일(shell/Rail.tsx)이 세션 목록을, 정식 Settings 패널이 설정 정적
// 컨텐츠를 이미 대신하고 있어(2026-09-18 CHANGELOG) 실제로 쓰이던 건
// clients.show 액션 하나뿐이었다 — 그 하나만 옮겼다.
import './layout/connected-screens-lazy.js';  // 본체는 `screens` 청크

// LEGACY_APP_SCRIPTS(classic <script> 순차 로더)는 F5에서 제거했다 — 위 정적
// import가 전부 대체했고, main.js가 실행되는 시점엔 이미 모든 모듈이 평가돼
// 있으므로(ES 모듈은 정적 import 그래프를 먼저 전부 해석한 뒤 진입 모듈 본문을
// 실행한다) bootApp()을 바로 불러도 안전하다 — classic script 시절엔 toast.js가
// "먼저 로드"됨을 명시적으로 기다려야 했지만 이제 그 문제 자체가 없다.
try {
  // 문서 전체에 위임 리스너 하나만 건다 — 트리거가 언제 생기든 상관없으므로
  // bootApp()보다 먼저 불러도 된다(오히려 첫 화면의 버튼을 놓치지 않는다).
  initTooltips();
  watchSelects();
  prefetchDialogs();
  prefetchUpload();
  // `appBooted`(아래)는 "배선 끝"이고 이건 "부팅 끝"이다(성공·실패 무관). 부팅 도중에
  // 새로고침하는 테스트가 진행 중인 부팅을 끊지 않도록 e2e가 이걸 기다린다(ADR-45).
  // appReady = 부팅 끝 **그리고** 설정 첫 로드 끝. bootApp()은 설정을 기다리지 않아(화면이 늦게
  // 뜨면 안 된다) 설정 도착 전에 부팅이 끝나기도 하는데, 그 뒤 도착한 설정이 _notify(getAll())로
  // 저장 스킨 등을 다시 입혀 e2e가 바꿔 둔 값을 되돌렸다(ADR-45 경합과 같은 계열).
  // 설정 쪽 대기는 5초 상한: core/api.js GET 마감(15초)보다 짧게 잡아, 설정이 끝내 안 와도
  // (load()가 불리지 않는 인증 전 경로 포함) appReady가 영원히 안 서는 일이 없게 한다.
  const settingsReady = Promise.race([
    settingsFirstLoad(),
    new Promise((resolve) => setTimeout(resolve, 5000)),
  ]);
  Promise.allSettled([bootApp(), settingsReady])
    .then(() => { document.documentElement.dataset.appReady = 'true'; });
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
  const activeClientsPath = () => {
    const s = getSession(activeSessionId());
    const name = s && (s.tmuxName || s.tmux_name);
    if (!name) return '';
    if (s.remote) return `/api/hosts/${encodeURIComponent(s.remote.host)}/clients?session=${encodeURIComponent(name)}&screen=${encodeURIComponent(s.remote.screen || '')}`;
    return `/api/tmux/clients?session=${encodeURIComponent(name)}`;
  };

  // 메뉴바의 에이전트 집계 칩. 4상태는 agent/state.js가 유일한 출처다(키: tmux 이름).
  // 완료는 「확인하지 않은 것」만 센다 — 이미 본 완료까지 세면 영원히 0이 안 된다.
  const agentCounts = () => {
    let waiting = 0, working = 0, done = 0;
    for (const [name, st] of allStatuses()) {
      if (st === 'waiting') waiting += 1;
      else if (st === 'working') working += 1;
      else if (st === 'done' && isUnseen(name)) done += 1;
    }
    return { waiting, working, done };
  };
  // 가장 오래 기다린 입력 대기 세션으로 이동한다. 고르는 규칙은 layout/focus.js 한 곳 — 집중 모드의 개입 알약도
  // 같은 세션을 가리킨다.
  registerAction('agents.jump', () => jumpToWaiting());
  // 키맵 id → 표시용 조합. core/keymap.js는 지연 청크가 직접 못 읽으므로 여기서 넘긴다.
  const keyHint = (id) => {
    const b = getBinding(id);
    return b && !b.unavailable ? displayCombo(b.combo) : '';
  };
  // 푸시 구독 상태. 이 브라우저가 못 쓰면(비보안 컨텍스트·미지원) 칩 자체를 안 그린다.
  const pushState = async () => {
    const P = window.VTPush;
    if (!P || P.blockReason()) return 'unavailable';
    return (await P.isSubscribed()) ? 'on' : 'off';
  };

  const hudRoot = document.getElementById('vt-hud');
  if (hudRoot) {
    import('./shell/Hud.tsx')
      .then(({ mountHud }) => mountHud(hudRoot, {
        vtFetch, activeTmuxName, activeClientsPath, getAction, e2eEnabled: E2E_ENABLED,
        agentCounts, subscribeAgents: onStatusChange, pushState,
        locale: () => (lang() === 'en' ? 'en-US' : 'ko-KR'),
        // 배치(ui.hud.layout)는 설정 화면에서 바뀐다 — core/settings.js는 지연 청크가 직접 못 읽으므로 여기서 넘긴다.
        getLayout: () => settingGet('ui.hud.layout'),
        subscribeLayout: (fn) => settingSubscribe((changed) => {
          const keys = Array.isArray(changed) ? changed : (changed && typeof changed === 'object' ? Object.keys(changed) : [changed]);
          if (!keys.length || keys.includes('ui.hud.layout')) fn();
        }),
      }))
      .catch((e) => console.error('[FarShell HUD]', e)); // HUD는 부가 정보 — 실패해도 앱은 계속
  }

  // 리디자인 2단계 — 제목 막대 36(FarShell 메뉴 · 호스트 · 저장소 › 브랜치 › 세션 · 팔레트 입력 · 도구).
  // 옛 워크스페이스 칩(HeaderExtras.tsx)과 메뉴바의 FarShell 메뉴가 여기로 왔다. 같은 지연 청크(shell.js).
  // 경로는 활성 칸의 세션에서 읽는다 — 브랜치·저장소 지도는 칸 머리 브랜치 칩과 같은 출처(layout/branch-chips.js).
  const titleRoot = document.getElementById('vt-titlebar');
  if (titleRoot) {
    const crumb = () => {
      const sid = activeSessionId();
      const s = sid ? getSession(sid) : null;
      if (!s) return {};
      const tmux = s.tmuxName || s.tmux_name || '';
      return { repo: repoOf(tmux), branch: branchOf(tmux), session: sessionDisplayName(sid) || tmux || null };
    };
    const subscribeCrumb = (fn) => {
      const offs = [storeSubscribe(fn), onLayoutChange(fn), onBranchChips(fn)];
      return () => { for (const off of offs) if (typeof off === 'function') off(); };
    };
    import('./shell/TitleBar.tsx')
      .then(({ mountTitleBar }) => mountTitleBar(titleRoot, {
        vtFetch, getAction, isMac, copyText: copyToClipboard, toast: showToast, crumb, subscribeCrumb,
        keyHint, focusActive: isFocusActive, focusEvent: FOCUS_EVENT,
      }))
      .catch((e) => console.error('[FarShell title bar]', e));
  }

  // 리디자인 1단계 — 작업대(활동 막대 + 사이드바). 사이드바 안에 워크트리 레일(N36 §5)과
  // dock 본문(N35 §6)이 함께 마운트된다. 같은 지연 청크(shell.js)를 공유한다. dock은 부팅 시
  // 펼친 도구 활동의 패널 렌더러(queue.js 등)를 곧바로 부르므로 그 액션들이 registerAction으로
  // 등록돼 있어야 한다(정적 import가 위에서 이미 끝났으므로 이 시점엔 전부 등록돼 있다).
  const workbenchRoot = document.getElementById('vt-workbench-slot');
  if (workbenchRoot) {
    import('./shell/Workbench.tsx')
      .then(({ mountWorkbench }) => mountWorkbench(workbenchRoot, {
        vtFetch, getAction,
        // 활동 막대 툴팁의 단축키.
        keyHint,
      }))
      .catch((e) => console.error('[FarShell workbench]', e));
  }

  // 리디자인 7단계 — 데스크톱 집중 모드의 떠 있는 부분(가장자리 노출 · 들어올 때 안내 · 터치용 끝내기)은 처음 켤 때
  // 같은 지연 청크(shell.js)에서 불러온다. 스위치는 core(layout/focus.js), 개입 알약은 두 폭 공용(layout/mobile-focus.js).
  setFocusUiLoader(() => import('./shell/Focus.tsx').then(({ mountFocus }) => {
    const host = document.createElement('div');
    host.id = 'vt-focus-ui';
    document.body.appendChild(host);
    return mountFocus(host, {
      getAction, keyHint,
      active: isFocusActive, focusEvent: FOCUS_EVENT,
      getSetting: settingGet, setSetting: settingSet, subscribeSettings: settingSubscribe,
    });
  }));

  // 부팅 완료 표시. `appBootFailed`(아래)와 짝이다 — 지금까지 실패만 표시하고
  // 성공은 표시하지 않아서, 밖에서는 "아직 부팅 중"과 "부팅 끝"을 구분할 수 없었다.
  //
  // 필요해진 계기: 실브라우저 스모크(E1)가 rail 버튼을 클릭했는데 아무 일도
  // 안 일어났다. 옛 레일은 index.html의 **정적 마크업**이라 그게 보인다고
  // 배선이 끝난 게 아니다 — 요소는 있고 리스너는 아직 없는 창이 존재한다.
  // sleep으로 덮으면 느려지고 CI에서 플레이키해진다. 앱이 직접 말하는 게 맞다.
  document.documentElement.dataset.appBooted = 'true';
} catch (error) {
  console.error('[FarShell bootstrap]', error);
  document.documentElement.dataset.appBootFailed = 'true';
}
