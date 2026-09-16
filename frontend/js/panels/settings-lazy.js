// 설정 화면(S4)의 지연 진입점 — panels/ports-lazy.js·files-lazy.js와 같은 구조.
// 화면 본체(panels/settings.js + panels/settings/** 9개 섹션)는 `panels` 청크로
// 빠지고 이 파일만 상시 로드(core.js)에 남는다.
//
// **액션·키 바인딩 등록이 여기 남아야 하는 이유**: 명령 팔레트가 부팅 시점에
// 키맵 레지스트리를 읽어 각 명령의 현재 바인딩을 보여준다(2.0 계약). 등록이
// 지연 청크 안으로 따라 들어가면 설정을 한 번도 안 연 세션에서는 `Mod+,`가
// 목록에도 없고 눌러도 안 먹는다.
import { registerAction } from '../core/dom.js';
import { register as registerKey } from '../core/keymap.js';

// 재진입 가드: 청크가 도착하기 전에 한 번 더 열리면 showSettings()가 두 번
// 불려 두 번째가 토글로 닫아버린다(ports-lazy.js의 _opening과 같은 이유).
let _opening = false;

function open() {
  if (_opening) return;
  _opening = true;
  import('./settings.js')
    .then((m) => m.showSettings())
    .catch((e) => console.error('[FarShell settings]', e))
    .finally(() => { _opening = false; });
}

registerAction('settings.show', open);
// S3에서 잡아둔 `Mod+,` 바인딩의 주인.
registerKey('settings', () => open());
