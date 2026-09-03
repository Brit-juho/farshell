// xterm 인스턴스 생성 + 애드온(fit/search/unicode11/webgl|canvas) + a11y 배선.
// F4에서 addSession(구 terminal.js :646-861 부근)에서 분리. term.open() 이후에만
// 가능한 배선(GPU 렌더러·복사/링크/터치)까지 여기서 끝내고 {term, fitAddon,
// searchAddon, wrapper}를 반환한다 — 나머지(WS 연결·탭 DOM)는 session.js가 조립.
import { isMobile, _isCoarsePointer } from '../core/env.js';
import { wireClipboard } from './selection.js';
import { wireLinks } from './links.js';
import { wireTouchScroll } from './touch.js';

// M6: 핀치로 조절한 폰트 크기가 있으면 그걸 기본값으로 — 없으면 기존 규칙.
// touch.js의 _setGlobalFontSize도 같은 상한/하한을 쓰므로 여기서 export해 공유한다
// (터치와 xterm-setup 양쪽에서 각자 선언하면 값이 어긋날 위험이 생긴다).
export const FONT_MIN = 8, FONT_MAX = 28;
const termFontSize = (() => {
  try {
    const saved = parseInt(localStorage.getItem('vt_font_size'), 10);
    if (saved >= FONT_MIN && saved <= FONT_MAX) return saved;
  } catch (_) {}
  return isMobile ? 12 : 14;
})();

// P1: addon-canvas.min.js를 WebGL이 없거나 실패했을 때만 동적으로 불러와
// term에 붙인다. 여러 세션이 동시에 이 경로를 타도 스크립트는 한 번만 로드.
let _canvasAddonLoading = null;
function _ensureCanvasAddon(term) {
  if (window.CanvasAddon) {
    try { term.loadAddon(new CanvasAddon.CanvasAddon()); } catch (_) {}
    return;
  }
  if (!_canvasAddonLoading) {
    _canvasAddonLoading = new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = '/static/vendor/addon-canvas.min.js';
      s.onload = resolve;
      s.onerror = resolve; // 실패해도 DOM 렌더러로 계속 동작(기능 저하만)
      document.head.appendChild(s);
    });
  }
  _canvasAddonLoading.then(() => {
    if (window.CanvasAddon) {
      try { term.loadAddon(new CanvasAddon.CanvasAddon()); } catch (_) {}
    }
  });
}

export function createXtermInstance(id) {
  const term = new Terminal({
    cursorBlink: true,
    // xterm 기본값(1000)은 서버 재접속 복원 예산(최대 256KB, server/pty_manager.py
    // SCROLLBACK_MAX_BYTES)에 비해 작아, 일반 텍스트 위주 세션에서는 서버가 보낸
    // scrollback 상당수가 도착 즉시 버려진다. Wave Terminal 기본값(2000)에 맞춤 —
    // 참고한 다른 프로젝트(wetty/ttyd/orca/blink/swell.sh)는 xterm 기본값을 그대로
    // 쓰거나 재접속 복원 자체를 지원하지 않아 참고할 표준값이 없었음(2026-09-02 조사).
    scrollback: 2000,
    fontSize: termFontSize,
    fontFamily: (window.getVtXtermFont ? window.getVtXtermFont() : "'IBM Plex Mono', ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace"),
    theme: (window.getVtXtermTheme ? window.getVtXtermTheme() : { background: '#1e1e2e' }),
    allowProposedApi: true,
    // ⚠ screenReaderMode는 매 write마다 접근성 hidden DOM/live-region을 유지한다.
    // 2026-07-09 CDP 실측 당시엔 동일 출력에 힙 증가가 ~8배(+1.6MB→+13.6MB)로
    // 기록돼 기본 off로 뒀었는데, M3 작업 중(2026-09-02) 같은 방식으로 재실측하니
    // 재현되지 않았다(접근성 DOM 노드 수가 출력량과 무관하게 고정폭 유지, GC 후
    // 힙도 on/off 비슷한 수준으로 수렴 — xterm.js 벤더 버전이 그 사이 바뀐 것으로
    // 추정, 다만 스트리밍 순간의 힙 스파이크는 on 쪽이 더 컸다). 이걸 감안해
    // 터치 기기(롱프레스 텍스트 선택이 필요한 쪽)는 기본 on, 데스크톱(마우스
    // 드래그 선택이 이미 되는 쪽)은 기본 off로 절충 — 순간 스파이크 리스크를
    // 필요한 쪽에만 감수시킨다. localStorage로 양쪽 다 강제 override 가능:
    // vt-a11y='1' 강제 on, '0' 강제 off, 미설정 시 위 기본 규칙.
    screenReaderMode: (() => {
      try {
        const v = localStorage.getItem('vt-a11y');
        if (v === '1') return true;
        if (v === '0') return false;
      } catch (_) {}
      return _isCoarsePointer();
    })(),
  });
  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  const searchAddon = new SearchAddon.SearchAddon();
  term.loadAddon(searchAddon);
  // 이모지 등 wide 문자 폭 계산을 최신 유니코드로 전환 — 없으면 xterm.js 기본값(V6
  // 테이블)이 최근 이모지를 좁은 문자(1칸)로 오판해, 폰트가 실제로는 2칸 너비로
  // 그리면서 다음 글자와 겹쳐 보인다(원격 웹 터미널에서만 발생, 로컬 Terminal.app/
  // iTerm2는 자체 폰트 렌더러라 폭 판정과 렌더링이 항상 일치해 영향 없음).
  if (window.Unicode11Addon) {
    term.loadAddon(new Unicode11Addon.Unicode11Addon());
    term.unicode.activeVersion = '11';
  }

  // 각 세션에 고유 wrapper div 생성 (show/hide로 탭 전환)
  const wrapper = document.createElement('div');
  wrapper.id = `term-${id}`;
  wrapper.style.cssText = 'height:100%;display:none;';
  document.getElementById('terminal-container').appendChild(wrapper);

  term.open(wrapper);

  // M5: xterm은 접근성 텍스트 레이어(.xterm-accessibility)에 기본
  // pointer-events:none을 건다 — 스크린리더가 "읽기만" 하도록 캔버스 위에 투명
  // 오버레이로만 존재하고, 실제 마우스/터치는 그 밑 캔버스로 그대로 통과시키기
  // 위해서다. 문제는 브라우저가 롱프레스 같은 네이티브 제스처의 히트타깃을
  // touchstart 시점에 한 번 확정한다는 것 — 롱프레스가 감지된 "다음에" JS로
  // pointer-events를 켜면 이미 늦어 텍스트 레이어가 대상이 될 수 없다. 그래서
  // 터치 기기(screenReaderMode가 켜진 쪽과 동일 조건)에서는 애초에 상시 켜둔다.
  // 대신 평소 짧은 탭(포커스/tmux 마우스 트래킹 클릭)까지 이 레이어가 가로채지
  // 않도록, 탭 하나는 밑 캔버스로 합성 이벤트를 만들어 그대로 전달한다.
  if (term.options.screenReaderMode && _isCoarsePointer()) {
    const a11yLayer = wrapper.querySelector('.xterm-accessibility');
    if (a11yLayer) {
      a11yLayer.style.pointerEvents = 'auto';
      let _tapStart = null; // {x,y,t} — 롱프레스/드래그로 번진 탭은 전달하지 않는다
      a11yLayer.addEventListener('pointerdown', (e) => {
        _tapStart = { x: e.clientX, y: e.clientY, t: Date.now() };
      });
      a11yLayer.addEventListener('pointerup', (e) => {
        const s = _tapStart; _tapStart = null;
        if (!s) return;
        const moved = Math.hypot(e.clientX - s.x, e.clientY - s.y);
        // 500ms/8px 안쪽이면 "탭"으로 보고 캔버스로 합성 클릭을 전달한다.
        // 그보다 길거나 크게 움직였으면 브라우저 자체 선택 제스처였을 가능성이
        // 높아 건드리지 않는다(이미 네이티브 선택이 진행 중일 수 있음).
        if (Date.now() - s.t > 500 || moved > 8) return;
        a11yLayer.style.pointerEvents = 'none';
        const target = document.elementFromPoint(e.clientX, e.clientY);
        a11yLayer.style.pointerEvents = 'auto';
        if (!target) return;
        for (const type of ['mousedown', 'mouseup', 'click']) {
          target.dispatchEvent(new MouseEvent(type, {
            bubbles: true, cancelable: true, view: window,
            clientX: e.clientX, clientY: e.clientY, button: 0,
          }));
        }
      });
    }
  }

  // GPU 렌더러 — 기본 DOM 렌더러는 활발한 스트리밍(TUI 등)에서 매 write마다 DOM
  // 노드를 갱신해 CPU/메모리 비용이 크다. WebGL 우선, 실패(GPU/드라이버 미지원 또는
  // context-lost) 시 Canvas로, 그마저 실패하면 DOM 그대로 유지한다(기능 저하 없음,
  // 성능만 낮음). WebGL/Canvas addon은 term.open() '이후'(DOM 부착 후)에만 로드 가능.
  // P1: addon-canvas.min.js(95KB)는 static <script>로 미리 안 실어둔다 — WebGL은
  // 대부분의 환경에서 성공해 실제로는 거의 안 쓰이므로, WebGL이 없거나 실패했을
  // 때만 동적으로 불러온다(_ensureCanvasAddon). DOM 렌더러로 잠깐 시작했다가
  // 로드 완료 시 Canvas로 업그레이드되는 것뿐이라 기능 저하는 없다.
  try {
    if (window.WebglAddon) {
      const webgl = new WebglAddon.WebglAddon();
      // P2: dispose만 하고 끝내면 xterm이 기본 DOM 렌더러로 추락한다(성능 저하).
      // ttyd 등도 컨텍스트 손실 시 재활성화는 안 하지만, 그건 업계 표준이 아니라
      // 우리 판단으로 한 단계 아래인 Canvas로라도 내려가게 한다.
      webgl.onContextLoss(() => {
        try { webgl.dispose(); } catch (_) {}
        _ensureCanvasAddon(term);
      });
      term.loadAddon(webgl);
    } else {
      _ensureCanvasAddon(term);
    }
  } catch (_) {
    _ensureCanvasAddon(term);
  }

  // 복사(자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 배선
  wireClipboard(id, term, wrapper);

  // T4: 출력 속 URL·파일 경로 자동 링크화 — URL은 새 탭, 경로는 코드 뷰어로.
  wireLinks(id, term);

  // 모바일 터치 스크롤 (tmux mouse on 이면 xterm이 터치를 앱으로 넘겨 스크롤이 죽는다)
  wireTouchScroll(id, term, wrapper);

  return { term, fitAddon, searchAddon, wrapper };
}
