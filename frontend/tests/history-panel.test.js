const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach(dom => dom.window.close()));
const flush = async () => { for (let i=0;i<6;i++) await new Promise(resolve => setImmediate(resolve)); };

async function setup(replica = false) {
  const env = createDomEnv('<body></body>'); doms.push(env.dom);
  const calls = [];
  env.window.fetch = async (url, options = {}) => {
    calls.push({url, body: options.body ? JSON.parse(options.body) : null});
    let data;
    if (url.includes('?')) data = {items:[{id:'parent',key:'origin:parent',origin:'origin',native_id:'Parent',replica}],next:null};
    else if (options.body) data = {replica_delivery:{peer:{state:'pending'}}};
    else data = {conversation:{host:'origin',native_id:'Parent',provider:'claude'},messages:[],manifests:[],
      children: url.endsWith('/parent') ? [{id:'child',native_id:'Child'}] : [],next:null};
    return {ok:true,status:200,json:async()=>data};
  };
  const module = await importFresh(path.join(__dirname,'../js/history/panel.js'), env.context, new Map());
  module.openHistory(); await flush();
  assert.equal(env.document.querySelector('#vt-history .vt-vw-loading'),null);
  env.document.querySelector('.vt-history-item').click(); await flush();
  assert.equal(env.document.querySelector('.vt-history-item').getAttribute('aria-pressed'),'true');
  return {...env,calls};
}

test('replicated child conversation keeps the replica origin', async () => {
  const {document,calls} = await setup(true);
  [...document.querySelectorAll('.vt-history-detail button')].find(button=>button.textContent==='Child').click();
  await flush();
  assert.equal(calls.at(-1).url,'/api/session-history/replicas/origin/child');
  assert.equal(document.querySelector('[aria-label="기록 삭제 범위"]'),null);
});

test('history deletion submits the explicitly chosen replica scope after confirmation', async () => {
  const {document,calls} = await setup();
  document.querySelector('[aria-label="기록 삭제 범위"]').value='replicas';
  [...document.querySelectorAll('.vt-history-detail button')].find(button=>button.textContent==='기록 삭제').click();
  await flush();
  assert.equal(calls.filter(call=>call.body?.deleted).length,0);
  document.querySelector('.vt-dialog-ok').click(); await flush();
  const request=calls.find(call=>call.body?.deleted);
  assert.equal(request.url,'/api/session-history/conversations/parent');
  assert.equal(request.body.delete_scope,'replicas');
});
