// 셀 배수 스냅 — pane 크기를 터미널 한 칸의 배수로 떨어뜨린다(iTerm2와 같은 성질).
//
// 왜 필요한가. xterm은 `cols × cellWidth`만 그린다. pane 상자가 그 배수가 아니면
// 남는 px은 **아무것도 안 그려진 띠**로 마지막 글자와 분할선 사이에 남는다.
// 분할선이 1px을 먹기 때문에 반반으로 나눠도 각 pane이 575.5px 같은 소수가 되고,
// 그 소수는 거의 항상 배수가 아니다(실측: 컨테이너 1152 → pane 575.5 → 71칸
// 568px → 7.5px이 빈 띠).
//
// ⚠ 이것과 **유령 스크롤바 15px은 다른 문제**다. 그쪽이 훨씬 컸고 원인도 달랐다
// (CSS로 스크롤바를 숨기면 xterm의 `offsetWidth - scrollAreaWidth || 15`가 0을
// 못 쓰고 15로 떨어진다 — term/xterm-setup.js의 주석 참고). 그걸 먼저 고치고
// 나서야 여기 남은 잔여가 "진짜 양자화"가 된다.
//
// 어디까지 없앨 수 있나. 전부는 아니다. 분할선 1px 때문에 두 pane이 **동시에**
// 배수일 수는 없다(배수 + 1 + 배수 ≠ 배수 합). 그래서 규칙을 이렇게 잡는다:
//
//   분할의 **첫째 칸을 배수로 못박고, 둘째 칸이 나머지를 먹는다.**
//
// 그러면 분할선 양쪽 중 첫째 칸 쪽은 빈틈이 0이고, 나머지는 둘째 칸의 **바깥쪽
// 끝**으로 밀려난다 — 대개 창 가장자리다. 경계마다 띠가 생기던 것이 트리 전체에
// 한 군데로 줄어든다.
//
// 세로는 pane 헤더(24px)를 빼고 잰다 — 칸이 놓이는 건 헤더 아래이므로
// `헤더 + k × cellHeight`가 되어야 배수다.

/**
 * px를 셀 배수로 내림한다. chrome은 셀이 안 놓이는 고정 높이(pane 헤더 등).
 * 스냅할 수 없으면(셀 크기를 모르거나 한 칸도 안 들어가면) null — 호출부가
 * 예전처럼 비율 flex로 떨어지게 한다. **절대 0을 돌려주지 않는다**: 0이면
 * pane이 사라진다.
 */
export function snapPx(px, cell, chrome = 0) {
  if (!Number.isFinite(px) || !Number.isFinite(cell) || cell <= 0) return null;
  const inner = px - chrome;
  if (inner < cell) return null;
  return Math.floor(inner / cell) * cell + chrome;
}

/**
 * 살아 있는 아무 터미널에서나 셀 크기를 읽는다 — 글꼴·글자 크기가 전역 설정이라
 * 한 창의 모든 터미널이 같은 셀을 쓴다(세션마다 다르면 이 가정이 깨지는데, 그때는
 * 어느 하나를 기준으로 삼는 게 아니라 아예 스냅을 안 하는 편이 낫다 — 지금은 그런
 * 경로가 없다).
 *
 * `_core._renderService`는 내부 API다. 없으면 null을 돌려주고, 호출부는 스냅 없이
 * 예전 그대로 동작한다.
 */
export function cellSizeFrom(sessions) {
  for (const s of sessions) {
    try {
      const d = s && s.term && s.term._core && s.term._core._renderService;
      const c = d && d.dimensions && d.dimensions.css && d.dimensions.css.cell;
      if (c && c.width > 0 && c.height > 0) return { w: c.width, h: c.height };
    } catch (_) { /* 내부 구조가 바뀌면 조용히 포기 */ }
  }
  return null;
}
