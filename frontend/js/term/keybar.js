// 모바일 특수키 바(keybar). F4에서 terminal.js(구 :1842-2018)에서 분리.
// 소프트 키보드에 없는 특수키/조합키를 활성 PTY에 원시 시퀀스로 주입한다.
// Sticky Ctrl: Ctrl을 한 번 탭하면 "다음 한 키만" Ctrl 조합으로 전송된다
// (keybar 문자 버튼 · 소프트 키보드 문자 양쪽 모두). Claude Code/셸의 Ctrl 단축키용.
// 순수 키-시퀀스 로직(_KEYBAR_SEQ, ctrlByte, Ctrl+화살표, sticky 변환)은
// lib/keyseq.js(window.VTKeySeq)로 분리돼 있다 — DOM/세션 상태가 없어 단위 테스트 대상.
// VTKeySeq는 아직 UMD(globalThis.VTKeySeq)라 bare identifier로 읽는다(F2 판단 유지).
import { activeSessionId, activeSession } from '../core/store.js';
import { icon } from '../ui/icons.js';
import { sendToPty } from './clipboard.js';
import { fitAndResize } from './resize.js';
import { _isCoarsePointer } from '../core/env.js';
import { getAction } from '../core/dom.js';

let _ctrlArmed = false;

// L7: 마이크의 물리적 위치 결정 — voice.js가 `#mic-btn-wrap`을 모듈 최상위에서
// `const`로 한 번만 캐시하므로(voice/recording.js), *어디에 있든* id만 유지되면
// 문제없다. 데스크톱(사용자 확인, 문서에 명시적 근거는 없었음)은 rail 설정
// 패널(#vt-rail-mic-slot, index.html 정적 마크업)에 그대로 두고, 터치 기기만
// keybar 슬롯으로 옮긴다 — keybar 자체가 pointer:coarse 전용이므로 그 판정
// 기준을 그대로 재사용한다. keybar 노출 여부(?keybar=1 강제 등)와는 무관하게
// "입력 방식이 무엇이냐"만 본다 — 아래 initKeybar()의 이른 return보다 먼저
// 실행해야 한다(강제 노출 없이 조용히 종료돼도 마이크는 제자리를 찾아가야 함).
// 2026-09-18 — 데스크톱 자리가 바뀌었다. 예전엔 숨은 옛 레일의 설정 플라이아웃
// (#vt-rail-mic-slot)에 있었고 `⋯ 더보기` 메뉴로만 닿을 수 있었다 — 그 메뉴의
// 나머지 항목이 전부 dock·팔레트·설정으로 옮겨가면서, ⋯는 사실상 **마이크 하나
// 때문에** 남아 있는 메뉴가 됐다. 그래서 마이크를 레일 바닥(#vt-rail-mic-home,
// 설정 버튼 옆)으로 꺼내고 ⋯를 없앤다.
//
// 레일은 지연 로드되는 Solid 컴포넌트라 이 모듈이 평가될 때는 아직 없다 —
// Rail.tsx가 마운트 직후 placeMicButton()을 한 번 더 부른다. 그래서 이 함수는
// **여러 번 불려도 안전**해야 하고(이미 제자리면 아무것도 안 한다), 레일이 아직
// 없으면 옛 슬롯에 그대로 둔다(음성이 조용히 사라지지 않게).
export function placeMicButton() {
  const mic = document.getElementById('mic-btn-wrap');
  if (!mic) return; // .needs-voice로 이미 숨겨졌거나(음성 미설치) 마크업 자체가 없는 테스트 환경
  const target = _isCoarsePointer()
    ? document.getElementById('keybar-slot-mic')
    : (document.getElementById('vt-rail-mic-home') || document.getElementById('vt-rail-mic-slot'));
  if (target && mic.parentElement !== target) target.appendChild(mic);
}
placeMicButton();

function _setCtrlArmed(on) {
  _ctrlArmed = on;
  const btn = document.querySelector('#keybar .kb-mod[data-mod="ctrl"]');
  if (btn) {
    btn.classList.toggle('armed', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

// term.onData 경로(ws.js)에서 호출 — armed Ctrl이면 입력 첫 글자에 Ctrl 조합 적용 후 해제.
export function applyStickyMod(data) {
  if (_ctrlArmed && data) {
    _setCtrlArmed(false);   // 입력이 오면 sticky 상태 소비(해제)
    return VTKeySeq.applyCtrlToInput(data);
  }
  return data;
}

function _focusActiveTerm() {
  const t = activeSession()?.term;
  if (t) { try { t.focus(); } catch (_) {} }
}

export function initKeybar() {
  const bar = document.getElementById('keybar');
  if (!bar) return;
  // 물리 키보드가 없는 터치 기기에서만 노출 (데스크톱은 CSS로도 숨기지만 이중 방어).
  // 강제 오버라이드: ?keybar=1 또는 localStorage vt_keybar='on' (터치 노트북/테스트용).
  const coarse = _isCoarsePointer();
  let _force = false;
  try {
    const _urlParams = new URLSearchParams(location.search);
    _force = _urlParams.get('keybar') === '1' || localStorage.getItem('vt_keybar') === 'on';
  } catch (_) {}
  if (!coarse && !_force) return;
  // 강제 노출 시엔 CSS의 pointer:fine 숨김을 이기도록 클래스 부여.
  if (_force) bar.classList.add('force-show');
  bar.hidden = false;

  // L7: 좌측 고정 슬롯 — 큐/업로드. 마이크는 이미 _placeMicButton()이 옮겨뒀다.
  // 이 둘은 keybar 밖의 다른 곳(rail·팔레트)과 액션을 공유하므로 재구현하지
  // 않는다 — queue.show는 rail.js가 등록한 게 아니라 queue.js가 F5에서 이미
  // registerAction해둔 진짜 소스이고, 업로드는 L4가 만들어둔 단일 #file-input을
  // 그대로 클릭한다.
  document.getElementById('keybar-slot-queue')?.addEventListener('click', () => getAction('queue.show')?.());
  document.getElementById('keybar-slot-upload')?.addEventListener('click', () => document.getElementById('file-input')?.click());

  // M3: ←/→ 버튼을 누른 채 드래그하면 끈 거리만큼 같은 방향으로 연속 이동.
  const ARROW_DRAG_STEP_PX = 14;
  let _dragArrow = null;
  // R4: 롱프레스 임계값 — 사람이 "누르고 있다"고 인지하는 최소 시간대(400~600ms 범주).
  const LONGPRESS_MS = 500;

  // pointerdown에서 preventDefault → 터미널 textarea 포커스를 뺏지 않아
  // 소프트 키보드가 내려가지 않는다. (버튼 탭마다 키보드가 닫히면 못 씀)
  bar.addEventListener('pointerdown', (e) => {
    // 접기/펴기 토글 — .kb가 아니므로 먼저 가로챈다.
    const toggle = e.target.closest('#keybar-toggle');
    if (toggle) {
      e.preventDefault();
      _setKeybarCollapsed(!bar.classList.contains('collapsed'));
      _focusActiveTerm();
      return;
    }
    const btn = e.target.closest('.kb');
    if (!btn) return;
    e.preventDefault();
    if (btn.dataset.mod === 'ctrl') { _setCtrlArmed(!_ctrlArmed); _focusActiveTerm(); return; }

    // R4: n/p처럼 data-longpress-tmux가 붙은 버튼은 짧게 누르면 평소처럼 문자를
    // 입력하고, LONGPRESS_MS 이상 누르고 있으면 그 대신 tmux prefix(Ctrl-B, 0x02)
    // + n/p를 보내 창을 전환한다(swell.sh 패턴). 다른 키들은 기존처럼 pointerdown
    // 즉시 발화 — 여기서만 pointerup까지 기다리는 예외를 둔다.
    // [M4] 롱프레스 키 변형 — R4가 n/p에 만든 그 동작을 **아무 키에나** 붙일 수
    // 있게 일반화한 것이다. `data-longpress-tmux`는 tmux prefix를 앞에 붙이고,
    // `data-longpress-seq`는 그 문자를 그대로 보낸다. 폰 키바는 자리가 12칸뿐
    // 이라 `_`·`\`·백틱 같은 키를 새 버튼으로 넣을 수 없는데, 이 둘은 짝이
    // 뚜렷해서(`-`↔`_`, `/`↔`\`) 같은 자리에 겹쳐도 헷갈리지 않는다.
    const lpTmux = btn.dataset.longpressTmux;
    const lpSeq = btn.dataset.longpressSeq;
    if (lpTmux || lpSeq) {
      let fired = false;
      btn.classList.add('holding');
      const timer = setTimeout(() => {
        fired = true;
        btn.classList.remove('holding');
        btn.classList.add('longpress-fired');
        setTimeout(() => btn.classList.remove('longpress-fired'), 150);
        sendToPty(activeSessionId(), lpTmux ? '\x02' + lpTmux : lpSeq);
        _focusActiveTerm();
      }, LONGPRESS_MS);
      const finish = (sendShort) => {
        clearTimeout(timer);
        btn.classList.remove('holding');
        bar.removeEventListener('pointerup', onUp);
        bar.removeEventListener('pointercancel', onCancel);
        if (sendShort && !fired) {
          const shortOut = VTKeySeq.keybarSeq({ key: btn.dataset.key, seq: btn.dataset.seq, ctrl: _ctrlArmed });
          if (shortOut) {
            if (_ctrlArmed) _setCtrlArmed(false);
            sendToPty(activeSessionId(), shortOut);
            _focusActiveTerm();
          }
        }
      };
      const onUp = (ev) => { if (ev.pointerId === e.pointerId) finish(true); };
      const onCancel = (ev) => { if (ev.pointerId === e.pointerId) finish(false); };
      bar.addEventListener('pointerup', onUp);
      bar.addEventListener('pointercancel', onCancel);
      return;
    }

    // armed면 keybarSeq가 Ctrl+화살표(단어 이동)·Ctrl+문자를 조합해 준다.
    const out = VTKeySeq.keybarSeq({ key: btn.dataset.key, seq: btn.dataset.seq, ctrl: _ctrlArmed });
    if (!out) return;
    if (_ctrlArmed) _setCtrlArmed(false);
    sendToPty(activeSessionId(), out);
    _focusActiveTerm();
    // M3: ←/→를 누른 채 그 방향으로 더 끌면 끈 거리만큼 같은 방향으로 반복
    // 전송한다(트랙패드형 연속 이동). 반대로 되끄는 건 무시한다 — 화살표는
    // 이미 보낸 걸 취소할 수 없어서, "얼마나 더 보냈는지"만 늘어나는 카운터로
    // 추적해야 화면에 보이는 커서 위치와 어긋나지 않는다.
    if (btn.dataset.key === 'left' || btn.dataset.key === 'right') {
      _dragArrow = { key: btn.dataset.key, pointerId: e.pointerId, startX: e.clientX, steps: 1 };
      try { btn.setPointerCapture(e.pointerId); } catch (_) {}
    }
  });

  bar.addEventListener('pointermove', (e) => {
    if (!_dragArrow || e.pointerId !== _dragArrow.pointerId) return;
    const dir = _dragArrow.key === 'right' ? 1 : -1;
    const advanced = (e.clientX - _dragArrow.startX) * dir;
    const targetSteps = Math.max(1, 1 + Math.floor(advanced / ARROW_DRAG_STEP_PX));
    while (_dragArrow.steps < targetSteps) {
      sendToPty(activeSessionId(), VTKeySeq.keybarSeq({ key: _dragArrow.key }));
      _dragArrow.steps++;
    }
  });
  const _endDragArrow = (e) => {
    if (_dragArrow && e.pointerId === _dragArrow.pointerId) _dragArrow = null;
  };
  bar.addEventListener('pointerup', _endDragArrow);
  bar.addEventListener('pointercancel', _endDragArrow);

  // 키보드 위로 띄우기 — visualViewport로 소프트 키보드 높이를 추정해 transform.
  //
  // 여기서 끝내면 안 된다: 바는 키보드 위로 올라가지만 #terminal-container의
  // 하단 여백은 그대로라, 터미널 마지막 줄(=프롬프트)이 키보드 뒤에 그대로
  // 깔린다. "액세서리 행이 터미널만 가린다"고 보고된 증상의 실체가 이것이다.
  // 그래서 두 값을 CSS 변수로 내보내 여백을 계산으로 만든다.
  //   --kb-height  : keybar가 실제로 차지하는 높이 (접힘/펼침·폰트·스킨 반영)
  //   --kb-overlap : 소프트 키보드가 레이아웃 뷰포트를 덮은 높이
  // 예전엔 이 둘을 --spacing*24 / *12 매직 넘버로 근사했는데, 접힘 상태와
  // 스킨별 버튼 높이가 달라지면 바로 어긋났다. 이제 측정값만 쓴다.
  const root = document.documentElement;
  let _lastMetrics = '';
  const positionBar = () => {
    const vv = window.visualViewport;
    const overlap = vv
      ? Math.max(0, window.innerHeight - (vv.height + vv.offsetTop))
      : 0;
    if (vv) bar.style.transform = overlap > 0 ? `translateY(${-overlap}px)` : '';
    // hidden(데스크톱)이면 offsetHeight가 0 — 그대로 0을 내보내면 된다.
    const height = bar.hidden ? 0 : bar.offsetHeight;
    const sig = `${height}|${overlap}`;
    if (sig === _lastMetrics) return;   // 스크롤 이벤트 폭탄에서 불필요한 fit 방지
    _lastMetrics = sig;
    root.style.setProperty('--kb-height', `${height}px`);
    root.style.setProperty('--kb-overlap', `${overlap}px`);
    // 터미널 가용 높이가 바뀌었으니 xterm 칸 수를 다시 센다. ws.js도
    // visualViewport resize를 듣지만, iOS는 키보드가 뜰 때 resize 없이
    // scroll만 쏘는 경우가 있어 여기서 한 번 더 확실히 건다(양쪽 모두
    // 디바운스가 걸려 있어 중복 호출은 합쳐진다).
    const activeId = activeSessionId();
    if (activeId) setTimeout(() => fitAndResize(activeId), 60);
  };
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', positionBar);
    window.visualViewport.addEventListener('scroll', positionBar);
  }
  window.addEventListener('resize', positionBar);
  // 바 자체의 높이 변화(접힘/펼침, 폰트 로드, 스킨 전환)도 여백에 반영해야
  // 한다 — 토글 핸들러에만 의존하면 폰트 지연 로드를 놓친다.
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => positionBar()).observe(bar);
  }

  // 접기/펴기 — 상태를 localStorage에 기억. 접으면 우하단 알약만 남고
  // 터미널 하단 여백이 줄어 화면을 더 쓴다. (함수 선언이라 위 핸들러에서 참조 가능)
  function _setKeybarCollapsed(collapsed) {
    // armed Ctrl이 접힘 상태로 넘어가면 하이라이트가 숨겨진 채 다음 입력이
    // Ctrl 조합으로 나가버린다(놀람). 접기/펴기 시 항상 해제.
    _setCtrlArmed(false);
    bar.classList.toggle('collapsed', collapsed);
    document.body.classList.toggle('kb-collapsed', collapsed);
    const tg = document.getElementById('keybar-toggle');
    if (tg) {
      // ▴▾ 글리프는 폰트마다 크기·중심이 제각각이라 다른 키바 버튼과
      // 광학적으로 안 맞았다. SVG 하나를 CSS로 뒤집어 쓴다.
      if (!tg.firstElementChild) tg.innerHTML = icon('chevron-down', 14);
      tg.classList.toggle('flipped', collapsed);
      tg.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      tg.setAttribute('aria-label', collapsed ? '특수키 바 펴기' : '특수키 바 접기');
    }
    try { localStorage.setItem('vt_keybar_collapsed', collapsed ? '1' : '0'); } catch (_) {}
    // 하단 여백이 바뀌었으니 xterm 칸 수 재계산.
    const activeId = activeSessionId();
    if (activeId) setTimeout(() => fitAndResize(activeId), 60);
    positionBar();
  }

  // 초기 상태 복원 (기본: 펼침)
  let _startCollapsed = false;
  try { _startCollapsed = localStorage.getItem('vt_keybar_collapsed') === '1'; } catch (_) {}
  if (_startCollapsed) _setKeybarCollapsed(true);

  positionBar();
}

// 원본(terminal.js)도 파일 하단에서 무조건 즉시 호출했다 — 동일하게 유지.
initKeybar();
