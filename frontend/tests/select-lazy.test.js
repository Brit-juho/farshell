// ui/select-lazy.js — select 드롭다운 본체를 select가 나타날 때(와 유휴 시간에) 한 번만 받아 넘기는 스텁.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach(w => w.close()));
const flush = () => new Promise(r => setTimeout(r, 10));

async function setup(html = '') {
  const env = createDomEnv(`<html><body>${html}</body></html>`);
  wins.push(env.window);
  const mod = await importFresh(path.join(__dirname, '../js/ui/select-lazy.js'), env.context, new Map());
  const calls = { load: 0, enhance: 0 };
  const load = async () => { calls.load++; return { autoEnhanceSelects: () => { calls.enhance++; } }; };
  return { w: env.window, doc: env.window.document, mod, calls, load };
}

test('select가 없으면 본체를 받지 않는다', async () => {
  const { doc, mod, calls, load } = await setup('<div id="a"></div>');
  mod.watchSelects({ load, root: doc, idle: false });
  doc.getElementById('a').append(doc.createElement('span'));
  await flush();
  assert.equal(calls.load, 0);
});

test('select가 나중에 생기면 본체를 한 번만 받아 autoEnhanceSelects를 부른다', async () => {
  const { doc, mod, calls, load } = await setup('<div id="a"></div>');
  mod.watchSelects({ load, root: doc, idle: false });
  const wrap = doc.createElement('div');
  wrap.innerHTML = '<label><select><option>a</option></select></label>';   // 후손에 select가 든 노드도 잡는다
  doc.getElementById('a').append(wrap);
  doc.getElementById('a').append(doc.createElement('select'));
  await flush();
  assert.equal(calls.load, 1);
  assert.equal(calls.enhance, 1);
});

test('처음부터 문서에 select가 있으면 바로 넘긴다', async () => {
  const { doc, mod, calls, load } = await setup('<select id="s"><option>a</option></select>');
  mod.watchSelects({ load, root: doc, idle: false });
  await flush();
  assert.equal(calls.load, 1);
  assert.equal(calls.enhance, 1);
});

test('유휴 미리받기는 select가 없어도 본체를 받는다', async () => {
  const { w, doc, mod, calls, load } = await setup();
  const handOver = mod.watchSelects({ load, root: doc, idle: false });
  await handOver();
  assert.equal(calls.load, 1);
  await handOver();
  assert.equal(calls.load, 1, '두 번째 호출은 같은 약속을 재사용한다');
});

test('본체 로드가 실패해도 던지지 않고, 다음 select에서 다시 시도한다', async () => {
  const { doc, mod, calls } = await setup();
  let n = 0;
  const load = async () => { n++; if (n === 1) throw new Error('chunk 404'); return { autoEnhanceSelects: () => { calls.enhance++; } }; };
  const origErr = console.error; console.error = () => {};
  try {
    const handOver = mod.watchSelects({ load, root: doc, idle: false });
    await handOver();
    assert.equal(calls.enhance, 0);
    await handOver();
    assert.equal(n, 2);
    assert.equal(calls.enhance, 1);
  } finally { console.error = origErr; }
});
