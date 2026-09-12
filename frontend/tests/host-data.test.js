// C1(80-multihost-agents.md §1) — 호스트 스위처 순수 로직(shell/host-data.ts).
// 계약: 로컬이 항상 첫 항목(서버 순서를 다시 정렬하지 않는다) · 등록이 풀린
// id는 로컬로 되돌아간다 · **오프라인은 되돌아가지 않는다** · 원격 행은
// 로컬 세션 id와 절대 겹치지 않는 합성 키를 쓴다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

let _mod = null;
async function mod() {
  if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); }
  return _mod;
}

const local = (over) => ({ id: 'local', label: 'macbook', online: true, sessions: [], ...over });
const peer = (over) => ({ id: 'gpu-box', label: 'gpu-box', online: true, latencyMs: 12, sessions: [], ...over });

test('메뉴는 서버가 준 순서를 그대로 유지한다 (로컬이 첫 항목)', async () => {
  const { buildHostMenu } = await mod();
  const items = buildHostMenu([local(), peer()], 'local');
  assert.deepStrictEqual(items.map((i) => i.id), ['local', 'gpu-box']);
  assert.strictEqual(items[0].active, true);
  assert.strictEqual(items[1].active, false);
});

test('detail: 온라인은 세션 수와 지연, 로컬은 지연을 안 붙인다', async () => {
  const { hostDetail } = await mod();
  assert.strictEqual(hostDetail(local({ sessions: [{ name: 'dev' }, { name: 'srv' }] })), '세션 2');
  assert.strictEqual(hostDetail(peer({ sessions: [{ name: 'dev' }] })), '세션 1 · 12ms');
});

test('오프라인 호스트는 이유를 사람 말로 보여준다', async () => {
  const { hostDetail, reasonLabel } = await mod();
  assert.strictEqual(hostDetail(peer({ online: false, reason: 'timeout' })), '응답 없음');
  assert.strictEqual(hostDetail(peer({ online: false, reason: undefined })), '연결 안 됨');
  // 모르는 이유도 삼키지 않는다 — 원문을 붙여 보여준다.
  assert.strictEqual(reasonLabel('teapot'), '연결 안 됨 · teapot');
});

test('등록이 풀린 호스트를 가리키면 로컬로 되돌린다', async () => {
  const { resolveActiveHost } = await mod();
  assert.strictEqual(resolveActiveHost([local(), peer()], 'gone'), 'local');
  assert.strictEqual(resolveActiveHost([local(), peer()], 'gpu-box'), 'gpu-box');
  assert.strictEqual(resolveActiveHost([], ''), 'local');
});

test('오프라인이라는 이유로는 되돌리지 않는다 (사용자의 선택이다)', async () => {
  const { resolveActiveHost, buildHostMenu } = await mod();
  const hosts = [local(), peer({ online: false, reason: 'refused' })];
  assert.strictEqual(resolveActiveHost(hosts, 'gpu-box'), 'gpu-box');
  const items = buildHostMenu(hosts, 'gpu-box');
  assert.strictEqual(items[1].active, true);
  assert.strictEqual(items[1].online, false);
});

test('원격 세션 행은 합성 키를 쓰고 remote 플래그가 붙는다', async () => {
  const { remoteSessionRows } = await mod();
  const rows = remoteSessionRows(peer({ sessions: [{ name: 'dev', status: 'waiting' }, { name: 'x', status: 'nonsense' }] }));
  assert.deepStrictEqual(rows.map((r) => r.sessionId), ['remote:gpu-box:dev', 'remote:gpu-box:x']);
  assert.strictEqual(rows[0].status, 'waiting');
  // 서버가 모르는 상태 문자열을 보내도 레일이 깨지지 않게 idle로 눕힌다.
  assert.strictEqual(rows[1].status, 'idle');
  assert.ok(rows.every((r) => r.remote === true && r.kind === 'session'));
});

test('세션 목록이 없는 호스트도 빈 배열로 동작한다', async () => {
  const { remoteSessionRows } = await mod();
  // Array.from: 번들은 jsdom 렐름에서 돌아 Array 프로토타입이 달라 deepStrictEqual이
  // 빈 배열끼리도 실패한다(rail-data.test.js가 같은 이유로 쓰는 관용구).
  assert.deepStrictEqual(Array.from(remoteSessionRows({ id: 'a', label: 'a', online: false })), []);
});
