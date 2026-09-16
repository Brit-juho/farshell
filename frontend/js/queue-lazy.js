// 프롬프트 큐(P4)의 지연 진입점 — panels/ports-lazy.js와 같은 구조·같은 이유.
// 화면(queue.js, 11KB)은 `panels` 청크로 빠지고 이 파일만 상시 로드에 남는다.
//
// ⚠ 이 주석의 옛 내용(공유 모듈을 import하면 안 된다)은 **2.1.6에서 해소됐다.**
// vite.config.js가 지연 대상만 열거하고 나머지를 전부 `core`로 못박으므로,
// 지연 청크가 core/store·layout/store·term/session을 평범하게 정적 import해도
// 승격이 일어나지 않는다. 그래서 settings.js·snippets.js도 이제 지연 청크다.
import { registerAction } from './core/dom.js';

// 재진입 가드 — 청크 도착 전에 액션이 한 번 더 불리면 두 번째가 토글로 닫는다.
let _opening = false;

registerAction('queue.show', () => {
  if (_opening) return;
  _opening = true;
  import('./queue.js')
    .then((m) => m.showQueue())
    .catch((e) => console.error('[FarShell queue]', e))
    .finally(() => { _opening = false; });
});
