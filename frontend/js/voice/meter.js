// 음량 미터 — RMS + 노이즈 플로어 적응식 레벨(0~1). 순수 함수/상태 객체, DOM 없음.
//
// 레벨 = (현재 RMS가 노이즈 플로어보다 몇 dB 위인가)를 0~1로 편 값.
// - 노이즈 플로어: 조용해지면 빠르게 따라 내려가고, 큰 소리에서는 아주 천천히만 올라간다
//   (말하는 동안 플로어가 같이 올라 미터가 죽는 것을 막는다).
// - 평활화: 상승은 빠르게(attack), 하강은 느리게(release) — 막대가 튀지 않고 말끝이 부드럽게 줄어든다.
// - 클리핑: 한 프레임이라도 |샘플| >= CLIP_LEVEL이면 CLIP_HOLD_MS 동안 clip=true.
// 시간은 호출부가 dtMs로 넘긴다(테스트에서 결정적이게).

export const CLIP_LEVEL = 0.98;
export const CLIP_HOLD_MS = 600;
export const ATTACK_MS = 40;
export const RELEASE_MS = 450;
export const FLOOR_MIN = 0.0005;            // 약 -66dBFS
export const FLOOR_MAX = 0.05;              // 약 -26dBFS — 시끄러운 방에서도 이 이상은 플로어로 보지 않는다
export const FLOOR_INIT = 0.003;
export const FLOOR_FALL_MS = 300;
export const FLOOR_RISE_MS = 15000;
export const MARGIN_DB = 6;                 // 플로어 위 이만큼까지는 0으로 본다
export const RANGE_DB = 30;                 // 그 위로 이만큼 올라가면 1

/** Float32 프레임의 { rms, peak }. 빈 배열이면 0. */
export function measure(samples) {
  const n = samples.length;
  if (!n) return { rms: 0, peak: 0 };
  let sum = 0, peak = 0;
  for (let i = 0; i < n; i++) {
    const v = samples[i];
    sum += v * v;
    const a = v < 0 ? -v : v;
    if (a > peak) peak = a;
  }
  return { rms: Math.sqrt(sum / n), peak };
}

const db = (x) => 20 * Math.log10(Math.max(x, 1e-9));
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

export function createMeter() {
  let floor = FLOOR_INIT;
  let level = 0;
  let clipLeft = 0;

  return {
    /** 프레임 하나를 반영하고 { level, clip, floor }를 돌려준다. dtMs: 이 프레임의 길이(ms). */
    update(rms, peak, dtMs) {
      const dt = dtMs > 0 ? dtMs : 0;
      if (!(rms >= 0)) rms = 0;
      // 노이즈 플로어: 아래로는 빠르게(순간적인 무음 한 프레임에 쏠리지 않을 만큼만), 위로는 아주 느리게.
      if (rms < floor) floor = Math.max(FLOOR_MIN, floor + (rms - floor) * Math.min(1, dt / FLOOR_FALL_MS));
      else floor = Math.min(FLOOR_MAX, floor + (rms - floor) * Math.min(1, dt / FLOOR_RISE_MS));
      const raw = clamp01((db(rms) - (db(floor) + MARGIN_DB)) / RANGE_DB);
      const tau = raw > level ? ATTACK_MS : RELEASE_MS;
      level += (raw - level) * (1 - Math.exp(-dt / tau));
      level = clamp01(level);
      if (peak >= CLIP_LEVEL) clipLeft = CLIP_HOLD_MS; else clipLeft = Math.max(0, clipLeft - dt);
      return { level, clip: clipLeft > 0, floor };
    },
    reset() { floor = FLOOR_INIT; level = 0; clipLeft = 0; },
  };
}
