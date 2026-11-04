// voice/pcm-session.js의 makeAckHandler — 청크 응답의 partial·endpoint 해석(순수 함수).
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

async function load() {
  const env = createDomEnv('<!doctype html><body></body>');
  const mod = await importFresh(path.join(__dirname, '../js/voice/pcm-session.js'), env.context, new Map());
  return { mod, close: () => env.dom.window.close() };
}

test('partial은 더 최근 seq만, 형식이 틀린 값은 무시한다', async () => {
  const { mod, close } = await load();
  const got = [];
  const h = mod.makeAckHandler({ onPartial: (p) => got.push(p) });
  h({ ackSeq: 0, partial: { text: 'a', seq: 0 } });
  h({ ackSeq: 1, partial: { text: 'b', seq: 2, clipped: true } });
  h({ ackSeq: 2, partial: { text: 'old', seq: 1 } });
  h({ partial: { text: 3, seq: 5 } }); h({ partial: { text: 'x', seq: '9' } }); h(null); h('str');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(got)), [{ text: 'a', clipped: false, seq: 0 }, { text: 'b', clipped: true, seq: 2 }]);
  close();
});

test('endpoint는 녹음당 한 번, 활성일 때만 부른다', async () => {
  const { mod, close } = await load();
  let n = 0; let active = false;
  const h = mod.makeAckHandler({ onEndpoint: () => n++, isActive: () => active });
  h({ endpoint: true });
  assert.strictEqual(n, 0);                                        // 이미 멈추는 중이면 무시
  active = true;
  h({ endpoint: 'yes' }); h({ endpoint: true }); h({ endpoint: true });
  assert.strictEqual(n, 1);
  close();
});
