// 누른 자리에서 끝나는 성공 표시.
//
// 2.2.1 — 토스트 호출부 138곳 중 절반 가까이가 성공 알림이었고, 그중 일부는
// "화면에서 이미 보이는 결과"를 다시 말하고 있었다. 규칙을 하나 세웠다:
//
//   화면에 증거가 남는 성공은 말하지 않는다.
//   보이지 않는 곳에서 끝난 일만 토스트로 말한다.
//
// 클립보드 복사는 그 사이에 있다 — 결과가 클립보드라 화면에는 아무 증거도
// 없지만, 알림이 필요한 범위는 "누른 버튼" 하나다. 그래서 화면 구석에 카드를
// 띄우는 대신 버튼의 아이콘을 잠깐 체크로 바꾼다. 같은 16px 상자 안에서
// 바뀌므로 레이아웃이 밀리지 않는다.
//
// 아이콘이 없는(글자만 있는) 버튼에는 아무 일도 하지 않는다 — 글자를 바꾸면
// 폭이 변해 줄이 밀린다. 그런 자리는 그대로 showToast를 쓴다.
import { icon } from './icons.js';

const FLASH_MS = 900;

export function okFlash(btn) {
  if (!btn) return;
  const svg = btn.querySelector(':scope > svg');
  if (!svg && !btn._vtFlashRestore) return;

  if (btn._vtFlashRestore) clearTimeout(btn._vtFlashTimer);
  else btn._vtFlashRestore = svg.outerHTML;

  const size = Number(svg?.getAttribute('width')) || 16;
  btn.innerHTML = icon('check', size);
  btn.classList.add('vt-ok-flash');
  btn._vtFlashTimer = setTimeout(() => {
    btn.innerHTML = btn._vtFlashRestore;
    btn._vtFlashRestore = null;
    btn.classList.remove('vt-ok-flash');
  }, FLASH_MS);
}
