// core/redact.js — 서버(server/client_log.py)와 같은 사례 파일을 읽는다. 두 구현이 어긋나면 이 시험과
// server/tests/test_client_log.py 의 같은 사례 시험 중 한쪽이 깨진다.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const cases = require('./fixtures/redact-cases.json');

const MOD = path.join(__dirname, '../js/core/redact.js');

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  const ns = await importFresh(MOD, env.context, new Map());
  return { redact: ns.redact, close: () => env.dom.window.close() };
}

test('공용 사례 파일의 모든 사례가 기대대로 가려진다', async () => {
  const { redact, close } = await load();
  try {
    for (const c of cases) assert.strictEqual(redact(c.in), c.out, `입력: ${c.in}`);
  } finally { close(); }
});

test('문자열이 아니면 그대로 돌려준다', async () => {
  const { redact, close } = await load();
  try {
    assert.strictEqual(redact(undefined), undefined);
    assert.strictEqual(redact(5), 5);
    assert.strictEqual(redact(''), '');
  } finally { close(); }
});

test('두 번 적용해도 결과가 같다(멱등)', async () => {
  const { redact, close } = await load();
  try {
    for (const c of cases) assert.strictEqual(redact(redact(c.in)), c.out);
  } finally { close(); }
});
