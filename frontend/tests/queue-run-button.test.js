// U8 — 「지금 실행」은 큐에 투입할 게 있을 때만 눌린다.
//
// 빈 큐에서도 눌렸고, 눌러봐야 서버가 할 일이 없다. 규칙 자체는 한 줄이지만
// **왜 못 누르는지**까지가 계약이다 — 이유 없이 회색인 버튼은 "고장났나"로
// 읽힌다. 그래서 disabled와 title을 같이 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const QUEUE_JS = path.join(__dirname, '../js/queue.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv(
    '<!doctype html><html><body><button id="vt-q-run">지금 실행</button></body></html>');
  _doms.push(env.dom);
  const mod = await importFresh(QUEUE_JS, env.context, new Map());
  return { mod, doc: env.context.document };
}

test('빈 큐에서는 비활성이고, 이유를 title로 말한다', async () => {
  const { mod, doc } = await load();
  const btn = mod.syncRunButton(doc, 0);
  assert.strictEqual(btn.disabled, true);
  assert.strictEqual(btn.title, '큐가 비어 있습니다');
});

test('항목이 있으면 눌린다', async () => {
  const { mod, doc } = await load();
  const btn = mod.syncRunButton(doc, 3);
  assert.strictEqual(btn.disabled, false);
  assert.strictEqual(btn.title, '한 건 지금 투입');
});

test('버튼이 아직 없으면(패널이 닫힌 상태) 조용히 넘어간다', async () => {
  const { mod } = await load();
  assert.strictEqual(mod.syncRunButton(null, 0), null);
  assert.strictEqual(mod.syncRunButton({}, 0), null);
});
