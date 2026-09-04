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
import './core/dom.js';
import './ui/toast.js';   // F5 — showToast/dismissToast. 다른 모듈이 참조하므로 먼저.
import './theme.js';      // F5 — classic script에서 전환. term/xterm-setup.js가 이걸 import한다.
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
import './layout/panes.js';   // L3 1단계 — 분할 pane 렌더러. session.js의 switchTo()가 이걸 거친다.
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
// F4 — grid.js(382줄)를 agent/{badges,status,preview}.js 3개로 분할.
// badges(어떤 아이콘) → status(일하는 중인지, badges를 소비) → preview(그리드
// 뷰 자체, status/badges를 소비) 순서로 의존한다.
import './agent/badges.js';
import './agent/status.js';
import './agent/preview.js';
// F4 — viewer.js(1230줄)를 panels/viewer/ 아래 6개로 분할. state(leaf) →
// shell/tree(서로 순환 참조, shell.js 헤더 주석 참고) → file → diff → git 순.
import './panels/viewer/state.js';
import './panels/viewer/file.js';
import './panels/viewer/tree.js';
import './panels/viewer/shell.js';
import './panels/viewer/diff.js';
import './panels/viewer/git.js';
// F5 — 나머지 classic script 9개(theme/toast는 위에서 이미 처리)를 마저 ES
// 모듈로 전환. picker.js↔term/session.js는 순환 import(picker.js 상단 주석),
// quickopen.js는 panels/viewer/*·term/session.js를 소비하므로 그 뒤에 둔다.
import './search.js';
import './picker.js';
import './ports.js';
import './queue.js';
import './snippets.js';
import './quickopen.js';
import './pushui.js';

// LEGACY_APP_SCRIPTS(classic <script> 순차 로더)는 F5에서 제거했다 — 위 정적
// import가 전부 대체했고, main.js가 실행되는 시점엔 이미 모든 모듈이 평가돼
// 있으므로(ES 모듈은 정적 import 그래프를 먼저 전부 해석한 뒤 진입 모듈 본문을
// 실행한다) bootApp()을 바로 불러도 안전하다 — classic script 시절엔 toast.js가
// "먼저 로드"됨을 명시적으로 기다려야 했지만 이제 그 문제 자체가 없다.
try {
  bootApp();
} catch (error) {
  console.error('[FarShell bootstrap]', error);
  document.documentElement.dataset.appBootFailed = 'true';
}
