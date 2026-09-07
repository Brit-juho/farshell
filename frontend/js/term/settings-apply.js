// S2 — 설정 변경을 **이미 살아 있는 xterm 인스턴스**에 즉시 반영한다.
//
// 왜 필요한가(실브라우저 검증에서 드러난 갭): 캐시가 없는 기기(첫 접속, 새
// 브라우저)에서는 서버 설정이 도착하기 **전에** 터미널이 이미 만들어진다.
// 그러면 다른 기기에서 키워둔 폰트가 이 기기에서만 기본값으로 뜬다 — 서버
// 저장은 성공했는데 사용자 눈에는 "동기화가 안 된다"로 보인다.
// 설정 스토어를 구독해 그때그때 반영하면, 같은 코드가
//   ① 늦게 도착한 서버 값 반영
//   ② 설정 화면(S4)에서 방금 바꾼 값의 즉시 반영
// 두 가지를 모두 처리한다(재시작 불필요라는 원칙이 이 파일 하나로 지켜진다).
import { allSessions } from '../core/store.js';
import { get as setting, subscribe as onSettings } from '../core/settings.js';
import { fitAndResize } from './resize.js';

// xterm 옵션에 그대로 얹을 수 있는 설정들. screenReaderMode는 여기 없다 —
// 그건 인스턴스 생성 시점에만 의미가 있고(내부 DOM 구조가 달라진다) 런타임에
// 뒤집으면 접근성 트리가 어긋난다. 그래서 S4에서 "새 탭부터 적용"으로 안내한다.
const OPTION_KEYS = {
  'terminal.fontSize': 'fontSize',
  'terminal.cursorStyle': 'cursorStyle',
  'terminal.cursorBlink': 'cursorBlink',
  'terminal.scrollback': 'scrollback',
};

export function applySettingsToTerminals(changed) {
  const keys = changed ? Object.keys(changed) : Object.keys(OPTION_KEYS);
  const opts = {};
  for (const key of keys) {
    const optName = OPTION_KEYS[key];
    if (optName) opts[optName] = setting(key);
  }
  if (Object.keys(opts).length === 0) return;

  for (const [id, s] of Object.entries(allSessions())) {
    if (!s || !s.term) continue;
    let sizeChanged = false;
    for (const [optName, value] of Object.entries(opts)) {
      if (value === undefined || s.term.options[optName] === value) continue;
      try {
        s.term.options[optName] = value;
        if (optName === 'fontSize') sizeChanged = true;
      } catch (_) { /* 잘못된 값은 xterm이 던진다 — 나머지 옵션은 계속 적용 */ }
    }
    // 폰트 크기가 바뀌면 셀 크기가 달라져 행·열 수가 바뀐다. 다시 fit하고
    // PTY에도 새 크기를 알려야 한다(기존 리사이즈 파이프 그대로 재사용).
    if (sizeChanged) requestAnimationFrame(() => fitAndResize(id));
  }
}

onSettings(applySettingsToTerminals);
