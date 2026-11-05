// recording.js의 PCM 청크 경로: 녹음 → 청크 업로드 → 결과 표시, 폴백, 취소 정리.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
// 처리 중인 가짜 fetch 수. flush 는 이 값이 0이 될 때까지 기다린다 — 고정 횟수만 기다리면
// 부하가 걸린 러너에서 응답 처리가 끝나기 전에 단언이 돌아 간헐적으로 실패했다.
let pendingFetches = 0;
// 타이머 단위로 양보한다. 워클릿의 종료 신호가 setTimeout(0)으로 오므로 setImmediate 만으로는 그보다 먼저 돌아온다.
const tick = () => new Promise((r) => setTimeout(r, 0));
const flush = async () => {
  for (let round = 0; round < 1000; round++) {
    for (let i = 0; i < 12; i++) await tick();
    if (pendingFetches === 0) break;
  }
};

async function build(o = {}) {
  const env = createDomEnv(html); doms.push(env.dom);
  const w = env.window;
  w.API_BASE = w.location.origin; w.registerAction = () => {};
  w.activeId = 'A'; w.getSession = () => ({ tmuxName: w.activeId });
  w.URL.createObjectURL = () => 'blob:test'; w.URL.revokeObjectURL = () => {};
  w.isSecureContext = true;
  const state = { nodes: [], ctxClosed: 0, tracksStopped: 0, mrCreated: 0 };
  if (!o.noWorklet) {
    w.AudioContext = class {
      constructor() { this.sampleRate = 48000; this.state = 'running'; this.destination = {}; this.audioWorklet = { addModule: async () => { if (o.addModuleFails) throw new Error('blocked'); } }; }
      resume() { return Promise.resolve(); } close() { state.ctxClosed++; return Promise.resolve(); }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createGain() { return { gain: {}, connect() {}, disconnect() {} }; }
    };
    w.AudioWorkletNode = class {
      constructor() { this.port = { postMessage: (m) => { if (m === 'stop') setTimeout(() => this.port.onmessage({ data: 'end' }), 0); }, onmessage: null }; state.nodes.push(this); }
      connect() {} disconnect() {}
    };
  }
  const chunks = [];
  const requests = [];
  w.fetch = async (url, opts = {}) => {
    pendingFetches++;
    try { return await fakeFetch(url, opts); } finally { pendingFetches--; }
  };
  const fakeFetch = async (url, opts = {}) => {
    url = String(url); requests.push({ url, opts });
    const json = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
    if (url.includes('/voice/chunk/cancel')) return json({ cancelled: true });
    if (url.includes('/voice/chunk')) {
      const seq = Number(/seq=(\d+)/.exec(url)[1]); const final = url.includes('final=1');
      if (o.chunkFail && o.chunkFail(seq, requests.length)) throw new TypeError('network');
      chunks.push({ seq, final, bytes: opts.body.length, headers: opts.headers });
      return final ? json({ ...(o.finalData || { text: '명령', injected: true }), ackSeq: seq, complete: true }) : json({ ackSeq: seq, complete: false, ...(o.ackExtra ? o.ackExtra(seq) : {}) });
    }
    if (url.includes('/voice/inject')) return json({ injected: true, text: JSON.parse(opts.body).text, name: 'A' });
    if (url.includes('/voice/input')) return json({ text: '명령', injected: true });
    const data = url.includes('/stt/status') ? { available: true, selected_valid: true, selected_ready: true, loaded: true, model: 'm', loaded_model: 'm' }
      : url.includes('/stt/catalog') ? { models: [{ id: 'm', label: '음성 모델', model_present: true, requirements: [] }] }
      : url.includes('/voice/target') ? { token: 'captured-A', name: 'A' } : {};
    return json(data);
  };
  const stream = { getTracks: () => [{ stop() { state.tracksStopped++; } }] };
  Object.defineProperty(w.navigator, 'mediaDevices', { value: { getUserMedia: async () => stream } });
  w.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor() { state.mrCreated++; w.recorder = this; this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({ data: new w.Blob(['audio']) }); this.onstop(); }
  };
  const cache = new Map();
  const statusPanel = await importFresh(path.join(__dirname, '../js/voice/status-panel.js'), env.context, cache);
  statusPanel.mountVoiceStatus();
  const recording = await importFresh(path.join(__dirname, '../js/voice/recording.js'), env.context, cache);
  const feed = (n, level = 0.1) => { for (let i = 0; i < n; i++) state.nodes[0].port.onmessage({ data: new w.Float32Array(2048).fill(level) }); };
  return { w, recording, requests, chunks, state, feed };
}

test('PCM 경로: 청크를 순서대로 올리고 final 응답을 기존 표시 코드로 보여 준다', async () => {
  const e = await build();
  await e.recording.startRecording();
  assert.strictEqual(e.state.mrCreated, 0);                       // MediaRecorder를 만들지 않는다
  assert.strictEqual(e.recording.isRecording, true);
  e.feed(24);                                                      // 49152샘플@48k = 16384샘플@16k -> 32768바이트 = 2청크 + 768바이트
  await flush();
  assert.deepStrictEqual(e.chunks.map((c) => [c.seq, c.final, c.bytes]), [[0, false, 16000], [1, false, 16000]]);
  assert.ok(e.chunks.every((c) => c.headers['X-Voice-Target'] === 'captured-A' && c.headers['X-Voice-Id']));
  assert.strictEqual(new Set(e.chunks.map((c) => c.headers['X-Voice-Id'])).size, 1);
  e.recording.stopRecording(); await flush();
  assert.deepStrictEqual(e.chunks.map((c) => [c.seq, c.final, c.bytes]).slice(2), [[2, true, 768]]);
  assert.strictEqual(e.requests.filter((r) => r.url.includes('/voice/input')).length, 0);
  assert.match(e.w.document.getElementById('mic-status').textContent, /입력 완료 — A: "명령"/);
  assert.strictEqual(e.state.tracksStopped, 1); assert.ok(e.state.ctxClosed >= 1);
});

test('음량 이벤트가 상태 패널 미터에 도달한다', async () => {
  const e = await build();
  await e.recording.startRecording();
  const meter = e.w.document.getElementById('voice-meter');
  assert.strictEqual(meter.hidden, false);
  e.feed(20, 0.3); await flush();
  assert.ok(Number(meter.style.getPropertyValue('--lv')) > 0.3);
  e.feed(1, 1.0);                                                  // 클리핑
  assert.strictEqual(meter.dataset.clip, '1');
  e.recording.stopRecording(); await flush();
});

test('빈 녹음도 final을 보내 서버 판단(오류 메시지)을 따른다', async () => {
  const e = await build();
  await e.recording.startRecording();
  e.recording.stopRecording(); await flush();
  assert.deepStrictEqual(e.chunks.map((c) => [c.seq, c.final, c.bytes]), [[0, true, 0]]);
});

test('끊김: 같은 seq를 다시 보내고 순서·내용이 유지된다', async () => {
  let failures = 0;
  const e = await build({ chunkFail: (seq) => seq === 1 && failures++ < 2 });
  await e.recording.startRecording();
  e.feed(24);
  e.recording.stopRecording();
  for (let i = 0; i < 30; i++) await new Promise((r) => setTimeout(r, 100));
  assert.deepStrictEqual(e.chunks.map((c) => c.seq), [0, 1, 2]);
  assert.strictEqual(failures, 3);                                 // 2번 실패 + 3번째 시도(성공) 때 카운터 증가
  assert.match(e.w.document.getElementById('mic-status').textContent, /입력 완료/);
});

test('워클릿이 없으면 MediaRecorder + /voice/input으로 폴백(동작 유지)', async () => {
  const e = await build({ noWorklet: true });
  await e.recording.startRecording();
  assert.strictEqual(e.state.mrCreated, 1);
  e.recording.stopRecording(); await flush();
  assert.strictEqual(e.requests.filter((r) => r.url.includes('/voice/input')).length, 1);
  assert.strictEqual(e.requests.filter((r) => r.url.includes('/voice/chunk')).length, 0);
});

test('addModule이 막히면(CSP 등) ctx를 닫고 같은 스트림으로 MediaRecorder 폴백', async () => {
  const e = await build({ addModuleFails: true });
  await e.recording.startRecording();
  assert.strictEqual(e.state.mrCreated, 1);
  assert.strictEqual(e.recording.isRecording, true);
  assert.ok(e.state.ctxClosed >= 1);
  assert.strictEqual(e.state.tracksStopped, 0);                    // 스트림은 MediaRecorder가 쓰는 중
  e.recording.stopRecording(); await flush();
  assert.strictEqual(e.requests.filter((r) => r.url.includes('/voice/input')).length, 1);
});

test('모델 미준비로 시작이 막히면 미리 만든 AudioContext를 닫는다', async () => {
  const e = await build();
  const orig = e.w.fetch;
  e.w.fetch = async (url, o) => String(url).includes('/stt/status')
    ? { ok: true, json: async () => ({ available: true, selected_valid: false }) } : orig(url, o);
  await e.recording.startRecording();
  assert.strictEqual(e.recording.isRecording, false);
  assert.ok(e.state.ctxClosed >= 1);
});

test('서버 끝점(endpoint)을 받으면 「녹음 종료」와 같이 final을 보낸다(한 번만)', async () => {
  const e = await build({ ackExtra: (seq) => (seq >= 1 ? { endpoint: true } : {}) });
  await e.recording.startRecording();
  e.feed(12); await flush();                                       // 청크 0: 아직
  assert.strictEqual(e.recording.isRecording, true);
  e.feed(24); await flush();                                       // 청크 1의 응답에 endpoint
  assert.strictEqual(e.recording.isRecording, false);
  for (let i = 0; i < 5; i++) await flush();
  const finals = e.chunks.filter((c) => c.final);
  assert.strictEqual(finals.length, 1);
  assert.match(e.w.document.getElementById('mic-status').textContent, /입력 완료 — A: "명령"/);
});

test('부분 결과는 상태 패널에 흐리게 보이고, 오래된 seq는 무시하며, 최종 결과가 오면 지운다', async () => {
  const partials = { 0: { text: '안녕', seq: 0, clipped: false }, 1: { text: '안녕하세요 목록', seq: 1, clipped: true }, 2: { text: '뒤늦은 옛 결과', seq: 0, clipped: false } };
  const e = await build({ ackExtra: (seq) => (partials[seq] ? { partial: partials[seq] } : {}) });
  await e.recording.startRecording();
  const el = e.w.document.getElementById('voice-partial');
  assert.strictEqual(el.hidden, true);
  e.feed(12); await flush();
  assert.strictEqual(el.hidden, false); assert.strictEqual(el.textContent, '안녕');
  e.feed(12); await flush();
  assert.strictEqual(el.textContent, '… 안녕하세요 목록');
  e.feed(12); await flush();
  assert.strictEqual(el.textContent, '… 안녕하세요 목록');          // seq가 더 오래된 결과로 되돌아가지 않는다
  assert.ok(!e.w.document.getElementById('mic-status').textContent.includes('안녕'));   // 입력·결과 줄과 섞이지 않는다
  e.recording.stopRecording(); for (let i = 0; i < 5; i++) await flush();
  assert.strictEqual(el.hidden, true); assert.strictEqual(el.textContent, '');
  assert.match(e.w.document.getElementById('mic-status').textContent, /입력 완료 — A: "명령"/);
});

// 확인 후 전송 — 청크 경로도 /voice/input과 같은 확인 상자(confirm.js)를 쓴다.
const PENDING = { text: 'ls -la', injected: false, pending_confirm: true, confirm_token: 'ctok', confirm_reason: 'shell', confirm_ttl_sec: 300, target_name: 'A' };

test('PCM 경로: 모든 청크가 X-Voice-Confirm: 1을 보내고, final의 pending_confirm이면 입력 대신 확인 상자를 띄운다', async () => {
  const e = await build({ finalData: PENDING });
  const $ = (id) => e.w.document.getElementById(id);
  await e.recording.startRecording();
  e.feed(24); await flush();
  e.recording.stopRecording(); for (let i = 0; i < 5; i++) await flush();
  assert.ok(e.chunks.length >= 2 && e.chunks.every((c) => c.headers['X-Voice-Confirm'] === '1'));
  assert.strictEqual(e.requests.filter((r) => r.url.includes('/voice/input')).length, 0);
  assert.strictEqual($('voice-confirm').hidden, false);
  assert.strictEqual($('voice-confirm-text').value, 'ls -la');
  assert.match($('voice-confirm-reason').textContent, /A · 일반 셸에 입력하기 전에 확인/);
  assert.ok(!/입력 완료/.test($('mic-status').textContent));
  assert.ok(!e.requests.some((r) => r.url.includes('/voice/inject')));
  // 고쳐서 「입력」 — 토큰·텍스트와 함께 청크 업로드에 쓴 X-Voice-Id를 보낸다(서버가 녹음 ID를 대조한다).
  $('voice-confirm-text').value = 'ls -l';
  $('voice-confirm-send').click(); await flush();
  const inj = e.requests.filter((r) => r.url.includes('/voice/inject'));
  assert.strictEqual(inj.length, 1);
  const body = JSON.parse(inj[0].opts.body);
  assert.deepStrictEqual([body.token, body.text, body.voice_id], ['ctok', 'ls -l', e.chunks[0].headers['X-Voice-Id']]);
  assert.strictEqual($('voice-confirm').hidden, true);
});

test('PCM 경로: 자동 종료(endpoint) 뒤 final이 확인 대기면 확인 상자로 간다', async () => {
  const e = await build({ finalData: PENDING, ackExtra: (seq) => (seq >= 1 ? { endpoint: true } : {}) });
  await e.recording.startRecording();
  e.feed(36); for (let i = 0; i < 5; i++) await flush();
  assert.strictEqual(e.recording.isRecording, false);
  assert.strictEqual(e.chunks.filter((c) => c.final).length, 1);
  assert.strictEqual(e.w.document.getElementById('voice-confirm').hidden, false);
  assert.ok(!e.requests.some((r) => r.url.includes('/voice/inject')));
});

test('PCM 경로: 부분 결과는 확인 모드에서도 입력·확인 상자와 무관하게 표시만 한다', async () => {
  const e = await build({ finalData: PENDING, ackExtra: (seq) => ({ partial: { text: 'ls', seq, clipped: false } }) });
  await e.recording.startRecording();
  e.feed(12); await flush();
  assert.strictEqual(e.w.document.getElementById('voice-partial').textContent, 'ls');
  assert.strictEqual(e.w.document.getElementById('voice-confirm').hidden, true);
  assert.ok(!e.requests.some((r) => r.url.includes('/voice/inject')));
  e.recording.stopRecording(); for (let i = 0; i < 5; i++) await flush();
  assert.strictEqual(e.w.document.getElementById('voice-partial').textContent, '');
  assert.strictEqual(e.w.document.getElementById('voice-confirm').hidden, false);
});

test('PCM 경로: 고정한 세션이 활성 세션과 다르면 /voice/target과 청크에 고정 세션을 쓴다', async () => {
  const e = await build();
  e.w.localStorage.setItem('vt_voice_pin', 'B');
  e.w.sessions = { A: { tmuxName: 'A' }, B: { tmuxName: 'B' } };
  e.w.getSession = (id) => e.w.sessions[id];
  await e.recording.startRecording();
  const tr = e.requests.find((r) => r.url.includes('/voice/target'));
  assert.strictEqual(JSON.parse(tr.opts.body).session_id, 'B');
  e.recording.stopRecording(); for (let i = 0; i < 5; i++) await flush();
  const chunkReqs = e.requests.filter((r) => /\/voice\/chunk\?/.test(r.url));
  assert.ok(chunkReqs.length >= 1 && chunkReqs.every((r) => /session_id=B(&|$)/.test(r.url)));
  assert.ok(chunkReqs.every((r) => r.opts.headers['X-Voice-Target'] === 'captured-A'));   // 서버가 B 대상으로 발급한 토큰(이 목에서는 고정값)
});
