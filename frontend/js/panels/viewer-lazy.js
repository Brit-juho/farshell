// ADR-26/N35 — 코드 뷰어(panels/viewer/*, 6파일·1268줄)를 지연 청크로 뺀다.
// **이 파일만 앱 본체(app.js)에 남는다** — 나머지 viewer/*.js는 전부
// vite.config.js의 manualChunks가 `shell.js`(고정 이름 지연 청크)로 보낸다.
//
// 왜 shell.js 하나만 열면 되는가: viewer/*.js 6개는 서로 순환 import로
// 촘촘히 얽혀 있어(shell↔tree, shell↔git, tree↔file 등) 그중 아무거나
// 하나를 로드해도 번들러가 나머지를 전부 같은 청크에 함께 넣는다 —
// `import('./viewer/shell.js')` 하나면 tree/git/diff/file/state가 전부
// 딸려 온다. 여기서 tree.js를 별도로 한 번 더 import하는 건 shell.js가
// 직접 export하지 않는 함수(_loadRecent/_selectFile, quickopen.js가 씀)를
// 얻기 위해서일 뿐, 네트워크 요청이 추가되는 게 아니다(이미 로드된 같은
// 청크의 모듈 네임스페이스를 그대로 돌려받는다).
//
// quickopen.js·picker.js 등 소비처는 **정적 import를 쓰면 안 된다** — 정적
// import는 그 모듈을 참조하는 쪽의 청크에 도로 끌어들인다(N34 커밋에서
// core/*가 shell.js에 복제됐던 것과 같은 함정). 이 파일이 내보내는
// loadViewer()를 통해서만 접근한다.
import { registerAction } from '../core/dom.js';

let _loading = null;

export function loadViewer() {
  if (!_loading) {
    // 순차로 연다(Promise.all 동시 실행 아님) — shell.js와 tree.js가 서로를
    // import하는 순환 관계라, 두 dynamic import()가 동시에 시작되면 두 로더가
    // 같은 모듈을 동시에 링크하려는 경쟁이 생긴다(테스트 하네스의 vm.Module
    // 링커에서 "request for '../../core/api.js' is not in cache"로 실제
    // 재현됨 — 실브라우저 스펙 구현은 이런 경쟁을 허용하지만 굳이 기대지
    // 않는다). shell.js를 먼저 완전히 로드·링크·평가하면 그 안에서 이미
    // tree.js까지 함께 끝나 있으므로, 뒤이은 tree.js import는 네트워크
    // 요청도 재평가도 없이 같은 네임스페이스를 즉시 돌려받는다.
    _loading = import('./viewer/shell.js')
      .then(async (shell) => ({ ...shell, ...(await import('./viewer/tree.js')) }));
  }
  return _loading;
}

// shell.js 자신도 모듈 최상단에서 `registerAction('viewer.show', ...)`을
// 부르지만, 그 줄은 청크가 실제로 로드된 뒤에야 실행된다 — 그 전까지 사용자가
// 코드뷰어를 열 방법 자체가 없다. 그래서 부팅 시 이 자리에 "누르면 청크부터
// 받아온다"는 임시 등록을 심어 둔다. 청크 로드가 끝나면 shell.js의 진짜
// registerAction이 같은 이름으로 덮어써서 이후 호출은 이 래퍼를 거치지
// 않는다(registry.set은 마지막 등록이 이긴다).
registerAction('viewer.show', () => { loadViewer().then((v) => v.showViewer()); });
