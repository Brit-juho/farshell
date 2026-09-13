// N12 — kitty 키보드 프로토콜(CSI u). 1층(인코딩)과 2층(협상)이 둘 다
// 순수 로직이라 DOM 이벤트 없이 검증 가능하다. 실제 키 입력이 PTY까지 가는
// 경로는 실브라우저 몫.
//
// 이 테스트가 지키는 가장 중요한 불변식: **모드를 안 켠 앱에는 아무것도
// 바뀌지 않는다.** 이게 깨지면 평범한 셸에 `\x1b[97;6u` 같은 쓰레기가 찍힌다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const KITTY_JS = path.join(__dirname, '../js/term/kitty-keys.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadKitty() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(KITTY_JS, env.context, new Map());
}

function key(k, mods = {}) {
  return {
    type: 'keydown',
    key: k,
    shiftKey: !!mods.shift,
    altKey: !!mods.alt,
    ctrlKey: !!mods.ctrl,
    metaKey: !!mods.meta,
  };
}

const ON = 1;   // disambiguate escape codes

test('모드가 꺼져 있으면 절대 인코딩하지 않는다 — 쓰레기 입력 방지', async () => {
  const K = await loadKitty();
  for (const e of [key('a', { ctrl: true, shift: true }), key('Escape'), key('Enter', { ctrl: true })]) {
    assert.strictEqual(K.encodeKey(e, 0), null);
  }
});

test('Escape는 수정자 없이도 인코딩한다 — 프로토콜의 존재 이유', async () => {
  const K = await loadKitty();
  assert.strictEqual(K.encodeKey(key('Escape'), ON), '\x1b[27u');
});

test('레거시 인코딩이 있는 조합은 xterm 기본 처리에 맡긴다', async () => {
  const K = await loadKitty();
  assert.strictEqual(K.encodeKey(key('a'), ON), null);              // 그냥 글자
  assert.strictEqual(K.encodeKey(key('A', { shift: true }), ON), null);
  assert.strictEqual(K.encodeKey(key('a', { alt: true }), ON), null);  // ESC 접두
  assert.strictEqual(K.encodeKey(key('c', { ctrl: true }), ON), null); // ^C
  assert.strictEqual(K.encodeKey(key('Enter'), ON), null);
  assert.strictEqual(K.encodeKey(key('Tab', { shift: true }), ON), null); // CSI Z
});

test('레거시가 없던 조합들이 드디어 전달된다', async () => {
  const K = await loadKitty();
  // Ctrl+Shift+A — 수정자 1+1(shift)+4(ctrl) = 6, 코드포인트는 소문자 a(97)
  assert.strictEqual(K.encodeKey(key('A', { ctrl: true, shift: true }), ON), '\x1b[97;6u');
  assert.strictEqual(K.encodeKey(key('Enter', { shift: true }), ON), '\x1b[13;2u');
  assert.strictEqual(K.encodeKey(key('Enter', { ctrl: true }), ON), '\x1b[13;5u');
  assert.strictEqual(K.encodeKey(key('Tab', { ctrl: true }), ON), '\x1b[9;5u');
  assert.strictEqual(K.encodeKey(key('1', { ctrl: true }), ON), '\x1b[49;5u');
  assert.strictEqual(K.encodeKey(key('k', { meta: true }), ON), '\x1b[107;9u');
});

test('수정자 키 자체와 모르는 키는 보내지 않는다', async () => {
  const K = await loadKitty();
  for (const k of ['Shift', 'Control', 'Alt', 'Meta', 'Dead', 'Unidentified']) {
    assert.strictEqual(K.encodeKey(key(k, { ctrl: true, shift: true }), ON), null);
  }
  // F1·방향키는 레거시 CSI 시퀀스가 이미 있어 xterm이 처리한다.
  assert.strictEqual(K.encodeKey(key('F1', { ctrl: true, shift: true }), ON), null);
  assert.strictEqual(K.encodeKey(key('ArrowUp', { ctrl: true, shift: true }), ON), null);
});

test('modifierValue — kitty는 1 + 비트합', async () => {
  const K = await loadKitty();
  assert.strictEqual(K.modifierValue(key('a')), 1);
  assert.strictEqual(K.modifierValue(key('a', { shift: true })), 2);
  assert.strictEqual(K.modifierValue(key('a', { ctrl: true, alt: true, shift: true, meta: true })), 16);
});

// ── 2층: 협상 상태기계 ────────────────────────────────────────────────────

test('push/pop — 앱이 빠져나가면 이전 값으로 되돌아간다', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState();
  assert.strictEqual(st.flags, 0);
  st.push(1);
  assert.strictEqual(st.flags, 1);
  st.push(5);
  assert.strictEqual(st.flags, 5);
  st.pop();
  assert.strictEqual(st.flags, 1);
  st.pop();
  assert.strictEqual(st.flags, 0);
});

test('pop은 바닥(꺼짐)을 비우지 못한다 — 과도한 pop으로 상태가 깨지지 않는다', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState();
  st.push(1);
  st.pop(99);
  assert.strictEqual(st.flags, 0);
});

test('스택 깊이 상한 — pop을 잊는 앱이 있어도 무한히 쌓이지 않는다', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState(4);
  for (let i = 0; i < 50; i++) st.push(1);
  assert.strictEqual(st.flags, 1);
  st.pop(50);
  assert.strictEqual(st.flags, 0, '바닥은 여전히 꺼짐이다');
});

test('set — mode 1=설정, 2=OR, 3=해제', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState();
  st.set(1, 1);
  assert.strictEqual(st.flags, 1);
  st.set(4, 2);
  assert.strictEqual(st.flags, 5);
  st.set(1, 3);
  assert.strictEqual(st.flags, 4);
  st.set(0, 1);
  assert.strictEqual(st.flags, 0);
});

test('정의되지 않은 플래그 비트와 쓰레기 값은 걸러낸다', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState();
  st.push(9999);
  assert.strictEqual(st.flags, 9999 & 31);
  st.push(-1);
  assert.strictEqual(st.flags, 0);
  st.push('nope');
  assert.strictEqual(st.flags, 0);
});

test('질의 응답은 앱이 켠 값을 그대로 되돌려준다', async () => {
  const K = await loadKitty();
  const st = new K.KittyKeyState();
  assert.strictEqual(st.queryReply(), '\x1b[?0u');
  st.push(5);
  assert.strictEqual(st.queryReply(), '\x1b[?5u');
});

test('wireKittyKeyboard — CSI 핸들러가 파서에 등록되고 모드를 바꾼다', async () => {
  const K = await loadKitty();
  const handlers = [];
  const written = [];
  const term = {
    parser: { registerCsiHandler: (spec, cb) => handlers.push([spec, cb]) },
    input: (d) => written.push(d),
  };
  const st = K.wireKittyKeyboard(term);
  const find = (prefix) => handlers.find(([s]) => s.prefix === prefix && s.final === 'u')[1];

  assert.strictEqual(handlers.length, 4);
  assert.strictEqual(find('>')([1]), true, '처리했다고 알려야 시퀀스가 화면에 새지 않는다');
  assert.strictEqual(st.flags, 1);
  find('=')([4, 2]);
  assert.strictEqual(st.flags, 5);
  find('?')([]);
  assert.deepStrictEqual(written.length, 1);
  assert.strictEqual(written[0], '\x1b[?5u');
  find('<')([1]);
  assert.strictEqual(st.flags, 0);
});

test('파서가 CSI 핸들러를 지원하지 않아도 죽지 않는다(모드는 항상 꺼짐)', async () => {
  const K = await loadKitty();
  const st = K.wireKittyKeyboard({});
  assert.strictEqual(st.flags, 0);
});
