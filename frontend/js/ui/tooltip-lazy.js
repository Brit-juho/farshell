// 공용 툴팁(ui/tooltip.js)의 부팅 쪽 — 본체(약 5KB)는 처음 쓸 때 받는다(리디자인 9단계, 초기 페이로드 여유).
//
// 툴팁은 마우스가 [data-tip]에 400ms 머물러야 뜨므로 부팅에 필요 없다. 여기서는 첫 pointerover·focusin
// 하나만 지켜보다가 본체를 받고(포인터 기기는 유휴 시간에 미리 받는다), 그 첫 트리거를 본체에 넘긴다 —
// 첫 툴팁도 놓치지 않는다. 터치는 hover가 없어 본체도 받지 않는다(tooltip.js와 같은 규칙).
let _loading = null;

function load() {
  if (!_loading) {
    _loading = import('./tooltip.js').then((m) => { m.initTooltips(); return m; });
    _loading.catch((e) => console.error('[FarShell tooltip]', e));
  }
  return _loading;
}

export function initTooltips() {
  const first = (e) => {
    if (e.type === 'pointerover' && e.pointerType === 'touch') return;
    const trigger = e.target instanceof Element ? e.target.closest('[data-tip]') : null;
    if (!trigger) return;
    document.removeEventListener('pointerover', first, true);
    document.removeEventListener('focusin', first, true);
    load().then((m) => m.adoptTrigger(trigger, e.type)).catch(() => {});
  };
  document.addEventListener('pointerover', first, true);
  document.addEventListener('focusin', first, true);
  let hover = false;
  try { hover = Boolean(window.matchMedia?.('(hover: hover)').matches); } catch (_) { /* matchMedia 없는 환경 */ }
  if (!hover) return;
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 2000));
  idle(() => { load().catch(() => {}); }, { timeout: 5000 });
}
