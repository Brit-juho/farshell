// AudioWorklet 프로세서 소스(문자열). pcm-capture.js가 Blob URL로 addModule 한다.
//
// 별도 asset 파일로 두지 않은 이유: voice.js는 고정 이름 단일 lib 산출물이고(vite.config.js, VOICE_BUILD=1),
// frontend/sw.js는 정해진 파일만 precache한다. 워클릿을 따로 서빙하면 산출물/SW 목록이 늘고 `?raw` import는
// 테스트용 vm-esm 로더가 못 읽는다. 문자열 상수 + Blob URL이면 번들 구조·SW를 건드리지 않는다.
// CSP는 서버가 frame-ancestors만 건다(security_headers.py) — script-src 제한이 없어 blob: 모듈이 막히지 않는다.
//
// 프로세서는 일부러 단순하다: 128프레임 렌더 퀀텀을 BLOCK 샘플씩 모아 메인 스레드로 보낸다(리샘플·PCM16 변환·
// 미터는 메인 스레드의 pcm.js/meter.js — 순수 함수라 테스트가 된다). 첫 채널만 쓴다.
export const WORKLET_NAME = 'fsh-pcm-capture';
export const WORKLET_BLOCK = 2048;

export const WORKLET_SOURCE = `
class FshPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(${WORKLET_BLOCK});
    this.len = 0;
    this.on = true;
    this.port.onmessage = (e) => { if (e.data === 'stop') this.on = false; };
  }
  process(inputs) {
    if (!this.on) { this.flush(); this.port.postMessage('end'); return false; }
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let off = 0;
    while (off < ch.length) {
      const n = Math.min(this.buf.length - this.len, ch.length - off);
      this.buf.set(ch.subarray(off, off + n), this.len);
      this.len += n; off += n;
      if (this.len === this.buf.length) this.flush();
    }
    return true;
  }
  flush() {
    if (!this.len) return;
    const out = this.buf.slice(0, this.len);
    this.len = 0;
    this.port.postMessage(out, [out.buffer]);
  }
}
registerProcessor('${WORKLET_NAME}', FshPcmCapture);
`;
