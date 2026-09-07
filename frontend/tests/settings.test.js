// S2 — core/settings.js. 설정의 단일 진실이 서버이고 localStorage는 캐시라는
// 규칙(ADR-5)이 실제로 지켜지는지, 그리고 **기존 값이 무손실로 이관되는지**가
// 핵심이다(사용자가 이미 쓰고 있던 폰트 크기·스킨·자동복사가 날아가면 안 된다).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

// pre: 모듈 평가 **전에** localStorage를 채우는 훅(마이그레이션은 평가 시점에
// 동기로 일어나므로, 나중에 넣으면 테스트가 의미를 잃는다).
async function load({ pre = () => {}, remote = undefined, failPut = false } = {}) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  const calls = [];
  window.fetch = (url, opts) => {
    calls.push({ url: String(url), opts });
    if (opts && opts.method === 'PUT') {
      return failPut
        ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ error: 'nope' }) })
        : Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(remote === undefined ? {} : { settings: remote }) });
  };
  const toasts = [];
  window.showToast = (msg) => toasts.push(msg);
  pre(window);
  const S = await importFresh(SETTINGS_JS, env.context, new Map());
  return { window, S, calls, toasts };
}

test('기본값 — 아무것도 저장된 게 없으면 스키마 기본값', async () => {
  const { S } = await load();
  assert.strictEqual(S.get('terminal.fontSize'), 14);
  assert.strictEqual(S.get('mouse.autocopyOnSelect'), true);
  assert.strictEqual(S.get('mouse.forwardToApp'), true);
});

test('알 수 없는 키는 undefined + 경고 (조용히 넘기지 않는다)', async () => {
  const { S } = await load();
  assert.strictEqual(S.get('nope.nope'), undefined);
});

test('set — 값이 즉시 반영되고 구독자에게 통지된다', async () => {
  const { S } = await load();
  const seen = [];
  S.subscribe((c) => seen.push(c));
  await S.set('terminal.fontSize', 20);
  assert.strictEqual(S.get('terminal.fontSize'), 20);
  assert.deepEqual(seen, [{ 'terminal.fontSize': 20 }]);
});

test('set — 범위를 벗어난 int는 clamp된다', async () => {
  const { S } = await load();
  await S.set('terminal.fontSize', 999);
  assert.strictEqual(S.get('terminal.fontSize'), 28);
  await S.set('terminal.fontSize', 1);
  assert.strictEqual(S.get('terminal.fontSize'), 8);
});

test('set — enum에 없는 값은 거부한다', async () => {
  const { S } = await load();
  const ok = await S.set('terminal.cursorStyle', 'sparkle');
  assert.strictEqual(ok, false);
  assert.strictEqual(S.get('terminal.cursorStyle'), 'block');
});

test('set — 서버에 PUT하고 localStorage 캐시에도 쓴다', async () => {
  const { S, calls, window } = await load();
  await S.set('terminal.fontSize', 18);
  const put = calls.find((c) => c.opts && c.opts.method === 'PUT');
  assert.ok(put, 'PUT이 나가야 한다');
  assert.deepEqual(JSON.parse(put.opts.body).settings, { 'terminal.fontSize': 18 });
  assert.match(window.localStorage.getItem('vt-settings-v1'), /terminal\.fontSize/);
});

test('저장 실패 — 조용히 큐잉하지 않고 토스트로 알린다', async () => {
  const { S, toasts } = await load({ failPut: true });
  const ok = await S.set('terminal.fontSize', 18);
  assert.strictEqual(ok, false);
  assert.strictEqual(S.get('terminal.fontSize'), 18, '이 기기에서는 반영된 상태로 둔다');
  assert.strictEqual(toasts.length, 1);
  assert.match(toasts[0], /저장하지 못했/);
});

// ── 마이그레이션 (S5의 "기존 값 무손실" 항목) ─────────────────────────────
test('마이그레이션 — 폰트 크기·스킨·자동복사·a11y·keybar 접기', async () => {
  const { S } = await load({
    pre: (w) => {
      w.localStorage.setItem('vt_font_size', '20');
      w.localStorage.setItem('vt-skin', 'catppuccin');
      w.localStorage.setItem('vt_autocopy_on_select', 'off');
      w.localStorage.setItem('vt-a11y', '1');
      w.localStorage.setItem('vt_keybar_collapsed', '1');
    },
  });
  assert.strictEqual(S.get('terminal.fontSize'), 20);
  assert.strictEqual(S.get('theme.skin'), 'catppuccin');
  assert.strictEqual(S.get('mouse.autocopyOnSelect'), false, "'off' → false");
  assert.strictEqual(S.get('a11y.screenReader'), 'on', "'1' → 'on'");
  assert.strictEqual(S.get('keybar.collapsed'), true);
});

test('마이그레이션 — 원본 키를 지우지 않는다(롤백 여지)', async () => {
  const { S, window } = await load({ pre: (w) => w.localStorage.setItem('vt_font_size', '20') });
  assert.strictEqual(S.get('terminal.fontSize'), 20);
  assert.strictEqual(window.localStorage.getItem('vt_font_size'), '20');
});

test('마이그레이션 — 이미 스토어에 값이 있으면 옛 키가 덮어쓰지 않는다', async () => {
  const { S } = await load({
    pre: (w) => {
      w.localStorage.setItem('vt-settings-v1', JSON.stringify({ 'terminal.fontSize': 22 }));
      w.localStorage.setItem('vt_font_size', '10');
    },
  });
  assert.strictEqual(S.get('terminal.fontSize'), 22);
});

test('마이그레이션 — 깨진 값은 무시하고 기본값을 지킨다', async () => {
  const { S } = await load({ pre: (w) => w.localStorage.setItem('vt_font_size', 'twenty') });
  assert.strictEqual(S.get('terminal.fontSize'), 14);
});

test('깨진 캐시 JSON이어도 부팅이 죽지 않는다', async () => {
  const { S } = await load({ pre: (w) => w.localStorage.setItem('vt-settings-v1', '{oops') });
  assert.strictEqual(S.get('terminal.fontSize'), 14);
});

// ── 서버 동기화 ───────────────────────────────────────────────────────────
test('load — 서버 값이 캐시를 이긴다(ADR-5)', async () => {
  const { S } = await load({
    pre: (w) => w.localStorage.setItem('vt-settings-v1', JSON.stringify({ 'terminal.fontSize': 22 })),
    remote: { 'terminal.fontSize': 16 },
  });
  assert.strictEqual(S.get('terminal.fontSize'), 22, '서버 응답 전에는 캐시 값(프리렌더)');
  await S.load();
  assert.strictEqual(S.get('terminal.fontSize'), 16, '서버 값이 도착하면 그게 이긴다');
});

test('load — 서버가 모르는 키/타입이 안 맞는 값은 버린다', async () => {
  const { S } = await load({ remote: { 'nope.key': 1, 'terminal.fontSize': 'huge' } });
  await S.load();
  assert.strictEqual(S.get('terminal.fontSize'), 14);
});

test('load — 서버에 아직 값이 없고 막 이관했으면 그 결과를 올린다', async () => {
  const { S, calls } = await load({
    pre: (w) => w.localStorage.setItem('vt_font_size', '20'),
    remote: {},
  });
  await S.load();
  const put = calls.find((c) => c.opts && c.opts.method === 'PUT');
  assert.ok(put, '이관 결과가 이 기기에만 남으면 안 된다');
  assert.deepEqual(JSON.parse(put.opts.body).settings['terminal.fontSize'], 20);
});

test('load — 서버가 죽어 있어도 캐시 값으로 계속 동작한다', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.localStorage.setItem('vt-settings-v1', JSON.stringify({ 'terminal.fontSize': 22 }));
  env.window.fetch = () => Promise.reject(new Error('offline'));
  const S = await importFresh(SETTINGS_JS, env.context, new Map());
  await S.load();
  assert.strictEqual(S.get('terminal.fontSize'), 22);
});
