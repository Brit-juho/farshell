// L3 2단계 — compact(<720px + pointer:coarse) 렌더 모드. leaf가 2개 이상이면
// 활성 leaf 하나만 화면 전체로 그리고, 나머지는 #terminal-pool로 보낸다
// (렌더링은 layout/panes.js가 한다 — 이 파일은 "지금이 compact인가"와
// "leaf를 어떤 순서로 나열할까"만 안다). 트리 자체는 건드리지 않는다 — 착수
// 전 설계 리뷰 원칙 1 그대로: compact는 "다르게 그리는 렌더 모드"이지
// "다른 트리"가 아니다.
//
// 좌우 스와이프로 leaf 간 이동 — §8이 정한 기존 제스처 설계(가장자리 20px
// 시작 + 터미널 영역 한정)를 그대로 흡수한다. 방향 확정 임계값(6px)은 이미
// 이 코드베이스가 쓰는 기법(ports.js의 `_wireSwipe`, U5 스와이프)과 동일하게
// 맞췄다 — 다른 값을 쓰면 기기마다 스와이프 손맛이 갈린다.
import { _isCoarsePointer } from '../core/env.js';
import { COMPACT_MAX } from './breakpoints.js';
import { getTree, getActivePaneId, setActivePane } from './store.js';

export function isCompactMode() {
  return _isCoarsePointer() && window.innerWidth < COMPACT_MAX;
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

// 활성 leaf를 스와이프 순서상 delta칸 옮긴다(+1=다음, -1=이전, 양끝은 순환).
// 순수 단계 이동 로직만 따로 export해 스와이프 제스처 없이도(단위 테스트,
// 나중에 생길 수 있는 키보드 단축키 등) 재사용할 수 있게 한다.
export function stepActivePane(delta) {
  const leaves = flattenLeaves(getTree());
  if (leaves.length < 2) return;
  const idx = leaves.findIndex((l) => l.id === getActivePaneId());
  const next = leaves[(((idx < 0 ? 0 : idx) + delta) % leaves.length + leaves.length) % leaves.length];
  setActivePane(next.id);
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
    if (!isCompactMode() || e.touches.length !== 1) { tracking = false; return; }
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
    // 왼쪽으로 밀면 다음 leaf, 오른쪽으로 밀면 이전 leaf — 탭/페이지 스와이프의
    // 통상적인 방향 관례.
    stepActivePane(dx < 0 ? 1 : -1);
  };
  containerEl.addEventListener('touchend', finish);
  containerEl.addEventListener('touchcancel', () => { tracking = false; });
}
