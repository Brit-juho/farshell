// `share` 청크의 공유 대화상자 진입점 — share-lazy.js(core)가 이 파일을 동적 import 한다.
// (re-export 구문 대신 import + export 로 쓴다: 테스트 하네스(vm-esm)가 import 줄만 훑어 그래프를 만든다.)
// 승인 모달·권한 화면은 authz/entry.js 의 통합 승인함이 맡는다.
import { openShareDialog, unshareSession } from './share-dialog.js';

export { openShareDialog, unshareSession };
