// 터치 키바 DOM 배선. 상태와 인코더는 core의 keybar/keyboard-input 하나를 공유한다.
import { sendVirtualKey } from './keyboard-input.js';
import { activeSessionId } from '../core/store.js';
import { fitAndResize } from './resize.js';
import { _isCoarsePointer } from '../core/env.js';
import { get as settingGet, set as settingSet, subscribe as settingSubscribe } from '../core/settings.js';
import { MODS, _mods, _activeMods, _renderMods, _consumeMods, _focusActiveTerm,
  _copyTerminalSelection, _selectAllVisible, setTerminalSelectionMode,
  resetKeybarMods, FOCUS_MAX_VISIBLE_H } from './keybar.js';
export function initKeybarUI() {
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
  document.documentElement.classList.add('vt-has-keybar');

  document.addEventListener('vt:session-switch', () => setTerminalSelectionMode(false));

  // M3: ←/→ 버튼을 누른 채 드래그하면 끈 거리만큼 같은 방향으로 연속 이동.
  const ARROW_DRAG_STEP_PX = 14;
  let _dragArrow = null;
  // 스크롤 줄에서 이만큼 움직이면 "누름"이 아니라 "밀기"로 본다.
  const TAP_SLOP_PX = 8;
  // 스크롤 줄에서 눌린 채 손을 떼기를 기다리는 포인터들(pointerId → 상태).
  const _pending = new Map();

  const _send = (btn) => {
    if (!sendVirtualKey(activeSessionId(), { key: btn.dataset.key, seq: btn.dataset.seq, mods: _activeMods() })) return;
    _consumeMods();
    _focusActiveTerm();
  };

  // 누르던 포인터 하나를 끝낸다. tap=true면 손을 뗀 것(발화), false면 취소.
  const _finish = (pointerId, tap) => {
    const p = _pending.get(pointerId);
    if (!p) return;
    _pending.delete(pointerId);
    p.btn.classList.remove('holding');
    const mod = p.btn.dataset.mod;
    if (mod) {
      const st = _mods[mod];
      st.held.delete(pointerId);
      // 다른 키와 조합에 쓰였으면 탭이 아니다 — latch를 바꾸지 않는다.
      if (tap && !p.moved && !st.usedWhileHeld) {
        st.latch = st.latch === 'off' ? 'once' : st.latch === 'once' ? 'lock' : 'off';
      }
      if (st.held.size === 0) st.usedWhileHeld = false;
      _renderMods();
      if (tap) _focusActiveTerm();
      return;
    }
    if (tap && !p.moved) _send(p.btn);
  };

  // pointerdown에서 preventDefault → 터미널 textarea 포커스를 뺏지 않아
  // 소프트 키보드가 내려가지 않는다. (버튼 탭마다 키보드가 닫히면 못 씀)
  bar.addEventListener('pointerdown', (e) => {
    const selectionCopy = e.target.closest('#keybar-selection-copy');
    if (selectionCopy) { e.preventDefault(); void _copyTerminalSelection(); return; }
    // 2026-09-23 — 선택 모드로 "들어가는" 버튼은 없앴다(term/touch-select.js의
    // 터미널 꾹 누르기가 대신한다). 안에서 쓰는 이 셋만 남는다.
    const selectionAll = e.target.closest('#keybar-selection-all');
    if (selectionAll) { e.preventDefault(); _selectAllVisible(); return; }
    const selectionDone = e.target.closest('#keybar-selection-done');
    if (selectionDone) { e.preventDefault(); setTerminalSelectionMode(false); return; }
    const btn = e.target.closest('.kb');
    if (!btn) return;
    e.preventDefault();

    // 방향키 구역은 스크롤하지 않으므로 누르는 즉시 보낸다(반응성·연속 이동).
    if (btn.closest('#keybar-arrows')) {
      _send(btn);
      if (btn.dataset.key === 'left' || btn.dataset.key === 'right') {
        _dragArrow = { key: btn.dataset.key, pointerId: e.pointerId, startX: e.clientX, steps: 1 };
        try { btn.setPointerCapture(e.pointerId); } catch (_) {}
      }
      return;
    }

    // 스크롤 줄: 손을 뗄 때 보낸다. 수식키는 누르는 순간부터 "누르고 있음"으로
    // 켜져야 다른 손가락의 키와 동시 입력이 된다.
    const mod = btn.dataset.mod;
    if (mod) {
      const st = _mods[mod];
      if (st.held.size === 0) st.usedWhileHeld = false;
      st.held.add(e.pointerId);
      _renderMods();
    }
    btn.classList.add('holding');
    _pending.set(e.pointerId, { btn, x: e.clientX, y: e.clientY, moved: false });
  });

  bar.addEventListener('pointermove', (e) => {
    const p = _pending.get(e.pointerId);
    if (p && !p.moved && (Math.abs(e.clientX - p.x) > TAP_SLOP_PX || Math.abs(e.clientY - p.y) > TAP_SLOP_PX)) {
      // 밀기로 판정 — 이 누름은 발화하지 않는다. 수식키는 스크롤 줄 밖에
      // 고정돼 있어 손가락이 조금 흘러도 "누르고 있음"을 유지한다.
      if (p.btn.dataset.mod) return;
      _finish(e.pointerId, false);
      return;
    }
    if (!_dragArrow || e.pointerId !== _dragArrow.pointerId) return;
    const dir = _dragArrow.key === 'right' ? 1 : -1;
    const advanced = (e.clientX - _dragArrow.startX) * dir;
    const targetSteps = Math.max(1, 1 + Math.floor(advanced / ARROW_DRAG_STEP_PX));
    while (_dragArrow.steps < targetSteps) {
      sendToPty(activeSessionId(), VTKeySeq.keySeq({ key: _dragArrow.key, mods: _activeMods() }));
      _dragArrow.steps++;
    }
  });
  bar.addEventListener('pointerup', (e) => {
    _finish(e.pointerId, true);
    if (_dragArrow && e.pointerId === _dragArrow.pointerId) _dragArrow = null;
  });
  // 브라우저가 가로 스크롤을 시작하면 pointercancel이 온다 — 발화하지 않는다.
  bar.addEventListener('pointercancel', (e) => {
    _finish(e.pointerId, false);
    if (_dragArrow && e.pointerId === _dragArrow.pointerId) _dragArrow = null;
  });

  // 스크롤 줄의 양 끝 페이드 — 더 밀 수 있는 쪽만 흐리게 해서 "옆에 키가 더
  // 있다"를 알린다. 마우스 휠(태블릿+마우스)은 세로 휠을 가로 이동으로 바꾼다.
  const strip = document.getElementById('keybar-scroll');
  if (strip) {
    const updateEdges = () => {
      const max = strip.scrollWidth - strip.clientWidth;
      strip.classList.toggle('more-left', strip.scrollLeft > 1);
      strip.classList.toggle('more-right', strip.scrollLeft < max - 1);
    };
    strip.addEventListener('scroll', updateEdges, { passive: true });
    strip.addEventListener('wheel', (e) => {
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      strip.scrollLeft += e.deltaY;
      e.preventDefault();
    }, { passive: false });
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(updateEdges).observe(strip);
    updateEdges();
  }

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
  //
  // 포커스 모드(2026-10-06): **보이는 높이**(visualViewport.height)가 FOCUS_MAX_VISIBLE_H 미만이면
  // (가로 폰 + 소프트 키보드: 400px 안팎에서 키보드가 200px 가까이 먹는다) 탭 바·칸 헤더·레일·
  // dock·HUD를 숨기고 터미널이 남은 높이를 다 쓰게 한다(`body.vt-kb-focus`, 20-overlay.css).
  // 키보드가 내려가 높이가 돌아오면 같은 함수가 클래스를 걷어 복귀한다 — 별도 상태가 없다.
  // 기준을 overlap이 아니라 visualViewport.height로 둔 이유: 레이아웃 뷰포트가 같이 줄어드는
  // 브라우저(overlap=0)에서도 "남은 높이가 작다"는 사실은 같기 때문이다.
  const root = document.documentElement;
  let _lastMetrics = '';
  const positionBar = () => {
    const vv = window.visualViewport;
    const overlap = vv
      ? Math.max(0, window.innerHeight - (vv.height + vv.offsetTop))
      : 0;
    document.body.classList.toggle('vt-keyboard-open', overlap > 0);
    const focus = !!vv && vv.height < FOCUS_MAX_VISIBLE_H;
    document.body.classList.toggle('vt-kb-focus', focus);
    if (vv) bar.style.transform = overlap > 0 ? `translateY(${-overlap}px)` : '';
    // hidden(데스크톱)이면 offsetHeight가 0 — 그대로 0을 내보내면 된다.
    const height = bar.hidden ? 0 : bar.offsetHeight;
    const sig = `${height}|${overlap}|${focus ? 1 : 0}`;
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
  // 바 자체의 높이 변화(선택 모드 전환, 폰트 로드, 스킨 전환)도 여백에 반영해야
  // 한다 — 토글 핸들러에만 의존하면 폰트 지연 로드를 놓친다.
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => positionBar()).observe(bar);
  }

  // 여닫기 — 헤더의 키보드 버튼(#keybar-open-btn)으로만 바뀐다. 닫히면 바가
  // display:none이 되어 positionBar()가 높이 0을 내보내고 터미널이 그만큼 넓어진다.
  // 텍스트 선택 중에는 복사·완료 줄이 키바 안에 있으므로 닫혀 있어도 보인다(CSS).
  const openBtn = document.getElementById('keybar-open-btn');
  const applyOpen = (open) => {
    bar.classList.toggle('closed', !open);
    if (openBtn) openBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
    // 닫힌 채 수식키가 켜져 있으면 다음 입력이 보이지 않는 조합으로 나간다.
    if (!open) resetKeybarMods();
    positionBar();
    const activeId = activeSessionId();
    if (activeId) setTimeout(() => fitAndResize(activeId), 60);
  };
  if (openBtn) {
    // pointerdown에서 막아야 터미널 포커스(소프트 키보드)가 유지된다.
    openBtn.addEventListener('pointerdown', (e) => e.preventDefault());
    openBtn.addEventListener('click', () => {
      settingSet('keybar.open', bar.classList.contains('closed'));
    });
  }
  applyOpen(Boolean(settingGet('keybar.open')));
  // set()은 바뀐 키만 알린다 — 이 키가 들어 있을 때만 반응한다(다른 설정
  // 알림을 "닫힘"으로 오독하지 않게, 2026-09-23 keybar.collapsed 사고와 같은 함정).
  settingSubscribe((all) => {
    if (!all || !('keybar.open' in all)) return;
    const open = Boolean(all['keybar.open']);
    if (open === bar.classList.contains('closed')) applyOpen(open);
  });

  positionBar();
}
