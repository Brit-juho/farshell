// 코드 뷰어 잔여 상태 — N35 §6에서 모달 뷰어(shell.js)와 폴더 트리(tree.js)가
// 사라지면서, 여기 있던 표시 모드·패널 폭·아이콘도 함께 없어졌다. 남은 것은
// dock 소스컨트롤 탭이 "어느 저장소를 볼지" 정할 때 쓰는 최소 상태와, 세 렌더러가
// 공유하는 메시지 헬퍼뿐이다.
//
// root/cwd는 지금 아무도 쓰지 않는 경로에서도 채워질 수 있어(향후 팔레트 파일
// 모드, 60 §3) 자리만 유지한다 — scm.js가 이 값이 비면 /api/fs/roots로 폴백한다.
export let _viewerState = {
  root: null,
  cwd: null,
};

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
