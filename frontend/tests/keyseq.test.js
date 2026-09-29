// keyseq.js 순수 로직 단위 테스트. 의존성 없이 Node 내장 러너로 실행:
//   node --test frontend/tests/keyseq.test.js
const { test } = require('node:test');
const assert = require('node:assert');
const K = require('../js/lib/keyseq.js');

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

test('keySeq: 이동·편집 키', () => {
  assert.strictEqual(K.keySeq({ key: 'home' }), '\x1b[H');
  assert.strictEqual(K.keySeq({ key: 'end' }), '\x1b[F');
  assert.strictEqual(K.keySeq({ key: 'pgup' }), '\x1b[5~');
  assert.strictEqual(K.keySeq({ key: 'pgdn' }), '\x1b[6~');
  assert.strictEqual(K.keySeq({ key: 'del' }), '\x1b[3~');
});

test('keySeq: F1~F12', () => {
  assert.strictEqual(K.keySeq({ key: 'f1' }), '\x1bOP');
  assert.strictEqual(K.keySeq({ key: 'f4' }), '\x1bOS');
  assert.strictEqual(K.keySeq({ key: 'f5' }), '\x1b[15~');
  assert.strictEqual(K.keySeq({ key: 'f10' }), '\x1b[21~');
  assert.strictEqual(K.keySeq({ key: 'f11' }), '\x1b[23~');
  assert.strictEqual(K.keySeq({ key: 'f12' }), '\x1b[24~');
});

test('keySeq: 수식 파라미터 1 + Shift1 + Alt2 + Ctrl4 (xterm)', () => {
  assert.strictEqual(K.keySeq({ key: 'right', mods: { shift: true } }), '\x1b[1;2C');
  assert.strictEqual(K.keySeq({ key: 'right', mods: { alt: true } }), '\x1b[1;3C');
  assert.strictEqual(K.keySeq({ key: 'right', mods: { ctrl: true, shift: true } }), '\x1b[1;6C');
  assert.strictEqual(K.keySeq({ key: 'end', mods: { shift: true } }), '\x1b[1;2F');
  assert.strictEqual(K.keySeq({ key: 'del', mods: { ctrl: true } }), '\x1b[3;5~');
  assert.strictEqual(K.keySeq({ key: 'f2', mods: { shift: true } }), '\x1b[1;2Q');
  assert.strictEqual(K.keySeq({ key: 'f5', mods: { ctrl: true, alt: true, shift: true } }), '\x1b[15;8~');
});

test('keySeq: Tab/Esc 수식, 고정 단축 키는 수식 무시', () => {
  assert.strictEqual(K.keySeq({ key: 'tab', mods: { shift: true } }), '\x1b[Z');
  assert.strictEqual(K.keySeq({ key: 'tab', mods: { alt: true } }), '\x1b\t');
  assert.strictEqual(K.keySeq({ key: 'esc', mods: { alt: true } }), '\x1b\x1b');
  assert.strictEqual(K.keySeq({ key: 'backtab' }), '\x1b[Z');
  assert.strictEqual(K.keySeq({ key: 'ctrlc', mods: { alt: true } }), '\x03');
  assert.strictEqual(K.keySeq({ key: 'prefix' }), '\x02');
});

test('applyModsToInput: 소프트 키보드 문자에 Ctrl/Alt/Shift, 멀티·비ASCII는 원문', () => {
  assert.strictEqual(K.applyModsToInput('c', { ctrl: true }), '\x03');
  assert.strictEqual(K.applyModsToInput('f', { alt: true }), '\x1bf');
  assert.strictEqual(K.applyModsToInput('a', { shift: true }), 'A');
  assert.strictEqual(K.applyModsToInput('c', { ctrl: true, alt: true }), '\x1b\x03');
  assert.strictEqual(K.applyModsToInput('ab', { ctrl: true }), 'ab');
  assert.strictEqual(K.applyModsToInput('안', { alt: true }), '안');
  assert.strictEqual(K.applyModsToInput('c', {}), 'c');
});
