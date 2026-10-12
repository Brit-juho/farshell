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

async function build() {
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
    const data = String(url).includes('/stt/status') ? { available: true, selected_valid: true, loaded: true, model: 'm', loaded_model: 'm' }
      : String(url).includes('/stt/catalog') ? { models: [{ id: 'm', label: '음성 모델', model_present: true, requirements: [] }] }
      : String(url).includes('/stt/preload') ? { loaded: true }
      : String(url).includes('/voice/target') ? { token: 'captured-A', name: 'A' }
      : { text: '명령', injected: true };
    return { ok: true, json: async () => data };
  };
  Object.defineProperty(w.navigator, 'mediaDevices', { value: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) } });
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
  await recording.startRecording(); // 준비
  await recording.startRecording(); // 녹음
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
