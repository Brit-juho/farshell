// 프롬프트 큐(P4)의 지연 진입점 — panels/ports-lazy.js와 같은 구조·같은 이유.
// 화면(queue.js, 11KB)은 `panels` 청크로 빠지고 이 파일만 app.js에 남는다.
//
// 이 모듈이 지연으로 빠질 수 있는 조건: core/store.js·layout/store.js·
// term/session.js를 **import하지 않는다**. 그 셋 중 하나라도 끌면 Rollup이
// 싱글톤을 청크 쪽으로 옮겨 app.js가 청크를 정적 import하게 되고, 지연이
// 지연이 아니게 된다(실측 — settings.js·snippets.js가 그래서 대상에서 빠졌다).
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
