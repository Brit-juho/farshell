// L3 2단계 — layout/compact.js 순수 로직 검증: leaf 나열 순서(flattenLeaves),
// compact 판정(isCompactMode), 스와이프 없이도 호출 가능한 단계 이동
// (stepActivePane). 실제 터치 제스처(wireCompactSwipe)는 jsdom이 TouchEvent를
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

test('isCompactMode — matchMedia가 없으면(기본 jsdom) 항상 false', async () => {
  const { C } = await loadCompact();
  assert.strictEqual(C.isCompactMode(), false);
});

test('isCompactMode — pointer:coarse + <720px일 때만 true', async () => {
  const { window, C } = await loadCompact();
  window.matchMedia = (q) => ({ matches: q === '(pointer:coarse)' });
  Object.defineProperty(window, 'innerWidth', { value: 600, configurable: true });
  assert.strictEqual(C.isCompactMode(), true);

  Object.defineProperty(window, 'innerWidth', { value: 900, configurable: true });
  assert.strictEqual(C.isCompactMode(), false, '720px 이상이면 pointer:coarse여도 compact가 아니다');
});

test('stepActivePane — leaf가 하나뿐이면 아무 일도 하지 않는다', async () => {
  const { S, C } = await loadCompact();
  const rootId = S.getActivePaneId();
  C.stepActivePane(1);
  assert.strictEqual(S.getActivePaneId(), rootId);
});

test('stepActivePane — +1/-1로 다음/이전 leaf로 순환 이동한다', async () => {
  const { S, C } = await loadCompact();
  const rootId = S.getActivePaneId();
  const midId = S.splitActivePane('row');
  const leafId = S.splitPane(midId, 'col'); // [rootId, midId, leafId], 활성=leafId

  C.stepActivePane(1); // leafId → rootId(순환)
  assert.strictEqual(S.getActivePaneId(), rootId);

  C.stepActivePane(-1); // rootId → leafId(역방향 순환)
  assert.strictEqual(S.getActivePaneId(), leafId);

  C.stepActivePane(-1); // leafId → midId
  assert.strictEqual(S.getActivePaneId(), midId);
});

// ── L8: leaf가 하나뿐일 때 같은 스와이프가 세션 전환이 된다 ───────────────
// switchTo(term/session.js)는 탭 DOM·xterm이 모두 있어야 끝까지 도는 함수라
// 여기서는 "무엇을 다음 대상으로 고르는가"(sessionOrder/stepView의 분기)만
// 검증한다 — 실제 전환 자체는 terminal-lifecycle.test.js가 이미 덮는다.
test('sessionOrder — 탭 DOM 순서를 따른다(드래그로 재정렬한 순서가 그대로)', async () => {
  const { window, C } = await loadCompact();
  window.document.body.innerHTML = `
    <div id="tabs">
      <div class="tab" data-session-id="b"></div>
      <div class="tab" data-session-id="a"></div>
    </div>`;
  assert.deepEqual(C.sessionOrder(), ['b', 'a']);
});

test('stepView — leaf가 2개 이상이면 pane 이동(세션은 안 건드림)', async () => {
  const { window, S, C } = await loadCompact();
  window.document.body.innerHTML = `
    <div id="tabs">
      <div class="tab" data-session-id="s1"></div>
      <div class="tab" data-session-id="s2"></div>
    </div>`;
  const rootId = S.getActivePaneId();
  S.splitActivePane('row'); // leaf 2개, 활성=새 leaf

  C.stepView(1); // 순환해서 rootId로
  assert.strictEqual(S.getActivePaneId(), rootId);
});

test('nextSessionId — 활성 세션 기준으로 다음/이전을 순환해서 고른다', async () => {
  const { window, C, core } = await loadCompact();
  window.document.body.innerHTML = `
    <div id="tabs">
      <div class="tab" data-session-id="a"></div>
      <div class="tab" data-session-id="b"></div>
      <div class="tab" data-session-id="c"></div>
    </div>`;
  core.setActive('c');
  assert.strictEqual(C.nextSessionId(1), 'a', '끝에서 +1이면 처음으로 순환');
  assert.strictEqual(C.nextSessionId(-1), 'b');
});

test('nextSessionId — 세션이 1개 이하면 null(전환할 대상 없음)', async () => {
  const { window, C } = await loadCompact();
  window.document.body.innerHTML = '<div id="tabs"><div class="tab" data-session-id="a"></div></div>';
  assert.strictEqual(C.nextSessionId(1), null);
});

test('stepView — leaf가 하나면 pane은 그대로 두고 세션 전환 경로로 간다', async () => {
  const { window, S, C } = await loadCompact();
  window.document.body.innerHTML = '<div id="tabs"></div>'; // 세션 0개 = 아무 일도 없음
  const before = S.getActivePaneId();
  C.stepView(1);
  assert.strictEqual(S.getActivePaneId(), before);
});
