// 720px 이상의 맨 위는 제목 막대(#vt-titlebar, shell/TitleBar.tsx) 하나다. #topbar는 뷰 탭 줄(#vt-wtabs)을 잃어
// 남는 게 없으므로 그 자리에서 숨기고(CSS), 거기 있던 것 중 넓은 화면에서도 필요한 것 — macOS 신호등(.traffic) ·
// 키바 버튼(#keybar-open-btn) · 윈도 캡션(.caption) — 을 제목 막대의 두 자리(#vt-mb-lead / #vt-mb-tools)로 옮긴다.
// ⌘K 버튼(#palette-toggle)은 옮기지 않는다 — 제목 막대 가운데의 팔레트 입력이 같은 일을 한다(리디자인 2단계).
//
// **복제하지 않고 옮긴다.** id가 하나여야 하고(keybar-ui.js·테스트가 id로 찾는다), 이미 걸린 리스너가 그대로
// 살아 있어야 한다. compact로 돌아오면 원래 자리(#topbar 안, 원래 순서)로 되돌린다.
// 두 자리 자체는 TitleBar.tsx가 마운트될 때 막대 왼쪽·오른쪽 끝으로 옮겨 가지만 id는 같아서 이 파일은 상관없다.
const WIDE = '(min-width:720px)';

function place(wide) {
  const topbar = document.getElementById('topbar');
  const lead = document.getElementById('vt-mb-lead');
  const tools = document.getElementById('vt-mb-tools');
  if (!topbar || !lead || !tools) return;
  const traffic = document.querySelector('.traffic');
  const keybar = document.getElementById('keybar-open-btn');
  const palette = document.getElementById('palette-toggle');
  const caption = document.querySelector('.caption');
  const snippets = document.getElementById('snippets-open-btn');
  if (wide) {
    if (traffic) lead.append(traffic);
    for (const el of [keybar, caption]) if (el) tools.append(el);
  } else {
    // 원래 순서: 신호등 · … · 키바 · 스니펫 · ⌘K · 캡션
    if (traffic) topbar.prepend(traffic);
    if (snippets) {
      if (keybar) snippets.before(keybar);
      if (palette && palette.previousElementSibling !== snippets) snippets.after(palette);
    } else {
      for (const el of [keybar, palette]) if (el) topbar.append(el);
    }
    if (caption) topbar.append(caption);
  }
}

export function initMenubarSlots() {
  const mq = window.matchMedia ? window.matchMedia(WIDE) : null;
  place(!!mq && mq.matches);
  if (mq) mq.addEventListener('change', (e) => place(e.matches));
}

initMenubarSlots();
