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
  // 마이크 버튼은 index.html에서 hidden 템플릿 안에 있다(실제 앱에서는 dock으로 옮겨 붙는다). jsdom 30부터는
  // 렌더되지 않는 요소의 focus()를 명세대로 무시하므로, 실제처럼 보이는 상태로 만들어 둔다.
  w.document.getElementById('vt-rail-settings-tpl')?.removeAttribute('hidden');
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
  assert.equal(w.document.activeElement.id, 'mic-btn-wrap');
});

test('별도 STT 버튼 없이 마이크 버튼의 상태 점이 서버 상태를 따른다', async () => {
  assert.equal(html.includes('vt-dock-voice-status'), false);
  const ready = await build({ missing: true });
  const mic = ready.w.document.getElementById('mic-btn-wrap');
  assert.equal(ready.w.document.getElementById('voice-status-toggle'), null);
  assert.equal(mic.dataset.voiceState, 'setup');
  assert.match(mic.getAttribute('aria-label'), /설치 필요/);
  const ok = await build();
  assert.equal(ok.w.document.getElementById('mic-btn-wrap').dataset.voiceState, undefined);
  const bad = await build({ failure: true });
  assert.equal(bad.w.document.getElementById('mic-btn-wrap').dataset.voiceState, 'error');
  const stage = (phase) => ok.w.dispatchEvent(new ok.w.CustomEvent('vt:voice-stage', { detail: { phase } }));
  stage('processing');
  assert.equal(ok.w.document.getElementById('mic-btn-wrap').dataset.voiceState, 'busy');
  stage('recording');
  assert.equal(ok.w.document.getElementById('mic-btn-wrap').dataset.voiceState, undefined);
});

test('우클릭과 길게 누르기는 패널을 열고, 길게 누른 뒤의 click은 녹음을 시작하지 않는다', async () => {
  const { w } = await build();
  const mic = w.document.getElementById('mic-btn-wrap');
  const panel = w.document.getElementById('voice-status-panel');
  mic.dispatchEvent(new w.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
  assert.equal(panel.hidden, false);
  w.document.getElementById('voice-status-close').click();
  assert.equal(panel.hidden, true);
  let clicks = 0;
  w.document.addEventListener('click', () => { clicks++; });
  mic.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.equal(clicks, 1, '평소 click은 그대로 전달된다');
  mic.dispatchEvent(new w.Event('pointerdown', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 560));
  assert.equal(panel.hidden, false);
  mic.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.equal(clicks, 1, '길게 누른 직후 click은 삼킨다');
  mic.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  assert.equal(clicks, 2);
});

test('녹음 중에만 음량 미터가 보이고, 값·클리핑을 그린다', async () => {
  const { w } = await build();
  const meter = w.document.getElementById('voice-meter');
  const lv = () => meter.style.getPropertyValue('--lv');
  assert.equal(meter.hidden, true);
  const stage = (phase, target) => w.dispatchEvent(new w.CustomEvent('vt:voice-stage', { detail: { phase, target } }));
  const level = (detail) => w.dispatchEvent(new w.CustomEvent('vt:voice-level', { detail }));
  level({ level: 0.9, clip: false });                         // 녹음 중이 아니면 무시
  assert.notEqual(lv(), '0.90');
  stage('recording', '작업'); await flush();
  assert.equal(meter.hidden, false);
  level({ level: 0.5, clip: false });
  assert.equal(lv(), '0.50'); assert.equal(meter.dataset.clip, undefined);
  assert.equal(meter.getAttribute('aria-valuenow'), '50');
  level({ level: 3, clip: true });                            // 범위 밖 값은 자른다
  assert.equal(lv(), '1.00'); assert.equal(meter.dataset.clip, '1');
  level({ level: NaN });
  assert.equal(lv(), '0.00'); assert.equal(meter.dataset.clip, undefined);
  stage('processing'); await flush();
  assert.equal(meter.hidden, true);
});

test('prefers-reduced-motion이면 미터를 5단계로 끊고 초당 4번만 갱신한다', async () => {
  const { w } = await build();
  w.matchMedia = (q) => ({ matches: q.includes('reduced-motion') });
  const meter = w.document.getElementById('voice-meter');
  w.dispatchEvent(new w.CustomEvent('vt:voice-stage', { detail: { phase: 'recording', target: 'x' } })); await flush();
  const level = (v) => w.dispatchEvent(new w.CustomEvent('vt:voice-level', { detail: { level: v, clip: false } }));
  level(0.47);
  assert.equal(meter.style.getPropertyValue('--lv'), '0.4');
  level(0.9);                                                 // 250ms 안의 두 번째 값은 건너뛴다
  assert.equal(meter.style.getPropertyValue('--lv'), '0.4');
});

test('부분 결과는 녹음·인식 중에만 흐리게 보이고, 끝나면 지운다', async () => {
  const { w } = await build();
  const el = w.document.getElementById('voice-partial');
  const stage = (phase, target) => w.dispatchEvent(new w.CustomEvent('vt:voice-stage', { detail: { phase, target } }));
  const partial = (detail) => w.dispatchEvent(new w.CustomEvent('vt:voice-partial', { detail }));
  partial({ text: '무시', clipped: false });                       // 녹음 중이 아니면 무시
  assert.equal(el.hidden, true);
  stage('recording', '작업'); await flush();
  partial({ text: '  ls 목록  ', clipped: false });
  assert.equal(el.hidden, false); assert.equal(el.textContent, 'ls 목록');
  assert.equal(el.getAttribute('aria-live'), null);
  partial({ text: '긴 문장 끝', clipped: true });
  assert.equal(el.textContent, '… 긴 문장 끝');
  partial({ text: '' });
  assert.equal(el.hidden, true);
  partial({ text: '다시' });
  stage('processing'); await flush();
  assert.equal(el.hidden, false);                                  // 인식 중에도 유지
  stage('done'); await flush();
  assert.equal(el.hidden, true); assert.equal(el.textContent, '');
});
