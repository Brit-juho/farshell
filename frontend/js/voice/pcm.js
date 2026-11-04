// PCM 변환 순수 함수 — 마이크 Float32 샘플을 서버가 받는 16kHz mono PCM16(LE)으로 바꾼다.
//
// DOM·전역 접근이 없어 단위 테스트가 된다. voice 번들 안에서만 쓰며 core/store.js·core/dom.js는 import하지 않는다
// (recording.js 상단 경고 참조).

export const TARGET_RATE = 16000;
export const CHUNK_MS = 500;
export const CHUNK_BYTES = (TARGET_RATE * CHUNK_MS / 1000) * 2;     // 16000바이트
export const MAX_RECORD_SEC = 60;
export const MAX_RECORD_BYTES = MAX_RECORD_SEC * TARGET_RATE * 2;   // 서버 상한(voice_chunks.MAX_TOTAL_BYTES)과 같다

/**
 * 스트리밍 면적 평균 리샘플러. 입력 샘플 하나하나를 "inRate/16000 폭의 구간"에 나눠 담아 평균을 낸다.
 * 다운샘플에서는 구간 평균이 간단한 저역 통과 필터를 겸해 에일리어싱을 줄인다(음성 인식 용도로 충분하다).
 * 청크 경계를 넘는 구간은 상태로 이어 간다.
 */
export class Resampler {
  constructor(inRate, outRate = TARGET_RATE) {
    if (!(inRate > 0) || !isFinite(inRate)) throw new RangeError('inRate');
    this.step = inRate / outRate;        // 출력 샘플 하나가 차지하는 입력 샘플 수
    this.acc = 0;                        // 현재 출력 구간의 가중합
    this.filled = 0;                     // 현재 구간에 이미 채운 폭
  }

  /** Float32Array(-1..1) → Float32Array(출력 샘플). 남는 부분은 다음 호출로 넘어간다. */
  process(input) {
    const out = [];
    const step = this.step;
    for (let i = 0; i < input.length; i++) {
      const x = input[i];
      let w = 1;
      while (w > 1e-9) {
        const take = Math.min(w, step - this.filled);
        this.acc += x * take;
        this.filled += take;
        w -= take;
        if (this.filled >= step - 1e-9) {
          out.push(this.acc / step);
          this.acc = 0; this.filled = 0;
        }
      }
    }
    return Float32Array.from(out);
  }
}

/** Float32(-1..1) → PCM16 LE 바이트. 범위를 벗어나거나 NaN인 값은 안전하게 자른다. */
export function floatToPcm16(samples) {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) {
    let s = samples[i];
    if (!(s === s)) s = 0;
    s = s < -1 ? -1 : s > 1 ? 1 : s;
    view.setInt16(i * 2, s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff), true);
  }
  return new Uint8Array(buf);
}

/** 바이트를 모아 정확히 chunkBytes 단위로 내보낸다. 마지막 남은 조각은 flush()로 받는다. */
export class Chunker {
  constructor(chunkBytes = CHUNK_BYTES) {
    this.size = chunkBytes;
    this.buf = new Uint8Array(chunkBytes);
    this.len = 0;
  }

  push(bytes) {
    const out = [];
    let off = 0;
    while (off < bytes.length) {
      const n = Math.min(this.size - this.len, bytes.length - off);
      this.buf.set(bytes.subarray(off, off + n), this.len);
      this.len += n; off += n;
      if (this.len === this.size) { out.push(this.buf.slice()); this.len = 0; }
    }
    return out;
  }

  /** 모인 나머지(없으면 빈 배열). 호출 뒤 비워진다. */
  flush() {
    const rest = this.buf.slice(0, this.len);
    this.len = 0;
    return rest;
  }
}
