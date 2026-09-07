// A5 — 상태 스토어(agent/state.js)를 화면에 칠하는 유일한 곳.
//
// 소비처가 넷(탭 · pane 헤더 · rail 세션 목록 · 파비콘)인데, 각자 WS 메시지를
// 따로 해석하면 A1 이전과 똑같은 문제(소비자마다 규칙이 갈라짐)로 돌아간다.
// 그래서 **읽기는 state.js 하나, 칠하기는 이 파일 하나**로 묶는다. rail처럼
// 자기 렌더 타이밍이 따로 있는 소비자는 `onStatusChange`로 다시 그린다.
//
// 상태 → 어느 tmux 세션인가는 서버가 정한다(A2의 3단 해석). 프런트는 세션
// 이름만 키로 쓴다 — 웹 세션 id는 재attach마다 바뀌어 키가 될 수 없다.
import { allSessions, getSession, subscribe } from '../core/store.js';
import { onLayoutChange } from '../layout/store.js';
import { getStatus, onStatusChange, applyStatusDot, ackLocal } from './state.js';

// 탭·pane 헤더에서는 **idle이면 dot을 아예 안 그린다.** 세션이 열 개면 회색 점
// 열 개가 상시로 붙어 있게 되는데, 그건 정보가 아니라 노이즈다("아무 일도
// 없음"은 기본값이라 표시할 필요가 없다). 반대로 rail 세션 목록은 여러 줄이
// 세로로 늘어선 "상태 열"이라 idle도 자리를 지키는 편이 읽기 쉬워서,
// 거기서는 rail.js가 항상 dot을 붙인다.
function _dotOrNone(container, status) {
  if (!status || status === 'idle') {
    container.querySelector(':scope > .status-dot')?.remove();
    return;
  }
  applyStatusDot(container, status);
}

function _tmuxName(sessionId) {
  const s = getSession(sessionId);
  return s && (s.tmuxName || s.tmux_name) || null;
}

// 탭 — 이름 앞에 dot 하나. 탭 **순서는 건드리지 않는다**(사용자가 드래그로
// 정한 의도다. 정렬은 rail·팔레트만 한다 — 40-agent-state.md 2-6).
function paintTabs() {
  document.querySelectorAll('#tabs .tab').forEach((tab) => {
    const status = getStatus(_tmuxName(tab.dataset.sessionId));
    _dotOrNone(tab, status);
    // 기존 .working/.done 클래스도 유지한다 — CSS(legacy)가 이미 쓰고 있고,
    // 상태 dot이 안 보이는 좁은 화면에서도 탭 자체가 강조돼야 한다.
    tab.classList.toggle('working', status === 'working');
    tab.classList.toggle('done', status === 'done');
    tab.classList.toggle('waiting', status === 'waiting');
  });
}

// pane 헤더 — 이름 옆 dot. 분할 화면에서 "어느 칸이 나를 기다리는지"가
// 한눈에 보여야 한다.
function paintPanes() {
  document.querySelectorAll('.vt-pane').forEach((pane) => {
    const nameEl = pane.querySelector('.vt-pane-name');
    if (!nameEl) return;
    // pane에 배정된 세션은 wrapper의 소유자다 — pane id로 트리를 다시 뒤지는
    // 대신, 이미 DOM에 있는 wrapper의 id에서 역으로 찾는다(렌더러와 결합하지
    // 않으려는 것: panes.js가 어떻게 그리든 이 함수는 그대로 동작한다).
    const wrapper = pane.querySelector('.vt-pane-body > [id^="term-"]');
    const sid = wrapper ? wrapper.id.replace(/^term-/, '') : null;
    const head = pane.querySelector('.vt-pane-head');
    if (!head) return;
    if (!sid) {
      head.querySelector(':scope > .status-dot')?.remove();
      return;
    }
    _dotOrNone(head, getStatus(_tmuxName(sid)));
  });
}

// 파비콘 — 탭 하나에 하나뿐이라 "가장 급한 세션"을 보여준다.
function paintFavicon() {
  if (!window.VTFavicon) return;
  let best = 'idle';
  const rank = { waiting: 0, done: 1, working: 2, idle: 3 };
  for (const id of Object.keys(allSessions())) {
    const st = getStatus(_tmuxName(id));
    if ((rank[st] ?? 9) < (rank[best] ?? 9)) best = st;
  }
  window.VTFavicon.set(best);
}

// L8 §8-b — 앱 아이콘 배지(Badging API). PWA라 iOS Live Activity는 못 쓰므로,
// **홈 화면 아이콘에 `waiting` 세션 수**를 띄우는 것이 "앱을 안 보고 있을 때
// 내 개입이 필요한지"를 알 수 있는 유일한 수단이다.
//
// `waiting`만 센다 — `working`은 기다리면 알아서 끝나고, `done`은 이미 푸시가
// 나간다. 배지는 "지금 나를 부르고 있다"만 의미해야 한다. 숫자가 상시로 떠
// 있으면 사용자는 곧 무시하게 된다.
function paintAppBadge() {
  if (!navigator.setAppBadge) return;   // 미지원(iOS 웹앱 밖, 일부 브라우저)
  let waiting = 0;
  for (const id of Object.keys(allSessions())) {
    if (getStatus(_tmuxName(id)) === 'waiting') waiting++;
  }
  // 실패는 조용히 무시한다 — 권한/컨텍스트 문제로 던질 수 있고(설치 안 된
  // PWA 등), 그게 화면 갱신을 막을 이유는 없다.
  try {
    if (waiting > 0) navigator.setAppBadge(waiting);
    else navigator.clearAppBadge?.();
  } catch (_) { /* 무시 */ }
}

export function paintAll() {
  paintTabs();
  paintPanes();
  paintFavicon();
  paintAppBadge();
}

// 상태가 바뀔 때마다 다시 칠한다. rAF로 묶어 한 프레임에 한 번만 —
// 스냅샷 하나에 여러 세션이 바뀌어도 DOM 작업은 1회다.
let _pending = false;
function schedulePaint() {
  if (_pending) return;
  _pending = true;
  requestAnimationFrame(() => { _pending = false; paintAll(); });
}
onStatusChange(schedulePaint);

// 탭/pane이 새로 생기면 그 DOM에도 지금 상태를 칠해야 한다 — 상태는 안 바뀌고
// 화면만 바뀐 경우라 onStatusChange로는 안 걸린다.
subscribe(schedulePaint);       // 세션 추가·삭제·전환
// 실브라우저 검증에서 발견: 분할/닫기/DnD는 **레이아웃 트리만** 바꾸므로
// core/store.js 구독으로는 안 걸린다. 그래서 방금 만들어진 pane 헤더에는 dot이
// 안 붙고, 세션이 빠져나간 pane에는 옛 dot이 그대로 남아 있었다(빈 pane에
// 'waiting'이 붙어 있는 상태를 실제로 재현). 레이아웃 변경도 함께 구독한다.
onLayoutChange(schedulePaint);

// 탭을 클릭하면 그 세션의 done 표시는 "확인했다"는 뜻이다(기존 T6 규칙과 동일 —
// 그때는 클래스를 직접 지웠고, 이제 상태 스토어를 내린다).
document.addEventListener('click', (e) => {
  const tab = e.target.closest && e.target.closest('#tabs .tab');
  if (!tab) return;
  const name = _tmuxName(tab.dataset.sessionId);
  if (name) ackLocal(name);
}, true);
