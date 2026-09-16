// 프롬프트 스니펫(L3)의 지연 진입점 — panels/ports-lazy.js와 같은 구조.
// 본체(snippets.js)는 `panels` 청크로 빠지고 이 파일만 상시 로드에 남는다.
import { registerAction } from './core/dom.js';

// 재진입 가드 — ports-lazy.js의 _opening과 같은 이유(청크 도착 전 두 번째
// 호출이 토글로 닫아버린다).
let _opening = false;

registerAction('snippets.show', () => {
  if (_opening) return;
  _opening = true;
  import('./snippets.js')
    .then((m) => m.showSnippets())
    .catch((e) => console.error('[FarShell snippets]', e))
    .finally(() => { _opening = false; });
});
