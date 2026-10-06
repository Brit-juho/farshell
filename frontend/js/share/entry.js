// `share` 청크의 단일 진입점 — share-lazy.js(core)가 이 파일 하나만 동적 import 한다.
// (re-export 구문 대신 import + export 로 쓴다: 테스트 하네스(vm-esm)가 import 줄만 훑어 그래프를 만든다.)
import { openShareDialog, unshareSession } from './share-dialog.js';
import { addRequest, setPending, resolveRequest, showPending } from './approvals.js';
import { openPermsPanel } from './perms.js';

export { openShareDialog, unshareSession, addRequest, setPending, resolveRequest, showPending, openPermsPanel };
