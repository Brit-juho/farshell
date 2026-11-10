'use strict';
// term/osc52.js — OSC 52 판정(순수 함수)과 핸들러의 세션별 첫 1회 경고.
// 모듈은 아무것도 import하지 않으므로 DOM 없이 바로 올린다.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const MOD = pathToFileURL(path.join(__dirname, '../js/term/osc52.js')).href;
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
const base = { isTmux: false, allowUntrusted: false, autocopy: true, replaying: false };

test('tmux 세션은 허용 설정과 무관하게 복사한다', async () => {
  const { osc52Decision } = await import(MOD);
  const d = osc52Decision({ ...base, isTmux: true, payload: b64('안녕 hello') });
  assert.deepStrictEqual({ a: d.action, t: d.text, r: d.reason }, { a: 'copy', t: '안녕 hello', r: 'tmux' });
});

test('일반 터미널은 기본 차단, 설정을 켜면 복사', async () => {
  const { osc52Decision } = await import(MOD);
  const blocked = osc52Decision({ ...base, payload: b64('x') });
  assert.strictEqual(blocked.action, 'block');
  assert.strictEqual(blocked.text, undefined);
  const allowed = osc52Decision({ ...base, allowUntrusted: true, payload: b64('x') });
  assert.strictEqual(allowed.action, 'copy');
  assert.strictEqual(allowed.text, 'x');
});

test('자동 복사가 꺼져 있으면 무시(tmux도 일반도)', async () => {
  const { osc52Decision } = await import(MOD);
  for (const isTmux of [true, false]) {
    const d = osc52Decision({ ...base, isTmux, allowUntrusted: true, autocopy: false, payload: b64('x') });
    assert.strictEqual(d.action, 'ignore');
  }
});

test('재생분 · 빈 payload · ? · 잘못된 base64는 무시', async () => {
  const { osc52Decision } = await import(MOD);
  assert.strictEqual(osc52Decision({ ...base, isTmux: true, replaying: true, payload: b64('x') }).reason, 'replay');
  assert.strictEqual(osc52Decision({ ...base, isTmux: true, payload: '' }).action, 'ignore');
  assert.strictEqual(osc52Decision({ ...base, isTmux: true, payload: '?' }).action, 'ignore');
  const bad = osc52Decision({ ...base, isTmux: true, payload: '!!!not base64!!!' });
  assert.deepStrictEqual({ a: bad.action, r: bad.reason }, { a: 'ignore', r: 'invalid-base64' });
});

test('1MB 경계 — 정확히 1,048,576바이트는 허용, 1바이트 초과는 무시', async () => {
  const { osc52Decision, OSC52_MAX_BYTES } = await import(MOD);
  assert.strictEqual(OSC52_MAX_BYTES, 1048576);
  const ok = Buffer.alloc(1048576, 0x61).toString('base64');
  const over = Buffer.alloc(1048577, 0x61).toString('base64');
  const a = osc52Decision({ ...base, isTmux: true, payload: ok });
  assert.strictEqual(a.action, 'copy');
  assert.strictEqual(a.text.length, 1048576);
  assert.strictEqual(osc52Decision({ ...base, isTmux: true, payload: over }).action, 'ignore');
  // 일반 터미널에서도 크기 검사가 차단 판정보다 먼저다(경고 토스트 없이 조용히 무시)
  assert.strictEqual(osc52Decision({ ...base, payload: over }).action, 'ignore');
  // 디코드 전에 거르는 길이 상한도 같은 결과
  assert.strictEqual(osc52Decision({ ...base, isTmux: true, payload: 'A'.repeat(1398108) }).reason, 'too-large');
});

test('미리보기 — 앞 40자 · 줄바꿈은 ⏎ · 길이는 글자 수', async () => {
  const { osc52Preview } = await import(MOD);
  assert.deepStrictEqual(osc52Preview('abc'), { preview: 'abc', length: 3 });
  const long = 'a'.repeat(40) + 'b';
  assert.deepStrictEqual(osc52Preview(long), { preview: 'a'.repeat(40) + '…', length: 41 });
  assert.strictEqual(osc52Preview('a'.repeat(40)).preview, 'a'.repeat(40));
  assert.strictEqual(osc52Preview('l1\nl2\r\nl3\rl4').preview, 'l1⏎l2⏎l3⏎l4');
  assert.strictEqual(osc52Preview('a\tb\x1b[31m').preview, 'a b [31m');
  // 이모지(서로게이트 쌍)는 한 글자로 센다
  assert.strictEqual(osc52Preview('😀😀').length, 2);
});

function harness({ tmux, allow = false, autocopy = true, copyOk = true }) {
  const toasts = [];
  const copies = [];
  const session = tmux ? { tmuxName: 'x' } : {};
  const deps = {
    session,
    setting: (k) => ({ 'terminal.osc52Untrusted': allow, 'mouse.autocopyOnSelect': autocopy })[k],
    copy: (txt) => { copies.push(txt); return Promise.resolve(copyOk); },
    toast: (msg, type) => toasts.push({ msg, type }),
    t: (s, v) => (v ? s.replace(/\{(\w+)\}/g, (_, k) => v[k]) : s),
  };
  return { deps, toasts, copies, session };
}

test('차단 경고는 세션마다 첫 1회만, 복사는 하지 않는다', async () => {
  const { handleOsc52 } = await import(MOD);
  const h = harness({ tmux: false });
  handleOsc52('c;' + b64('evil'), h.deps);
  handleOsc52('c;' + b64('evil2'), h.deps);
  assert.strictEqual(h.copies.length, 0);
  assert.strictEqual(h.toasts.length, 1);
  assert.strictEqual(h.toasts[0].type, 'warn');
  assert.match(h.toasts[0].msg, /막았습니다/);
  // 다른 세션은 다시 한 번 경고한다
  const h2 = harness({ tmux: false });
  handleOsc52('c;' + b64('evil'), h2.deps);
  assert.strictEqual(h2.toasts.length, 1);
});

test('차단되지 않는 경로(크기 초과·재생분)는 경고 토스트도 없다', async () => {
  const { handleOsc52 } = await import(MOD);
  const h = harness({ tmux: false });
  handleOsc52('c;' + Buffer.alloc(1048577, 1).toString('base64'), h.deps);
  h.session._replayingScrollback = true;
  handleOsc52('c;' + b64('x'), h.deps);
  assert.strictEqual(h.toasts.length, 0);
  assert.strictEqual(h.session._osc52Warned, undefined);
});

test('복사 성공 시 미리보기 토스트(텍스트는 그대로 넘긴다), 실패하면 토스트 없음', async () => {
  const { handleOsc52 } = await import(MOD);
  const h = harness({ tmux: true });
  handleOsc52('c;' + b64('<b>hi</b>\nsecond'), h.deps);
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(h.copies, ['<b>hi</b>\nsecond']);
  assert.strictEqual(h.toasts.length, 1);
  assert.strictEqual(h.toasts[0].msg, '클립보드에 복사됨 (OSC52): "<b>hi</b>⏎second" · 16자');
  const f = harness({ tmux: true, copyOk: false });
  handleOsc52('c;' + b64('x'), f.deps);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(f.toasts.length, 0);
});

test('일반 터미널도 설정을 켜면 복사하고 경고는 없다', async () => {
  const { handleOsc52 } = await import(MOD);
  const h = harness({ tmux: false, allow: true });
  handleOsc52(';' + b64('ok'), h.deps); // 선택자(c) 없이도 payload를 읽는다
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(h.copies, ['ok']);
  assert.strictEqual(h.toasts.length, 1);
  assert.notStrictEqual(h.toasts[0].type, 'warn');
});

test('tmux_name 필드(서버 응답 원형)도 tmux로 본다', async () => {
  const { handleOsc52 } = await import(MOD);
  const h = harness({ tmux: false });
  h.session.tmux_name = 'a';
  handleOsc52('c;' + b64('x'), h.deps);
  await new Promise((r) => setImmediate(r));
  assert.deepStrictEqual(h.copies, ['x']);
});
