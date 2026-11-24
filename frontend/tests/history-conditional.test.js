// historyApi 조건부 조회 — 마지막 응답과 ETag를 메모리에만 들고 If-None-Match로 보내, 304면 들고 있던 값을 그대로 돌려준다.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach((dom) => dom.window.close()));

async function setup(script) {
  const env = createDomEnv('<body></body>'); doms.push(env.dom);
  const calls = [];
  env.window.fetch = async (url, options = {}) => {
    calls.push({ url, headers: { ...(options.headers || {}) }, method: options.method });
    const next = script.shift();
    const headers = new Map(Object.entries(next.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
    return { ok: next.status >= 200 && next.status < 300, status: next.status, headers: { get: (k) => headers.get(k.toLowerCase()) ?? null },
             json: async () => next.body };
  };
  const api = await importFresh(path.join(__dirname, '../js/history/api.js'), env.context, new Map());
  return { api, calls };
}

test('같은 내용이면 304로 답을 받고 들고 있던 값(같은 객체)을 돌려준다', async () => {
  const { api, calls } = await setup([
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { sessions: [1, 2] } },
    { status: 304, headers: { ETag: 'W/"a1"' } },
  ]);
  const first = await api.historyApi('/api/session-catalog?x=1', { conditional: true });
  const second = await api.historyApi('/api/session-catalog?x=1', { conditional: true });
  assert.deepEqual(first, { sessions: [1, 2] });
  assert.equal(second, first, '같은 객체 — 화면이 다시 그리지 않는다');
  assert.equal(calls[0].headers['If-None-Match'], undefined);
  assert.equal(calls[1].headers['If-None-Match'], 'W/"a1"');
});

test('내용이 바뀌면 200의 새 값과 새 ETag로 바뀐다', async () => {
  const { api, calls } = await setup([
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
    { status: 200, headers: { ETag: 'W/"b2"' }, body: { v: 2 } },
    { status: 304, headers: { ETag: 'W/"b2"' } },
  ]);
  assert.deepEqual(await api.historyApi('/p', { conditional: true }), { v: 1 });
  assert.deepEqual(await api.historyApi('/p', { conditional: true }), { v: 2 });
  assert.deepEqual(await api.historyApi('/p', { conditional: true }), { v: 2 });
  assert.equal(calls[2].headers['If-None-Match'], 'W/"b2"');
});

test('conditional을 안 주면 If-None-Match를 보내지 않고 아무것도 들고 있지 않는다', async () => {
  const { api, calls } = await setup([
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
  ]);
  await api.historyApi('/p');
  await api.historyApi('/p');
  assert.equal(calls[1].headers['If-None-Match'], undefined);
});

test('오류(401·500)를 만나면 들고 있던 값을 버려 다음에는 전체를 받는다', async () => {
  const { api, calls } = await setup([
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
    { status: 500, body: { error: 'store_unavailable' } },
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
  ]);
  await api.historyApi('/p', { conditional: true });
  await assert.rejects(api.historyApi('/p', { conditional: true }));
  await api.historyApi('/p', { conditional: true });
  assert.equal(calls[1].headers['If-None-Match'], 'W/"a1"');
  assert.equal(calls[2].headers['If-None-Match'], undefined);
});

test('쓰기(POST)에는 조건부를 켜도 If-None-Match를 붙이지 않는다', async () => {
  const { api, calls } = await setup([{ status: 200, headers: { ETag: 'W/"a1"' }, body: { ok: true } }]);
  await api.historyApi('/p', { body: { a: 1 }, conditional: true });
  assert.equal(calls[0].headers['If-None-Match'], undefined);
});

test('ETag 없는 200이면 들고 있던 것을 지운다', async () => {
  const { api, calls } = await setup([
    { status: 200, headers: { ETag: 'W/"a1"' }, body: { v: 1 } },
    { status: 200, body: { v: 2 } },
    { status: 200, headers: { ETag: 'W/"c3"' }, body: { v: 3 } },
  ]);
  await api.historyApi('/p', { conditional: true });
  await api.historyApi('/p', { conditional: true });
  await api.historyApi('/p', { conditional: true });
  assert.equal(calls[2].headers['If-None-Match'], undefined);
});
