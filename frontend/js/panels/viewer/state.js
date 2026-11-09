// 코드 뷰어 잔여 상태. 파일 트리는 Solid(`frontend/js/files/`, 단계 5)로 옮겨 가 자기 상태를 따로 갖는다 —
// 여기는 트리 패널(tree.js)이 마지막으로 연 루트만 적어 둔다. 단계 9에서 옛 트리가 쓰던 `expanded`와 행
// 아이콘 상수(_ICON_INSERT·_ICON_CHEVRON)를 지웠다.
//
// root/cwd는 dock 소스컨트롤 탭이 "어느 저장소를 볼지" 정할 때도 폴백으로 쓴다
// — scm.js가 이 값이 비면 /api/fs/roots로 대신 폴백한다. 오버레이(overlay.js)는 제목의 상대 경로에 쓴다.
export let _viewerState = {
  root: null,
  cwd: null,
};

// 파일을 외부 공유 링크로 내보낸다 — 스냅샷 복사 + 링크 발급 다이얼로그(panels/files/files.js).
// 같은 청크의 다른 모듈이라 정적 import 대신 지연 import로 부른다(파일 탭 지연 진입점과 같은 이유).
export function _shareFile(path) {
  if (!path) return;
  import('../files/files.js')
    .then((m) => m.shareFsPath(path))
    .catch((e) => console.error('[FarShell share]', e));
}

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
