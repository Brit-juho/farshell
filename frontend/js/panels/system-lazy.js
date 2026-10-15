// 시스템(CPU·RAM·네트워크) 패널의 지연 진입점 — panels/ports-lazy.js와 같은 구조·같은 이유.
// 화면 자체(panels/system.js)는 `panels` 청크로 빠지고 이 파일만 상시 로드에 남는다.
import { registerAction } from '../core/dom.js';

// 재진입 가드: 청크가 도착하기 전에 두 번째 호출이 토글로 패널을 닫아버리는 것을 막는다.
let _opening = false;

registerAction('system.open', () => {
  if (_opening) return;
  _opening = true;
  import('./system.js')
    .then((m) => m.showSystem())
    .catch((e) => console.error('[FarShell system]', e))
    .finally(() => { _opening = false; });
});
