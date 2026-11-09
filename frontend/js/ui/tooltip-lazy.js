// 공용 툴팁(ui/tooltip.js)의 부팅 쪽 — 본체(약 5KB)는 처음 쓸 때 받는다(리디자인 9단계, 초기 페이로드 여유).
//
// 툴팁은 마우스가 [data-tip]에 400ms 머물러야 뜨므로 부팅에 필요 없다. 여기서는 첫 pointerover·focusin
// 하나만 지켜보다가 본체를 받고(포인터 기기는 유휴 시간에 미리 받는다), 그 첫 트리거를 본체에 넘긴다 —
// 첫 툴팁도 놓치지 않는다. 터치는 hover가 없어 본체도 받지 않는다(tooltip.js와 같은 규칙).
//
// 받기에 실패하면(재배포 뒤 옛 청크 404, 일시적 네트워크 끊김) 실패한 약속을 붙들고 있지 않는다 —
// _loading을 비우고 첫 트리거 감시를 다시 건다. 실패 직후의 트리거는 건너뛰고, 기다리는 시간은
// 실패할 때마다 두 배로 늘린다(1s → 최대 60s). 성공하면 초기화한다.
let _loading = null;
let _retryAt = 0;
let _backoff = 0;
const BACKOFF_MIN = 1000;
const BACKOFF_MAX = 60000;

function load() {
  if (!_loading) {
    const p = import('./tooltip.js').then((m) => { m.initTooltips(); _backoff = 0; _retryAt = 0; return m; });
    _loading = p;
    p.catch((e) => {
      console.error('[FarShell tooltip]', e);
      if (_loading === p) _loading = null;
      _backoff = _backoff ? Math.min(_backoff * 2, BACKOFF_MAX) : BACKOFF_MIN;
      _retryAt = Date.now() + _backoff;
      watch();
    });
  }
  return _loading;
}

function first(e) {
  if (e.type === 'pointerover' && e.pointerType === 'touch') return;
  const trigger = e.target instanceof Element ? e.target.closest('[data-tip]') : null;
  if (!trigger) return;
  if (Date.now() < _retryAt) return; // 실패 직후 — 감시는 유지한 채 다음 트리거를 기다린다
  unwatch();
  load().then((m) => m.adoptTrigger(trigger, e.type)).catch(() => {});
}

// 같은 처리기를 두 번 붙여도 addEventListener가 하나로 친다(유휴 미리 받기 실패 때 감시가 이미 붙어 있다).
function watch() {
  document.addEventListener('pointerover', first, true);
  document.addEventListener('focusin', first, true);
}

function unwatch() {
  document.removeEventListener('pointerover', first, true);
  document.removeEventListener('focusin', first, true);
}

export function initTooltips() {
  watch();
  let hover = false;
  try { hover = Boolean(window.matchMedia?.('(hover: hover)').matches); } catch (_) { /* matchMedia 없는 환경 */ }
  if (!hover) return;
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 2000));
  idle(() => { load().catch(() => {}); }, { timeout: 5000 });
}
