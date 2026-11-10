// AudioWorklet 프로세서 — pcm-capture.js가 `audioWorklet.addModule('/static/js/voice/pcm-worklet.js')`로 올린다.
// 정적 파일인 이유: 문서 CSP가 `script-src 'self'`라서 Blob URL 모듈은 막힌다(워클릿 로드는 script-src를 따른다).
// 이 파일은 vite 번들 밖의 classic 스크립트다. 이름·블록 크기는 pcm-worklet-src.js의 상수와 같아야 한다
// (frontend/tests/voice-pcm.test.js가 대조한다).
//
// 프로세서는 일부러 단순하다: 128프레임 렌더 퀀텀을 BLOCK 샘플씩 모아 메인 스레드로 보낸다(리샘플·PCM16 변환·
// 미터는 메인 스레드의 pcm.js/meter.js — 순수 함수라 테스트가 된다). 첫 채널만 쓴다.
class FshPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
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
registerProcessor('fsh-pcm-capture', FshPcmCapture);
