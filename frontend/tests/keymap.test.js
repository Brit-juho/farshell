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
  // `settings`(Mod+,)는 비-mac에서도 그대로 Ctrl 조합이다 — 터미널이 안 쓰는 키라
  // defNonMac이 필요 없었다.
  assert.strictEqual(K.match(kd(window, { key: ',', ctrlKey: true })).id, 'settings');
  assert.strictEqual(K.match(kd(window, { key: ',', metaKey: true })).id, 'settings');
});

test('mac에서는 Cmd가 Mod이고 Ctrl은 별개다', async () => {
  const { K, window } = await load({ platform: 'MacIntel' });
  assert.strictEqual(K.match(kd(window, { key: 'f', metaKey: true })).id, 'search');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true })), null, 'mac의 Ctrl+F는 다른 키다');
  // Ctrl+Shift+E(코드 뷰어)는 mac에서도 그대로 Ctrl 조합이다
  assert.strictEqual(K.match(kd(window, { key: 'e', ctrlKey: true, shiftKey: true })).id, 'viewer');
});

// 2026-09-18 회귀 — 비-mac에서 `Ctrl+…` 표기 바인딩이 아예 안 잡혔다.
// comboFromEvent는 비-mac의 ctrlKey를 `mod`로 내보내는데 normalize가 문자열
// `Ctrl`을 `ctrl`로 남겨 영영 어긋났다. 그 결과 Ctrl+Shift+V를 우리가 못 잡아
// preventDefault도 못 했고, 크롬의 "서식 없이 붙여넣기"가 그대로 발동해
// 붙여넣기가 두 번 들어갔다(term/selection.js의 paste 경로와 겹쳐서).
test('비-mac에서 Ctrl 표기 바인딩은 Mod와 같은 키다', async () => {
  const { K, window } = await load({ platform: 'Linux x86_64' });
  assert.strictEqual(K.normalize('Ctrl+Shift+V'), 'mod+shift+v');
  assert.strictEqual(K.match(kd(window, { key: 'V', ctrlKey: true, shiftKey: true })).id, 'paste');
  assert.strictEqual(K.match(kd(window, { key: 'E', ctrlKey: true, shiftKey: true })).id, 'viewer');
});

test('mac에서 Ctrl 표기 바인딩은 Mod(Cmd)와 별개로 남는다', async () => {
  const { K, window } = await load({ platform: 'MacIntel' });
  assert.strictEqual(K.normalize('Ctrl+Shift+V'), 'ctrl+shift+v');
  assert.strictEqual(K.match(kd(window, { key: 'V', ctrlKey: true, shiftKey: true })).id, 'paste');
  assert.strictEqual(K.match(kd(window, { key: 'V', metaKey: true, shiftKey: true })), null, 'Cmd+Shift+V는 붙여넣기 단축키가 아니다');
});

test('수식키가 모자라면 매칭되지 않는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  assert.strictEqual(K.match(kd(window, { key: 'f' })), null);
  assert.strictEqual(K.match(kd(window, { key: 'f', shiftKey: true })), null, 'Ctrl 없는 Shift+F는 search가 아니다');
  assert.strictEqual(K.match(kd(window, { key: 'k', ctrlKey: true })), null, '비-mac의 Ctrl+K는 셸의 kill-line 몫이다');
});

// ── 실행 · passthrough ────────────────────────────────────────────────────
test('handleKeydown — 등록된 핸들러를 실행하고 기본동작을 막는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  let ran = 0;
  K.register('search', () => ran++);
  const e = kd(window, { key: 'f', ctrlKey: true, shiftKey: true });
  const consumed = K.handleKeydown(e);
  assert.strictEqual(ran, 1);
  assert.strictEqual(consumed, true);
  assert.strictEqual(e.defaultPrevented, true);
});

test('passthrough — 동작은 하되 키를 터미널로 흘린다(검색 키를 셸에 돌려주기)', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  let ran = 0;
  K.register('search', () => ran++);
  await K.setPassthrough('search', true);

  const e = kd(window, { key: 'f', ctrlKey: true, shiftKey: true });
  const consumed = K.handleKeydown(e);
  assert.strictEqual(ran, 1, '동작 자체는 그대로 실행된다');
  assert.strictEqual(consumed, false);
  assert.strictEqual(e.defaultPrevented, false, '터미널이 키를 받아야 하므로 막지 않는다');
});

test('핸들러가 없는 액션은 조용히 통과시킨다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  const e = kd(window, { key: 'b', altKey: true, shiftKey: true });   // railToggle, 핸들러 미등록
  assert.strictEqual(K.handleKeydown(e), false);
  assert.strictEqual(e.defaultPrevented, false);
});

test('핸들러가 예외를 던져도 다음 키 입력이 죽지 않는다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  K.register('search', () => { throw new Error('boom'); });
  assert.strictEqual(K.handleKeydown(kd(window, { key: 'f', ctrlKey: true, shiftKey: true })), true);
});

// ── 재바인딩 · 충돌 ───────────────────────────────────────────────────────
test('setBinding — 재바인딩하면 새 조합으로 매칭된다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  await K.setBinding('search', 'Mod+Shift+G');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true, shiftKey: true })), null, '옛 조합은 더는 안 잡힌다');
  assert.strictEqual(K.match(kd(window, { key: 'g', ctrlKey: true, shiftKey: true })).id, 'search');
});

test('reset — 기본값으로 돌아온다', async () => {
  const { K, window } = await load({ platform: 'Linux' });
  await K.setBinding('search', 'Mod+Shift+G');
  await K.reset('search');
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true, shiftKey: true })).id, 'search');
});

test('conflicts — 기본 바인딩끼리는 충돌이 없다', async () => {
  const { K } = await load();
  assert.deepEqual(K.conflicts(), {});
});

test('conflicts — 같은 조합에 둘을 바인딩하면 잡아낸다', async () => {
  const { K } = await load();
  await K.setBinding('palette', K.list().find((b) => b.id === 'search').combo);
  const c = K.conflicts();
  assert.strictEqual(Object.keys(c).length, 1);
  assert.deepEqual(Object.values(c)[0].sort(), ['palette', 'search']);
});

// ── 브라우저가 먼저 먹는 키 ───────────────────────────────────────────────
test('브라우저가 먼저 먹는 조합은 어느 플랫폼에서도 기본값으로 쓰지 않는다', async () => {
  // `Mod+W`(탭 닫기)뿐 아니라 `Mod+Shift+W`(창 닫기)·`Mod+Shift+N`(시크릿 창)도
  // 일반 탭에서는 우리에게 오지 않는다 — 기본값이 그 위에 있으면 조용히 안 된다.
  for (const platform of ['MacIntel', 'Linux x86_64']) {
    const { K } = await load({ platform });
    for (const b of K.list()) {
      assert.strictEqual(b.unavailable, false, `${platform}: ${b.id}(${b.combo})가 브라우저 예약 위에 있다`);
    }
  }
  const mac = await load({ platform: 'MacIntel' });
  const macCombo = (id) => mac.K.normalize(mac.K.list().find((b) => b.id === id).combo);
  assert.strictEqual(macCombo('paneClose'), 'ctrl+shift+w');
  assert.strictEqual(macCombo('worktreeNew'), 'ctrl+shift+n');
  const linux = await load({ platform: 'Linux' });
  const linuxCombo = (id) => linux.K.normalize(linux.K.list().find((b) => b.id === id).combo);
  assert.strictEqual(linuxCombo('paneClose'), 'alt+shift+w');
  assert.strictEqual(linuxCombo('worktreeNew'), 'alt+shift+n');
});

// 2026-09-18 — 예약 검사가 원문 문자열 비교라, 설정 화면이 저장하는 정규형
// ('mod+shift+w')은 걸러지지 않았다. 사용자가 직접 고른 예약 조합만 경고 없이
// 통과하던 셈이다.
test('재바인딩이 저장하는 정규형 표기도 예약 검사에 걸린다', async () => {
  const { K, window } = await load({ standalone: false, platform: 'MacIntel' });
  // 설정 화면은 comboFromEvent가 만든 소문자 표기를 그대로 저장한다.
  const recorded = K.comboFromEvent(kd(window, { key: 'w', metaKey: true, shiftKey: true }));
  assert.strictEqual(recorded, 'mod+shift+w');
  await K.setBinding('paneClose', recorded);
  assert.strictEqual(K.list().find((x) => x.id === 'paneClose').unavailable, true);
  assert.strictEqual(K.match(kd(window, { key: 'w', metaKey: true, shiftKey: true })), null);
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
  assert.strictEqual(K.match(kd(window, { key: 'f', ctrlKey: true, shiftKey: true })).id, 'search');
});

// 2026-09-18 — 플랫폼별 기본값(defNonMac). 비-mac의 `Mod`는 Ctrl이라,
// mac에서 안전하던 Cmd 조합이 그대로 셸·tmux의 키를 뺏고 있었다.
test('비-mac 기본값은 셸·tmux가 쓰는 Ctrl 키를 하나도 안 가져간다', async () => {
  const { K, window } = await load({ platform: 'Linux x86_64' });
  // readline forward-char / kill-line / EOF, 그리고 tmux prefix.
  for (const key of ['f', 'k', 'd', 'b']) {
    assert.strictEqual(
      K.match(kd(window, { key, ctrlKey: true })), null,
      `Ctrl+${key.toUpperCase()}는 터미널 몫이어야 한다`,
    );
  }
  const combo = (id) => K.normalize(K.list().find((b) => b.id === id).combo);
  assert.strictEqual(combo('search'), 'mod+shift+f');
  assert.strictEqual(combo('palette'), 'mod+shift+k');
  assert.strictEqual(combo('splitRight'), 'mod+shift+d');
  // 브라우저가 먼저 먹는 Ctrl+Shift+W/N을 피해 Alt+Shift로 내려간 것들.
  assert.strictEqual(combo('searchInPane'), 'alt+shift+f');
  assert.strictEqual(combo('splitDown'), 'alt+shift+d');
  assert.strictEqual(combo('paneClose'), 'alt+shift+w');
  assert.strictEqual(combo('railToggle'), 'alt+shift+b');
  assert.strictEqual(combo('worktreeNew'), 'alt+shift+n');
  // 실제로 눌렀을 때도 잡혀야 한다(표기만 바꾸고 매칭이 안 되면 의미가 없다).
  assert.strictEqual(K.match(kd(window, { key: 'B', altKey: true, shiftKey: true })).id, 'railToggle');
  assert.strictEqual(K.match(kd(window, { key: 'K', ctrlKey: true, shiftKey: true })).id, 'palette');
});

test('mac 기본값은 그대로다 — Cmd 조합은 터미널과 겹치지 않는다', async () => {
  const { K, window } = await load({ platform: 'MacIntel' });
  const combo = (id) => K.normalize(K.list().find((b) => b.id === id).combo);
  assert.strictEqual(combo('search'), 'mod+f');
  assert.strictEqual(combo('palette'), 'mod+k');
  assert.strictEqual(combo('splitRight'), 'mod+d');
  assert.strictEqual(combo('railToggle'), 'mod+b');
  assert.strictEqual(K.match(kd(window, { key: 'b', metaKey: true })).id, 'railToggle');
  // mac의 Ctrl+B는 여전히 터미널(tmux prefix) 몫이다.
  assert.strictEqual(K.match(kd(window, { key: 'b', ctrlKey: true })), null);
});

test('사용자가 바꾼 값은 플랫폼 기본값을 이긴다', async () => {
  const { K, window } = await load({ platform: 'Linux x86_64' });
  await K.setBinding('railToggle', 'Ctrl+Shift+L');
  assert.strictEqual(K.match(kd(window, { key: 'B', altKey: true, shiftKey: true })), null);
  assert.strictEqual(K.match(kd(window, { key: 'L', ctrlKey: true, shiftKey: true })).id, 'railToggle');
});
