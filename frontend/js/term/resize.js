// xterm fit + PTY 리사이즈 통보. F4에서 terminal.js(구 :1020-1052)에서 분리.
import { getSession } from '../core/store.js';

function sendResize(ws, term, s) {
  if (ws.readyState !== WebSocket.OPEN) return;
  // 같은 크기를 다시 보내면 PTY가 SIGWINCH를 받아 Claude 같은 TUI가 화면 전체를
  // 다시 그린다(대량 출력). fitAndResize가 resize·focus·탭전환마다 호출되므로,
  // 실제로 cols/rows가 바뀐 경우에만 보내 불필요한 전체 재도색을 없앤다.
  if (s && s._lastCols === term.cols && s._lastRows === term.rows) return;
  if (s) { s._lastCols = term.cols; s._lastRows = term.rows; }
  ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
}

// fit(xterm 칸 수 재계산) + PTY에 크기 통보를 항상 함께 한다. 예전엔 곳곳에서
// fit만 하고 sendResize를 빠뜨려(switchTo 등) xterm 칸 수와 PTY 칸 수가 어긋났고,
// 그 결과 Claude Code 같은 TUI가 박스/입력줄을 엉뚱한 행에 그리고 줄이 겹쳐 보였다.
// 외부(panels/panel.js·viewer.js)에서도 bare identifier로 호출하므로 window 브리지 필요.
export function fitAndResize(id) {
  const s = getSession(id);
  if (!s || !s.wrapper) return;
  // 숨김 탭(display:none)은 컨테이너가 0-height라 fit이 rows를 1로 깨뜨린다 —
  // 보이는 탭에서만 측정한다. switchTo가 표시 직후 다시 호출해 준다.
  if (s.wrapper.style.display === 'none') return;
  // ⚠ fitAddon.fit()은 호출될 때마다 무조건 dimension을 재계산하고, xterm.js 내부적으로
  // (this._terminal.rows/cols가 계산값과 조금이라도 다르면) _renderService.clear()를
  // 실행한다 — 문자 아틀라스(glyph 캐시) 폐기 + 재생성으로, xterm.js 자체 이슈(#955)에서도
  // "비용이 크다"고 명시된 작업이다. 아래 _lastCols/_lastRows 가드는 서버로 보내는 WS
  // 메시지만 막을 뿐 이 내부 fit() 호출 자체는 막지 못해서, 탭 전환/포커스마다(피사체 크기가
  // 실제로는 그대로인데) 서브픽셀 반올림 오차만으로도 매번 아틀라스가 갈아엎어질 수 있다.
  // 컨테이너의 실제 픽셀 크기가 안 바뀌었으면 fit() 자체를 건너뛴다.
  const cw = s.wrapper.clientWidth, ch = s.wrapper.clientHeight;
  if (s._lastFitW === cw && s._lastFitH === ch) return;
  s._lastFitW = cw; s._lastFitH = ch;
  try { s.fitAddon.fit(); } catch (_) { return; }
  const w = s.ws;
  if (w && w.readyState === WebSocket.OPEN) sendResize(w, s.term, s);
}

window.fitAndResize = fitAndResize;
