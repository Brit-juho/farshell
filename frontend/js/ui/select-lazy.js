// <select> 드롭다운(ui/select.js, 약 12KB)의 지연 진입점 — panels/system-lazy.js와 같은 구조·같은 이유.
// index.html에는 <select>가 없고 전부 화면을 열 때 생기므로, 본체는 `select` 청크로 빼고
// 이 스텁만 상시 로드에 남는다. 하는 일은 둘뿐이다:
//   1. <select>가 문서에 처음 나타나는 순간 본체를 받아 autoEnhanceSelects()를 넘긴다
//      (본체가 문서 전체를 다시 훑으므로 기다리는 사이 생긴 select도 빠지지 않는다).
//   2. 부팅 뒤 유휴 시간에 미리 받아, 첫 화면을 열 때 기본 select가 비치는 일을 없앤다.
// 청크 로드가 실패해도 기본 <select>가 그대로 동작한다(값·이벤트는 원래부터 네이티브가 정본).

const defaultLoad = () => import('./select.js');

export function watchSelects({ load = defaultLoad, root = document, idle = true } = {}) {
  let pending = null;
  let observer = null;
  const handOver = () => {
    if (!pending) {
      pending = Promise.resolve()
        .then(load)
        .then((m) => { observer?.disconnect(); observer = null; m.autoEnhanceSelects(root); })
        .catch((e) => { pending = null; console.error('[FarShell select]', e); });
    }
    return pending;
  };
  const hasSelect = (node) => node.nodeType === 1 && (node.tagName === 'SELECT' || !!node.querySelector?.('select'));

  if (root.querySelector('select')) handOver();
  if (!pending && typeof MutationObserver === 'function') {
    observer = new MutationObserver((records) => {
      for (const r of records) for (const n of r.addedNodes) if (hasSelect(n)) { handOver(); return; }
    });
    observer.observe(root.body || root.documentElement, { childList: true, subtree: true });
  }
  if (idle) {
    const prefetch = () => { void handOver(); };
    if (typeof requestIdleCallback === 'function') requestIdleCallback(prefetch, { timeout: 4000 });
    else setTimeout(prefetch, 2000);
  }
  return handOver;
}
