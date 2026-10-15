// `share` 청크 안의 승인함 단일 진입점 — share-lazy.js(core)와 설정 「권한」 섹션이 이 파일 하나만 동적 import 한다.
// (re-export 구문 대신 import + export 로 쓴다: 테스트 하네스(vm-esm)가 import 줄만 훑어 그래프를 만든다.)
import { setPending, refresh, onEvent, onChange, pending } from './store.js';
import { wire, showPending, resolveRequest } from './inbox.js';

let badgeWired = false;
/** 대기 건수를 상시 코드(share-lazy.js)가 칠하는 배지에 알린다. */
function wireBadge() {
  if (badgeWired) return;
  badgeWired = true;
  onChange((ev) => {
    if (ev.type === 'pending' && typeof window.vtAuthzBadge === 'function') window.vtAuthzBadge(pending().length);
  });
}
wire();
wireBadge();

export { setPending, refresh, onEvent, showPending, resolveRequest };
