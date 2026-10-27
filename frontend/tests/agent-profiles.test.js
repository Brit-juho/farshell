const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { answerConfirm } = require('./helpers/dialog-stub');
const doms = [];
after(() => doms.forEach(d => d.window.close()));
const flush = () => new Promise(r => setImmediate(r));

async function setup({ reply, hostId } = {}) {
  const env = createDomEnv('<body></body>');
  doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  const calls = [];
  env.window.fetch = async (url, options = {}) => {
    const route = url.split('?')[0];
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ route, body });
    let data = reply ? await reply(route, body) : undefined;
    if (data === undefined) {
      if (route.endsWith('/list')) data = { profiles: [{ id: 'sample', tool: 'claude', name: '<img src=x onerror=alert(1)>', revision: 1, purpose: 'test', state: 'ready' }], accounts: [], sessions: [] };
      else if (route.endsWith('/sources')) data = { sources: [{ id: 'source', tool: 'claude', kind: 'clauth', name: 'source' }] };
      else if (route.endsWith('/preview')) data = { token: 'review-only', files: [] };
      else if (route.endsWith('/apply')) data = { status: 'ok' };
      else data = {};
    }
    return { ok: true, status: 200, json: async () => data };
  };
  const cache = new Map();
  await importFresh(path.join(__dirname, '../js/ui/toast.js'), env.context, cache);
  const routes = await importFresh(path.join(__dirname, '../js/core/host-route.js'), env.context, cache);
  if (hostId) routes.setRelayHost(hostId);
  const mod = await importFresh(path.join(__dirname, '../js/panels/agent-profiles.js'), env.context, cache);
  const host = env.document.createElement('main');
  env.document.body.appendChild(host);
  const manager = mod.mount(host);
  for (let i=0;i<8;i++) await flush();
  return { ...env, host, calls, manager };
}

test('원본 비밀값을 요청하지 않으며 프로필 이름은 텍스트로 표시한다', async () => {
  const {host,calls,manager} = await setup();
  assert.equal(host.querySelector('img'), null);
  assert.match(host.textContent, /<img/);
  assert.deepEqual(calls.map(c => c.route), ['/api/agent-profiles/read/list', '/api/agent-profiles/read/sources']);
  manager.dispose();
});

test('입력 취소 뒤 다시 그린 실행 버튼으로 키보드 초점을 복원한다', async () => {
  const {host,document,calls,manager} = await setup();
  const origin = [...host.querySelectorAll('button')].find(b => b.textContent === '프로필 이름 변경');
  origin.focus();
  origin.click();
  for (let i=0;i<4;i++) await flush();
  await answerConfirm(document, {accept:false});
  for (let i=0;i<8;i++) await flush();
  assert.equal(document.activeElement.textContent, '프로필 이름 변경');
  assert.equal(document.activeElement.isConnected, true);
  assert.equal(calls.some(c => c.route.endsWith('/apply')), false);
  manager.dispose();
});

test('API 키 복제는 검토 전 적용하지 않고 적용에는 opaque 토큰만 보낸다', async () => {
  const {host,document,calls,manager} = await setup();
  [...host.querySelectorAll('button')].find(b => b.textContent === 'API 키 포함 테스트 복제 검토').click();
  for(let i=0;i<6;i++) await flush();
  const preview = calls.find(c => c.route.endsWith('/preview'));
  assert.equal(preview.body.payload.include_private, true);
  assert.equal(calls.some(c => c.route.endsWith('/apply')), false);
  await answerConfirm(document);
  for(let i=0;i<8;i++) await flush();
  assert.deepEqual(calls.find(c => c.route.endsWith('/apply')).body, {token:'review-only'});
  manager.dispose();
});

test('dispose 뒤 늦은 응답이 UI를 다시 만들지 않는다', async () => {
  let resolve;
  const pending = new Promise(r => {resolve=r;});
  const {host,manager} = await setup({reply: async route => route.endsWith('/list') ? pending : undefined});
  manager.dispose();
  host.replaceChildren();
  resolve({profiles:[{id:'late',name:'late',tool:'claude'}]});
  for(let i=0;i<8;i++) await flush();
  assert.equal(host.textContent, '');
});
