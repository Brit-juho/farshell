// 2026-09-28 — term/host-view.js. 헤더 칩·레일 스위처·터미널 칸 세 곳이 "지금
// 어느 호스트를 보나"에 같은 답을 내야 한다(사용자 지적: 스위처는 M5인데 칩과
// 칸은 M1). 판정 규칙 자체를 고정한다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const HOST_VIEW_JS = path.join(__dirname, '../js/term/host-view.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(HOST_VIEW_JS, env.context, new Map());
}

const local = { tmuxName: 'dev' };
const remote = (host, tmux) => ({ tmuxName: tmux, host, remote: { host, tmux, screen: 's' } });

test('세션의 호스트: 원격은 remote.host, 나머지는 로컬, 레코드 없음은 null', async () => {
  const H = await load();
  assert.strictEqual(H.hostOfSession(remote('m5', 'x')), 'm5');
  assert.strictEqual(H.hostOfSession(local), 'local');
  assert.strictEqual(H.hostOfSession(null), null);
});

test('활성 칸의 호스트: 세션이 있으면 세션, 비었으면 탭의 호스트', async () => {
  const H = await load();
  assert.strictEqual(H.viewHost(remote('m5', 'x'), 'local'), 'm5');
  assert.strictEqual(H.viewHost(local, 'm5'), 'local');   // 원격 탭에 로컬 세션을 넣었으면 로컬
  assert.strictEqual(H.viewHost(null, 'm5'), 'm5');       // 스위처가 연 빈 원격 탭
  assert.strictEqual(H.viewHost(null, undefined), 'local');
});

test('호스트의 첫 열린 세션 — 열린 순서대로, 없으면 null', async () => {
  const H = await load();
  const entries = [['a', local], ['b', remote('m5', 'x')], ['c', remote('m5', 'y')]];
  assert.strictEqual(H.firstSessionForHost(entries, 'm5'), 'b');
  assert.strictEqual(H.firstSessionForHost(entries, 'local'), 'a');
  assert.strictEqual(H.firstSessionForHost(entries, 'gpu'), null);
});

test('열린 원격 세션은 id가 아니라 레코드(호스트+tmux 이름)로 찾는다', async () => {
  // 이름을 바꾸면 id(remote:m5:old)는 그대로, remote.tmux만 새 이름이 된다.
  // id로 찾으면 새 이름 행을 눌렀을 때 같은 세션이 탭 두 개로 열린다.
  const H = await load();
  const renamed = remote('m5', 'new');
  const entries = [['remote:m5:old', renamed]];
  assert.strictEqual(H.findRemoteSession(entries, 'm5', 'new'), 'remote:m5:old');
  assert.strictEqual(H.findRemoteSession(entries, 'm5', 'old'), null);
  assert.strictEqual(H.findRemoteSession(entries, 'gpu', 'new'), null);
  assert.strictEqual(H.findRemoteSession([['x', local]], 'm5', 'dev'), null);
});

test('빈 탭 재사용 — 같은 호스트의 빈 탭만, 호스트가 비어 있으면 로컬로 본다', async () => {
  const H = await load();
  const tabs = [
    { id: 't1', hostId: 'local', empty: true },
    { id: 't2', hostId: 'm5', empty: false },
    { id: 't3', hostId: 'm5', empty: true },
    { id: 't4', hostId: undefined, empty: true },
  ];
  assert.strictEqual(H.emptyTabForHost(tabs, 'm5'), 't3');
  assert.strictEqual(H.emptyTabForHost(tabs, 'local'), 't1');
  assert.strictEqual(H.emptyTabForHost(tabs.slice(1, 2), 'm5'), null);
  assert.strictEqual(H.emptyTabForHost([{ id: 'x', empty: true }], 'local'), 'x');
});
