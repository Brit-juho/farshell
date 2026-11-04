// voice/pcm.js — 리샘플러·PCM16 변환·청크 분할, pcm-capture 지원 판별.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = (f = 'pcm.js') => import(pathToFileURL(path.join(__dirname, '../js/voice', f)).href);

test('Resampler: 48k→16k는 3샘플 평균, 청크 경계를 넘어도 이어진다', async () => {
  const { Resampler } = await load();
  const r = new Resampler(48000);
  const a = r.process(Float32Array.from([0.3, 0.3, 0.3, 0.6, 0.6]));   // 5개: 출력 1개 + 2개 남음
  const b = r.process(Float32Array.from([0.6, 0, 0, 0]));              // 남은 2 + 1 = 0.6 평균, 다음 0
  assert.strictEqual(a.length, 1); assert.ok(Math.abs(a[0] - 0.3) < 1e-6);
  assert.strictEqual(b.length, 2); assert.ok(Math.abs(b[0] - 0.6) < 1e-6 && Math.abs(b[1]) < 1e-6);
});

test('Resampler: 44.1k 출력 개수는 길이×16000/44100에 가깝다(1초)', async () => {
  const { Resampler } = await load();
  const r = new Resampler(44100);
  let n = 0;
  for (let i = 0; i < 10; i++) n += r.process(new Float32Array(4410)).length;
  assert.ok(Math.abs(n - 16000) <= 1, String(n));
});

test('Resampler: 16k는 그대로, 8k는 두 배로 늘린다, 잘못된 입력률은 거부', async () => {
  const { Resampler } = await load();
  assert.deepStrictEqual(Array.from(new Resampler(16000).process(Float32Array.from([0.25, -0.5]))), [0.25, -0.5]);
  assert.strictEqual(new Resampler(8000).process(new Float32Array(10)).length, 20);
  assert.throws(() => new Resampler(0));
  assert.throws(() => new Resampler(NaN));
});

test('floatToPcm16: 리틀엔디언, 범위 밖·NaN은 안전하게 자름', async () => {
  const { floatToPcm16 } = await load();
  const b = floatToPcm16(Float32Array.from([0, 1, -1, 2, -2, NaN, 0.5]));
  const v = new DataView(b.buffer);
  const out = [0, 1, 2, 3, 4, 5, 6].map((i) => v.getInt16(i * 2, true));
  assert.deepStrictEqual(out, [0, 32767, -32768, 32767, -32768, 0, 16384]);
  assert.strictEqual(b.length, 14);
});

test('Chunker: 정확히 chunkBytes 단위로 내보내고 나머지는 flush', async () => {
  const { Chunker } = await load();
  const c = new Chunker(4);
  assert.deepStrictEqual(c.push(Uint8Array.from([1, 2, 3])), []);
  const out = c.push(Uint8Array.from([4, 5, 6, 7, 8, 9]));
  assert.deepStrictEqual(out.map((x) => Array.from(x)), [[1, 2, 3, 4], [5, 6, 7, 8]]);
  assert.deepStrictEqual(Array.from(c.flush()), [9]);
  assert.strictEqual(c.flush().length, 0);
});

test('상수: 청크 16000바이트(500ms), 60초 상한 = 서버 상한 1,920,000바이트', async () => {
  const { CHUNK_BYTES, MAX_RECORD_BYTES } = await load();
  assert.strictEqual(CHUNK_BYTES, 16000);
  assert.strictEqual(MAX_RECORD_BYTES, 1920000);
});

test('supportsPcmCapture: 워클릿·보안 컨텍스트가 없으면 false(→ MediaRecorder 폴백)', async () => {
  const { supportsPcmCapture, primeAudioContext } = await load('pcm-capture.js');
  const ok = { AudioContext: function () {}, AudioWorkletNode: function () {}, isSecureContext: true, Blob, URL };
  assert.strictEqual(supportsPcmCapture(ok), true);
  assert.strictEqual(supportsPcmCapture({ ...ok, AudioWorkletNode: undefined }), false);
  assert.strictEqual(supportsPcmCapture({ ...ok, isSecureContext: false }), false);
  assert.strictEqual(supportsPcmCapture({}), false);
  assert.strictEqual(primeAudioContext({}), null);
});

test('primeAudioContext: 기기 기본 샘플레이트로 만들고 resume을 호출, 생성 실패는 null', async () => {
  const { primeAudioContext } = await load('pcm-capture.js');
  const calls = [];
  function AC(opts) { calls.push(opts); this.resume = () => { calls.push('resume'); return Promise.resolve(); }; }
  const g = { AudioContext: AC, AudioWorkletNode: function () {}, isSecureContext: true, Blob, URL };
  assert.ok(primeAudioContext(g));
  assert.ok(!calls[0].sampleRate);
  assert.strictEqual(calls[1], 'resume');
  assert.strictEqual(primeAudioContext({ ...g, AudioContext: function () { throw new Error('no'); } }), null);
});

// 가짜 AudioContext/AudioWorkletNode — 워클릿이 보낸 Float32 블록이 청크·레벨·상한으로 이어지는지.
function fakeAudio(rate) {
  const state = { node: null, closed: false, added: [], revoked: [] };
  class Node { constructor() { this.port = { postMessage: (m) => { if (m === 'stop') setTimeout(() => this.port.onmessage({ data: 'end' }), 0); }, onmessage: null }; state.node = this; } connect() {} disconnect() {} }
  const ctx = {
    sampleRate: rate, state: 'running', destination: {},
    audioWorklet: { addModule: async (u) => { state.added.push(u); } },
    createMediaStreamSource: () => new Node(), createGain: () => ({ gain: {}, connect() {}, disconnect() {} }),
    close: () => { state.closed = true; return Promise.resolve(); }, resume: async () => {},
  };
  const g = { AudioWorkletNode: Node, Blob, URL: { createObjectURL: () => 'blob:x', revokeObjectURL: (u) => state.revoked.push(u) } };
  return { ctx, g, state, post: (f32) => state.node.port.onmessage({ data: f32 }) };
}
const tracks = () => { const s = { stopped: 0 }; return { s, stream: { getTracks: () => [{ stop: () => s.stopped++ }] } }; };

test('startPcmCapture: 48k 블록 → 16000바이트 청크, 레벨 콜백, stop은 나머지를 돌려주고 정리', async () => {
  const { startPcmCapture } = await load('pcm-capture.js');
  const a = fakeAudio(48000); const t = tracks();
  const chunks = [], levels = [];
  const cap = await startPcmCapture(a.ctx, t.stream, { onChunk: (c) => chunks.push(c), onLevel: (l) => levels.push(l) }, a.g);
  for (let i = 0; i < 12; i++) a.post(new Float32Array(2048).fill(0.1));   // 24576샘플@48k = 8192샘플@16k = 16384바이트
  assert.strictEqual(chunks.length, 1); assert.strictEqual(chunks[0].length, 16000);
  assert.strictEqual(levels.length, 12);
  const rest = await cap.stop();
  assert.strictEqual(rest.length, 384);
  assert.strictEqual(t.s.stopped, 1); assert.ok(a.state.closed); assert.deepStrictEqual(a.state.revoked, ['blob:x']);
  assert.strictEqual((await cap.stop()).length, 0);          // 두 번째 stop은 무해
});

test('startPcmCapture: 60초(1,920,000바이트)를 넘는 입력은 버리고 onLimit을 한 번 부른다', async () => {
  const { startPcmCapture } = await load('pcm-capture.js');
  const a = fakeAudio(16000); const t = tracks();
  let total = 0, limits = 0;
  const cap = await startPcmCapture(a.ctx, t.stream, { onChunk: (c) => { total += c.length; }, onLimit: () => limits++ }, a.g);
  for (let i = 0; i < 16000 * 62 / 2048; i++) a.post(new Float32Array(2048).fill(0.1));
  const rest = await cap.stop();
  assert.strictEqual(total + rest.length, 1920000);
  assert.strictEqual(limits, 1);
});

test('startPcmCapture: 컨텍스트가 돌지 않거나 addModule이 실패하면 던지고 ctx를 닫는다(→ 폴백)', async () => {
  const { startPcmCapture } = await load('pcm-capture.js');
  const a = fakeAudio(48000); a.ctx.state = 'suspended'; a.ctx.resume = async () => {};
  await assert.rejects(startPcmCapture(a.ctx, tracks().stream, {}, a.g));
  assert.ok(a.state.closed);
  const b = fakeAudio(48000); b.ctx.audioWorklet.addModule = async () => { throw new Error('blocked'); };
  await assert.rejects(startPcmCapture(b.ctx, tracks().stream, {}, b.g), /blocked/);
  assert.ok(b.state.closed && b.state.revoked.length === 1);
});
