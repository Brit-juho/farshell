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
  // 배치 안 된 세션(대기실 #vt-term-stage에 있는 것)은 측정하지 않는다. 그쪽
  // 상자는 **컨테이너 크기**라, 재보면 pane 크기가 아닌 값으로 fit해서 PTY에
  // 엉뚱한 SIGWINCH가 간다. 배치되면 surface.js의 refit 게이트가 실측해서
  // 다시 부른다.
  //
  // ⚠ 2026-09-18 이전에는 이 검사가 `wrapper.style.visibility === 'hidden'`
  // 이었다. 표면 레이어 시절엔 숨김을 **인라인 스타일**로 표현했기 때문인데,
  // 지금은 대기실의 CSS가 숨기므로 그 인라인 값이 비어 있다 — 그대로 뒀으면
  // 검사가 영영 통과해 대기 중 터미널까지 fit됐을 것이다. 배치는 이제
  // "부모가 누구인가"로 표현되므로 검사도 그걸 본다.
  if (!s.wrapper.closest('.vt-pane-body')) return;
  // ⚠ fitAddon.fit()은 호출될 때마다 무조건 dimension을 재계산하고, xterm.js 내부적으로
  // (this._terminal.rows/cols가 계산값과 조금이라도 다르면) _renderService.clear()를
  // 실행한다 — 문자 아틀라스(glyph 캐시) 폐기 + 재생성으로, xterm.js 자체 이슈(#955)에서도
  // "비용이 크다"고 명시된 작업이다. 아래 _lastCols/_lastRows 가드는 서버로 보내는 WS
  // 메시지만 막을 뿐 이 내부 fit() 호출 자체는 막지 못해서, 탭 전환/포커스마다(피사체 크기가
  // 실제로는 그대로인데) 서브픽셀 반올림 오차만으로도 매번 아틀라스가 갈아엎어질 수 있다.
  // 컨테이너의 실제 픽셀 크기가 안 바뀌었으면 fit() 자체를 건너뛴다.
  const cw = s.wrapper.clientWidth, ch = s.wrapper.clientHeight;
  // L2: display:none은 위에서 이미 걸렀지만, 그것만으로는 부족하다 — flex/트랜지션
  // 레이아웃이 아직 자리를 잡기 전(예: 방금 보여진 프레임, 분할 pane 애니메이션 중)엔
  // display:block이면서도 0×0으로 측정될 수 있다. 이전 값과 다르다는 이유만으로
  // fit()을 부르면 xterm이 0(또는 음수) 칸으로 계산해 PTY에 그 크기를 그대로
  // 통보할 위험이 있다 — 다음 실측 프레임을 기다린다(호출자가 rAF로 재시도).
  if (cw <= 0 || ch <= 0) return;
  if (s._lastFitW === cw && s._lastFitH === ch) return;
  s._lastFitW = cw; s._lastFitH = ch;
  try { s.fitAddon.fit(); } catch (_) { return; }
  const w = s.ws;
  if (w && w.readyState === WebSocket.OPEN) sendResize(w, s.term, s);
}

window.fitAndResize = fitAndResize;
