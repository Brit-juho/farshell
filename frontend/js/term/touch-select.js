// 모바일 텍스트 선택 — 꾹 눌러 단어를 고르고, 자체 핸들 두 개를 끌어 범위를
// 넓힌다. F4 계열 분리 파일들과 같은 자리(term/)에 둔다.
//
// 왜 자체 핸들인가(브라우저 네이티브 Range를 안 쓰는 이유) — xterm 5.5의
// 접근성 레이어(AccessibilityManager)는 PTY 출력이 있을 때마다(스피너·tmux
// 상태줄·평범한 다음 줄) 화면에 보이는 행을 **통째로 다시 그린다**(최대 1초
// 디바운스). 그 과정에서 각 행 div의 텍스트 노드가 교체되는데, DOM 규칙상
// 그 순간 그 노드에 걸쳐 있던 브라우저의 네이티브 Range 선택은 양 끝이
// (그 div, 0)으로 붕괴한다 — 한 줄 안의 드래그 선택이었다면 그대로 캐럿이
// 되고, xterm은 `isCollapsed`를 보고 선택을 아예 지워버린다. 실기기 기록
// (Android device, docs/archive/v2.0/TODOS-2026-09-01-backlog.md)의 "Caret까지만
// 되고 Range로 안 넓혀진다"가 바로 이 증상이다 — 사용자가 아무것도 안 해도
// 터미널에 출력이 있기만 하면 선택이 무너지므로, 이 접근으로는 근본적으로
// 못 고친다.
//
// xterm 자신의 선택 모델(`term.select`/`getSelection`/`clearSelection`,
// SelectionService가 별도 렌더러로 하이라이트를 그린다)은 그 재렌더에
// 영향받지 않는다 — 그래서 이 파일은 브라우저 Range를 전혀 만들지 않고
// xterm의 API만 호출한다. 진입/키보드 숨김/복사·완료 바 표시는
// keybar.js의 setTerminalSelectionMode가 이미 갖고 있어 그대로 쓴다 — 이
// 파일이 새로 하는 일은 "무엇으로 그 모드에 들어가는가"(터미널 꾹 누르기)와
// "그 안에서 범위를 어떻게 넓히는가"(핸들 드래그)뿐이다.
import { setTerminalSelectionMode } from './keybar.js';
import { activeSessionId } from '../core/store.js';

// dnd.js(레일 드래그 소스)·Fleet.tsx(세션 행 꾹 눌러 메뉴)와 같은 상수 —
// 사람이 "누르고 있다"고 인지하는 최소 시간대, 탭과 드래그를 가르는 최소
// 이동 거리. 마우스는 대상이 아니다(데스크톱은 마우스 드래그로 이미 선택된다).
const LONGPRESS_MS = 500;
const LONGPRESS_MOVE_PX = 8;

function cellMetrics(wrapper, term) {
  return {
    w: wrapper.clientWidth / term.cols,
    h: wrapper.clientHeight / term.rows,
  };
}

// 클라이언트 좌표 → 뷰포트 상대 (col,row), 0..cols-1 / 0..rows-1로 잘라낸다.
function pointToCell(wrapper, term, clientX, clientY) {
  const rect = wrapper.getBoundingClientRect();
  const { w, h } = cellMetrics(wrapper, term);
  if (!w || !h) return null;
  const col = Math.max(0, Math.min(term.cols - 1, Math.floor((clientX - rect.left) / w)));
  const row = Math.max(0, Math.min(term.rows - 1, Math.floor((clientY - rect.top) / h)));
  return { col, row };
}

// (col,row) → wrapper 기준 픽셀. 핸들을 셀의 아래쪽 모서리에 앉힌다(글자
// 자체를 손잡이가 가리지 않게, iOS/Android 관용구와 같은 배치).
function cellToPoint(wrapper, term, col, row) {
  const { w, h } = cellMetrics(wrapper, term);
  return { x: col * w, y: (row + 1) * h };
}

// 20-overlay.css `.vt-term-handle::after`의 top(6px) + height/2(8px) — 노브
// 원(물방울)의 시각적 중심이 cellToPoint가 돌려주는 앵커점에서 얼마나
// 아래로 그려지는지. 드래그 중 손가락이 이 노브 중심을 누른다고 보고
// 거꾸로 (col,row)를 복원할 때 쓴다 — CSS 값이 바뀌면 여기도 같이 바꿔야
// 손잡이가 자기 칸에서 미끄러진다.
const HANDLE_KNOB_CENTER_PX = 14;

// 핸들 드래그 전용 좌표 변환 — pointToCell과 달리 "손가락이 지금 어느
// 칸 위에 있는가"가 아니라 "이 손가락 위치라면 손잡이가 어느 칸을
// 가리켜야 앵커점(cellToPoint)과 다시 맞는가"를 구한다. 실측(2026-09-23,
// 격리 서버 + 합성 포인터 이벤트): 고정폭 보정값(-12px) 대신 이 역산을
// 쓰지 않으면 셀 높이가 18px대인 흔한 폰트 크기에서 드래그가 옆 줄로
// 새어(한 줄 아래를 가리켜) 선택이 의도보다 한 줄 더 걸렸다.
function pointToCellForHandle(wrapper, term, clientX, clientY) {
  const rect = wrapper.getBoundingClientRect();
  const { w, h } = cellMetrics(wrapper, term);
  if (!w || !h) return null;
  const col = Math.max(0, Math.min(term.cols - 1, Math.floor((clientX - rect.left) / w)));
  const row = Math.max(0, Math.min(term.rows - 1,
    Math.round((clientY - rect.top - HANDLE_KNOB_CENTER_PX) / h) - 1));
  return { col, row };
}

const flatIndex = (cols, col, row) => row * cols + col;
const cellFromFlat = (cols, idx) => ({ col: idx % cols, row: Math.floor(idx / cols) });

// 뷰포트 상대 행 하나의 전체 텍스트(cols 길이, 오른쪽 안 자름) — select()의
// column 좌표계와 맞추려면 실제 글자만큼만(trim) 있는 translateToString(true)가
// 아니라 칸 그대로여야 한다.
function lineTextAt(term, viewportRow) {
  try {
    const absRow = term.buffer.active.viewportY + viewportRow;
    const line = term.buffer.active.getLine(absRow);
    return line ? line.translateToString(false) : '';
  } catch (_) {
    return '';
  }
}

// 클릭 지점을 감싼 "단어"의 [시작, 끝) — 공백 경계 기준(터미널 WORD와 같은
// 결, 기호가 섞인 경로·플래그도 하나로 묶인다). 공백 위를 눌렀으면 그 한
// 칸만 고른다 — 핸들이 항상 유효한 범위를 갖고 시작해야 한다.
function wordBoundsAt(lineText, col) {
  const isSpace = (ch) => ch === undefined || ch === ' ';
  if (isSpace(lineText[col])) return { start: col, end: col + 1 };
  let start = col;
  while (start > 0 && !isSpace(lineText[start - 1])) start--;
  let end = col;
  while (end < lineText.length && !isSpace(lineText[end])) end++;
  return { start, end };
}

export function wireTouchSelect(id, term, wrapper) {
  const handles = document.createElement('div');
  handles.className = 'vt-term-select-handles';
  handles.hidden = true;
  const startHandle = document.createElement('div');
  startHandle.className = 'vt-term-handle start';
  startHandle.setAttribute('aria-hidden', 'true');
  const endHandle = document.createElement('div');
  endHandle.className = 'vt-term-handle end';
  endHandle.setAttribute('aria-hidden', 'true');
  handles.append(startHandle, endHandle);
  wrapper.appendChild(handles);

  let active = false;
  // 뷰포트 상대 flat index(row*cols+col) — 두 핸들 각각의 "자기 자리"다.
  // 드래그 중에는 항상 min/max로 정렬해 term.select에 넘긴다(다른 쪽을
  // 넘어가면 두 손잡이가 화면에서 서로 스쳐 지나간다 — 정식 OS 선택 UI처럼
  // 역할이 바뀌지는 않지만, 범위 자체는 항상 올바르다).
  let anchorA = 0, anchorB = 0;

  const cols = () => term.cols;

  function applySelection() {
    // term.select(column,row,length)에는 항상 정렬된 값을 넘긴다(length가
    // 음수면 안 된다) — 하지만 핸들 "자리"(아래 positionHandles)는 일부러
    // 정렬하지 않는다. 시작 손잡이가 끝 손잡이를 넘어가도 손가락 밑
    // 손잡이가 그대로 따라와야 한다(손잡이가 서로 자리를 바꾸며 튀면 훨씬
    // 어색하다) — 선택되는 글자는 어차피 min/max로 항상 올바르다.
    const lo = Math.min(anchorA, anchorB);
    const hi = Math.max(anchorA, anchorB);
    const { col, row } = cellFromFlat(cols(), lo);
    try { term.select(col, row, hi - lo); } catch (_) {}
    positionHandles();
  }

  function positionHandles() {
    const a = cellFromFlat(cols(), anchorA);
    const b = cellFromFlat(cols(), anchorB);
    const ap = cellToPoint(wrapper, term, a.col, a.row);
    const bp = cellToPoint(wrapper, term, b.col, b.row);
    startHandle.style.transform = `translate(${ap.x}px, ${ap.y}px)`;
    endHandle.style.transform = `translate(${bp.x}px, ${bp.y}px)`;
  }

  function enter(row, col) {
    const text = lineTextAt(term, row);
    const { start, end } = wordBoundsAt(text, col);
    anchorA = flatIndex(cols(), start, row);
    anchorB = flatIndex(cols(), end, row);
    active = true;
    setTerminalSelectionMode(true);
    applySelection();
    handles.hidden = false;
  }

  function exit() {
    active = false;
    handles.hidden = true;
  }

  // 다른 경로(완료·복사 성공·세션 전환)로 선택 모드가 꺼지면 핸들도 같이
  // 접는다 — 이 리스너는 세션마다(=wireTouchSelect 호출마다) 하나씩 생기지만
  // 각자 자기 active 플래그만 보고 판단하므로 문제없다.
  document.addEventListener('vt:terminal-selection', (e) => {
    if (!active) return;
    if (!e.detail?.active) exit();
  });

  // "모두 선택"(keybar.js) 같은 외부 변경 — 이 파일이 모르는 새 범위이므로
  // xterm에게 지금 선택이 뭔지 되물어(getSelectionPosition, 버퍼 절대좌표)
  // 뷰포트 상대로 바꿔 다시 앉힌다. 내 드래그가 만든 변경은 이 경로를 안 타고
  // 위 applySelection이 이미 정확한 로컬 좌표로 처리한다 — 왕복이 필요 없다.
  document.addEventListener('vt:terminal-selection-changed', () => {
    if (!active || id !== activeSessionId()) return;
    try {
      const pos = term.getSelectionPosition?.();
      if (!pos) return;
      const viewportY = term.buffer.active.viewportY;
      anchorA = flatIndex(cols(), pos.start.x, pos.start.y - viewportY);
      anchorB = flatIndex(cols(), pos.end.x, pos.end.y - viewportY);
      positionHandles();
    } catch (_) { /* 무시 — 다음 상호작용이 바로잡는다 */ }
  });

  // ── 꾹 눌러 진입 ──────────────────────────────────────────────────────
  let pressTimer = null;
  let pressStart = null; // {x,y}
  const clearPressTimer = () => { if (pressTimer != null) { clearTimeout(pressTimer); pressTimer = null; } };

  wrapper.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' || active) return;
    pressStart = { x: e.clientX, y: e.clientY };
    clearPressTimer();
    pressTimer = setTimeout(() => {
      pressTimer = null;
      const cell = pointToCell(wrapper, term, e.clientX, e.clientY);
      if (cell) enter(cell.row, cell.col);
    }, LONGPRESS_MS);
  });
  wrapper.addEventListener('pointermove', (e) => {
    if (pressTimer == null || !pressStart) return;
    if (Math.abs(e.clientX - pressStart.x) > LONGPRESS_MOVE_PX
      || Math.abs(e.clientY - pressStart.y) > LONGPRESS_MOVE_PX) clearPressTimer();
  });
  wrapper.addEventListener('pointerup', clearPressTimer);
  wrapper.addEventListener('pointercancel', clearPressTimer);
  // iOS의 자체 롱프레스 콜아웃/선택 제안이 우리 것과 겹쳐 뜨는 걸 한 번 더
  // 막는다 — CSS(user-select:none)가 1차 방어선이고, 이건 그래도 새는
  // 브라우저의 네이티브 컨텍스트 메뉴 자체를 끊는 2차 방어선이다.
  wrapper.addEventListener('contextmenu', (e) => { if (active) e.preventDefault(); });

  // ── 핸들 드래그 ───────────────────────────────────────────────────────
  function wireHandleDrag(handle, isStart) {
    let dragging = false;
    handle.addEventListener('pointerdown', (e) => {
      if (!active) return;
      e.preventDefault();
      e.stopPropagation(); // wrapper의 꾹 눌러 진입 타이머가 같이 돌지 않게 한다.
      dragging = true;
      try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    });
    handle.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      e.preventDefault();
      const cell = pointToCellForHandle(wrapper, term, e.clientX, e.clientY);
      if (!cell) return;
      const idx = flatIndex(cols(), cell.col, cell.row);
      if (isStart) anchorA = idx; else anchorB = idx;
      applySelection();
    });
    const endDrag = (e) => {
      if (!dragging) return;
      dragging = false;
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
    };
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
  }
  wireHandleDrag(startHandle, true);
  wireHandleDrag(endHandle, false);
}
