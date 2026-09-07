// S3 — core/keymap.js. 재바인딩보다 중요한 건 **passthrough**다: `Mod+F`는
// 셸에서 forward-char인데 웹이 가로채고 사용자가 되돌릴 방법이 없었다.
// 그리고 `Mod+W`처럼 브라우저가 먼저 먹는 키는 "조용히 안 되는" 상태가
// 최악이므로, 그 사실이 값으로 드러나는지도 함께 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const KEYMAP_JS = path.join(__dirname, '../js/core/keymap.js');
const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load({ standalone = false, platform = '' } = {}) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  Object.defineProperty(window.navigator, 'platform', { value: platform, configurable: true });
  window.matchMedia = (q) => ({ matches: standalone && q.includes('standalone'), addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  const S = await importFresh(SETTINGS_JS, env.context, cache);
  const K = await importFresh(KEYMAP_JS, env.context, cache);
  return { window, K, S };
}

const kd = (window, opts) => new window.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...opts });

// ── 표기 정규화 ───────────────────────────────────────────────────────────
test('normalize — 순서·대소문자가 달라도 같은 조합으로 본다', async () => {
  const { K } = await load();
  assert.strictEqual(K.normalize('Shift+Mod+F'), K.normalize('mod+shift+f'));
  assert.strictEqual(K.normalize('Mod+Shift+F'), 'mod+shift+f');
});

test('normalize — Cmd/Meta/Ctrl 표기를 Mod로 흡수한다', async () => {
  const { K } = await load();
  assert.strictEqual(K.normalize('Cmd+K'), 'mod+k');
  assert.strictEqual(K.normalize('Meta+K'), 'mod+k');
});

test('normalize — 특수키 이름은 원형을 지킨다', async () => {
  const { K } = await load();
  assert.strictEqual(K.normalize('Mod+Shift+ArrowLeft'), 'mod+shift+ArrowLeft');
});

// ── 이벤트 매칭 ───────────────────────────────────────────────────────────
test('비-mac에서는 Ctrl이 Mod다', async () => {
  const { K, window } = await load({ platform: 'Linux x86_64' });
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })).id, 'search');
  assert.strictEqual(K.match(kd(window, { key: 'f', metaKey: true })).id, 'search');
});

test('mac에서는 Cmd가 Mod이고 Ctrl은 별개다', async () => {
  const { K, window } = await load({ platform: 'MacIntel' });
  assert.strictEqual(K.match(kd(window, { key: 'f', metaKey: true })).id, 'search');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })), null, 'mac의 Ctrl+F는 다른 키다');
  // Ctrl+Shift+E(코드 뷰어)는 mac에서도 그대로 Ctrl 조합이다
  assert.strictEqual(K.match(kd(window, { key: 'e', ctrlKey: true, shiftKey: true })).id, 'viewer');
});

test('수식키가 모자라면 매칭되지 않는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  assert.strictEqual(K.match(kd(window, { key: 'f' })), null);
  assert.strictEqual(K.match(kd(window, { key: 'k', ctrlKey: true, shiftKey: true })), null);
});

// ── 실행 · passthrough ────────────────────────────────────────────────────
test('handleKeydown — 등록된 핸들러를 실행하고 기본동작을 막는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  let ran = 0;
  K.register('search', () => ran++);
  const e = kd(window, { key: 'f', ctrlKey: true });
  const consumed = K.handleKeydown(e);
  assert.strictEqual(ran, 1);
  assert.strictEqual(consumed, true);
  assert.strictEqual(e.defaultPrevented, true);
});

test('passthrough — 동작은 하되 키를 터미널로 흘린다(Mod+F를 셸에 돌려주기)', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  let ran = 0;
  K.register('search', () => ran++);
  await K.setPassthrough('search', true);

  const e = kd(window, { key: 'f', ctrlKey: true });
  const consumed = K.handleKeydown(e);
  assert.strictEqual(ran, 1, '동작 자체는 그대로 실행된다');
  assert.strictEqual(consumed, false);
  assert.strictEqual(e.defaultPrevented, false, '터미널이 키를 받아야 하므로 막지 않는다');
});

test('핸들러가 없는 액션은 조용히 통과시킨다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  const e = kd(window, { key: 'b', ctrlKey: true });   // railToggle, 핸들러 미등록
  assert.strictEqual(K.handleKeydown(e), false);
  assert.strictEqual(e.defaultPrevented, false);
});

test('핸들러가 예외를 던져도 다음 키 입력이 죽지 않는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  K.register('search', () => { throw new Error('boom'); });
  assert.strictEqual(K.handleKeydown(kd(window, { key: 'f', ctrlKey: true })), true);
});

// ── 재바인딩 · 충돌 ───────────────────────────────────────────────────────
test('setBinding — 재바인딩하면 새 조합으로 매칭된다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  await K.setBinding('search', 'Mod+Shift+F');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })), null, '옛 조합은 더는 안 잡힌다');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true, shiftKey: true })).id, 'search');
});

test('reset — 기본값으로 돌아온다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  await K.setBinding('search', 'Mod+Shift+F');
  await K.reset('search');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })).id, 'search');
});

test('conflicts — 기본 바인딩끼리는 충돌이 없다', async () => {
  const { K } = await load();
  assert.deepEqual(K.conflicts(), {});
});

test('conflicts — 같은 조합에 둘을 바인딩하면 잡아낸다', async () => {
  const { K } = await load();
  await K.setBinding('palette', 'Mod+F');
  const c = K.conflicts();
  assert.deepEqual(Object.keys(c), ['mod+f']);
  assert.deepEqual(c['mod+f'].sort(), ['palette', 'search']);
});

// ── 브라우저가 먼저 먹는 키 ───────────────────────────────────────────────
test('Mod+W 기본값을 쓰지 않는다 — 브라우저 탭 닫기와 충돌하므로', async () => {
  const { K } = await load();
  const paneClose = K.list().find((b) => b.id === 'paneClose');
  assert.strictEqual(K.normalize(paneClose.combo), 'mod+shift+w');
});

test('일반 탭에서 Mod+W로 재바인딩하면 unavailable로 표시되고 매칭도 안 된다', async () => {
  const { K, window } = await load({ standalone: false, platform: 'Linux' });
  await K.setBinding('paneClose', 'Mod+W');
  const b = K.list().find((x) => x.id === 'paneClose');
  assert.strictEqual(b.unavailable, true, '조용히 안 되는 대신 그 사실이 값으로 드러나야 한다');
  assert.strictEqual(K.match(kd(window, { key: 'w', ctrlKey: true })), null);
});

test('PWA standalone에서는 Mod+W도 가로챌 수 있다', async () => {
  const { K, window } = await load({ standalone: true, platform: 'Linux' });
  await K.setBinding('paneClose', 'Mod+W');
  const b = K.list().find((x) => x.id === 'paneClose');
  assert.strictEqual(b.unavailable, false);
  assert.strictEqual(K.match(kd(window, { key: 'w', ctrlKey: true })).id, 'paneClose');
});

// ── 표시 ──────────────────────────────────────────────────────────────────
test('displayCombo — mac은 기호, 그 외는 텍스트', async () => {
  const mac = await load({ platform: 'MacIntel' });
  assert.strictEqual(mac.K.displayCombo('Mod+Shift+F'), '⌘⇧F');
  const linux = await load({ platform: 'Linux' });
  assert.strictEqual(linux.K.displayCombo('Mod+Shift+F'), 'Ctrl+Shift+F');
  assert.strictEqual(linux.K.displayCombo('Mod+Shift+ArrowLeft'), 'Ctrl+Shift+←');
});

test('깨진 재정의 JSON이어도 기본 바인딩으로 동작한다', async () => {
  const { K, S, window } = await load({ platform: 'Linux' });
  await S.set('keymap.overrides', '{oops');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })).id, 'search');
});
