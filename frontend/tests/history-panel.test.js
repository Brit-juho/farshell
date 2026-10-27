const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const doms = [];
after(() => doms.forEach(dom => dom.window.close()));
const flush = async () => { for (let i=0;i<6;i++) await new Promise(resolve => setImmediate(resolve)); };

async function setup(replica = false, manifests = [], english = false) {
  const env = createDomEnv('<body></body>'); doms.push(env.dom);
  if (english) {
    const fs = require('node:fs');
    const vm = require('node:vm');
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../js/locales/en.js'),'utf8'),env.context);
    env.window.vtT = source => env.window.__VT_I18N[source] || source;
  }
  const calls = [];
  env.window.fetch = async (url, options = {}) => {
    calls.push({url, body: options.body ? JSON.parse(options.body) : null});
    let data;
    if (url.includes('?')) data = {items:[{id:'parent',key:'origin:parent',origin:'origin',native_id:'Parent',replica}],next:null};
    else if (options.body) data = {replica_delivery:{peer:{state:'pending'}}};
    else data = {conversation:{host:'origin',native_id:'Parent',provider:'claude'},messages:[],manifests,
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

for (const replica of [false,true]) for (const english of [false,true]) {
  test(`raw preservation and omitted search content are distinguished (${replica ? 'replica' : 'local'}, ${english ? 'en' : 'ko'})`,async()=>{
    const {document}=await setup(replica,[{id:'manifest',state:'saved',raw_complete:true,length:2000000,
      projection:{omitted_reasons:['oversized_record','<img src=x onerror=alert(1)>']}}],english);
    const text=document.querySelector('.vt-history-manifest').textContent;
    assert.ok(text.includes(english ? 'Full original preserved' : '원본 전체 보존'));
    assert.ok(text.includes(english ? 'Records exceeding 1 MiB' : '1 MiB를 초과한 행'));
    assert.ok(text.includes(english ? 'Unsupported transcript format' : '지원하지 않는 기록 형식'));
    assert.equal(document.querySelector('.vt-history-manifest img'),null);
  });
}

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

for (const english of [false, true]) for (const yolo of [false, true]) {
  test(`Codex resume reports restored YOLO ${yolo ? 'on' : 'off'} (${english ? 'en' : 'ko'})`, async () => {
    const env = createDomEnv('<body></body>'); doms.push(env.dom);
    if (english) {
      const vm = require('node:vm'), fs = require('node:fs');
      vm.runInContext(fs.readFileSync(path.join(__dirname,'../js/locales/en.js'),'utf8'),env.context);
      env.window.vtT = source => env.window.__VT_I18N[source] || source;
    }
    const toasts = [], opened = [];
    env.window.showToast = (...args) => toasts.push(args);
    env.window.vtOpenSessionFromRail = async value => opened.push(value);
    env.window.fetch = async () => ({ok:true,json:async()=>({session_id:'task',runtime_id:'run',name:'sample',execution_policy:{provider:'codex',yolo}})});
    const m = await importFresh(path.join(__dirname,'../js/history/session-actions.js'),env.context,new Map());
    await m.resumeSession('task');
    assert.equal(toasts.length,1);
    assert.equal(toasts[0][1],yolo ? 'warn' : 'info');
    assert.ok(toasts[0][0].includes(english ? `YOLO ${yolo ? 'on' : 'off'}` : (yolo ? '켠' : '끈')));
    assert.equal(opened[0].catalogSessionId,'task');
  });
}

test('unknown permissions refuse resume without opening a terminal',async()=>{
  const env=createDomEnv('<body></body>');doms.push(env.dom);
  env.window.vtOpenSessionFromRail=()=>assert.fail('must not open');
  env.window.fetch=async()=>({ok:false,status:409,json:async()=>({error:'execution_policy_unavailable'})});
  const m=await importFresh(path.join(__dirname,'../js/history/session-actions.js'),env.context,new Map());
  await assert.rejects(m.resumeSession('task'),/이전 권한 설정을 확인하지 못해/);
});
