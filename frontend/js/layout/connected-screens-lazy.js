// 「연결된 화면」 지연 진입점 — queue-lazy.js와 같은 구조.
// 모달 본체(connected-screens.js)와 목록(clients.js)은 `screens` 청크로 빠지고
// 이 파일만 상시 로드에 남는다. 본체가 평가되면 같은 이름으로 액션·브리지를
// 다시 등록하므로(Map.set) 두 번째 호출부터는 이 스텁을 거치지 않는다.
import { registerAction } from '../core/dom.js';

function open(sessionId) {
  return import('./connected-screens.js')
    .then((m) => m.openPanel(typeof sessionId === 'string' ? sessionId : undefined))
    .catch((e) => console.error('[FarShell clients]', e));
}

registerAction('clients.show', open);
// Rail.tsx/Fleet.tsx가 특정 세션을 지정해 부르는 자리 — 정적 import 불가(지연 청크 제약).
window.vtShowClients = open;
