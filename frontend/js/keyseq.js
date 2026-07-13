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

  // keybar 버튼 → 최종 전송 시퀀스. opts: { key, seq, ctrl }
  //  - seq(문자 버튼)가 있으면 그것, 없으면 key로 KEYBAR_SEQ 조회
  //  - ctrl(=armed)이면: 화살표는 CTRL_ARROW, 인쇄가능 단일문자는 ctrlByte,
  //    그 외 명명키(Esc/Tab)는 표준 Ctrl 시퀀스가 없어 평범한 키로 통과
  function keybarSeq(opts) {
    var base = opts.seq != null ? opts.seq : (KEYBAR_SEQ[opts.key] || '');
    if (!base) return '';
    if (opts.ctrl) {
      if (opts.key && CTRL_ARROW[opts.key]) return CTRL_ARROW[opts.key];
      if (base.length === 1 && base >= ' ') return ctrlByte(base);
    }
    return base;
  }

  var api = {
    KEYBAR_SEQ: KEYBAR_SEQ,
    CTRL_ARROW: CTRL_ARROW,
    ctrlByte: ctrlByte,
    applyCtrlToInput: applyCtrlToInput,
    keybarSeq: keybarSeq,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VTKeySeq = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
