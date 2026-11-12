// 폰 시트(대화상자) 키 처리 — Esc로 닫고, Tab은 시트 안에서만 돈다(aria-modal인데 Tab이 배경 목록으로 새면
// 그 뒤의 Esc가 시트에 닿지 않아 닫을 수 없게 된다). panels/panel.js의 포커스 트랩과 같은 규칙이지만 shell 지연 청크라
// 그 모듈(core 상태를 import)을 쓰지 않고 여기 둔다.
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export function sheetFocusables(el: HTMLElement): HTMLElement[] {
  return Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((x) => !x.closest('[hidden], [inert]'));
}

/** 시트의 keydown. 처리했으면 true. */
export function onSheetKeyDown(e: KeyboardEvent, el: HTMLElement, close: () => void): boolean {
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return true; }
  if (e.key !== 'Tab') return false;
  const items = sheetFocusables(el);
  if (items.length === 0) { e.preventDefault(); return true; }
  const first = items[0], last = items[items.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (!active || !el.contains(active)) { e.preventDefault(); first.focus(); return true; }
  if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); return true; }
  if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); return true; }
  return false;
}
