// AudioWorklet으로 마이크를 16kHz PCM16 청크로 캡처한다. recording.js의 MediaRecorder 경로가 못 쓰일 때를 위해
// 지원 판별과 시작 실패를 모두 호출부가 받아 폴백하게 한다.
//
// iOS Safari: AudioContext는 사용자 제스처 안에서 만들고 resume()해야 소리를 처리한다. 그래서 컨텍스트는 제스처
// 안(startRecording 맨 앞, 비동기 대기 전)에서 primeAudioContext()로 만든다. 보안 컨텍스트(https/localhost)가
// 아니면 getUserMedia도 AudioWorklet도 없으므로 지원 안 함으로 본다.
//
// core/store.js·core/dom.js를 import하지 않는다(recording.js 상단 경고).
import { WORKLET_NAME, WORKLET_URL } from './pcm-worklet-src.js';
import { Resampler, floatToPcm16, Chunker, MAX_RECORD_BYTES } from './pcm.js';
import { measure, createMeter } from './meter.js';

export function supportsPcmCapture(g = globalThis) {
  const AC = g.AudioContext || g.webkitAudioContext;
  return !!(AC && g.AudioWorkletNode && g.isSecureContext !== false);
}

/** 사용자 제스처 안에서 호출한다. 지원하지 않으면 null. 컨텍스트는 호출부가 닫을 책임이 있다(startPcmCapture가 닫기도 한다). */
export function primeAudioContext(g = globalThis) {
  if (!supportsPcmCapture(g)) return null;
  const AC = g.AudioContext || g.webkitAudioContext;
  let ctx;
  // 샘플레이트는 기기 기본값을 쓰고 16kHz 변환은 pcm.js가 한다. 16kHz를 요청하면 Firefox처럼 스트림과 컨텍스트의
  // 샘플레이트가 다를 때 연결 자체를 거부하는 브라우저가 있어서, 어디서나 같은 길을 타게 한다.
  try { ctx = new AC({ latencyHint: 'interactive' }); } catch { return null; }
  try { const p = ctx.resume(); if (p && p.catch) p.catch(() => {}); } catch { /* 아래 start에서 상태를 본다 */ }
  return ctx;
}

/**
 * 스트림을 캡처해 청크를 내보낸다.
 * @param ctx primeAudioContext()의 결과
 * @param stream getUserMedia 스트림(소유권은 호출부 — stop()이 트랙을 멈춘다)
 * @param cb { onChunk(Uint8Array 16000바이트), onLevel({level,clip}), onLimit() }
 * @returns {{ stop(): Promise<Uint8Array> 마지막 남은 바이트, sampleRate }}
 */
export async function startPcmCapture(ctx, stream, cb, g = globalThis) {
  let node = null, src = null, sink = null;
  const cleanup = () => {
    try { node && node.disconnect(); } catch {}
    try { src && src.disconnect(); } catch {}
    try { sink && sink.disconnect(); } catch {}
    try { ctx.close(); } catch {}
  };
  try {
    if (ctx.state === 'suspended') await ctx.resume();
    if (ctx.state !== 'running') throw new Error('audio context not running: ' + ctx.state);
    await ctx.audioWorklet.addModule(WORKLET_URL);
    src = ctx.createMediaStreamSource(stream);
    node = new g.AudioWorkletNode(ctx, WORKLET_NAME, { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: 'explicit' });
    // 일부 브라우저(Safari)는 destination에 이어지지 않은 노드를 돌리지 않는다. 음량 0으로 잇는다.
    sink = ctx.createGain(); sink.gain.value = 0;
    src.connect(node); node.connect(sink); sink.connect(ctx.destination);
  } catch (e) { cleanup(); throw e; }

  const rate = ctx.sampleRate;
  const resampler = new Resampler(rate);
  const chunker = new Chunker();
  const meter = createMeter();
  let sent = 0, stopped = false, closed = false, limited = false;

  const feed = (bytes) => {
    // 서버 상한(60초)을 넘는 바이트는 버린다 — 넘으면 서버가 전체를 거부하므로 클라이언트가 먼저 자른다.
    const room = MAX_RECORD_BYTES - sent;
    if (room <= 0) return;
    const part = bytes.length > room ? bytes.subarray(0, room) : bytes;
    sent += part.length;
    for (const c of chunker.push(part)) cb.onChunk(c);
    if (sent >= MAX_RECORD_BYTES && !limited) { limited = true; cb.onLimit && cb.onLimit(); }
  };

  let onEnd = null;
  node.port.onmessage = (e) => {
    if (e.data === 'end') { if (onEnd) onEnd(); return; }
    if (closed) return;
    const f32 = e.data;
    const m = measure(f32);
    cb.onLevel && cb.onLevel(meter.update(m.rms, m.peak, (f32.length / rate) * 1000));
    feed(floatToPcm16(resampler.process(f32)));
  };

  return {
    sampleRate: rate,
    /** 캡처를 멈추고 청크 단위로 못 채운 나머지를 돌려준다. 워클릿이 쥔 마지막 블록까지 받은 뒤 트랙·컨텍스트를 닫는다. */
    async stop() {
      if (stopped) return new Uint8Array(0);
      stopped = true;
      stream.getTracks().forEach((t) => t.stop());
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 200);     // 'end'가 안 와도 멈추지 않는다
        onEnd = () => { clearTimeout(timer); resolve(); };
        try { node.port.postMessage('stop'); } catch { clearTimeout(timer); resolve(); }
      });
      closed = true;
      // 리샘플러에 남은 구간은 버린다(최대 1출력 샘플 = 62.5us).
      cleanup();
      return chunker.flush();
    },
  };
}
