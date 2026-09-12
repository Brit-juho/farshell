// 50-files-share.md §4 — dock 파일 탭의 **지연 진입점**. 이 파일만 app.js에
// 남고 실제 화면(panels/files/files.js)은 `panels` 청크로 빠진다
// (viewer-lazy.js와 같은 구조·같은 이유). 그냥 정적 import 했더니 app.js가
// 300KiB 상한을 넘어 build-output.test.js가 실제로 떨어졌다 — 파일 탭은
// 자주 여는 화면이 아니므로 열 때 받는 게 맞다.
import { registerAction } from '../core/dom.js';

// 재진입 가드: dock은 "활성 탭의 패널이 DOM에 없으면 연다"는 effect로 돌아서,
// 청크가 도착하기 전에 effect가 한 번 더 돌면 showFiles()가 두 번 불리고
// 두 번째 호출이 **토글로 닫아버린다**(scm.show가 겪은 그 버그와 동일).
let _opening = false;

registerAction('files.show', () => {
  if (_opening) return;
  _opening = true;
  import('./files/files.js')
    .then((m) => m.showFiles())
    .catch((e) => console.error('[FarShell files]', e))
    .finally(() => { _opening = false; });
});
