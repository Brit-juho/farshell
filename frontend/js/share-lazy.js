// 에이전트 세션 공유 — 상시 로드되는 얇은 진입점(queue-lazy.js 와 같은 구조).
// 대화상자는 `share` 청크(vite.config.js)에 있고, 여기는 행 메뉴가 부르는 브리지와 import() 뿐이다.
let mod = null;
const load = () => (mod || (mod = import('./share/entry.js')));
const warn = (e) => console.error('[FarShell share]', e);

window.vtShareOpen = (name, share) => load().then((m) => m.openShareDialog(name, share)).catch(warn);
window.vtShareUnshare = (name) => load().then((m) => m.unshareSession(name)).catch(warn);
