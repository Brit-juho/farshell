// N12 — kitty 키보드 프로토콜(진행형 향상, "CSI u").
//
// 왜 필요한가: 레거시 터미널 입력 인코딩에는 자리가 없어서 아예 전달되지
// 않는 조합이 많다. Ctrl+Shift+문자, Shift+Enter, Ctrl+Enter, Ctrl+Tab이
// 전부 그렇다 — 웹 터미널에서 그 키를 눌러도 앱은 아무것도 못 받는다.
// helix·kakoune·neovim·최신 셸이 이미 이 프로토콜을 쓴다.
//
// **3층 중 2층까지만 구현한다**(2.1.4 사용자 승인 범위):
//   1층 브라우저 인코딩 — 이 파일의 `encodeKey`.
//   2층 앱과의 협상 — 이 파일의 `KittyKeyState` + CSI 핸들러. 앱이 켜지
//        않은 모드로 인코딩해 보내면 **쓰레기 입력**이 된다(모드를 안 켠
//        셸에 `\x1b[97;6u`가 그대로 찍힌다). 그래서 기본값은 꺼짐이고,
//        앱이 `CSI > flags u`로 켰을 때만 인코딩한다.
//   3층 tmux `extended-keys` — **여기 없다.** 사용자 tmux 서버 옵션을
//        코드가 바꾸는 일이라 별도 승인이 필요하다. tmux 3.3+에서 그 옵션이
//        꺼져 있으면 우리가 보낸 CSI u가 tmux에서 막혀 안쪽 앱까지 못 간다 —
//        즉 tmux 안에서는 이 기능이 조용히 아무 일도 안 할 수 있다.
//        (사용자가 직접 켜려면: `set -s extended-keys on`)
//
// 모드 플래그(kitty 스펙):
//   1 disambiguate escape codes   2 report event types
//   4 report alternate keys       8 report all keys as escape codes
//   16 report associated text
// 우리는 **1만 소비한다** — 나머지는 받아서 그대로 보관하고 질의에 되돌려
// 준다(앱이 자기가 켠 값을 다시 읽을 수 있어야 한다). 소비하지 않는 플래그를
// 켰다고 해서 우리가 그 동작을 하는 척하면 그게 더 나쁘다.

// ── 1층: 인코딩 ────────────────────────────────────────────────────────────

const SPECIAL = {
  Escape: 27,
  Enter: 13,
  Tab: 9,
  Backspace: 127,
};

/** kitty의 수정자 비트 — 값은 "1 + 비트합"이다(수정자가 없으면 1). */
export function modifierValue(e) {
  return 1
    + (e.shiftKey ? 1 : 0)
    + (e.altKey ? 2 : 0)
    + (e.ctrlKey ? 4 : 0)
    + (e.metaKey ? 8 : 0);
}

/**
 * 이 조합이 **레거시 인코딩을 갖는가**. 스펙의 "disambiguate escape codes"는
 * 레거시 표현이 있는 키는 그대로 두고, 표현이 없거나 모호한 것만 CSI u로
 * 바꾸라고 한다. 여기서 true면 xterm 기본 처리에 맡긴다.
 *
 * 레거시가 있는 것: 수정자 없음 · Shift만 · Alt만(ESC 접두) · Ctrl+글자류.
 * 레거시가 없는 것: Ctrl+Shift+*, Ctrl+Alt+*, Super(Cmd) 포함, Ctrl+Enter,
 * Ctrl+Tab, Shift+Enter 등 — 지금까지 웹 터미널에서 그냥 사라지던 조합들이다.
 */
export function hasLegacyEncoding(key, mods) {
  if (mods === 1) return true;                   // 수정자 없음
  // Shift만: 문자는 시프트된 글자 자체가 레거시고 Tab은 CSI Z가 있다.
  // 하지만 Shift+Enter·Shift+Backspace에는 레거시 표현이 없다 — 지금까지
  // 웹 터미널에서 그냥 Enter로 보이던(줄바꿈이 돼 버리던) 그 키다.
  if (mods === 2) return key.length === 1 || key === 'Tab';
  if (mods === 3) return true;                   // Alt만 (ESC 접두)
  if (mods === 5) {                              // Ctrl만
    if (key.length === 1) {
      const c = key.toLowerCase();
      // Ctrl+글자와 Ctrl+@[\]^_ 만 레거시 제어문자가 있다. Ctrl+숫자·Ctrl+/ 등은 없다.
      return (c >= 'a' && c <= 'z') || '@[\\]^_ '.includes(c);
    }
    return false;                                // Ctrl+Enter/Tab/Backspace는 없다
  }
  return false;
}

/** 이 키의 kitty 코드포인트. 모르는 키(F1·방향키 등)는 null — 그런 키는
 *  레거시 CSI 시퀀스가 이미 있으므로 xterm에 맡긴다. */
export function keyCodePoint(key) {
  if (Object.prototype.hasOwnProperty.call(SPECIAL, key)) return SPECIAL[key];
  // 한 글자 키만 다룬다. Shift 상태와 무관하게 **눌린 자판의 기본 문자**를
  // 보낸다(대문자 A가 아니라 a + shift 수정자) — 스펙이 그렇게 정의한다.
  if (key.length === 1 || Array.from(key).length === 1) {
    return key.toLowerCase().codePointAt(0);
  }
  return null;
}

/**
 * KeyboardEvent → CSI u 시퀀스. 인코딩하지 않아야 하면 null(= 호출자는
 * xterm 기본 처리에 맡긴다).
 *
 * flags에 1(disambiguate)이 없으면 **무조건 null이다** — 이것이 "모드를 안 켠
 * 앱에 쓰레기가 들어가지 않는다"를 보장하는 지점이다.
 */
export function encodeKey(e, flags) {
  if (!(flags & 1)) return null;
  if (e.type !== 'keydown') return null;
  const key = e.key;
  if (!key || key === 'Dead' || key === 'Unidentified') return null;
  // 수정자 키 자체는 보내지 않는다(report all keys=8을 소비하지 않으므로).
  if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock'].includes(key)) return null;

  const mods = modifierValue(e);
  const cp = keyCodePoint(key);
  if (cp == null) return null;

  // Escape는 수정자가 없어도 인코딩한다 — 이 프로토콜의 존재 이유(이스케이프
  // 시퀀스의 시작인지 사용자가 누른 Esc인지 구분)가 정확히 이것이다.
  if (key !== 'Escape' && hasLegacyEncoding(key, mods)) return null;

  return mods === 1 ? `\x1b[${cp}u` : `\x1b[${cp};${mods}u`;
}

// ── 2층: 앱과의 협상 ──────────────────────────────────────────────────────

/**
 * 모드 스택. kitty는 push/pop 구조를 요구한다 — 앱이 자기 모드를 켰다가
 * 빠져나갈 때 **이전 값으로** 되돌려야 하기 때문이다(vim이 셸 안에서 떴다
 * 지는 경우). 스택이 없으면 vim을 닫은 뒤 셸이 모드가 켜진 채로 남는다.
 */
export class KittyKeyState {
  constructor(maxDepth = 16) {
    this._stack = [0];        // 바닥은 항상 0(꺼짐) — pop으로 비우지 못한다.
    this._maxDepth = maxDepth;
  }

  get flags() {
    return this._stack[this._stack.length - 1];
  }

  /** `CSI > flags u` — 현재 값을 쌓고 새 값을 올린다. */
  push(flags) {
    // 깊이 상한: 앱이 push만 하고 pop을 잊어도 메모리가 늘지 않게. 넘치면
    // 가장 오래된 것을 버린다(바닥 0은 유지) — 되돌릴 곳이 사라지는 것보다
    // 무한히 쌓이는 쪽이 나쁘다.
    if (this._stack.length >= this._maxDepth) this._stack.splice(1, 1);
    this._stack.push(_sane(flags));
  }

  /** `CSI < n u` — n개(기본 1) 되돌린다. */
  pop(n = 1) {
    for (let i = 0; i < Math.max(1, n); i++) {
      if (this._stack.length > 1) this._stack.pop();
    }
  }

  /** `CSI = flags ; mode u` — mode 1=설정, 2=OR, 3=AND NOT(해제). */
  set(flags, mode = 1) {
    const cur = this.flags;
    const v = _sane(flags);
    let next = v;
    if (mode === 2) next = cur | v;
    else if (mode === 3) next = cur & ~v;
    this._stack[this._stack.length - 1] = next;
  }

  /** `CSI ? u` 질의에 대한 응답 — 앱이 자기가 켠 값을 다시 읽는다. */
  queryReply() {
    return `\x1b[?${this.flags}u`;
  }
}

function _sane(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) return 0;
  return n & 31;    // 정의된 플래그 5비트만
}

/**
 * 한 터미널에 프로토콜을 배선한다. 반환값은 이 터미널의 모드 상태 —
 * 키 핸들러(term/selection.js)가 현재 flags를 읽는 데 쓴다.
 *
 * 응답과 인코딩 결과는 `term.input()`으로 되돌린다 — xterm의 onData를 그대로
 * 태우므로 세션 WS 배선(term/ws.js)을 이 파일이 몰라도 되고, sticky 수정자
 * 같은 기존 가공도 한 곳에서만 일어난다.
 */
export function wireKittyKeyboard(term) {
  const state = new KittyKeyState();
  const parser = term && term.parser;
  if (!parser || typeof parser.registerCsiHandler !== 'function') return state;

  // 핸들러가 true를 반환하면 "내가 처리했다"는 뜻 — 시퀀스가 화면에 새지 않는다.
  parser.registerCsiHandler({ prefix: '>', final: 'u' }, (params) => {
    state.push(params[0] ?? 0);
    return true;
  });
  parser.registerCsiHandler({ prefix: '<', final: 'u' }, (params) => {
    state.pop(params[0] ?? 1);
    return true;
  });
  parser.registerCsiHandler({ prefix: '=', final: 'u' }, (params) => {
    state.set(params[0] ?? 0, params[1] ?? 1);
    return true;
  });
  parser.registerCsiHandler({ prefix: '?', final: 'u' }, () => {
    try { term.input(state.queryReply(), false); } catch (_) { /* 응답 실패는 앱이 타임아웃으로 처리한다 */ }
    return true;
  });

  return state;
}
