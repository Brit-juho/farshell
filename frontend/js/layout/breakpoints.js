// 반응형 4구간 경계값(10-shell-layout.md §3 — compact/regular/wide/xwide).
// dnd.js(pane 상한 게이트)·compact.js(<720px+pointer:coarse 렌더 모드 전환)·
// shell/*(레일·dock 폭 티어)가 반드시 같은 숫자를 봐야 한다 — 두 곳에 따로
// 상수를 박아두면 언젠가 어긋난다(L3 2단계 착수 전 검토에서 승격, N16에서
// 4구간으로 확장).
export const COMPACT_MAX = 720;
export const REGULAR_MAX = 1280;
export const WIDE_MAX = 1600;
