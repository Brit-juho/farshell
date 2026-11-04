// voice/chunk-upload.js — 순서 전송·ack 정리·재전송·서버 상태 유실·4xx·취소.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = () => import(pathToFileURL(path.join(__dirname, '../js/voice/chunk-upload.js')).href);
const bytes = (n) => Uint8Array.from([n, 0]);
const ok = (data) => ({ res: { ok: true, status: 200 }, data });
const flush = () => new Promise((r) => setImmediate(r));

// 서버 흉내: 누적 ack, 마지막 final까지 모이면 complete.
function fakeServer({ failFirst = {}, lose = new Set() } = {}) {
  const got = new Map(); let finalSeq = null; const log = []; const fails = { ...failFirst };
  async function send(seq, b, final) {
    log.push(seq + (final ? 'F' : ''));
    if (fails[seq] > 0) { fails[seq]--; throw new TypeError('network'); }
    if (lose.has(seq)) { lose.delete(seq); got.set(seq, b); if (final) finalSeq = seq; throw new TypeError('response lost'); }  // 서버는 받았는데 응답 유실
    got.set(seq, b); if (final) finalSeq = seq;
    let ack = -1; while (got.has(ack + 1)) ack++;
    if (finalSeq !== null && ack === finalSeq) return ok({ ackSeq: ack, complete: true, text: 'done' });
    return ok({ ackSeq: ack, complete: false });
  }
  return { send, got, log };
}

const noSleep = async () => {};

test('정상: 순서대로 보내고 final 응답이 결과', async () => {
  const { createChunkUploader } = await load();
  const s = fakeServer();
  const up = createChunkUploader({ send: s.send, sleep: noSleep });
  up.push(bytes(1)); up.push(bytes(2)); up.finish(bytes(3));
  const r = await up.result();
  assert.strictEqual(r.data.text, 'done');
  assert.deepStrictEqual(s.log, ['0', '1', '2F']);
  assert.strictEqual(up.pendingChunks, 0);
});

test('빈 나머지도 final로 보낸다(서버는 빈 final을 받는다)', async () => {
  const { createChunkUploader } = await load();
  const s = fakeServer();
  const up = createChunkUploader({ send: s.send, sleep: noSleep });
  up.push(bytes(1)); up.finish();
  await up.result();
  assert.deepStrictEqual(s.log, ['0', '1F']);
});

test('끊김: 같은 seq를 다시 보내고 중간에 쌓이는 조각은 보관한다', async () => {
  const { createChunkUploader } = await load();
  const s = fakeServer({ failFirst: { 1: 5 } });         // seq 1이 backoff 3회(총 4번) + 한 번 더 실패
  const up = createChunkUploader({ send: s.send, sleep: noSleep });
  up.push(bytes(1)); up.push(bytes(2)); up.push(bytes(3));
  await flush();
  up.finish(bytes(4));
  const r = await up.result();
  assert.strictEqual(r.data.complete, true);
  assert.deepStrictEqual([...s.got.keys()], [0, 1, 2, 3]);
  assert.ok(s.log.filter((x) => x === '1').length >= 6);
  assert.deepStrictEqual(s.log.slice(-2), ['2', '3F']);
});

test('응답만 유실: 같은 seq 재전송은 서버가 무시하고 ack만 돌려준다', async () => {
  const { createChunkUploader } = await load();
  const s = fakeServer({ lose: new Set([1]) });
  const up = createChunkUploader({ send: s.send, sleep: noSleep });
  up.push(bytes(1)); up.push(bytes(2)); up.finish(bytes(3));
  const r = await up.result();
  assert.strictEqual(r.data.text, 'done');
  assert.deepStrictEqual(s.log, ['0', '1', '1', '2F']);
});

test('오래 끊기면 giveUpMs 뒤 거부하고 청크는 보관', async () => {
  const { createChunkUploader } = await load();
  let t = 0; const s = fakeServer({ failFirst: { 0: 1e9 } });
  const up = createChunkUploader({ send: s.send, sleep: async (ms) => { t += ms; }, now: () => t, giveUpMs: 5000 });
  up.push(bytes(1)); up.finish();
  await assert.rejects(up.result());
  assert.strictEqual(up.pendingChunks, 2);                // 보관 중
});

test('result() 재호출은 보관한 청크를 이어서 보낸다', async () => {
  const { createChunkUploader } = await load();
  let t = 0; let down = true; const inner = fakeServer();
  const send = (seq, b, f) => { if (down) return Promise.reject(new TypeError('net')); return inner.send(seq, b, f); };
  const up = createChunkUploader({ send, sleep: async (ms) => { t += ms; }, now: () => t, giveUpMs: 3000 });
  up.push(bytes(1)); up.finish(bytes(2));
  await assert.rejects(up.result());
  down = false;
  const r = await up.result();
  assert.strictEqual(r.data.complete, true);
  assert.deepStrictEqual([...inner.got.keys()], [0, 1]);
});

test('서버가 상태를 잃으면(ack < seq) 재시도 없이 실패', async () => {
  const { createChunkUploader } = await load();
  const send = async (seq) => ok({ ackSeq: seq === 0 ? 0 : -1, complete: false });
  const up = createChunkUploader({ send, sleep: noSleep });
  up.push(bytes(1)); up.push(bytes(2));
  await assert.rejects(up.result(), /lost/);
});

test('4xx는 재시도 없이 결과로 돌려준다(오류 메시지는 호출부가 표시)', async () => {
  const { createChunkUploader } = await load();
  let n = 0;
  const send = async () => { n++; return { res: { ok: false, status: 410 }, data: { error: '만료', code: 'voice_expired' } }; };
  const up = createChunkUploader({ send, sleep: noSleep });
  up.push(bytes(1)); up.finish();
  const r = await up.result();
  assert.strictEqual(r.res.status, 410); assert.strictEqual(n, 1);
});

test('늦게 채워진 누락 청크의 응답(complete)도 최종 결과다', async () => {
  const { createChunkUploader } = await load();
  const send = async (seq, b, final) => ok(final ? { ackSeq: seq - 1, complete: false } : seq === 0 ? { ackSeq: 0, complete: false } : { ackSeq: 1, complete: true, text: 'late' });
  const up = createChunkUploader({ send, sleep: noSleep });
  up.push(bytes(1)); up.push(bytes(2));
  const r = await up.result();
  assert.strictEqual(r.data.text, 'late');
});

test('취소: 더 보내지 않고 서버 취소를 한 번 호출, result는 거부', async () => {
  const { createChunkUploader } = await load();
  let cancels = 0; const s = fakeServer();
  const up = createChunkUploader({ send: s.send, cancel: () => { cancels++; }, sleep: noSleep });
  const p = up.result();
  up.push(bytes(1)); up.cancel(); up.cancel(); up.push(bytes(2));
  await assert.rejects(p, /cancelled/);
  assert.strictEqual(cancels, 1);
  assert.ok(!s.log.includes('1'));
});

test('makeChunkSender: URL·헤더, 429는 던지고 4xx는 반환', async () => {
  const { makeChunkSender } = await load();
  const seen = [];
  let status = 200;
  const apiFetch = async (url, opts) => { seen.push({ url, opts }); return { ok: status < 400, status, json: async () => ({ ackSeq: 1 }) }; };
  const send = makeChunkSender({ apiFetch, base: 'http://h', voiceId: 'v-1', token: 'tok', sessionId: 's 1' });
  await send(3, bytes(1), true);
  assert.strictEqual(seen[0].url, 'http://h/voice/chunk?seq=3&final=1&session_id=s%201');
  assert.strictEqual(seen[0].opts.headers['X-Voice-Id'], 'v-1');
  assert.strictEqual(seen[0].opts.headers['X-Voice-Target'], 'tok');
  status = 429;
  await assert.rejects(send(4, bytes(1), false), (e) => e.status === 429);
  status = 409;
  assert.strictEqual((await send(4, bytes(1), false)).res.status, 409);
});

test('완결 전 ack 응답은 onAck로 넘기고, onAck가 던져도 업로드는 계속한다', async () => {
  const { createChunkUploader } = await load();
  const seen = [];
  const send = async (seq, b, final) => (final ? ok({ ackSeq: seq, complete: true, text: 'done' })
    : ok({ ackSeq: seq, complete: false, partial: { text: 'p' + seq, seq, clipped: false } }));
  const up = createChunkUploader({ send, sleep: noSleep, onAck: (d) => { seen.push(d.partial.text); if (d.ackSeq === 0) throw new Error('ui bug'); } });
  up.push(bytes(1)); up.push(bytes(2)); up.finish();
  const r = await up.result();
  assert.strictEqual(r.data.text, 'done');
  assert.deepStrictEqual(seen, ['p0', 'p1']);                       // final(complete) 응답은 onAck로 가지 않는다
});
