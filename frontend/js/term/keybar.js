// 모바일 특수키 바(keybar). F4에서 terminal.js(구 :1842-2018)에서 분리.
// 소프트 키보드에 없는 특수키/조합키를 활성 PTY에 원시 시퀀스로 주입한다.
// Sticky Ctrl: Ctrl을 한 번 탭하면 "다음 한 키만" Ctrl 조합으로 전송된다
// (keybar 문자 버튼 · 소프트 키보드 문자 양쪽 모두). Claude Code/셸의 Ctrl 단축키용.
// 순수 키-시퀀스 로직(_KEYBAR_SEQ, ctrlByte, Ctrl+화살표, sticky 변환)은
// lib/keyseq.js(window.VTKeySeq)로 분리돼 있다 — DOM/세션 상태가 없어 단위 테스트 대상.
// VTKeySeq는 아직 UMD(globalThis.VTKeySeq)라 bare identifier로 읽는다(F2 판단 유지).
import { activeSessionId, activeSession } from '../core/store.js';
import { sendToPty } from './clipboard.js';
import { fitAndResize } from './resize.js';
import { _isCoarsePointer } from '../core/env.js';

let _ctrlArmed = false;

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
    if (btn.dataset.longpressTmux) {
      let fired = false;
      btn.classList.add('holding');
      const timer = setTimeout(() => {
        fired = true;
        btn.classList.remove('holding');
        btn.classList.add('longpress-fired');
        setTimeout(() => btn.classList.remove('longpress-fired'), 150);
        sendToPty(activeSessionId(), '\x02' + btn.dataset.longpressTmux);
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
  const positionBar = () => {
    const vv = window.visualViewport;
    if (!vv) return;
    const overlap = Math.max(0, window.innerHeight - (vv.height + vv.offsetTop));
    bar.style.transform = overlap > 0 ? `translateY(${-overlap}px)` : '';
  };
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', positionBar);
    window.visualViewport.addEventListener('scroll', positionBar);
  }
  window.addEventListener('resize', positionBar);

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
      tg.textContent = collapsed ? '▴' : '▾';
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
