// 「로그아웃」「캐시 지우기」의 지연 진입점 — 본체는 shell 청크(shell/app-session.ts)에 있고 여기는 액션 등록만 남는다.
//
// 액션 등록이 여기(상시 로드)에 있어야 하는 이유: FarShell 메뉴(TitleBar)와 명령 팔레트가 같은 액션 id를 `getAction`으로
// 찾는다. shell 청크가 core/dom.js를 직접 import하면 레지스트리가 복제돼 둘이 서로 다른 객체를 본다(Hud.tsx 머리말).
import { registerAction } from './dom.js';
import { vtFetch } from './api.js';
import { confirmDialog } from './dialog-lazy.js';
import { showToast } from '../ui/toast.js';

// 재진입 가드 — 청크가 도착하기 전·확인 대화상자가 떠 있는 동안 또 눌러도 한 번만 돈다.
let _busy = false;

function run(name) {
  if (_busy) return;
  _busy = true;
  import('../shell/app-session.ts')
    .then((m) => m[name]({ vtFetch, confirm: confirmDialog, toast: showToast }))
    .catch((e) => console.error('[FarShell app-session]', e))
    .finally(() => { _busy = false; });
}

registerAction('session.logout', () => run('logout'));
registerAction('app.clear-cache', () => run('clearAppCache'));
