// 세션 ID와 별칭(사람이 보는 이름) — core/store.js의 호스트별 별칭 지도와, 그 지도를 읽는 표시 지점들.
// 고정하는 것: 지도 키는 호스트+ID(원격 세션에 로컬 별칭이 붙지 않는다), 별칭을 모르는 행은 이전 별칭을 유지하고
// 목록에서 사라진 세션만 지운다, sessionDisplayName은 세션이 사는 맥의 별칭, 큐·음성 목록에 `별칭 (ID)` 표기가 없다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const J = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(extra = []) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  const cache = new Map();
  const core = await importFresh(J('core/store.js'), env.context, cache);
  const mods = {};
  for (const f of extra) mods[f] = await importFresh(J(f), env.context, cache);
  return { w: env.window, core, mods };
}

test('지도 키는 호스트+ID — 원격 세션에는 같은 ID의 로컬 별칭이 붙지 않는다', async () => {
  const { core } = await load();
  core.setSessionAliases([{ name: 's-1', alias: 'farshell' }]);
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [{ name: 's-1' }] }]);   // 옛 서버 — alias 없음
  assert.strictEqual(core.sessionAlias('s-1'), 'farshell');
  assert.strictEqual(core.sessionAlias('s-1', 'local'), 'farshell');
  assert.strictEqual(core.sessionAlias('s-1', 'm5'), 's-1', '원격은 그 맥의 별칭만(모르면 ID)');
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [{ name: 's-1', alias: 'gpu 작업' }] }]);
  assert.strictEqual(core.sessionAlias('s-1', 'm5'), 'gpu 작업');
  assert.strictEqual(core.sessionAlias('s-1'), 'farshell', '원격 갱신이 로컬을 건드리지 않는다');
  // 로컬 목록 갱신도 원격 항목을 지우지 않는다.
  core.setSessionAliases([]);
  assert.strictEqual(core.sessionAlias('s-1'), 's-1');
  assert.strictEqual(core.sessionAlias('s-1', 'm5'), 'gpu 작업');
});

test('별칭을 모르는 행(기록을 잠깐 못 읽음)은 이전 별칭을 유지하고, 목록에서 사라진 세션만 지운다', async () => {
  const { core } = await load();
  core.setSessionAliases([{ name: 's-1', alias: '배포' }, { name: 's-2', alias: 'api' }]);
  core.setSessionAliases([{ name: 's-1', alias: null, catalog: null }, { name: 's-2', alias: null, catalog: null }]);
  assert.strictEqual(core.sessionAlias('s-1'), '배포');
  assert.strictEqual(core.sessionAlias('s-2'), 'api');
  core.setSessionAliases([{ name: 's-1', alias: null, catalog: null }]);
  assert.strictEqual(core.sessionAlias('s-1'), '배포');
  assert.strictEqual(core.sessionAlias('s-2'), 's-2', '사라진 세션은 지운다');
  // 배열이 아니면(실패 응답) 아무것도 지우지 않는다.
  core.setSessionAliases(null);
  assert.strictEqual(core.sessionAlias('s-1'), '배포');
  // 그 전 단계 서버의 catalog.title도 읽는다. 제어 문자는 공백으로.
  core.setSessionAliases([{ name: 's-1', catalog: { title: '새\n이름' } }]);
  assert.strictEqual(core.sessionAlias('s-1'), '새 이름');
});

test('연결이 끊긴 호스트(빈 목록)와 로컬 요약 항목은 원격 지도를 건드리지 않는다', async () => {
  const { core } = await load();
  core.setSessionAliases([{ name: 'dev', alias: '로컬 dev' }]);
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [{ name: 'dev', alias: '원격 dev' }] }]);
  core.setHostSessionAliases([
    { id: 'local', online: true, sessions: [{ name: 'dev' }] },
    { id: 'm5', online: false, reason: 'timeout', sessions: [] },
  ]);
  assert.strictEqual(core.sessionAlias('dev'), '로컬 dev');
  assert.strictEqual(core.sessionAlias('dev', 'm5'), '원격 dev');
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [] }]);
  assert.strictEqual(core.sessionAlias('dev', 'm5'), 'dev', '연결된 상태의 빈 목록이면 지운다');
});

test('setSessionAlias(방금 바꾼 별칭)는 그 호스트 항목만 고친다, 빈 값은 지운다', async () => {
  const { core, w } = await load();
  core.setSessionAlias('s-1', '처음');
  core.setSessionAlias('s-1', '원격', 'm5');
  assert.strictEqual(core.sessionAlias('s-1'), '처음');
  assert.strictEqual(core.sessionAlias('s-1', 'm5'), '원격');
  core.setSessionAlias('s-1', '');
  assert.strictEqual(core.sessionAlias('s-1'), 's-1');
  assert.strictEqual(typeof w.sessionAlias, 'function', 'voice.js·지연 청크용 window 브리지');
  assert.strictEqual(w.sessionAlias('s-1', 'm5'), '원격');
});

test('sessionDisplayName은 세션이 사는 맥의 별칭을 쓰고, 별칭이 바뀌면 구독자에게 알린다', async () => {
  const { core } = await load();
  core.registerSession('web-1', { tmuxName: 's-1' });
  core.registerSession('remote:m5:s-1', { tmuxName: 's-1', host: 'm5', remote: { host: 'm5', tmux: 's-1' } });
  assert.strictEqual(core.sessionDisplayName('web-1'), 's-1', '모르면 ID');
  let calls = 0;
  core.subscribe(() => { calls += 1; });
  core.setSessionAliases([{ name: 's-1', alias: 'farshell' }]);
  assert.strictEqual(calls, 1);
  core.setSessionAliases([{ name: 's-1', alias: 'farshell' }]);
  assert.strictEqual(calls, 1, '같은 값이면 깨우지 않는다');
  assert.strictEqual(core.sessionDisplayName('web-1'), 'farshell');
  assert.strictEqual(core.sessionDisplayName('remote:m5:s-1'), 's-1', '원격 세션에 로컬 별칭이 붙지 않는다');
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [{ name: 's-1', alias: 'gpu' }] }]);
  assert.strictEqual(core.sessionDisplayName('remote:m5:s-1'), 'gpu');
});

test('큐 — 대상 목록 글자는 별칭(ID를 덧붙이지 않는다), 값은 ID, 항목 칩도 별칭', async () => {
  const { w, core, mods } = await load(['queue.js']);
  const q = mods['queue.js'];
  const sel = w.document.createElement('select');
  q.appendLocalTargets(sel, [{ name: 's-1', alias: '배포' }, { name: 's-2', alias: null, catalog: null }, { name: '' }]);
  const opts = [...sel.options].map((o) => [o.value, o.textContent]);
  assert.strictEqual(JSON.stringify(opts), JSON.stringify([['s-1', '배포'], ['s-2', 's-2']]));
  assert.strictEqual(core.sessionAlias('s-1'), '배포', '목록을 공용 지도에도 싣는다');
  const local = q.targetChipText('s-1', null);
  assert.strictEqual(local.text, '배포');
  assert.strictEqual(local.tip, '대상 세션: 배포');
  core.setHostSessionAliases([{ id: 'm5', online: true, sessions: [{ name: 's-1', alias: 'gpu' }] }]);
  const remote = q.targetChipText('s-1', 'm5');
  assert.strictEqual(remote.text, 'gpu · m5');
  assert.strictEqual(remote.tip, '대상: m5 호스트의 gpu 세션');
});

test('음성 고정 — 목록 글자는 공용 지도의 별칭, `별칭 (ID)` 표기가 없다', async () => {
  const { w, core, mods } = await load(['voice/target-pin.js']);
  const pin = mods['voice/target-pin.js'];
  w.sessions = { 'web-a': { tmuxName: 's-1' }, 'web-b': { tmuxName: 's-2' }, 'remote:m5:s-3': { tmuxName: 's-3', remote: { host: 'm5', tmux: 's-3' } } };
  core.setSessionAliases([{ name: 's-1', alias: '배포' }, { name: 's-2', alias: 'api' }]);
  assert.strictEqual(pin.pinLabel('s-1'), '배포');
  assert.strictEqual(pin.pinLabel('s-9'), 's-9', '모르면 ID');
  const sel = w.document.createElement('select');
  pin.fillPinSelect(sel);
  const opts = [...sel.options].map((o) => [o.value, o.textContent]);
  assert.strictEqual(JSON.stringify(opts.slice(1)), JSON.stringify([['s-1', '배포'], ['s-2', 'api']]), '원격 세션은 후보가 아니다');
  assert.ok(opts.every(([, label]) => !/\(s-\d\)/.test(label)));
});

test('권한 요청함의 세션 이름은 별칭', async () => {
  const { core, mods } = await load(['authz/data.js']);
  core.setSessionAliases([{ name: 's-1', alias: '배포' }]);
  const a = mods['authz/data.js'];
  assert.strictEqual(a.requester({ subject: { kind: 'agent', name: 's-1' } }), '배포');
  assert.strictEqual(a.requester({ subject: { kind: 'peer', name: 's-1' } }), 's-1', '다른 맥 요청자는 이 맥 지도로 바꾸지 않는다');
  assert.strictEqual(a.resourceText({ kind: 'generic', resource: { id: 'session:local/s-1', label: 's-1' } }), '배포');
  assert.strictEqual(a.resourceText({ kind: 'share', resource: { id: 'session:local/x:1', label: 's-1' } }), 's-1', '공유 닉네임은 그대로');
  assert.strictEqual(a.resourceText({ kind: 'generic', resource: { id: 'session:local/s-1', label: '이미 별칭' } }), '이미 별칭');
});
