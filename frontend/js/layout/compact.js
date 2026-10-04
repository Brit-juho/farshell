// L3 2단계 — compact(<720px) 렌더 모드. leaf가 2개 이상이면
// 활성 leaf 하나만 화면 전체로 그리고, 나머지는 표면 레이어(layout/surface.js)가
// 화면 밖으로 숨긴다(렌더링은 layout/panes.js가 한다 — 이 파일은 "지금이
// compact인가"와 "leaf를 어떤 순서로 나열할까"만 안다). 트리 자체는 건드리지
// 않는다 — 착수 전 설계 리뷰 원칙 1 그대로: compact는 "다르게 그리는 렌더
// 모드"이지 "다른 트리"가 아니다.
//
// 좌우 스와이프로 leaf 간 이동 — §8이 정한 기존 제스처 설계(가장자리 20px
// 시작 + 터미널 영역 한정)를 그대로 흡수한다. 방향 확정 임계값(6px)은 이미
// 이 코드베이스가 쓰는 기법(ports.js의 `_wireSwipe`, U5 스와이프)과 동일하게
// 맞췄다 — 다른 값을 쓰면 기기마다 스와이프 손맛이 갈린다.
import { _isCoarsePointer } from '../core/env.js';
import { COMPACT_MAX } from './breakpoints.js';
import { getActivePaneId, getTabsWithTrees } from './store.js';
import { getSession } from '../core/store.js';
import { revealSession } from '../term/session.js';

export function isCompactMode() {
  return window.innerWidth < COMPACT_MAX;
}

// depth-first(a→b) leaf 나열. dir 값(row/col)은 여기서 안 본다 — 설계 원칙 1:
// compact에서는 dir이 "스와이프 순서 힌트"로만 재해석되고, 그 힌트가 바로 이
// 나열 순서 자체다(분할해서 생긴 새 leaf는 항상 형제 바로 다음/직전에 온다).
export function flattenLeaves(node, out = []) {
  if (node.t === 'leaf') { out.push(node); return out; }
  flattenLeaves(node.a, out);
  flattenLeaves(node.b, out);
  return out;
}

// ── 세션 이동 순서 — 저장되는 레이아웃이 **유일한 출처**다 ─────────────────────
// 순서 = 뷰 탭 순서 → 뷰 안 칸 순서(왼쪽 위부터, flattenLeaves). 빈 칸·뷰어/패널
// 칸·스토어에 없는 세션(unreachable 원격 칸)은 건너뛴다. 별도 순서 배열이 없으므로
// 새로고침 뒤에도 같고, 데스크톱에서 뷰 탭을 드래그하면 모바일 순서가 따라온다.
// 스와이프·키맵(다음/이전 탭)·모바일 세션 시트·팔레트가 전부 이 목록 하나를 읽는다.
function _isOpenLeaf(leaf) {
  return !!leaf.session && leaf.kind !== 'viewer' && leaf.kind !== 'panel' && !!getSession(leaf.session);
}

/** 열린 세션 목록 — `[{id, tabId, paneId}]`, 화면 순서. */
export function openSessionOrder() {
  const out = [];
  for (const t of getTabsWithTrees()) {
    for (const leaf of flattenLeaves(t.tree)) {
      if (_isOpenLeaf(leaf)) out.push({ id: leaf.session, tabId: t.id, paneId: leaf.id });
    }
  }
  return out;
}

export function openSessionIds() {
  return openSessionOrder().map((e) => e.id);
}

/** 현재 칸에서 delta(+1 다음, -1 이전)만큼 간 세션 — 끝에서는 순환. 활성 칸이
 *  비었거나 뷰어여도 "그 칸 자리"를 기준으로 가장 가까운 세션을 고른다. 갈 곳이
 *  없으면(열린 세션이 현재 하나뿐) null. 순수 선택 로직이라 단위 테스트 대상. */
export function nextOpenSession(delta) {
  const flat = [];
  for (const t of getTabsWithTrees()) {
    for (const leaf of flattenLeaves(t.tree)) flat.push({ leaf, tabId: t.id });
  }
  const activePane = getActivePaneId();
  const cur = flat.findIndex((e) => e.leaf.id === activePane);
  const n = flat.length;
  const dir = delta < 0 ? -1 : 1;
  for (let k = 1; k <= n; k++) {
    // 현재 칸을 못 찾으면(방어) 처음/끝에서 시작한다.
    const i = (((cur < 0 ? (dir > 0 ? -1 : n) : cur) + dir * k) % n + n) % n;
    const e = flat[i];
    if (!_isOpenLeaf(e.leaf)) continue;
    if (i === cur) return null;
    return { id: e.leaf.session, tabId: e.tabId, paneId: e.leaf.id };
  }
  return null;
}

/** 다음/이전 세션으로 **이동만** 한다 — 그 세션의 뷰로 전환하고 그 칸에 포커스.
 *  트리는 건드리지 않는다(switchTo는 활성 칸에 "배정"하므로 다른 뷰의 세션을
 *  끌어오고 지금 칸 세션을 밀어냈다). 끝에서 순환. */
export function stepSession(delta) {
  const next = nextOpenSession(delta);
  if (next) revealSession(next.id);
}

// 스와이프 한 번의 동작 — 키맵 다음/이전 탭(모바일)과 같은 함수.
export function stepView(delta) {
  stepSession(delta);
}

const EDGE_PX = 20;    // §8: 화면 가장자리 20px에서 시작한 것만 인정
const DECIDE_PX = 6;   // 방향 확정 임계값 — ports.js _wireSwipe와 동일
const COMMIT_PX = 60;  // 실제로 넘길지 판정하는 이동 거리(손 떨림 방지)

// containerEl(=#terminal-container, "터미널 영역 한정")에 한 번만 건다 —
// 자식 엘리먼트(키바·상단바 등은 형제/조상이 다르므로 애초에 이 리스너까지
// 버블링되지 않는다. 세로 스크롤(term/touch.js)·핀치(M6)는 각자 독립적으로
// 방향/터치 개수를 판정하므로 서로 밟지 않는다 — 자세한 설계는 30-layout-shell.md
// L3 2단계 검증 기록 참고.
export function wireCompactSwipe(containerEl) {
  let startX = 0, startY = 0, tracking = false, decided = false, horizontal = false;

  containerEl.addEventListener('touchstart', (e) => {
    if (!isCompactMode() || !_isCoarsePointer() || e.touches.length !== 1) { tracking = false; return; }
    const x = e.touches[0].clientX;
    if (x > EDGE_PX && x < window.innerWidth - EDGE_PX) { tracking = false; return; }
    startX = x;
    startY = e.touches[0].clientY;
    tracking = true; decided = false; horizontal = false;
  }, { passive: true });

  containerEl.addEventListener('touchmove', (e) => {
    if (!tracking || e.touches.length !== 1) return;
    const x = e.touches[0].clientX, y = e.touches[0].clientY;
    const dx = x - startX, dy = y - startY;
    if (!decided) {
      if (Math.abs(dx) < DECIDE_PX && Math.abs(dy) < DECIDE_PX) return;
      horizontal = Math.abs(dx) > Math.abs(dy);
      decided = true;
      if (!horizontal) { tracking = false; return; } // 세로 스크롤에 양보
    }
    e.preventDefault();
  }, { passive: false });

  const finish = (e) => {
    if (!tracking || !decided || !horizontal) { tracking = false; return; }
    tracking = false;
    const touch = e.changedTouches && e.changedTouches[0];
    if (!touch) return;
    const dx = touch.clientX - startX;
    if (Math.abs(dx) < COMMIT_PX) return;
    // 왼쪽으로 밀면 다음 세션, 오른쪽으로 밀면 이전 세션 — 탭/페이지 스와이프의
    // 통상적인 방향 관례.
    stepView(dx < 0 ? 1 : -1);
  };
  containerEl.addEventListener('touchend', finish);
  containerEl.addEventListener('touchcancel', () => { tracking = false; });
}
