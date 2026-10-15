// 큐 패널: 권한 재확인으로 막힌 항목은 서버가 준 사람용 문구(blocked_text)를 보이고, 없으면 코드(blocked_reason)를 그대로.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(path.join(__dirname, '../js/queue.js'), env.context, new Map());
}

test('blocked_text 가 있으면 그것을, 없으면 blocked_reason 코드를 보인다', async () => {
  const m = await load();
  assert.strictEqual(m.blockedLine({ blocked_reason: 'grant_revoked', blocked_text: '권한이 회수되어 멈췄습니다' }), '차단됨: 권한이 회수되어 멈췄습니다');
  assert.strictEqual(m.blockedLine({ blocked_reason: 'send_failed' }), '차단됨: send_failed');
});

test('자동 재시도 중이면 횟수가 붙는다', async () => {
  const m = await load();
  assert.strictEqual(m.blockedLine({ blocked_reason: 'authz_unavailable', blocked_text: '권한 저장소를 읽지 못했습니다', retry_at: 5, attempts: 2 }), '차단됨: 권한 저장소를 읽지 못했습니다 — 자동 재시도 (2회째)');
});
