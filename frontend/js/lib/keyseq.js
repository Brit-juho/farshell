// keybar 순수 키-시퀀스 로직 — 브라우저 전역(window.VTKeySeq)과 Node 테스트
// (require('keyseq.js')) 양쪽에서 재사용하려고 상태 없는 함수만 분리했다.
// 여기 함수는 DOM/세션 상태를 만지지 않는다 → 단위 테스트 대상.
(function (root) {
  'use strict';

  // 특수키 → PTY 원시 시퀀스 (소프트 키보드에 없는 키들)
  var KEYBAR_SEQ = {
    esc: '\x1b', tab: '\t',
    up: '\x1b[A', down: '\x1b[B', right: '\x1b[C', left: '\x1b[D',
  };

  // Ctrl+화살표 → CSI 수식 시퀀스(xterm 표준, modifier 5 = Ctrl).
  // 셸/에디터에서 단어 단위 커서 이동 등에 쓰인다. 화살표 외 명명키는 표준
  // Ctrl 시퀀스가 없어 여기 넣지 않는다(그 경우 평범한 키로 통과).
  var CTRL_ARROW = {
    up: '\x1b[1;5A', down: '\x1b[1;5B', right: '\x1b[1;5C', left: '\x1b[1;5D',
  };

  // Ctrl+<char> → 제어 문자. a-z → \x01..\x1a, 그 외 @[\]^_ 등은 & 0x1f.
  function ctrlByte(ch) {
    var lc = ch.toLowerCase();
    var code = lc.charCodeAt(0);
    if (code >= 97 && code <= 122) return String.fromCharCode(code - 96);
    return String.fromCharCode(ch.charCodeAt(0) & 0x1f);
  }

  // 소프트 키보드로 친 입력에 sticky Ctrl 적용. Ctrl 조합이 의미 있는 건
  // ASCII 단일 문자뿐이라, 그 경우만 조합한다.
  //  - 멀티문자(붙여넣기·IME 확정): 첫 글자만 바꾸면 나머지가 raw로 새므로 원문
  //  - 단일 CJK 등 비ASCII: Ctrl+한글은 의미 없음 → 엉뚱한 제어바이트 대신 원문
  function applyCtrlToInput(data) {
    if (data && data.length === 1 && data.charCodeAt(0) < 128) return ctrlByte(data);
    return data;
  }

  // 2026-09-29 — 키바가 Home/End/PgUp/PgDn/Del/F1~F12와 Alt·Shift 수식키를
  // 갖게 됐다. 수식키가 붙은 시퀀스는 xterm 표준(CSI 1;N) 한 가지 규칙으로
  // 만든다 — 키마다 조합표를 두면 수식키 3개 × 키 20여 개가 되어 반드시 틀린다.
  //   csi  : ESC [ <final>        → 수식 붙으면 ESC [ 1 ; N <final>
  //   ss3  : ESC O <final>(F1~F4) → 수식 붙으면 ESC [ 1 ; N <final>
  //   tilde: ESC [ <n> ~          → 수식 붙으면 ESC [ <n> ; N ~
  var NAMED = {
    up: { csi: 'A' }, down: { csi: 'B' }, right: { csi: 'C' }, left: { csi: 'D' },
    home: { csi: 'H' }, end: { csi: 'F' },
    del: { tilde: 3 }, pgup: { tilde: 5 }, pgdn: { tilde: 6 },
    f1: { ss3: 'P' }, f2: { ss3: 'Q' }, f3: { ss3: 'R' }, f4: { ss3: 'S' },
    f5: { tilde: 15 }, f6: { tilde: 17 }, f7: { tilde: 18 }, f8: { tilde: 19 },
    f9: { tilde: 20 }, f10: { tilde: 21 }, f11: { tilde: 23 }, f12: { tilde: 24 },
  };
  // 수식키와 상관없이 늘 같은 값을 보내는 단축 버튼. ⇧Tab·^C·tmux prefix는
  // 그 자체가 이미 조합이라 수식키를 또 얹지 않는다.
  var FIXED = { backtab: '\x1b[Z', ctrlc: '\x03', prefix: '\x02' };

  // xterm 수식 파라미터: 1 + Shift(1) + Alt(2) + Ctrl(4).
  function modParam(mods) {
    if (!mods) return 1;
    return 1 + (mods.shift ? 1 : 0) + (mods.alt ? 2 : 0) + (mods.ctrl ? 4 : 0);
  }

  // 키바 버튼 → 최종 전송 시퀀스. opts: { key, seq, mods: { ctrl, alt, shift } }
  // (예전 호출부 호환: opts.ctrl도 받는다)
  //  - FIXED: 수식 무시
  //  - NAMED: 수식이 있으면 CSI 1;N 형식
  //  - esc/tab: Shift+Tab → ESC[Z, Alt → ESC 접두, Ctrl은 표준 시퀀스가 없어 무시
  //  - seq(문자): Ctrl → 제어바이트, Alt → ESC 접두, Shift → 대문자
  function keySeq(opts) {
    var mods = opts.mods || { ctrl: !!opts.ctrl };
    var alt = mods.alt ? '\x1b' : '';
    if (opts.key && FIXED[opts.key]) return FIXED[opts.key];
    var n = opts.key && NAMED[opts.key];
    if (n) {
      var m = modParam(mods);
      if (n.tilde) return '\x1b[' + n.tilde + (m > 1 ? ';' + m : '') + '~';
      if (n.ss3) return m > 1 ? '\x1b[1;' + m + n.ss3 : '\x1bO' + n.ss3;
      return m > 1 ? '\x1b[1;' + m + n.csi : '\x1b[' + n.csi;
    }
    if (opts.key === 'esc') return alt + '\x1b';
    if (opts.key === 'tab') return mods.shift ? '\x1b[Z' : alt + '\t';
    var base = opts.seq != null ? String(opts.seq) : '';
    if (!base) return '';
    if (base.length === 1 && base >= ' ' && base.charCodeAt(0) < 128) {
      if (mods.shift) base = base.toUpperCase();
      if (mods.ctrl) base = ctrlByte(base);
    }
    return alt + base;
  }

  // 소프트 키보드로 친 입력에 키바 수식키 적용. 의미 있는 건 ASCII 단일 문자뿐
  // (applyCtrlToInput과 같은 이유) — 그 외는 원문 그대로.
  function applyModsToInput(data, mods) {
    if (!data || !mods || data.length !== 1 || data.charCodeAt(0) >= 128) return data;
    return keySeq({ seq: data, mods: mods });
  }

  // 예전 이름 — 호출부·테스트 호환.
  function keybarSeq(opts) { return keySeq(opts); }

  var api = {
    KEYBAR_SEQ: KEYBAR_SEQ,
    CTRL_ARROW: CTRL_ARROW,
    ctrlByte: ctrlByte,
    applyCtrlToInput: applyCtrlToInput,
    keybarSeq: keybarSeq,
    keySeq: keySeq,
    modParam: modParam,
    applyModsToInput: applyModsToInput,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VTKeySeq = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
