// keyseq.js 순수 로직 단위 테스트. 의존성 없이 Node 내장 러너로 실행:
//   node --test frontend/tests/keyseq.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const K = require('../js/keyseq.js');

test('ctrlByte: a-z(대소문자) → 제어문자', () => {
  assert.strictEqual(K.ctrlByte('c'), '\x03');   // Ctrl+C
  assert.strictEqual(K.ctrlByte('C'), '\x03');   // 대문자도 동일
  assert.strictEqual(K.ctrlByte('a'), '\x01');
  assert.strictEqual(K.ctrlByte('z'), '\x1a');
  assert.strictEqual(K.ctrlByte('d'), '\x04');   // Ctrl+D(EOF)
  assert.strictEqual(K.ctrlByte('l'), '\x0c');   // Ctrl+L(clear)
});

test('ctrlByte: 비문자(@[\\]^_ 등) → & 0x1f', () => {
  assert.strictEqual(K.ctrlByte('@'), '\x00');   // Ctrl+@ = NUL
  assert.strictEqual(K.ctrlByte('['), '\x1b');   // Ctrl+[ = ESC
});

test('applyCtrlToInput: ASCII 단일문자만 조합, 멀티·비ASCII·빈문자는 원문', () => {
  assert.strictEqual(K.applyCtrlToInput('c'), '\x03');   // ASCII 단일 → Ctrl+C
  assert.strictEqual(K.applyCtrlToInput('xy'), 'xy');    // 멀티(붙여넣기) → 원문
  assert.strictEqual(K.applyCtrlToInput('안'), '안');     // 단일 CJK(비ASCII) → 원문
  assert.strictEqual(K.applyCtrlToInput(''), '');         // 빈문자
});

test('keybarSeq: 평범한 명명키/문자 버튼', () => {
  assert.strictEqual(K.keybarSeq({ key: 'up' }), '\x1b[A');
  assert.strictEqual(K.keybarSeq({ key: 'down' }), '\x1b[B');
  assert.strictEqual(K.keybarSeq({ key: 'left' }), '\x1b[D');
  assert.strictEqual(K.keybarSeq({ key: 'right' }), '\x1b[C');
  assert.strictEqual(K.keybarSeq({ key: 'esc' }), '\x1b');
  assert.strictEqual(K.keybarSeq({ key: 'tab' }), '\t');
  assert.strictEqual(K.keybarSeq({ seq: '|' }), '|');   // 문자 버튼
  assert.strictEqual(K.keybarSeq({ key: 'nope' }), ''); // 미지의 키 → 빈문자
});

test('keybarSeq: Ctrl+화살표 → CSI 수식(단어 이동)', () => {
  assert.strictEqual(K.keybarSeq({ key: 'left', ctrl: true }), '\x1b[1;5D');
  assert.strictEqual(K.keybarSeq({ key: 'right', ctrl: true }), '\x1b[1;5C');
  assert.strictEqual(K.keybarSeq({ key: 'up', ctrl: true }), '\x1b[1;5A');
  assert.strictEqual(K.keybarSeq({ key: 'down', ctrl: true }), '\x1b[1;5B');
});

test('keybarSeq: Ctrl+문자 버튼 → 제어바이트', () => {
  assert.strictEqual(K.keybarSeq({ seq: '|', ctrl: true }), String.fromCharCode(0x7c & 0x1f));
});

test('keybarSeq: Ctrl+Esc/Tab → 표준 시퀀스 없어 평범한 키로 통과', () => {
  assert.strictEqual(K.keybarSeq({ key: 'esc', ctrl: true }), '\x1b');
  assert.strictEqual(K.keybarSeq({ key: 'tab', ctrl: true }), '\t');
});
