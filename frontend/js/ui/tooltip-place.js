// 툴팁 자리 계산 — DOM을 모르는 순수 함수라 단위 시험이 가능하다(tests/tooltip-place.test.js).
//
// 입력은 전부 뷰포트 좌표다. `bounds`는 트리거가 속한 패널(사이드바·도크)의 안쪽 사각형이고, 있으면
// 툴팁은 그 안에 머문다 — 옆 패널(터미널·다른 도구)을 덮고 테두리가 안 보이던 문제(2026-11 화면 점검 6-2)의
// 원인은 사이드바 가장자리의 행이 `right`로 열려 패널 경계를 넘던 것이었다. 반대편으로 뒤집고, 거기도
// 안 들어가면 위·아래로 열되 가로는 패널 안으로 당긴다. 패널보다 툴팁이 넓으면 패널 제한은 버리고
// 뷰포트 안에만 둔다.
const OPPOSITE = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' };

/**
 * @param {{rect:{left:number,right:number,top:number,bottom:number,width:number,height:number},
 *          w:number,h:number,side:string,vw:number,vh:number,gap?:number,edge?:number,
 *          bounds?:{left:number,right:number,top:number,bottom:number}|null}} a
 * @returns {{x:number,y:number,side:string}}
 */
export function computePlacement(a) {
  const { rect: r, w, h, vw, vh } = a;
  const gap = a.gap ?? 8;
  const edge = a.edge ?? 8;
  let b = a.bounds || null;
  // 패널 안쪽 경계는 뷰포트 안으로 자른다. 툴팁이 패널 폭보다 넓으면 패널 제한을 풀어 뷰포트로 돌아간다.
  if (b) {
    b = { left: Math.max(b.left, edge), right: Math.min(b.right, vw - edge), top: Math.max(b.top, edge), bottom: Math.min(b.bottom, vh - edge) };
    if (b.right - b.left < w) b = null;
  }
  const lo = { x: b ? b.left : edge, y: edge };
  const hi = { x: b ? b.right : vw - edge, y: vh - edge };
  const fits = {
    top: r.top - gap - h >= edge,
    bottom: r.bottom + gap + h <= vh - edge,
    left: r.left - gap - w >= lo.x,
    right: r.right + gap + w <= hi.x,
  };
  let s = a.side;
  if (!fits[s]) s = fits[OPPOSITE[s]] ? OPPOSITE[s] : (['bottom', 'top', 'left', 'right'].find((k) => fits[k]) || s);
  let x;
  let y;
  if (s === 'top' || s === 'bottom') {
    x = r.left + r.width / 2 - w / 2;
    y = s === 'top' ? r.top - gap - h : r.bottom + gap;
  } else {
    x = s === 'left' ? r.left - gap - w : r.right + gap;
    y = r.top + r.height / 2 - h / 2;
  }
  x = Math.max(lo.x, Math.min(x, hi.x - w));
  y = Math.max(lo.y, Math.min(y, hi.y - h));
  return { x, y, side: s };
}
