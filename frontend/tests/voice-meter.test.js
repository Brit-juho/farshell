// voice/meter.js — RMS·노이즈 플로어 적응·평활화(상승 빠름/하강 느림)·클리핑 표시.
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const load = () => import(pathToFileURL(path.join(__dirname, '../js/voice/meter.js')).href);
const FRAME = 43;   // 2048샘플 @ 48kHz

function run(m, rms, peak, ms) {
  let r;
  for (let t = 0; t < ms; t += FRAME) r = m.update(rms, peak, FRAME);
  return r;
}

test('measure: RMS·peak, 빈 입력은 0', async () => {
  const { measure } = await load();
  assert.deepStrictEqual(measure(new Float32Array(0)), { rms: 0, peak: 0 });
  const r = measure(Float32Array.from([0.5, -0.5, 0.5, -0.5]));
  assert.ok(Math.abs(r.rms - 0.5) < 1e-6 && r.peak === 0.5);
});

test('무음이면 레벨 0', async () => {
  const { createMeter } = await load();
  const m = createMeter();
  assert.strictEqual(run(m, 0, 0, 1000).level, 0);
});

test('상승은 빠르고 하강은 느리다', async () => {
  const { createMeter, ATTACK_MS, RELEASE_MS } = await load();
  const m = createMeter();
  run(m, 0.002, 0.01, 1000);                       // 배경 소음
  const up = run(m, 0.2, 0.5, 4 * ATTACK_MS);      // 말 시작
  assert.ok(up.level > 0.6, `상승 ${up.level}`);
  const loud = run(m, 0.2, 0.5, 500).level;
  const down = run(m, 0.002, 0.01, ATTACK_MS * 4).level;   // 같은 시간 동안 하강
  assert.ok(down > loud * 0.5, `하강이 상승보다 느려야 한다: ${down} vs ${loud}`);
  assert.ok(run(m, 0.002, 0.01, RELEASE_MS * 6).level < 0.05, '충분히 지나면 거의 0');
});

test('레벨은 항상 0~1', async () => {
  const { createMeter } = await load();
  const m = createMeter();
  for (const rms of [0, 1, 5, -1, NaN, 1e-12]) {
    const r = m.update(rms, 1, FRAME);
    assert.ok(r.level >= 0 && r.level <= 1, String(rms));
  }
});

test('노이즈 플로어 적응: 같은 배경 소음이 오래 이어지면 미터가 가라앉는다', async () => {
  const { createMeter } = await load();
  const m = createMeter();
  run(m, 0.0005, 0.002, 1000);                      // 아주 조용한 방 → 플로어 낮음
  const first = m.update(0.02, 0.05, FRAME).level;  // 갑자기 시끄러운 배경
  const later = run(m, 0.02, 0.05, 60000).level;    // 오래 이어짐
  assert.ok(later < Math.max(first, 0.99) && later <= 1);
  const lowFloor = createMeter(); run(lowFloor, 0.0005, 0.002, 1000);
  const hiFloor = createMeter(); run(hiFloor, 0.02, 0.05, 60000);
  assert.ok(run(lowFloor, 0.05, 0.1, 400).level > run(hiFloor, 0.05, 0.1, 400).level, '플로어가 높으면 같은 소리도 덜 올라온다');
});

test('플로어는 상한을 넘지 않는다(아주 시끄러운 입력이 계속돼도 미터가 죽지 않음)', async () => {
  const { createMeter, FLOOR_MAX } = await load();
  const m = createMeter();
  const r = run(m, 0.5, 0.9, 5 * 60 * 1000);
  assert.ok(r.floor <= FLOOR_MAX + 1e-12);
});

test('클리핑 표시는 CLIP_HOLD_MS 동안 유지된 뒤 꺼진다', async () => {
  const { createMeter, CLIP_HOLD_MS } = await load();
  const m = createMeter();
  assert.strictEqual(m.update(0.3, 0.5, FRAME).clip, false);
  assert.strictEqual(m.update(0.9, 1.0, FRAME).clip, true);
  assert.strictEqual(m.update(0.3, 0.5, CLIP_HOLD_MS - 100).clip, true);
  assert.strictEqual(m.update(0.3, 0.5, 200).clip, false);
});

test('reset은 초기 상태로 되돌린다', async () => {
  const { createMeter, FLOOR_INIT } = await load();
  const m = createMeter();
  run(m, 0.3, 1, 1000);
  m.reset();
  const r = m.update(0, 0, FRAME);
  assert.strictEqual(r.level, 0);
  assert.strictEqual(r.clip, false);
  assert.ok(r.floor <= FLOOR_INIT);
});
