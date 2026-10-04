// L3 2단계 — layout/compact.js 순수 로직 검증: leaf 나열 순서(flattenLeaves),
// compact 판정(isCompactMode). 실제 터치 제스처(wireCompactSwipe)는 jsdom이 TouchEvent를
// 제대로 지원하지 않아 여기서 시뮬레이션하지 않는다 — §8의 5종 제스처 충돌
// 회피(가장자리 20px+터미널 영역 한정+6px 방향 임계값)는 실브라우저/실기기
// 검증 대상(30-layout-shell.md L3 2단계 기록 참고).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const COMPACT_JS = path.join(__dirname, '../js/layout/compact.js');
const STORE_JS = path.join(__dirname, '../js/layout/store.js');
const CORE_STORE_JS = path.join(__dirname, '../js/core/store.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function loadCompact() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const S = await importFresh(STORE_JS, env.context, cache);
  const C = await importFresh(COMPACT_JS, env.context, cache);
  const core = await importFresh(CORE_STORE_JS, env.context, cache);
  return { window: env.window, S, C, core };
}

test('flattenLeaves — leaf 하나면 그 leaf 하나만 담긴 배열', async () => {
  const { S, C } = await loadCompact();
  const leaves = C.flattenLeaves(S.getTree());
  assert.strictEqual(leaves.length, 1);
  assert.strictEqual(leaves[0].id, S.getActivePaneId());
});

test('flattenLeaves — depth-first(a→b) 순서로 나열된다', async () => {
  const { S, C } = await loadCompact();
  const rootId = S.getActivePaneId();
  const midId = S.splitActivePane('row');   // root(a) - mid(b), 활성=mid
  const leafId = S.splitPane(midId, 'col'); // root(a) - (mid(a) - leaf(b))

  // vm context에서 만든 배열이라 deepStrictEqual은 프로토타입 불일치로
  // 실패한다(layout-tree.test.js와 같은 함정) — deepEqual(비-strict)로 비교.
  const ids = C.flattenLeaves(S.getTree()).map((l) => l.id);
  assert.deepEqual(ids, [rootId, midId, leafId]);
});

test('isCompactMode — 포인터 종류와 무관하게 폭이 720px 미만이면 true', async () => {
  const { window, C } = await loadCompact();
  Object.defineProperty(window, 'innerWidth', { value: 600, configurable: true });
  assert.strictEqual(C.isCompactMode(), true);
});

test('isCompactMode — 720px 이상이면 coarse 포인터여도 false', async () => {
  const { window, C } = await loadCompact();
  window.matchMedia = (q) => ({ matches: q === '(pointer:coarse)' });
  Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true });
  assert.strictEqual(C.isCompactMode(), false, '720px 이상이면 pointer:coarse여도 compact가 아니다');
});

// 이동 순서·단계 이동(openSessionOrder/stepSession)은 session-order.test.js가 덮는다.
