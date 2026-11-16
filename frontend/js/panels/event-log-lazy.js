// 기록 패널의 지연 진입점 — panels/system-lazy.js 와 같은 구조·같은 이유(본문은 eventlog 청크).
// 토스트의 「자세히」·상태 막대 칸·팔레트·HUD 칩이 `eventlog.open` 액션(인자: 펼칠 기록 id)으로 연다.
import { registerAction } from '../core/dom.js';
import { reportError } from '../core/event-log.js';

// 재진입 가드: 청크가 도착하기 전에 두 번째 호출이 토글로 패널을 닫아버리는 것을 막는다.
let _opening = false;

registerAction('eventlog.open', (id) => {
  if (_opening) return;
  _opening = true;
  import('./event-log/panel.js')
    .then((m) => m.showEventLog(typeof id === 'string' ? id : ''))
    .catch((e) => reportError('event-log', e))
    .finally(() => { _opening = false; });
});
