const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
const flush = () => new Promise((r) => setImmediate(r));

async function build(o = {}) {
  const env = createDomEnv(html); doms.push(env.dom);
  const w = env.window;
  w.API_BASE = w.location.origin; w.registerAction = () => {};
  w.activeId = 'A'; w.getSession = () => ({ tmuxName: w.activeId });
  w.URL.createObjectURL = () => 'blob:test'; w.URL.revokeObjectURL = () => {};
  w.WebSocket = class { constructor() { this.readyState = 1; w.voiceWS = this; } send(s) { (this.sent ||= []).push(JSON.parse(s)); } close() {} };
  w.Audio = class { pause() {} load() {} removeAttribute() {} play() { return w.blocked ? Promise.reject(new Error('blocked')) : Promise.resolve(); } };
  const requests = [];
  w.fetch = async (url, opts = {}) => {
    requests.push({ url: String(url), opts });
    if (String(url).includes('/voice/input') && o.input) return o.input(requests);
    const data = String(url).includes('/stt/status') ? { available: true, selected_valid: true, selected_ready: true, loaded: true, model: 'm', loaded_model: 'm', ...o.status }
      : String(url).includes('/stt/catalog') ? { models: [{ id: 'm', label: '음성 모델', model_present: true, requirements: [] }] }
      : String(url).includes('/stt/preload') ? { loaded: true }
      : String(url).includes('/voice/target') ? { token: 'captured-A', name: 'A' }
      : { text: '명령', injected: true };
    return { ok: true, json: async () => data };
  };
  Object.defineProperty(w.navigator, 'mediaDevices', { value: { getUserMedia: o.getUserMedia || (async () => ({ getTracks: () => [{ stop() {} }] })) } });
  w.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor() { w.recorder = this; this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({ data: new w.Blob(['audio']) }); this.onstop(); }
  };
  const cache = new Map();
  const statusPanel = await importFresh(path.join(__dirname, '../js/voice/status-panel.js'), env.context, cache);
  statusPanel.mountVoiceStatus();
  const recording = await importFresh(path.join(__dirname, '../js/voice/recording.js'), env.context, cache);
  return { w, recording, requests };
}

test('녹음 후 탭이 바뀌어도 시작 때 대상과 토큰으로 보낸다', async () => {
  const { w, recording, requests } = await build();
  await recording.startRecording();
  assert.strictEqual(recording.isRecording, true);
  assert.strictEqual(w.document.getElementById('voice-status-panel').hidden, false);
  assert.match(w.document.getElementById('voice-record-meta').textContent, /입력 대상: A/);
  assert.strictEqual(w.document.getElementById('voice-panel-record').textContent, '녹음 종료');
  w.activeId = 'B'; recording.stopRecording(); await flush(); await flush();
  const sent = requests.find((r) => r.url.includes('/voice/input'));
  assert.ok(sent.url.includes('session_id=A'));
  assert.strictEqual(sent.opts.headers['X-Voice-Target'], 'captured-A');
  assert.strictEqual(requests.filter((r) => r.url.includes('/voice/input')).length, 1);
  assert.match(w.document.getElementById('mic-status').textContent, /입력 완료 — A/);
  assert.match(w.document.getElementById('voice-record-meta').textContent, /입력 대상: A/);
});

test('자동 재생 차단 후 취소하면 수동 재생 버튼과 오디오를 해제한다', async () => {
  const { w } = await build();
  w.blocked = true;
  w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_connected', generation: 'g' }) });
  await w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_audio', id: 'one', generation: 'g', name: 'A', media_type: 'audio/wav', audio: w.btoa('audio') }) });
  await flush();
  assert.ok(w.document.getElementById('play-pending-btn'));
  await w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_cancel', id: 'one' }) });
  assert.strictEqual(w.document.getElementById('play-pending-btn'), null);
  assert.ok(w.voiceWS.sent.some((m) => m.id === 'one' && m.status === 'cancelled'));
});

test('녹음 중 재연결돼도 TTS를 재생하지 않고 일시정지를 다시 보내며, 녹음이 끝나면 다시 재생한다', async () => {
  const { w, recording } = await build();
  let played = 0;
  w.Audio = class { pause() {} load() {} removeAttribute() {} play() { played++; return Promise.resolve(); } };
  w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_connected', generation: 'g' }) });
  await recording.startRecording();
  assert.ok(w.voiceWS.sent.some((m) => m.type === 'voice_barge' && m.pause === true));
  w.voiceWS.sent = [];
  // 녹음 중 웹소켓 재연결(서버는 끊긴 화면의 일시정지를 지운다) — 다시 보낸다
  w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_connected', generation: 'g2' }) });
  assert.deepStrictEqual(w.voiceWS.sent[0], { type: 'voice_barge', pause: true });   // 연결 직후 맨 먼저
  // 그 틈에 도착한 음성은 재생하지 않고 취소로 알린다
  await w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_audio', id: 'late', generation: 'g2', name: 'A', media_type: 'audio/wav', audio: w.btoa('audio') }) });
  await flush();
  assert.strictEqual(played, 0);
  assert.ok(w.voiceWS.sent.some((m) => m.id === 'late' && m.status === 'cancelled'));
  recording.stopRecording(); await flush(); await flush();
  assert.ok(w.voiceWS.sent.some((m) => m.type === 'voice_barge' && m.pause === false));
  await w.voiceWS.onmessage({ data: JSON.stringify({ type: 'voice_audio', id: 'next', generation: 'g2', name: 'A', media_type: 'audio/wav', audio: w.btoa('audio') }) });
  await flush();
  assert.strictEqual(played, 1);
});

const uniq = (requests, part) => requests.filter((r) => r.url.includes(part)).length;

test('마이크가 없으면 입력 대상을 만들지 않고 이전 재전송 보관분도 건드리지 않는다', async () => {
  const { w, recording, requests } = await build({ getUserMedia: async () => { const e = new Error('x'); e.name = 'NotFoundError'; throw e; } });
  await recording.startRecording();
  assert.strictEqual(recording.isRecording, false);
  assert.strictEqual(uniq(requests, '/voice/target'), 0);
  assert.match(w.document.getElementById('mic-status').textContent, /마이크/);
});

test('모델이 준비되지 않은 환경에서는 올리기 요청 없이 안내만 보인다', async () => {
  const { w, recording, requests } = await build({ status: { loaded: false, selected_ready: false } });
  await recording.startRecording();
  assert.strictEqual(uniq(requests, '/stt/preload'), 0);
  assert.strictEqual(uniq(requests, '/voice/target'), 0);
  assert.match(w.document.getElementById('mic-status').textContent, /준비/);
});

test('받아 둔 모델이 안 올라가 있으면 첫 탭은 올리기로 쓰고 녹음하지 않는다', async () => {
  const { recording, requests } = await build({ status: { loaded: false } });
  await recording.startRecording();
  assert.strictEqual(uniq(requests, '/stt/preload'), 1);
  assert.strictEqual(recording.isRecording, false);
});

test('target 준비가 실패하면 연 마이크를 닫는다', async () => {
  let stopped = 0;
  const env = await build({ getUserMedia: async () => ({ getTracks: () => [{ stop() { stopped++; } }] }) });
  const orig = env.w.fetch;
  env.w.fetch = async (url, opts) => String(url).includes('/voice/target') ? { ok: false, json: async () => ({ error: '대상 없음' }) } : orig(url, opts);
  await env.recording.startRecording();
  assert.strictEqual(stopped, 1);
  assert.strictEqual(env.recording.isRecording, false);
});

async function record(env) {
  await env.recording.startRecording();
  env.recording.stopRecording();
  await flush(); await flush();
}

test('500은 재시도하지 않고 다시 보내기 버튼도 만들지 않는다', async () => {
  const env = await build({ input: async () => ({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }) });
  const btn = env.w.document.getElementById('mic-retry-btn');
  await record(env);
  assert.strictEqual(btn.hidden, true);
  assert.strictEqual(uniq(env.requests, '/voice/input'), 1);
  assert.match(env.w.document.getElementById('mic-status').textContent, /boom/);
});

test('일시 장애는 다시 보내기를 한 번 주고, 그래도 실패하면 버튼을 거둔다', async () => {
  const env = await build({ input: async () => ({ ok: false, status: 503, json: async () => ({}) }) });
  const btn = env.w.document.getElementById('mic-retry-btn');
  env.w.setTimeout = (fn, ms) => globalThis.setTimeout(fn, ms > 0 && ms < 5000 ? 0 : ms);
  await env.recording.startRecording();
  env.recording.stopRecording();
  for (let i = 0; i < 20 && btn.hidden; i++) await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual(btn.hidden, false);
  btn.click();
  const st = env.w.document.getElementById('mic-status');
  for (let i = 0; i < 40 && !/새로 녹음/.test(st.textContent); i++) await new Promise((r) => setTimeout(r, 50));
  assert.strictEqual(btn.hidden, true);
  assert.match(env.w.document.getElementById('mic-status').textContent, /새로 녹음/);
});
