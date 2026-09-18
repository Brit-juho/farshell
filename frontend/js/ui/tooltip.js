// 공용 툴팁 — 네이티브 `title` 속성을 대체한다.
//
// 왜 바꾸나. title은 "공짜 툴팁"처럼 보이지만 UI로서는 네 가지가 고장나 있다:
//   1. 지연이 브라우저 고정값(약 1초)이다. 접힌 36px dock처럼 **아이콘만 있는**
//      자리에서 1초는 "이게 뭐지"를 해결해주지 못하는 시간이다.
//   2. 테마를 못 입힌다. 6개 스킨을 만들어놨는데 툴팁만 OS 회색 상자다.
//      게다가 OS/언어에 따라 글꼴·모서리·그림자가 제각각이라 화면이 깨져 보인다.
//      (사용자 보고: "버튼에 마우스 올리면 나오는 UI가 다르다")
//   3. 터치에서는 아무 일도 안 일어난다. 폰에서 아이콘만 있는 버튼은 영원히
//      이름이 없다 — 그쪽은 aria-label이 대신한다(아래 「접근성」).
//   4. 줄바꿈·부가 정보를 못 넣는다. "포트"만 말하고 "몇 개 열려 있는지"는 못 말한다.
//
// 왜 CSS ::after가 아니라 JS 싱글톤인가. 툴팁이 필요한 자리(dock 탭 행·레일)는
// 하나같이 `overflow:hidden` 컨테이너 안이다(80-dock.css의 .vt-dock-tabs). CSS
// 툴팁은 그 상자 안에 그려지므로 **잘린다**. body에 붙은 position:fixed 요소
// 하나만이 어느 컨테이너에도 안 갇힌다. 요소가 하나뿐이라 DOM 비용도 0에 가깝다.
//
// 접근성. 이 모듈은 **이름을 만들어주지 않는다**. data-tip은 보조 설명이고,
// 아이콘 전용 버튼의 이름은 여전히 aria-label의 몫이다. title을 data-tip으로
// 옮길 때 그 요소에 텍스트도 aria-label도 없으면 aria-label을 같이 붙여야 한다.
// 떠 있는 동안에는 트리거에 aria-describedby="vt-tip"을 걸어 스크린리더가 본문을
// 읽게 한다(이미 aria-describedby가 있으면 건드리지 않는다).
//
// 사용법:
//   <button aria-label="포트" data-tip="포트" data-tip-sub="14개 열림" data-tip-side="left">
//   data-tip-side: top(기본) | bottom | left | right. 안 들어가면 반대편으로
//   뒤집고, 그래도 안 되면 들어가는 변을 찾는다.

const OPEN_DELAY = 400;   // 처음 머물렀을 때. 스쳐 지나가는 커서에는 안 뜬다.
const WARM_MS = 300;      // 방금 닫힌 직후 옆 버튼으로 옮기면 지연 없이 뜬다.
const GAP = 8;            // 트리거와의 간격
const EDGE = 8;           // 뷰포트 가장자리 여백

let tipEl = null;
let current = null;       // 지금 툴팁의 주인인 트리거 요소
let openTimer = 0;
let lastHideAt = 0;
let wired = false;
// 떠 있는 동안 트리거의 data-tip*이 바뀌면 따라 그린다. dock 배지 수치는
// 15초마다 갱신되므로, 안 보고 있으면 열어둔 툴팁만 옛 수치를 말한다.
// 호출부가 "툴팁을 새로 그려달라"고 기억할 필요가 없도록 여기서 감시한다.
const liveObserver = typeof MutationObserver === 'function'
  ? new MutationObserver(() => { if (current) render(current); })
  : null;

function ensureEl() {
  if (tipEl && tipEl.isConnected) return tipEl;
  tipEl = document.createElement('div');
  tipEl.id = 'vt-tip';
  tipEl.className = 'vt-tip';
  tipEl.setAttribute('role', 'tooltip');
  tipEl.setAttribute('aria-hidden', 'true');
  document.body.appendChild(tipEl);
  return tipEl;
}

// 트리거 기준으로 자리를 잡는다. 측정이 필요하므로 내용은 이미 들어가 있어야
// 하고, 요소는 visibility:hidden(= 레이아웃은 있음)이어야 한다 — display:none
// 이면 offsetWidth가 0이라 매번 왼쪽 위에 붙는다.
function place(trigger, side) {
  const el = ensureEl();
  const r = trigger.getBoundingClientRect();
  const w = el.offsetWidth, h = el.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  const fits = {
    top: r.top - GAP - h >= EDGE,
    bottom: r.bottom + GAP + h <= vh - EDGE,
    left: r.left - GAP - w >= EDGE,
    right: r.right + GAP + w <= vw - EDGE,
  };
  const opposite = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' };
  let s = side;
  if (!fits[s]) s = fits[opposite[s]] ? opposite[s] : (['bottom', 'top', 'left', 'right'].find((k) => fits[k]) || side);

  let x, y;
  if (s === 'top' || s === 'bottom') {
    x = r.left + r.width / 2 - w / 2;
    y = s === 'top' ? r.top - GAP - h : r.bottom + GAP;
  } else {
    x = s === 'left' ? r.left - GAP - w : r.right + GAP;
    y = r.top + r.height / 2 - h / 2;
  }
  // 가장자리로 밀리면 화면 안으로 당긴다(뒤집기로 해결 안 되는 축).
  x = Math.max(EDGE, Math.min(x, vw - EDGE - w));
  y = Math.max(EDGE, Math.min(y, vh - EDGE - h));

  // 화살표를 안 쓰는 대신 **트리거 쪽에서 자라게** 해서 출처를 남긴다. 화살표는
  // 6개 스킨의 배경·테두리를 각각 따라가야 해서 항상 한 스킨에서 어긋난다.
  el.style.transformOrigin = `${Math.max(0, Math.min(r.left + r.width / 2 - x, w))}px ${Math.max(0, Math.min(r.top + r.height / 2 - y, h))}px`;
  el.style.left = `${Math.round(x)}px`;
  el.style.top = `${Math.round(y)}px`;
}

// 내용만 다시 채우고 자리를 다시 잡는다(열림 상태는 건드리지 않는다).
function render(trigger) {
  const text = trigger.getAttribute('data-tip');
  if (!text) { hideTip(); return; }
  const el = ensureEl();
  el.textContent = '';
  const main = document.createElement('span');
  main.className = 'vt-tip-main';
  main.textContent = text;
  el.appendChild(main);
  const sub = trigger.getAttribute('data-tip-sub');
  if (sub) {
    const s = document.createElement('span');
    s.className = 'vt-tip-sub';
    s.textContent = sub;
    el.appendChild(s);
  }
  place(trigger, trigger.getAttribute('data-tip-side') || 'top');
}

function show(trigger) {
  openTimer = 0;
  if (!trigger.isConnected || !trigger.getAttribute('data-tip')) return;
  current = trigger;
  render(trigger);
  const el = ensureEl();
  el.classList.add('open');
  el.setAttribute('aria-hidden', 'false');
  if (!trigger.hasAttribute('aria-describedby')) {
    trigger.setAttribute('aria-describedby', 'vt-tip');
    trigger._vtTipAria = true;
  }
  if (liveObserver) liveObserver.observe(trigger, { attributes: true, attributeFilter: ['data-tip', 'data-tip-sub'] });
}

export function hideTip() {
  if (openTimer) { clearTimeout(openTimer); openTimer = 0; }
  if (!current) return;
  if (current._vtTipAria) {
    current.removeAttribute('aria-describedby');
    delete current._vtTipAria;
  }
  current = null;
  if (liveObserver) liveObserver.disconnect();
  lastHideAt = Date.now();
  if (tipEl) {
    tipEl.classList.remove('open');
    tipEl.setAttribute('aria-hidden', 'true');
  }
}

function schedule(trigger) {
  if (current === trigger) return;
  if (openTimer) { clearTimeout(openTimer); openTimer = 0; }
  const warm = Date.now() - lastHideAt < WARM_MS;
  if (current) hideTip();
  if (warm) show(trigger);
  else openTimer = setTimeout(() => show(trigger), OPEN_DELAY);
}

export function initTooltips() {
  if (wired) return;
  wired = true;

  document.addEventListener('pointerover', (e) => {
    // 터치는 hover가 없다 — 탭 한 번에 툴팁이 떴다가 그대로 남는다. 그쪽
    // 기기에서 아이콘의 이름은 aria-label이 맡는다.
    if (e.pointerType === 'touch') return;
    const t = e.target instanceof Element ? e.target.closest('[data-tip]') : null;
    if (!t) { if (current) hideTip(); else if (openTimer) { clearTimeout(openTimer); openTimer = 0; } return; }
    if (t.getAttribute('aria-disabled') === 'true' || t.disabled) return;
    schedule(t);
  });

  document.addEventListener('pointerout', (e) => {
    if (e.pointerType === 'touch') return;
    const t = e.target instanceof Element ? e.target.closest('[data-tip]') : null;
    if (!t) return;
    // 트리거 **안쪽**(아이콘 span 등)으로 옮겨가는 건 나가는 게 아니다.
    const to = e.relatedTarget;
    if (to instanceof Node && t.contains(to)) return;
    hideTip();
  });

  // 키보드 — 클릭으로 들어온 포커스에는 안 뜬다(:focus-visible). 마우스로
  // 누른 직후 툴팁이 남아 있으면 "눌렀다"는 피드백을 가린다.
  document.addEventListener('focusin', (e) => {
    const t = e.target instanceof Element ? e.target.closest('[data-tip]') : null;
    if (!t) return;
    let visible = true;
    try { visible = t.matches(':focus-visible'); } catch (_) {}
    if (visible) show(t);
  });
  document.addEventListener('focusout', () => hideTip());

  // 눌렀다 = 사용자가 이미 안다. 붙잡고 있을 이유가 없다.
  document.addEventListener('pointerdown', () => hideTip(), true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideTip(); });
  // 스크롤·리사이즈는 트리거를 옮겨버린다. 다시 재려고 애쓰기보다 닫는 게
  // 맞다 — 커서가 그대로면 pointerover 없이도 다음 이동에서 곧 다시 뜬다.
  window.addEventListener('scroll', () => hideTip(), { capture: true, passive: true });
  window.addEventListener('resize', () => hideTip());
  window.addEventListener('blur', () => hideTip());
}
