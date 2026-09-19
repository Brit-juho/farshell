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

test('set — 서버에 PUT하고 localStorage 캐시에도 쓴다 (global 키)', async () => {
  const { S, calls, window } = await load();
  await S.set('mouse.autocopyOnSelect', false);
  const put = calls.find((c) => c.opts && c.opts.method === 'PUT');
  assert.ok(put, 'PUT이 나가야 한다');
  assert.match(String(put.url), /\/api\/workspace(\?|$)/, 'global 키는 workspace로 간다');
  assert.deepEqual(JSON.parse(put.opts.body).settings, { 'mouse.autocopyOnSelect': false });
  assert.match(window.localStorage.getItem('vt-settings-v1'), /mouse\.autocopyOnSelect/);
});

// N3(60-settings-palette.md §1) — terminal.fontSize는 device 스코프다. 같은
// set()이 **다른 엔드포인트·다른 캐시 키**로 가는지가 전체 기능의 핵심이다.
test('set — device 스코프 키는 /api/device-settings와 별도 캐시로 간다', async () => {
  const { S, calls, window } = await load();
  await S.set('terminal.fontSize', 18);
  const put = calls.find((c) => c.opts && c.opts.method === 'PUT');
  assert.ok(put, 'PUT이 나가야 한다');
  assert.match(String(put.url), /\/api\/device-settings(\?|$)/);
  assert.deepEqual(JSON.parse(put.opts.body).settings, { 'terminal.fontSize': 18 });
  assert.match(window.localStorage.getItem('vt-settings-device-v1'), /terminal\.fontSize/);
  assert.strictEqual(window.localStorage.getItem('vt-settings-v1'), null, 'global 캐시는 안 건드린다');
});

test('scopeOf — 스키마가 정한 스코프를 그대로 돌려준다(화면 2b 배지용)', async () => {
  const { S } = await load();
  assert.strictEqual(S.scopeOf('terminal.fontSize'), 'device');
  assert.strictEqual(S.scopeOf('mouse.autocopyOnSelect'), 'global');
  assert.strictEqual(S.scopeOf('nope.nope'), 'global', '모르는 키도 기본은 global');
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

// E2(2026-09-08) — "맥에서도 열기"가 스토어로 늦게 합류했다. 그전에는 값의 주인이
// DOM 체크박스였고 localStorage는 부팅 때 복원만 했다. 이미 켜둔 사용자의 선택이
// 승격 과정에서 조용히 꺼지면 새 세션마다 맥 창이 안 떠서 "고장난 것처럼" 보인다.
test('마이그레이션 — vt_auto_mac(on/off) → session.openOnMac', async () => {
  const on = await load({ pre: (w) => w.localStorage.setItem('vt_auto_mac', 'on') });
  assert.strictEqual(on.S.get('session.openOnMac'), true, "'on' → true");

  const off = await load({ pre: (w) => w.localStorage.setItem('vt_auto_mac', 'off') });
  assert.strictEqual(off.S.get('session.openOnMac'), false, "'off' → false");
});

test('마이그레이션 — vt_auto_mac이 없으면 기본값 off', async () => {
  // 옛 코드의 기본값도 off였다(`localStorage.getItem('vt_auto_mac') ?? 'off'`).
  // 여기서 기본값이 true로 뒤집히면 "안 켠 사람에게 갑자기 맥 창이 뜨는" 회귀다.
  const { S } = await load({});
  assert.strictEqual(S.get('session.openOnMac'), false);
});

test('마이그레이션 — 원본 키를 지우지 않는다(롤백 여지)', async () => {
  const { S, window } = await load({ pre: (w) => w.localStorage.setItem('vt_font_size', '20') });
  assert.strictEqual(S.get('terminal.fontSize'), 20);
  assert.strictEqual(window.localStorage.getItem('vt_font_size'), '20');
});

test('마이그레이션 — 이미 스토어에 값이 있으면 옛 키가 덮어쓰지 않는다', async () => {
  // fontSize는 device 스코프라 캐시도 device 쪽 키에 미리 있어야 한다.
  const { S } = await load({
    pre: (w) => {
      w.localStorage.setItem('vt-settings-device-v1', JSON.stringify({ 'terminal.fontSize': 22 }));
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
    pre: (w) => w.localStorage.setItem('vt-settings-device-v1', JSON.stringify({ 'terminal.fontSize': 22 })),
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
  env.window.localStorage.setItem('vt-settings-device-v1', JSON.stringify({ 'terminal.fontSize': 22 }));
  env.window.fetch = () => Promise.reject(new Error('offline'));
  const S = await importFresh(SETTINGS_JS, env.context, new Map());
  await S.load();
  assert.strictEqual(S.get('terminal.fontSize'), 22);
});

// N3-1 — 핀치 줌이 바꾸는 값이 **기기 스코프**인가.
//
// 폰에서 두 손가락으로 키운 글씨가 맥 터미널까지 키우면 안 된다. 그 규칙은
// `terminal.fontSize`의 `scope: 'device'` 한 글자에 달려 있는데, 그걸 고정하는
// 검사가 없었다 — 스키마 한 줄이 지워져도 아무도 못 잡는다는 뜻이다.
// 스코프 이름만 보지 않고 **실제로 기기 엔드포인트로 나가는지**까지 본다:
// 스코프는 라우팅을 통해서만 의미를 갖는다.
test('terminal.fontSize는 기기 스코프다 — 폰에서 키워도 맥은 그대로', async () => {
  const { S, calls } = await load();
  assert.strictEqual(S.scopeOf('terminal.fontSize'), 'device');

  calls.length = 0;
  await S.set('terminal.fontSize', 22);
  const puts = calls.filter((c) => c.opts && c.opts.method === 'PUT');
  assert.strictEqual(puts.length, 1, `PUT이 한 번이어야 한다: ${JSON.stringify(puts)}`);
  assert.match(puts[0].url, /device-settings/,
    `기기 엔드포인트로 나가야 한다: ${puts[0].url}`);
});

test('theme.skin은 전역 스코프다 — 스킨은 모든 기기에서 같아야 한다', async () => {
  // 짝이 되는 반대 사례. 둘 다 고정해야 "스코프 분리"가 검사된 것이다 —
  // 한쪽만 보면 전부 device로 바꿔도 통과한다.
  const { S, calls } = await load();
  assert.strictEqual(S.scopeOf('theme.skin'), 'global');

  calls.length = 0;
  await S.set('theme.skin', 'macos');
  const puts = calls.filter((c) => c.opts && c.opts.method === 'PUT');
  assert.strictEqual(puts.length, 1);
  assert.doesNotMatch(puts[0].url, /device-settings/, puts[0].url);
});
