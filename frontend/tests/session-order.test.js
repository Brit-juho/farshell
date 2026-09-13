// N37 3단계 — 세션 **순서**의 단일 출처가 core/store.js의 order 배열이라는 것.
// 2.1.3까지는 `#tabs .tab`의 DOM 순서가 그 출처였고, 그 DOM은 곧 사라진다.
// 여기서 고정하는 불변식: 삽입 위치 · 제거 후 잔여 순서 · 재정렬 · 복원 시
// 순서 일괄 지정(목록에 없는 세션을 잃지 않는다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const STORE_JS = path.join(__dirname, '../js/core/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadStore() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(STORE_JS, env.context, new Map());
}

// vm 컨텍스트가 realm을 가르므로 배열은 reference-equal 비교가 통하지 않는다 —
// 순서만 보면 되니 문자열로 눌러서 비교한다.
function ord(S) { return S.orderedSessionIds().join(','); }

function add(S, ...ids) {
  for (const id of ids) S.registerSession(id, { displayName: id });
}

test('등록 순서가 곧 세션 순서다', async () => {
  const S = await loadStore();
  add(S, 'a', 'b', 'c');
  assert.strictEqual(ord(S), ['a', 'b', 'c'].join(','));
});

test('insertBeforeId를 주면 그 앞에 끼워 넣는다 — 복원이 원래 자리를 지키는 경로', async () => {
  const S = await loadStore();
  add(S, 'a', 'c');
  S.registerSession('b', { displayName: 'b' }, 'c');
  assert.strictEqual(ord(S), ['a', 'b', 'c'].join(','));
});

test('없는 id를 insertBefore로 주면 끝에 붙는다(조용히 유실되지 않는다)', async () => {
  const S = await loadStore();
  add(S, 'a');
  S.registerSession('b', { displayName: 'b' }, 'nope');
  assert.strictEqual(ord(S), ['a', 'b'].join(','));
});

test('제거하면 순서에서도 빠진다 — 유령 id가 남아 순환이 헛돌지 않는다', async () => {
  const S = await loadStore();
  add(S, 'a', 'b', 'c');
  S.removeSessionRecord('b');
  assert.strictEqual(ord(S), ['a', 'c'].join(','));
});

test('moveSessionBefore — 앞/뒤 양방향, 바뀐 경우에만 true', async () => {
  const S = await loadStore();
  add(S, 'a', 'b', 'c');
  assert.strictEqual(S.moveSessionBefore('c', 'a'), true);
  assert.strictEqual(ord(S), ['c', 'a', 'b'].join(','));
  assert.strictEqual(S.moveSessionBefore('c', 'b', true), true);
  assert.strictEqual(ord(S), ['a', 'b', 'c'].join(','));
  assert.strictEqual(S.moveSessionBefore('a', 'a'), false);
});

test('moveSessionByOffset — 끝에서는 순환하지 않고 멈춘다', async () => {
  const S = await loadStore();
  add(S, 'a', 'b', 'c');
  assert.strictEqual(S.moveSessionByOffset('a', -1), false);
  assert.strictEqual(ord(S), ['a', 'b', 'c'].join(','));
  assert.strictEqual(S.moveSessionByOffset('a', 1), true);
  assert.strictEqual(ord(S), ['b', 'a', 'c'].join(','));
  assert.strictEqual(S.moveSessionByOffset('c', 1), false);
});

test('setSessionOrder — 목록에 없는 세션은 뒤에 남긴다(복원 중 새로 생긴 세션을 잃지 않는다)', async () => {
  const S = await loadStore();
  add(S, 'a', 'b', 'new');
  S.setSessionOrder(['b', 'a', 'dead']);
  assert.strictEqual(ord(S), ['b', 'a', 'new'].join(','));
});

test('구독자는 순서 변경에도 통지받는다', async () => {
  const S = await loadStore();
  add(S, 'a', 'b');
  let hits = 0;
  const off = S.subscribe(() => { hits += 1; });
  S.moveSessionBefore('b', 'a');
  S.setSessionOrder(['a', 'b']);
  off();
  assert.strictEqual(hits, 2);
});
