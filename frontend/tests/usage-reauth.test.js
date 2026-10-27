// 사용량 카드의 「재인증」 버튼이 새 터미널에 보내는 명령 — 프로필 이름이 셸에 들어가므로
// 허용 문자만 통과해야 한다(그 밖은 버튼을 아예 그리지 않는다).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const USAGE_JS = path.join(__dirname, '../js/panels/usage.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(USAGE_JS, env.context, new Map());
}

test('reauthCommand — clauth 프로필은 이름으로, 코덱스는 codex login', async () => {
  const { reauthCommand } = await load();
  assert.strictEqual(reauthCommand({ name: 'acct', provider: 'anthropic' }), 'clauth login acct');
  assert.strictEqual(reauthCommand({ name: 'my.profile-2_x', provider: '' }), 'clauth login my.profile-2_x');
  assert.strictEqual(reauthCommand({ name: 'codex', provider: 'codex' }), 'codex login');
});

test('reauthCommand — 셸 메타문자·공백·옵션처럼 보이는 이름은 거부한다', async () => {
  const { reauthCommand } = await load();
  for (const bad of ['a b', 'a;rm -rf ~', '$(id)', '`id`', 'a&&b', '-rf', '', 'a\nb', 'a|b', "a'b", 'a"b', '../x', 'x'.repeat(65)]) {
    assert.strictEqual(reauthCommand({ name: bad, provider: 'anthropic' }), null, JSON.stringify(bad));
  }
  assert.strictEqual(reauthCommand({ provider: 'anthropic' }), null);
});

test('reauthCommand — 관리 프로필은 FarShell 로그인만 사용하고 API 키에는 로그인 명령을 만들지 않는다', async () => {
  const { reauthCommand } = await load();
  const profile_id = 'a'.repeat(32);
  assert.equal(reauthCommand({ profile_id, provider: 'codex', name: 'review' }), `fsh profile login ${profile_id}`);
  assert.equal(reauthCommand({ profile_id, provider: 'claude', quota_scope: 'api-key' }), null);
  assert.equal(reauthCommand({ profile_id, provider: 'claude', quota_scope: 'unsupported' }), null);
  assert.equal(reauthCommand({ profile_id: 'bad; echo x', provider: 'codex' }), null);
});
