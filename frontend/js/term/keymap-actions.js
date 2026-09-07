// S3 — 레지스트리에 아직 주인이 없던 액션들을 실제 동작에 연결하고, 마지막에
// `wire()`로 document 리스너를 건다.
//
// 왜 이 파일이 따로 있나: 각 모듈이 자기 액션을 `register()`하는 게 원칙이지만
// (search.js가 'search'를, quickopen.js가 'palette'를 등록하듯), 분할·pane 닫기·
// rail 토글은 **소유 모듈이 키보드를 다루지 않는다**(layout/store.js는 순수
// 상태, rail.js는 클릭 배선). 그런 액션을 각 파일에 억지로 넣으면 레이아웃
// 모듈이 키보드를 알게 된다 — 대신 여기 한 곳에 모은다.
//
// document 리스너 자체는 core/keymap.js가 모듈 평가 시점에 건다 — 등록 순서와
// 무관하므로(핸들러는 키를 받은 순간 조회된다) 이 파일이 늦게 로드돼도 된다.
import { register as registerKey } from '../core/keymap.js';
import { splitActivePane, closePane, getActivePaneId } from '../layout/store.js';
import { canSplit } from '../layout/panes.js';

registerKey('splitRight', () => { if (canSplit()) splitActivePane('row'); });
registerKey('splitDown', () => { if (canSplit()) splitActivePane('col'); });
registerKey('paneClose', () => closePane(getActivePaneId()));

// rail 토글 — rail.js는 자기 DOM만 알면 되도록 data-action 위임을 쓰고 있다.
// 그 진입점을 그대로 눌러준다(중복 구현하지 않는다).
registerKey('railToggle', () => {
  const btn = document.querySelector('#vt-rail .vt-rail-btn[data-rail="session"]');
  if (btn) btn.click();
});

// 'settings' 액션의 주인은 panels/settings.js다(S4에서 생겼다) — 여기서
// 중복 등록하지 않는다. 같은 id에 두 번 register하면 나중 것이 이기므로
// 모듈 로드 순서에 따라 동작이 갈릴 수 있다.
