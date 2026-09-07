// 반응형 3구간 경계값(20-design-system.md §2-1과 동일). dnd.js(pane 상한
// 게이트)와 compact.js(<720px+pointer:coarse 렌더 모드 전환)가 반드시 같은
// 숫자를 봐야 한다 — 두 곳에 따로 상수를 박아두면 언젠가 어긋난다(L3 2단계
// 착수 전 검토에서, dnd.js가 이미 갖고 있던 로컬 상수를 여기로 승격했다).
export const COMPACT_MAX = 720;
export const REGULAR_MAX = 1024;
