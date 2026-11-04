// core/dialog-lazy.js — 확인·입력·알림 대화상자의 지연 진입점. 본체를 못 받으면 브라우저 기본 대화상자로 물러난다.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach(w => w.close()));

// 청크 로드 실패를 흉내 내려고 dialog.js를 못 찾는 그래프에서 읽는다 — 실제 import()가 거부되는 경로.
async function stubWithBrokenChunk() {
  const env = createDomEnv('<html><body></body></html>');
  const w = env.window; wins.push(w);
  const log = [];
  w.confirm = (m) => { log.push(['confirm', m]); return true; };
  w.prompt = (m, d) => { log.push(['prompt', m, d]); return null; };
  w.alert = (m) => { log.push(['alert', m]); };
  const src = require('node:fs').readFileSync(path.join(__dirname, '../js/core/dialog-lazy.js'), 'utf8')
    .replace("import('./dialog.js')", "Promise.reject(new Error('chunk 404'))");
  const tmp = path.join(require('node:os').tmpdir(), `dialog-lazy-${process.pid}-${Date.now()}.mjs`);
  require('node:fs').writeFileSync(tmp, src);
  const mod = await importFresh(tmp, env.context, new Map());
  return { mod, log };
}

test('본체를 못 받으면 확인은 window.confirm으로 물러난다(조용히 취소·승인하지 않는다)', async () => {
  const { mod, log } = await stubWithBrokenChunk();
  assert.equal(await mod.confirmDialog('삭제할까요?'), true);
  assert.deepEqual(log, [['confirm', '삭제할까요?']]);
});

test('본체를 못 받으면 입력은 window.prompt, 알림은 window.alert로 물러난다', async () => {
  const { mod, log } = await stubWithBrokenChunk();
  assert.equal(await mod.promptDialog('이름?', { defaultValue: 'a' }), null);
  await mod.alertDialog('끝');
  assert.deepEqual(log, [['prompt', '이름?', 'a'], ['alert', '끝']]);
});

test('받는 데 한 번 실패해도 다음 호출에서 다시 시도한다', async () => {
  const { mod, log } = await stubWithBrokenChunk();
  await mod.confirmDialog('a');
  await mod.confirmDialog('b');
  assert.equal(log.length, 2);
});
