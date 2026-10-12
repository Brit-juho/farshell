const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

async function build({ missing = false, remote = false, failure = false } = {}) {
  const env = createDomEnv(html); doms.push(env.dom);
  const w = env.window;
  w.API_BASE = w.location.origin;
  w.vtRelayHost = () => remote ? 'peer' : 'local';
  const actions = {}; w.registerAction = (name, fn) => { actions[name] = fn; };
  const requests = [];
  const model = { id: 'm', label: '음성 모델', model_present: !missing, requirements: [] };
  const status = { available: true, selected_valid: true, model: 'm', loaded: false, loaded_model: null };
  w.fetch = async (url, opts = {}) => {
    requests.push({ url: String(url), opts });
    if (failure) throw new Error('offline');
    return { ok: true, status: 200, json: async () => String(url).includes('/catalog') ? { models: [model] } : status };
  };
  const home = w.document.createElement('div'); home.id = 'vt-dock-voice-status'; w.document.body.append(home);
  const module = await importFresh(path.join(__dirname, '../js/voice/status-panel.js'), env.context, new Map());
  w.vtRelayHost = () => remote ? 'peer' : 'local';
  module.mountVoiceStatus(); await flush();
  return { w, module, actions, requests, model, status };
}

test('설치 여부와 메모리 적재를 구분하며 상태 조회가 모델을 받거나 올리지 않는다', async () => {
  const { w, requests, status, actions } = await build({ missing: true });
  assert.equal(w.document.getElementById('voice-stt-state').textContent, '설치 필요');
  assert.equal(w.document.getElementById('voice-panel-record').disabled, true);
  assert.equal(w.document.getElementById('voice-status-panel').hidden, true);
  actions['voice.status'](); await flush();
  assert.equal(w.document.getElementById('voice-status-panel').hidden, false);
  assert.ok(requests.every((r) => !r.opts.method || r.opts.method === 'GET'));
  assert.ok(requests.every((r) => !r.url.includes('/preload') && !r.url.includes('/setup')));
  const { module, model } = await build();
  assert.equal(module.describeStt(status, { models: [model] }).state, '모델 준비 필요');
  status.loaded = true; status.loaded_model = 'm';
  assert.equal(module.describeStt(status, { models: [model] }).state, '사용 준비됨');
  status.loaded_model = 'other';
  assert.equal(module.describeStt(status, { models: [model] }).state, '모델 준비 필요');
});

test('서버 연결 실패와 원격 호스트에서는 녹음을 막고 원인을 표시한다', async () => {
  for (const options of [{ failure: true }, { remote: true }]) {
    const { w, requests } = await build(options);
    assert.equal(w.document.getElementById('voice-stt-state').textContent, options.remote ? '직접 접속 필요' : '상태 확인 실패');
    assert.equal(w.document.getElementById('voice-panel-record').disabled, true);
    if (options.remote) assert.equal(requests.length, 0);
  }
});

test('녹음 대상·종료·처리 상태를 보여 주고 패널을 닫아도 재확인할 수 있다', async () => {
  const { w, actions } = await build();
  const panel = w.document.getElementById('voice-status-panel');
  const stage = (phase, target) => w.dispatchEvent(new w.CustomEvent('vt:voice-stage', { detail: { phase, ...(target !== undefined ? { target } : {}) } }));
  stage('recording', '원래 pane');
  assert.equal(panel.hidden, false);
  assert.match(w.document.getElementById('voice-record-meta').textContent, /원래 pane.*0초/);
  assert.equal(w.document.getElementById('voice-panel-record').textContent, '녹음 종료');
  stage('processing');
  assert.equal(w.document.getElementById('voice-panel-record').disabled, true);
  assert.equal(w.document.getElementById('voice-panel-record').textContent, '인식 중');
  w.document.getElementById('mic-status').textContent = '입력되지 않음 — 대상이 사라짐';
  stage('error'); await flush();
  w.document.getElementById('voice-status-close').click();
  assert.equal(panel.hidden, true);
  actions['voice.status']();
  assert.match(w.document.getElementById('mic-status').textContent, /입력되지 않음/);
  assert.match(w.document.getElementById('voice-record-meta').textContent, /원래 pane/);
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(panel.hidden, true);
  assert.equal(w.document.activeElement.id, 'voice-status-toggle');
});
