// tmux 세션 상태 표시 — D5: 예전엔 🟢(웹에 열림)/🖥️(데스크톱 attach)/💤(잠듦)
// 이모지였다. 플랫폼마다 렌더가 갈리고(라이트 스킨에서 특히 튐) 색맹 사용자에게
// 색만으로 구분되는 정보이기도 해서, 색 점 + 이미 옆에 붙던 한글 상태 텍스트로
// 대체한다(텍스트는 원래도 있었다 — 이모지가 중복 정보였다).
// tmux-panel.js(⋯ 메뉴 목록)와 boot.js(온보딩 목록)가 완전히 같은 판정 로직을
// 복붙해 갖고 있었다 — 여기 하나로 합친다.
export function tmuxStatus(s) {
  const openInWeb = !!s.web_session_id;
  const attached = s.attached > 0;
  return {
    color: openInWeb ? 'var(--ok)' : attached ? 'var(--info)' : 'var(--sub)',
    text: openInWeb ? '웹에 열림' : attached ? '데스크톱 attach' : '잠듦',
  };
}

// 점 하나만 있는 <span>을 만들어 돌려준다 — 호출부가 라벨 텍스트 앞에 붙인다.
export function tmuxStatusDot(s) {
  const { color } = tmuxStatus(s);
  const dot = document.createElement('span');
  dot.className = 'vt-dot';
  dot.style.background = color;
  dot.setAttribute('aria-hidden', 'true');
  return dot;
}
