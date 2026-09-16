// 포트 대시보드(P3)의 지연 진입점 — panels/files-lazy.js와 같은 구조·같은 이유.
// 화면 자체(panels/ports/ports.js, 400줄)는 `panels` 청크로 빠지고 이 파일만
// 상시 로드(2.1.6부터 core.js)에 남는다. 파일 탭을 추가하면서 app.js가 300KiB 상한에 닿았고, 포트도
// "열 때 받으면 되는" 화면이라 같은 처리를 했다.
import { registerAction } from '../core/dom.js';

// 재진입 가드: dock 효과가 청크 도착 전에 한 번 더 돌면 showPorts()가 두 번
// 불려 두 번째가 토글로 닫아버린다(scm.show·files.show가 겪은 그 버그).
let _opening = false;

registerAction('ports.show', () => {
  if (_opening) return;
  _opening = true;
  import('./ports/ports.js')
    .then((m) => m.showPorts())
    .catch((e) => console.error('[FarShell ports]', e))
    .finally(() => { _opening = false; });
});
