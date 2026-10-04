// voice/capture.js — 순수 함수(형식 선택·Content-Type·제약·에러 분류·재시도) 단위 테스트.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = () => import(pathToFileURL(path.join(__dirname, '../js/voice/capture.js')).href);
const supportOnly = (...list) => (t) => list.includes(t);

test('pickRecorderMime: webm 지원이면 opus webm 우선', async () => {
  const { pickRecorderMime } = await load();
  assert.strictEqual(pickRecorderMime(supportOnly('audio/webm;codecs=opus', 'audio/webm', 'audio/mp4')), 'audio/webm;codecs=opus');
  assert.strictEqual(pickRecorderMime(supportOnly('audio/webm')), 'audio/webm');
});

test('pickRecorderMime: mp4만 지원(iPhone Safari)', async () => {
  const { pickRecorderMime } = await load();
  assert.strictEqual(pickRecorderMime(supportOnly('audio/mp4')), 'audio/mp4');
});

test('pickRecorderMime: 아무것도 지원 안 하면 빈 문자열(브라우저 기본), 함수 없어도 안전', async () => {
  const { pickRecorderMime } = await load();
  assert.strictEqual(pickRecorderMime(() => false), '');
  assert.strictEqual(pickRecorderMime(undefined), '');
  assert.strictEqual(pickRecorderMime(() => { throw new Error('x'); }), '');
});

test('contentTypeOf: codecs 제거·정규화·알 수 없는 형식', async () => {
  const { contentTypeOf } = await load();
  assert.strictEqual(contentTypeOf('audio/webm;codecs=opus'), 'audio/webm');
  assert.strictEqual(contentTypeOf('audio/mp4;codecs=mp4a.40.2'), 'audio/mp4');
  assert.strictEqual(contentTypeOf('audio/ogg; codecs=opus'), 'audio/ogg');
  assert.strictEqual(contentTypeOf('AUDIO/WAV'), 'audio/wav');
  assert.strictEqual(contentTypeOf('audio/x-wav'), 'audio/wav');
  assert.strictEqual(contentTypeOf(''), 'audio/webm');
  assert.strictEqual(contentTypeOf(undefined), 'audio/webm');
  assert.strictEqual(contentTypeOf('audio/flac'), 'audio/webm');
});

test('buildAudioConstraints / buildRecorderOptions 형태', async () => {
  const { buildAudioConstraints, buildRecorderOptions } = await load();
  assert.deepStrictEqual(buildAudioConstraints(), {
    channelCount: 1, sampleRate: { ideal: 16000 },
    echoCancellation: true, noiseSuppression: true, autoGainControl: true,
  });
  assert.deepStrictEqual(buildRecorderOptions('audio/mp4'), { audioBitsPerSecond: 32000, mimeType: 'audio/mp4' });
  assert.deepStrictEqual(buildRecorderOptions(''), { audioBitsPerSecond: 32000 });
});

test('classifyMicError 4종 + 안내 문구', async () => {
  const { classifyMicError, micErrorMessage } = await load();
  assert.strictEqual(classifyMicError({ name: 'NotAllowedError' }), '권한');
  assert.strictEqual(classifyMicError({ name: 'NotFoundError' }), '마이크 없음');
  assert.strictEqual(classifyMicError({ name: 'NotSupportedError' }), '이 브라우저는 녹음 형식을 지원하지 않음');
  assert.strictEqual(classifyMicError({ name: 'TypeError' }), '이 브라우저는 녹음 형식을 지원하지 않음');
  assert.strictEqual(classifyMicError({ name: 'NotReadableError' }), '마이크를 열 수 없음');
  assert.strictEqual(classifyMicError(null), '마이크를 열 수 없음');
  const msgs = new Set(['NotAllowedError', 'NotFoundError', 'NotSupportedError', 'Other'].map((n) => micErrorMessage({ name: n })));
  assert.strictEqual(msgs.size, 4);
});

test('retryWithBackoff: 첫 시도 성공이면 sleep 없음', async () => {
  const { retryWithBackoff } = await load();
  const sleeps = [];
  const r = await retryWithBackoff(async () => 'ok', [1000, 3000], async (ms) => { sleeps.push(ms); });
  assert.strictEqual(r, 'ok');
  assert.deepStrictEqual(sleeps, []);
});

test('retryWithBackoff: 재시도 후 성공', async () => {
  const { retryWithBackoff } = await load();
  const sleeps = []; let n = 0;
  const r = await retryWithBackoff(async () => { if (++n < 3) throw new Error('net'); return n; }, [1000, 3000], async (ms) => { sleeps.push(ms); });
  assert.strictEqual(r, 3);
  assert.deepStrictEqual(sleeps, [1000, 3000]);
});

test('retryWithBackoff: 끝내 실패하면 마지막 에러, 총 1+delays회', async () => {
  const { retryWithBackoff } = await load();
  const sleeps = []; let n = 0;
  await assert.rejects(
    retryWithBackoff(async () => { throw new Error('e' + (++n)); }, [1000, 3000], async (ms) => { sleeps.push(ms); }),
    /e3/,
  );
  assert.strictEqual(n, 3);
  assert.deepStrictEqual(sleeps, [1000, 3000]);
});

test('retryWithBackoff: noRetry 에러는 즉시 포기', async () => {
  const { retryWithBackoff } = await load();
  let n = 0;
  await assert.rejects(retryWithBackoff(async () => { n++; throw Object.assign(new Error('x'), { noRetry: true }); }, [1, 1], async () => {}));
  assert.strictEqual(n, 1);
});

test('newVoiceId — randomUUID가 있으면 그것을, 없으면 영숫자 24자 대체 값을 쓴다', async () => {
  const { newVoiceId } = await load();
  assert.strictEqual(newVoiceId(Math.random, { randomUUID: () => 'abc-123' }), 'abc-123');
  const id = newVoiceId(() => 0.5, null);
  assert.match(id, /^[a-z0-9]{24}$/);
  assert.notStrictEqual(newVoiceId(() => 0.1, null), newVoiceId(() => 0.9, null));
});
