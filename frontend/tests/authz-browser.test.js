// 설정 「브라우저」 패널 — 어댑터 켜기/끄기(첫 사용 동의), 사용 불가 이유, 탭 소유 목록과 닫기, 기능이 꺼진 서버(404) 안내.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { answerConfirm } = require('./helpers/dialog-stub');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });

const adapter = (name, over = {}) => ({ name, profile: 'isolated', available: true, enabled: false, reason: null, ops: ['open'], ...over });
const tab = (id, over = {}) => ({ id, adapter: 'playwright', state: 'open', owner: 'agent:local/$1:1', ownerName: 'alpha', pair: null, humanTaken: false, lastUsed: Date.now() / 1000 - 5, ...over });

async function mount(adapters, tabs, routes = {}, consented = true) {
  const t = await loadShare({
    'GET /api/browser/adapters': () => ({ json: { enabled: true, consented, consentText: consented ? null : '처음 켭니다', adapters } }),
    'GET /api/browser/tabs': () => ({ json: { tabs } }),
    ...routes,
  }, { files: ['authz/browser.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  const api = t.mods[0].mountBrowser(host);
  return { ...t, host, api, rows: () => [...host.querySelectorAll('.vt-br-adapter')], tabs: () => [...host.querySelectorAll('.vt-br-tab')] };
}

test('어댑터 목록: 켜짐·꺼짐·사용 불가(이유)와 격리 프로필 표시, 사용 불가에는 버튼이 없다', async () => {
  const t = await mount([adapter('playwright', { enabled: true }), adapter('cdp'), adapter('gstack', { available: false, reason: 'not_implemented' })], []);
  await waitFor(() => t.rows().length === 3);
  const [a, b, c] = t.rows();
  assert.match(a.textContent, /playwright.*켜짐/);
  assert.match(a.textContent, /격리/);
  assert.match(b.textContent, /cdp.*꺼짐/);
  assert.match(c.textContent, /사용 불가/);
  assert.match(c.textContent, /아직 구현되지 않았습니다/);
  assert.strictEqual(c.querySelector('.vt-br-toggle'), null);
  assert.match(a.querySelector('.vt-br-toggle').textContent, /끄기/);
  assert.match(b.querySelector('.vt-br-toggle').textContent, /켜기/);
});

test('탭 목록: 소유자·상태·페어, 사람이 이어받은 탭 구분, 닫기(확인 → DELETE → 다시 읽기)', async () => {
  let tabs = [tab('bt-1', { pair: 'pr-x' }), tab('bt-2', { state: 'orphaned' }), tab('bt-3', { owner: null, ownerName: '', humanTaken: true, state: 'released' })];
  const t = await mount([adapter('playwright', { enabled: true })], tabs, {
    'GET /api/browser/tabs': () => ({ json: { tabs } }),
    'DELETE /api/browser/tabs/bt-1': () => { tabs = tabs.filter((x) => x.id !== 'bt-1'); return { json: { ok: true } }; },
  });
  await waitFor(() => t.tabs().length === 3);
  const [a, b, c] = t.tabs();
  assert.match(a.textContent, /alpha.*bt-1/);
  assert.match(a.textContent, /페어 pr-x/);
  assert.match(b.textContent, /끊김/);
  assert.match(c.textContent, /사람이 이어받음/);
  assert.match(c.textContent, /소유권 풀림/);
  a.querySelector('.vt-br-close').click();
  await answerConfirm(t.document, { accept: false });
  await tick(30);
  assert.strictEqual(t.calls.filter((c2) => c2.method === 'DELETE').length, 0, '취소하면 보내지 않는다');
  a.querySelector('.vt-br-close').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => t.tabs().length === 2);
  assert.strictEqual(t.calls.filter((c2) => c2.method === 'DELETE').length, 1);
});

test('처음 켤 때는 동의 문구를 확인받고 consent 를 실어 보낸다. 취소하면 아무것도 안 보낸다', async () => {
  let enabled = false;
  const t = await mount([adapter('playwright')], [], {
    'POST /api/browser/adapters/playwright/enable': () => { enabled = true; return { json: { ok: true } }; },
  }, false);
  await waitFor(() => t.rows().length === 1);
  t.host.querySelector('.vt-br-toggle').click();
  await answerConfirm(t.document, { accept: false });
  await tick(30);
  assert.strictEqual(t.calls.filter((c) => c.method === 'POST').length, 0);
  t.host.querySelector('.vt-br-toggle').click();
  const dlg = await answerConfirm(t.document, { accept: true });
  void dlg;
  await waitFor(() => enabled);
  const post = t.calls.find((c) => c.method === 'POST');
  assert.deepStrictEqual(post.body, { enabled: true, consent: true });
});

test('동의한 뒤에는 consent 없이 켜고 끈다', async () => {
  const t = await mount([adapter('playwright', { enabled: true })], [], { 'POST /api/browser/adapters/playwright/enable': () => ({ json: { ok: true } }) });
  await waitFor(() => t.rows().length === 1);
  t.host.querySelector('.vt-br-toggle').click();
  await waitFor(() => t.calls.some((c) => c.method === 'POST'));
  assert.deepStrictEqual(t.calls.find((c) => c.method === 'POST').body, { enabled: false });
});

test('기능이 꺼진 서버(404)는 오류가 아니라 안내 한 줄', async () => {
  const t = await loadShare({ 'GET /api/browser/adapters': () => ({ status: 404, json: { detail: 'Not Found' } }) }, { files: ['authz/browser.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  t.mods[0].mountBrowser(host);
  await waitFor(() => host.querySelector('[data-disabled]'));
  assert.match(host.textContent, /브라우저 기능이 꺼져 있습니다.*VT_AUTHZ_ENFORCE.*browser/);
  assert.strictEqual(host.querySelector('.vt-authz-err').textContent, '');
});

test('서버 오류는 이유를 보인다', async () => {
  const t = await mount([adapter('cdp')], [], { 'POST /api/browser/adapters/cdp/enable': () => ({ status: 409, json: { error: 'adapter_unavailable', reason: '이 어댑터를 쓸 수 없습니다: browser_binary_not_found' } }) });
  await waitFor(() => t.rows().length === 1);
  t.host.querySelector('.vt-br-toggle').click();
  await answerConfirm(t.document, { accept: true }).catch(() => {});
  await waitFor(() => /browser_binary_not_found/.test(t.host.querySelector('.vt-authz-err').textContent));
});
