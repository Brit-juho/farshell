const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
async function load(file) {
  const env = createDomEnv('<!doctype html><body></body>');
  doms.push(env.dom);
  const mod = await importFresh(path.join(__dirname, '../js/', file), env.context, new Map());
  return { ...env, mod };
}
const flush = () => new Promise((r) => setImmediate(r));

test('native summaries whitelist fields, reject duplicate and unsafe ids and preserve idle', async () => {
  const { mod } = await load('shell/native-subagents.js');
  const r = mod.normalizeSubagents({ parent_id: 'parent', limited: true, items: [
    { id: 'child', label: '<script>bad</script>', state: 'idle', _transcript: '/private' },
    { id: 'child', state: 'working' }, { id: '../bad', state: 'working' }, { id: 'bad', state: 'waiting' },
  ] });
  assert.equal(r.items.length, 1);
  assert.equal(r.items[0].state, 'idle');
  assert.equal(r.items[0]._transcript, undefined);
  assert.equal(mod.nativeStateLabel('idle'), '유휴');
  assert.equal(r.limited, true);
});

test('transcript merge deduplicates overlapping pages and resets replaced files', async () => {
  const { mod } = await load('panels/subagent-poll.js');
  const old = [{ id: 'a', kind: 'assistant', text: 'old' }];
  const merged = mod.mergeTranscript(old, { items: [{ id: 'a', kind: 'assistant', text: 'new' }, { id: 'b', kind: 'reasoning', text: 'secret' }] });
  assert.equal(merged.length, 1);
  assert.equal(merged[0].text, 'new');
  assert.equal(mod.mergeTranscript(old, { reset: true, items: [] }).length, 0);
});

test('polling is serial and stop aborts in-flight work and ignores its late response', async () => {
  const { mod } = await load('panels/subagent-poll.js');
  let resolve, signal, received = 0, requests = 0;
  const poll = mod.startSubagentPolling({ state: () => 'working', visible: () => true,
    fetchPage: (s) => { signal = s; requests++; return new Promise((r) => { resolve = r; }); },
    receive: () => received++, error: (e) => { throw e; } });
  poll.refresh(); poll.refresh();
  assert.equal(requests, 1);
  poll.stop();
  assert.equal(signal.aborted, true);
  resolve({ items: [] }); await flush();
  assert.equal(received, 0);
});

test('finished rows perform exactly three final retries and visibility pauses work', async () => {
  const { mod } = await load('panels/subagent-poll.js');
  const timers = new Map(); let serial = 0, calls = 0, visible = true;
  const poll = mod.startSubagentPolling({ state: () => 'done', visible: () => visible,
    fetchPage: async () => { calls++; return {}; }, receive: () => {}, error: () => {},
    later: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; },
    cancel: (id) => timers.delete(id) });
  await flush();
  for (const delay of [500, 1500, 3000]) {
    const [id, job] = timers.entries().next().value;
    assert.equal(job.ms, delay); timers.delete(id); job.fn(); await flush();
  }
  assert.equal(calls, 4); assert.equal(timers.size, 0);
  visible = false; poll.visibility(); assert.equal(timers.size, 0);
  visible = true; poll.visibility(); await flush(); assert.equal(calls, 5);
  poll.stop(); assert.equal(timers.size, 0);
});

// 리디자인 6단계(시안 O15) — 따로 뜨던 「서브에이전트 기록」 창은 없어지고 기록 본체만 남았다(작업 기록 창 오른쪽이 쓴다).
test('transcript renders hostile text literally and replaces the temporary final answer', async () => {
  const { mod, window, document } = await load('panels/subagent-viewer.js');
  let next = { parent_id: 'parent', state: 'done', next_cursor: 'cursor', items: [], last_message: '<img src=x onerror=alert(1)>', pending: false, label: '<b>Scout</b>' };
  window.vtFetch = async () => next;
  window.agentDetails = () => ({ sample: { subagents: { parent_id: 'parent', items: [{ id: 'child', state: 'done' }] } } });
  const list = document.createElement('div'); document.body.append(list);
  const metas = [];
  const view = mod.mountSubagentTranscript(list, { tmuxName: 'sample', parentId: 'parent', child: { id: 'child', label: 'Scout', state: 'done' }, host: 'local', meta: (m) => metas.push(m) });
  await flush();
  assert.equal(list.querySelector('img'), null);
  assert.equal(metas.at(-1).label, '<b>Scout</b>', '이름은 호출자가 textContent로 넣는다');
  assert.match(list.textContent, /<img src=x/);
  assert.match(list.querySelector('.vt-wl-entry-sub').textContent, /기록 반영 대기/);
  next = { ...next, items: [{ id: 'answer', kind: 'assistant', text: next.last_message }] };
  view.refresh(); await flush();
  assert.equal(list.querySelectorAll('.vt-wl-entry').length, 1);
  view.stop();
  assert.equal(typeof mod.showSubagent, 'undefined', '예전 별도 창 진입점은 없다');
  assert.equal(typeof mod.showSubagents, 'undefined');
});
