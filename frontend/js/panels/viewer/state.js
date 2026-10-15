// 코드 뷰어 잔여 상태 — N35 §6에서 모달 뷰어(shell.js)와 폴더 트리(tree.js)가
// 사라지면서, 여기 있던 표시 모드·패널 폭·아이콘도 함께 없어졌다. 이후 dock
// "파일" 탭(구 tree.js를 되살린 것, N35 번복 — 2026-09-22)이 root/cwd/expanded를
// 다시 채우기 시작했다 — 이 파일이 남겨둔 자리를 그대로 썼다(주석 [6]행이
// 예고했던 "향후" 소비자).
//
// root/cwd는 dock 소스컨트롤 탭이 "어느 저장소를 볼지" 정할 때도 폴백으로 쓴다
// — scm.js가 이 값이 비면 /api/fs/roots로 대신 폴백한다.
export let _viewerState = {
  root: null,
  cwd: null,
  expanded: new Set(),   // 펼쳐진 디렉토리 절대경로 — 접었다 다시 펼 때 재요청 판단에 씀
};

// L2: 파일 트리 행의 "터미널에 경로 삽입" 버튼.
// icons.js의 'terminal' 토큰과 path가 같지만(우연이 아니라 같은 화살표 의미),
// 그쪽은 24px 레일/dock 아이콘 전제라 12px 인라인 버튼에는 이 상수를 쓴다.
export const _ICON_INSERT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
// 트리 행 셰브런 — icons.js의 'chevron-down'과 기본 방향이 다르다(그쪽은
// 아래를 기본으로 CSS 회전, 이건 오른쪽을 기본으로 두고 펼치면 90도 돌려
// 아래를 향한다 — 30-viewer.css의 .vt-vw-chev 회전 규칙이 이 방향을 전제로
// 짜여 있어 icons.js 쪽으로 통일하면 회전 각도까지 다시 계산해야 한다).
export const _ICON_CHEVRON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 6 6 6-6 6"/></svg>';

// 정적 안내 메시지용 — textContent 만 쓰므로 이스케이프가 필요 없다.
// 줄바꿈은 <br> 엘리먼트로 표현한다(문자열 조립 없이).
export function _setMsg(container, className, lines) {
  container.innerHTML = '';
  const div = document.createElement('div');
  div.className = className;
  lines.forEach((line, i) => {
    if (i > 0) div.appendChild(document.createElement('br'));
    div.appendChild(document.createTextNode(line));
  });
  container.appendChild(div);
}
