// 모바일 터치 스크롤 + 핀치 폰트 크기 조절. F4에서 terminal.js(구 :278-402)에서 분리.
//
// 왜 필요한가: tmux가 `set -g mouse on`(config/vt-tmux.conf)이라 터미널이 마우스
// 트래킹 모드로 들어간다(term.modes.mouseTrackingMode !== 'none'). 그러면 xterm.js는
// 포인터 입력을 뷰포트 스크롤이 아니라 '앱(tmux)'으로 넘긴다. 데스크톱은 휠이 있어서
// tmux가 그걸 copy-mode 스크롤로 번역해주지만, 터치에는 휠이 없다 → 폰에서 화면을
// 위아래로 못 넘기는 상태가 된다.
//
// 그래서 세로 드래그를 감지해 직접 휠 이벤트를 합성한다. 인코딩은 xterm.js가 하던
// 대로 맡기므로 데스크톱 휠과 동작이 정확히 같아진다(tmux copy-mode 진입 → 스크롤).
// 마우스 트래킹이 꺼진 세션(일반 셸)에서는 앱에 보낼 곳이 없으므로 xterm 자체
// 스크롤백을 직접 움직인다.
import { allSessions, activeSessionId } from '../core/store.js';
import { fitAndResize } from './resize.js';
import { FONT_MIN, FONT_MAX } from './xterm-setup.js';

export function wireTouchScroll(id, term, wrapper) {
    // 한 노치로 칠 이동 거리(px). 너무 작으면 손 떨림에 스크롤이 튄다.
    const NOTCH_PX = 18;
    // 이 정도는 움직여야 '스크롤 의도'로 본다. 탭(포커스/키보드)을 막지 않기 위함.
    const SLOP = 8;

    let startX = 0, startY = 0, lastY = 0, acc = 0, engaged = false, multi = false;
    // M6: 핀치(두 손가락) = 폰트 크기 조절. 페이지 자체는 이미
    // <meta viewport ... user-scalable=no>로 브라우저 핀치줌이 꺼져 있어
    // (index.html) 네이티브 확대와 충돌하지 않는다 — 손가락이 2개가 되는
    // 순간부터 스크롤 로직 대신 이 경로를 탄다.
    let pinchStartDist = null, pinchStartSize = null;

    wrapper.addEventListener('touchstart', (e) => {
      multi = e.touches.length > 1;
      if (e.touches.length === 2) {
        pinchStartDist = _touchDist(e.touches);
        pinchStartSize = term.options.fontSize;
        return;
      }
      if (multi) return;
      startX = e.touches[0].clientX;
      startY = lastY = e.touches[0].clientY;
      acc = 0;
      engaged = false;
    }, { passive: true });

    wrapper.addEventListener('touchmove', (e) => {
      if (e.touches.length === 2 && pinchStartDist) {
        e.preventDefault();
        const ratio = _touchDist(e.touches) / pinchStartDist;
        const next = Math.round(pinchStartSize * ratio);
        if (next !== term.options.fontSize) _setGlobalFontSize(next);
        return;
      }
      if (multi || e.touches.length !== 1) return;
      const y = e.touches[0].clientY;
      const x = e.touches[0].clientX;

      if (!engaged) {
        const dy = Math.abs(y - startY);
        const dx = Math.abs(x - startX);
        if (dy < SLOP) return;        // 아직 탭인지 드래그인지 모른다
        if (dx > dy) return;          // 가로 제스처는 앱/선택에 맡긴다
        engaged = true;
      }

      // 여기서부터는 스크롤 제스처다. 앱으로 드래그가 새면 tmux가 선택을 시작한다.
      e.preventDefault();

      acc += y - lastY;
      lastY = y;

      while (Math.abs(acc) >= NOTCH_PX) {
        const dir = acc > 0 ? 1 : -1;   // +1 = 손가락을 아래로 = 위(과거)로 스크롤
        acc -= dir * NOTCH_PX;
        _scrollNotch(term, wrapper, dir);
      }
    }, { passive: false });

    const _endPinch = () => {
      if (pinchStartDist) { pinchStartDist = null; pinchStartSize = null; _hideFontSizeBadge(); }
      engaged = false; multi = false;
    };
    wrapper.addEventListener('touchend', _endPinch, { passive: true });
    wrapper.addEventListener('touchcancel', _endPinch, { passive: true });
}

function _touchDist(touches) {
  const dx = touches[0].clientX - touches[1].clientX;
  const dy = touches[0].clientY - touches[1].clientY;
  return Math.hypot(dx, dy);
}

// 모든 세션에 폰트 크기를 동시 적용 + 다음 세션 기본값으로 저장.
// fitAndResize는 컨테이너 픽셀 크기가 안 바뀌면 재계산을 건너뛰는데(성능 가드),
// 폰트 크기 변경은 컨테이너 크기는 그대로인 채 글자 셀 크기만 바뀌는 경우라
// 그 가드를 반드시 무효화해야 한다(_lastFitW/H 리셋).
function _setGlobalFontSize(px) {
  px = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px)));
  try { localStorage.setItem('vt_font_size', String(px)); } catch (_) {}
  const sessions = allSessions();
  for (const sid in sessions) {
    const s = sessions[sid];
    s.term.options.fontSize = px;
    s._lastFitW = s._lastFitH = null;
  }
  const activeId = activeSessionId();
  if (activeId && sessions[activeId]) fitAndResize(activeId);
  _showFontSizeBadge(px);
}

let _fontBadgeEl = null;
function _showFontSizeBadge(px) {
  if (!_fontBadgeEl) {
    _fontBadgeEl = document.createElement('div');
    _fontBadgeEl.className = 'vt-font-badge';
    document.body.appendChild(_fontBadgeEl);
  }
  _fontBadgeEl.textContent = `${px}px`;
  _fontBadgeEl.hidden = false;
}
function _hideFontSizeBadge() {
  if (_fontBadgeEl) _fontBadgeEl.hidden = true;
}

function _scrollNotch(term, wrapper, dir) {
  const tracking = term.modes && term.modes.mouseTrackingMode
    && term.modes.mouseTrackingMode !== 'none';
  if (tracking) {
    // 앱이 마우스를 잡고 있다 → 휠로 넘겨 데스크톱과 같은 경로를 타게 한다.
    const scr = wrapper.querySelector('.xterm-screen');
    if (!scr) return;
    const r = scr.getBoundingClientRect();
    scr.dispatchEvent(new WheelEvent('wheel', {
      deltaY: -dir * 120,             // 손가락 아래로(dir=+1) → 휠 업
      deltaMode: 0,
      clientX: r.left + Math.min(40, r.width / 2),
      clientY: r.top + Math.min(40, r.height / 2),
      bubbles: true,
      cancelable: true,
    }));
  } else {
    // 앱이 마우스를 안 잡는다(일반 셸) → xterm 자체 스크롤백을 움직인다.
    try { term.scrollLines(-dir * 3); } catch (_) {}
  }
}
