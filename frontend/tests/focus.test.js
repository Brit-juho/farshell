// 리디자인 7단계 — 데스크톱 집중 모드.
// 1부: core 스위치(layout/focus.js) — `<html class="vt-focus">`, 숨은 막대의 inert, 저장하지 않는 `ui.focus.active`,
//      `focus.toggle` 액션, 키맵 `focusMode`(mac ⌘. · 그 밖 Alt+Shift+. 물리 키), 사용자 재지정 보존.
// 2부: 설정 색인 — 모양 › 집중 모드 네 줄(팔레트 `>`와 설정 화면이 같은 정의를 읽는다).
// 3부: 떠 있는 층(shell/Focus.tsx) — 개입 알약 수 · 이동, 가장자리 노출(드러난 쪽만 inert 해제 · 400ms 뒤 닫힘 · Esc),
//      들어올 때 안내 횟수, 설정으로 끄기, 마우스 없는 기기의 끝내기 버튼.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { loadSolidBuild } = require('./helpers/solid-env');

const FOCUS_JS = path.join(__dirname, '../js/layout/focus.js');
const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');
const KEYMAP_JS = path.join(__dirname, '../js/core/keymap.js');
const DOM_JS = path.join(__dirname, '../js/core/dom.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const CHROME = '<header id="vt-titlebar"><button id="tb-btn">x</button></header><div id="vt-workbench-slot"><div id="vt-workbench"><button id="wb-btn">s</button></div></div><footer id="vt-hud"></footer>';

async function loadCore({ platform = 'MacIntel' } = {}) {
  const env = createDomEnv(`<!doctype html><html><body>${CHROME}</body></html>`);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  const puts = [];
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'PUT') puts.push({ url: String(url), body: String(opts.body || '') });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  Object.defineProperty(window.navigator, 'platform', { value: platform, configurable: true });
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  const S = await importFresh(SETTINGS_JS, env.context, cache);
  const K = await importFresh(KEYMAP_JS, env.context, cache);
  const D = await importFresh(DOM_JS, env.context, cache);
  const F = await importFresh(FOCUS_JS, env.context, cache);
  return { window, document: window.document, S, K, D, F, puts };
}

const kd = (window, opts) => new window.KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...opts });
const tick = (w, ms = 0) => new Promise((r) => w.setTimeout(r, ms));

// ── 1부: core 스위치 ─────────────────────────────────────────────────────

test('focus.toggle — html.vt-focus를 켜고 끄며, 숨은 막대 셋에 inert를 걸고 푼다', async () => {
  const { window, document, D, F } = await loadCore();
  const root = document.documentElement;
  const events = [];
  window.addEventListener(F.FOCUS_EVENT, (e) => events.push(e.detail.on));
  assert.strictEqual(root.classList.contains('vt-focus'), false);
  D.getAction('focus.toggle')();
  assert.strictEqual(root.classList.contains('vt-focus'), true);
  for (const id of F.FOCUS_CHROME_IDS) {
    assert.strictEqual(document.getElementById(id).hasAttribute('inert'), true, `${id}는 inert`);
  }
  assert.strictEqual(F.isFocusActive(), true);
  D.getAction('focus.toggle')();
  assert.strictEqual(root.classList.contains('vt-focus'), false);
  for (const id of F.FOCUS_CHROME_IDS) assert.strictEqual(document.getElementById(id).hasAttribute('inert'), false);
  assert.deepStrictEqual(events, [true, false], '바뀔 때마다 한 번씩 알린다');
  D.getAction('focus.exit')();
  assert.deepStrictEqual(events, [true, false], '이미 꺼져 있으면 focus.exit은 아무것도 하지 않는다');
});

test('끌 때 드러난 쪽 클래스도 함께 지운다', async () => {
  const { document, F } = await loadCore();
  F.toggleFocus(true);
  document.documentElement.classList.add('vt-reveal-left');
  F.toggleFocus(false);
  assert.strictEqual(document.documentElement.className.includes('vt-reveal'), false);
});

test('ui.focus.active는 저장하지 않는다 — 서버 PUT도 로컬 캐시도 없고, 나머지 집중 모드 키는 기기 설정이다', async () => {
  const { window, S, F, puts } = await loadCore();
  F.toggleFocus(true);
  await tick(window, 10);
  assert.strictEqual(S.get('ui.focus.active'), true);
  assert.strictEqual(puts.length, 0, `저장 요청이 나갔다: ${JSON.stringify(puts)}`);
  const cached = JSON.parse(window.localStorage.getItem('vt-settings-device-v1') || '{}');
  assert.strictEqual('ui.focus.active' in cached, false);
  for (const k of ['ui.focus.active', 'ui.focus.edgeReveal', 'ui.focus.pill', 'ui.focus.edgeDelay', 'ui.focus.hintCount']) {
    assert.strictEqual(S.scopeOf(k), 'device', `${k}는 이 기기`);
  }
  await S.set('ui.focus.edgeDelay', '500');
  assert.ok(puts.some((p) => p.url.includes('/api/device-settings') && p.body.includes('ui.focus.edgeDelay')), '반응 시간은 기기 설정으로 저장');
  assert.strictEqual(S.get('ui.focus.edgeReveal'), true);
  assert.strictEqual(S.get('ui.focus.pill'), true);
  assert.strictEqual(S.get('ui.focus.edgeDelay'), '500');
  assert.strictEqual(S.get('ui.focus.hintCount'), 0);
});

test('서버에 ui.focus.active가 남아 있어도 받지 않는다(새로고침하면 늘 꺼진 채 시작)', async () => {
  const env = createDomEnv(`<!doctype html><html><body>${CHROME}</body></html>`);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ settings: { 'ui.focus.active': true, 'ui.focus.pill': false } }) });
  const cache = new Map();
  const S = await importFresh(SETTINGS_JS, env.context, cache);
  await S.load();
  assert.strictEqual(S.get('ui.focus.active'), false);
  assert.strictEqual(S.get('ui.focus.pill'), false, '저장하는 키는 서버 값을 받는다');
});

test('켤 때 숨은 막대 안에 키보드 초점이 있으면 그 자리에 남기지 않는다', async () => {
  const { document, F } = await loadCore();
  const btn = document.getElementById('wb-btn');
  btn.focus();
  assert.strictEqual(document.activeElement, btn);
  F.toggleFocus(true);
  assert.notStrictEqual(document.activeElement, btn, 'inert 안의 초점은 풀린다');
});

test('지연 UI 로더는 처음 켤 때 한 번만 부른다', async () => {
  const { window, F } = await loadCore();
  let calls = 0;
  F.setFocusUiLoader(() => { calls += 1; return Promise.resolve(); });
  assert.strictEqual(calls, 0, '켜기 전에는 부르지 않는다(초기 페이로드·지연 청크를 건드리지 않는다)');
  F.toggleFocus(true); F.toggleFocus(false); F.toggleFocus(true);
  await tick(window, 5);
  assert.strictEqual(calls, 1);
});

test('키맵 focusMode — mac ⌘. · 그 밖 Alt+Shift+.(Shift로 `>`가 돼도 물리 키로 잡는다), 충돌 없음', async () => {
  for (const platform of ['MacIntel', 'Linux x86_64']) {
    const { window, document, K } = await loadCore({ platform });
    const b = K.getBinding('focusMode');
    assert.ok(b, platform);
    assert.strictEqual(K.normalize(b.combo), platform === 'MacIntel' ? 'mod+.' : 'alt+shift+.');
    assert.strictEqual(b.keyKind, 'code');
    assert.deepStrictEqual(Object.keys(K.conflicts()), [], `${platform}: 충돌`);
    const e = platform === 'MacIntel'
      ? kd(window, { key: '.', code: 'Period', metaKey: true })
      : kd(window, { key: '>', code: 'Period', altKey: true, shiftKey: true });
    assert.strictEqual(K.match(e)?.id, 'focusMode', `${platform}: 매칭`);
    // 실제 키 입력이 집중 모드를 켠다(라우터 → 키맵 → focus.js의 register).
    document.body.dispatchEvent(platform === 'MacIntel'
      ? kd(window, { key: '.', code: 'Period', metaKey: true })
      : kd(window, { key: '>', code: 'Period', altKey: true, shiftKey: true }));
    assert.strictEqual(document.documentElement.classList.contains('vt-focus'), true, `${platform}: 키로 켜짐`);
    // 비-mac의 Ctrl+.은 터미널 몫으로 남는다.
    if (platform !== 'MacIntel') assert.strictEqual(K.match(kd(window, { key: '.', code: 'Period', ctrlKey: true })), null);
  }
});

test('키맵 focusMode — 사용자가 정한 조합은 그대로 이긴다(재지정 보존)', async () => {
  const { window, K } = await loadCore({ platform: 'MacIntel' });
  await K.setBinding('focusMode', 'Ctrl+Shift+Z');
  const b = K.getBinding('focusMode');
  assert.strictEqual(K.normalize(b.combo), 'ctrl+shift+z');
  assert.strictEqual(K.match(kd(window, { key: 'Z', code: 'KeyZ', ctrlKey: true, shiftKey: true }))?.id, 'focusMode');
  assert.strictEqual(K.match(kd(window, { key: '.', code: 'Period', metaKey: true })), null, '옛 기본 조합은 더 이상 아니다');
  await K.reset('focusMode');
  assert.strictEqual(K.normalize(K.getBinding('focusMode').combo), 'mod+.');
});

test('설정 화면에서 ⇧.를 캡처해도 같은 키로 맞는다(물리 키 `.`)', async () => {
  const { window, K } = await loadCore({ platform: 'Linux x86_64' });
  const captured = K.comboFromEvent(kd(window, { key: '>', code: 'Period', altKey: true, shiftKey: true }));
  assert.strictEqual(captured, 'alt+shift+.');
  await K.setBinding('focusMode', captured);
  assert.strictEqual(K.getBinding('focusMode').keyKind, 'code');
});

// ── 2부: 설정 색인 ───────────────────────────────────────────────────────

test('설정 색인 — 모양 › 집중 모드 네 줄이 팔레트 `>`에 그 자리에서 바꿀 수 있는 줄로 나온다', async () => {
  const env = await loadSolidBuild();
  _doms.push(env.dom);
  const keys = [...env.FOCUS_ITEMS].map((i) => i.key);
  assert.deepStrictEqual(keys, ['ui.focus.active', 'ui.focus.edgeReveal', 'ui.focus.pill', 'ui.focus.edgeDelay']);
  for (const k of keys) assert.ok(env.SETTINGS_SCHEMA[k], `${k}가 스키마에 있다`);
  const entries = env.buildSettingEntries(null).filter((e) => keys.includes(e.key));
  assert.strictEqual(entries.length, 4);
  for (const e of entries) {
    assert.strictEqual(e.section, 'appearance');
    assert.match(e.path, /모양 › 집중 모드/);
  }
  const delay = entries.find((e) => e.key === 'ui.focus.edgeDelay');
  assert.strictEqual(env.stepSettingValue(delay, '250', 1), '500');
  assert.strictEqual(env.stepSettingValue(delay, '500', 1), '0');
  assert.strictEqual(env.formatSettingValue(delay, '0'), '바로');
  assert.ok(env.buildSettingEntries(null).some((e) => e.label === '집중 모드' && e.kind === 'bool'));
  assert.ok(env.matchSettingEntry(entries[0], 'zen'), '별칭으로도 찾는다');
  // 모양 섹션이 안 보이는 곳(섹션 목록에서 뺀 경우)에서는 나오지 않는다.
  assert.strictEqual(env.buildSettingEntries(['terminal']).some((e) => keys.includes(e.key)), false);
});

// ── 3부: 떠 있는 층(Focus.tsx) ───────────────────────────────────────────

function focusDeps(env, over = {}) {
  const calls = [];
  const settings = { 'ui.focus.edgeReveal': true, 'ui.focus.pill': true, 'ui.focus.edgeDelay': '0', 'ui.focus.hintCount': 0, ...(over.settings || {}) };
  const subs = new Set();
  const agentSubs = new Set();
  let counts = over.counts || { waiting: 0, working: 0, done: 0 };
  let first = over.first || null;
  let active = over.active ?? true;
  const deps = {
    getAction: (n) => () => calls.push(n),
    keyHint: (id) => (id === 'focusMode' ? '⌘.' : ''),
    agentCounts: () => counts,
    subscribeAgents: (fn) => { agentSubs.add(fn); return () => agentSubs.delete(fn); },
    oldestWaiting: () => first,
    active: () => active,
    focusEvent: 'vt:focus-change',
    getSetting: (k) => settings[k],
    setSetting: (k, v) => { settings[k] = v; for (const fn of subs) fn({ [k]: v }); },
    subscribeSettings: (fn) => { subs.add(fn); return () => subs.delete(fn); },
  };
  return {
    deps, calls, settings,
    setAgents(c, f) { counts = c; first = f; for (const fn of agentSubs) fn(); },
    setActive(on) {
      active = on;
      const root = env.document.documentElement;
      root.classList.toggle('vt-focus', on);
      for (const id of ['vt-workbench-slot', 'vt-titlebar', 'vt-hud']) {
        const el = env.document.getElementById(id);
        el.inert = on; if (on) el.setAttribute('inert', ''); else el.removeAttribute('inert');
      }
      env.window.dispatchEvent(new env.window.CustomEvent('vt:focus-change', { detail: { on } }));
    },
  };
}

async function freshLayer(over = {}) {
  const env = await loadSolidBuild();
  _doms.push(env.dom);
  env.document.body.innerHTML = `${CHROME}<div id="vt-wgrail"><div role="button" data-state="idle" class="row"><span class="name">a</span><button class="more">⋯</button></div></div><div id="host"></div>`;
  if (over.hover === false) env.window.matchMedia = (q) => ({ matches: !q.includes('hover: hover'), addEventListener() {}, removeEventListener() {} });
  else env.window.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
  const h = focusDeps(env, over);
  if (h.deps.active()) h.setActive(true);
  env.mountFocus(env.document.getElementById('host'), h.deps);
  await tick(env.window, 20);
  return { env, ...h };
}

const raf = (w) => new Promise((r) => w.requestAnimationFrame(() => r()));

test('개입 알약 — waiting이 없으면 숨고, 생기면 「개입 N · 세션 · 종류」, 누르면 agents.jump · 끝내기는 focus.exit', async () => {
  const { env, calls, setAgents } = await freshLayer({ settings: { 'ui.focus.hintCount': 3 } });
  const { document, window } = env;
  assert.strictEqual(document.querySelector('.vt-focus-pill'), null, 'waiting 0이면 알약 없음');
  setAgents({ waiting: 2, working: 1, done: 0 }, { tmuxName: 'fsh-dev', sessionId: 's1', name: 'sample-dev', kind: 'approval' });
  await raf(window); await tick(window, 5);
  const pill = document.querySelector('.vt-focus-pill');
  assert.ok(pill);
  assert.strictEqual(pill.querySelector('.vt-focus-pill-count').textContent, '개입 2');
  assert.strictEqual(pill.querySelector('.vt-focus-pill-name').textContent, 'sample-dev');
  assert.strictEqual(pill.querySelector('.vt-focus-pill-kind').textContent, '승인 요청');
  assert.match(pill.querySelector('.vt-focus-pill-exit').textContent, /⌘\.끝내기/);
  pill.querySelector('.vt-focus-pill-go').click();
  pill.querySelector('.vt-focus-pill-exit').click();
  assert.deepStrictEqual(calls, ['agents.jump', 'focus.exit']);
  setAgents({ waiting: 0, working: 1, done: 0 }, null);
  await raf(window); await tick(window, 5);
  assert.strictEqual(document.querySelector('.vt-focus-pill'), null, '다 처리하면 사라진다');
});

test('개입 알약 — 설정으로 끄면 waiting이 있어도 숨는다', async () => {
  const h = await freshLayer({ counts: { waiting: 1, working: 0, done: 0 }, settings: { 'ui.focus.hintCount': 3 } });
  const { document } = h.env;
  assert.ok(document.querySelector('.vt-focus-pill'));
  h.deps.setSetting('ui.focus.pill', false);
  await tick(h.env.window, 5);
  assert.strictEqual(document.querySelector('.vt-focus-pill'), null);
});

test('가장자리 노출 — 드러난 쪽만 inert를 풀고 클래스를 붙인다. 밖으로 나가면 400ms 뒤, Esc면 바로 닫힌다', async () => {
  const { env } = await freshLayer({ settings: { 'ui.focus.hintCount': 3 } });
  const { document, window } = env;
  const root = document.documentElement;
  const edges = [...document.querySelectorAll('.vt-focus-edge')].map((e) => e.dataset.edge);
  assert.deepStrictEqual(edges, ['left', 'top', 'bottom'], '오른쪽 가장자리는 쓰지 않는다');
  const left = document.querySelector('.vt-focus-edge[data-edge="left"]');
  left.dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 5);
  assert.ok(root.classList.contains('vt-reveal-left'));
  assert.strictEqual(document.getElementById('vt-workbench-slot').hasAttribute('inert'), false, '드러난 작업대는 누를 수 있다');
  assert.strictEqual(document.getElementById('vt-titlebar').hasAttribute('inert'), true, '다른 막대는 그대로 inert');
  assert.ok(document.querySelector('.vt-focus-scrim'), '왼쪽 노출은 뒤를 어둡게 한다');
  // 막대 안에서 움직이면 열린 채로.
  document.getElementById('wb-btn').dispatchEvent(new window.PointerEvent('pointermove', { bubbles: true }));
  await tick(window, 450);
  assert.ok(root.classList.contains('vt-reveal-left'));
  // 밖(뒤 막)으로 나가면 400ms 뒤 닫힌다.
  document.querySelector('.vt-focus-scrim').dispatchEvent(new window.PointerEvent('pointermove', { bubbles: true }));
  await tick(window, 200);
  assert.ok(root.classList.contains('vt-reveal-left'), '400ms 전에는 아직 열려 있다');
  await tick(window, 300);
  assert.strictEqual(root.classList.contains('vt-reveal-left'), false);
  assert.strictEqual(document.getElementById('vt-workbench-slot').hasAttribute('inert'), true, '닫히면 다시 inert');
  // 위 가장자리 → 제목 막대, Esc로 바로 닫힘.
  document.querySelector('.vt-focus-edge[data-edge="top"]').dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 5);
  assert.ok(root.classList.contains('vt-reveal-top'));
  assert.strictEqual(document.querySelector('.vt-focus-scrim'), null, '위 · 아래는 어둡게 하지 않는다');
  window.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await tick(window, 5);
  assert.strictEqual(root.classList.contains('vt-reveal-top'), false);
});

test('가장자리 노출 — 반응 시간 전에 띠를 벗어나면 열리지 않는다(250ms)', async () => {
  const { env } = await freshLayer({ settings: { 'ui.focus.edgeDelay': '250', 'ui.focus.hintCount': 3 } });
  const { document, window } = env;
  const bottom = document.querySelector('.vt-focus-edge[data-edge="bottom"]');
  bottom.dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 100);
  bottom.dispatchEvent(new window.PointerEvent('pointerleave'));
  await tick(window, 250);
  assert.strictEqual(document.documentElement.classList.contains('vt-reveal-bottom'), false);
  bottom.dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 300);
  assert.ok(document.documentElement.classList.contains('vt-reveal-bottom'), '머무르면 열린다');
  assert.strictEqual(document.getElementById('vt-hud').hasAttribute('inert'), false);
});

test('왼쪽 노출에서 세션 행을 고르면 바로 닫힌다(행 안의 ⋯ 버튼은 아니다)', async () => {
  const { env } = await freshLayer({ settings: { 'ui.focus.hintCount': 3 } });
  const { document, window } = env;
  document.querySelector('.vt-focus-edge[data-edge="left"]').dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 5);
  document.querySelector('#vt-wgrail .more').click();
  await tick(window, 5);
  assert.ok(document.documentElement.classList.contains('vt-reveal-left'), '⋯은 메뉴를 여는 것이라 닫지 않는다');
  document.querySelector('#vt-wgrail .name').click();
  await tick(window, 5);
  assert.strictEqual(document.documentElement.classList.contains('vt-reveal-left'), false);
});

test('가장자리에 막대 띄우기를 끄면 감지 띠가 없다', async () => {
  const { env } = await freshLayer({ settings: { 'ui.focus.edgeReveal': false, 'ui.focus.hintCount': 3 } });
  assert.strictEqual(env.document.querySelectorAll('.vt-focus-edge').length, 0);
});

test('들어올 때 안내 — 세 번까지만 보이고 횟수를 센다. 끄면 사라진다', async () => {
  const h = await freshLayer({ settings: { 'ui.focus.hintCount': 2 } });
  const { document } = h.env;
  const hint = document.querySelector('.vt-focus-hint');
  assert.ok(hint, '세 번째 진입이라 보인다');
  assert.match(hint.textContent, /가장자리에 마우스를 대면 막대가 나타납니다/);
  assert.match(hint.textContent, /⌘\.끝내기/);
  assert.strictEqual(h.settings['ui.focus.hintCount'], 3);
  h.setActive(false);
  await tick(h.env.window, 5);
  assert.strictEqual(document.querySelector('.vt-focus-hint'), null);
  h.setActive(true);
  await tick(h.env.window, 5);
  assert.strictEqual(document.querySelector('.vt-focus-hint'), null, '세 번 본 뒤에는 생략');
  assert.strictEqual(h.settings['ui.focus.hintCount'], 3);
});

test('마우스가 없는 기기 — 가장자리 띠 대신 끝내기 버튼이 늘 있다', async () => {
  const { env, calls } = await freshLayer({ hover: false, settings: { 'ui.focus.hintCount': 3 } });
  const { document } = env;
  assert.strictEqual(document.querySelectorAll('.vt-focus-edge').length, 0);
  const exit = document.querySelector('.vt-focus-exit');
  assert.ok(exit);
  exit.click();
  assert.deepStrictEqual(calls, ['focus.exit']);
});

test('꺼져 있을 때는 아무것도 그리지 않고, 끄면 드러난 막대도 닫는다', async () => {
  const h = await freshLayer({ counts: { waiting: 1, working: 0, done: 0 }, settings: { 'ui.focus.hintCount': 3 } });
  const { document, window } = h.env;
  document.querySelector('.vt-focus-edge[data-edge="top"]').dispatchEvent(new window.PointerEvent('pointerenter'));
  await tick(window, 5);
  assert.ok(document.documentElement.classList.contains('vt-reveal-top'));
  h.setActive(false);
  await tick(window, 5);
  assert.strictEqual(document.documentElement.className.includes('vt-reveal'), false);
  assert.strictEqual(document.querySelector('#host').children.length, 0);
  for (const id of ['vt-workbench-slot', 'vt-titlebar', 'vt-hud']) {
    assert.strictEqual(document.getElementById(id).hasAttribute('inert'), false, `${id}: 끈 뒤 inert를 되살리지 않는다`);
  }
});

test('순수 함수 — 반응 시간 · 안내 횟수 · 「안」 판정', async () => {
  const env = await loadSolidBuild();
  _doms.push(env.dom);
  assert.strictEqual(env.edgeDelayMs('0'), 0);
  assert.strictEqual(env.edgeDelayMs('500'), 500);
  assert.strictEqual(env.edgeDelayMs('nope'), 250);
  assert.strictEqual(env.shouldShowHint(0), true);
  assert.strictEqual(env.shouldShowHint(3), false);
  env.document.body.innerHTML = '<div id="c"><span id="in"></span></div><div class="vt-mb-pop"><button id="m"></button></div><p id="out"></p>';
  const c = env.document.getElementById('c');
  assert.strictEqual(env.insideReveal(env.document.getElementById('in'), c), true);
  assert.strictEqual(env.insideReveal(env.document.getElementById('m'), c), true, 'Portal 메뉴 안도 안이다');
  assert.strictEqual(env.insideReveal(env.document.getElementById('out'), c), false);
  assert.deepStrictEqual({ ...env.EDGE_CHROME }, { left: 'vt-workbench-slot', top: 'vt-titlebar', bottom: 'vt-hud' });
});
