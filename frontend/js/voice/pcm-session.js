// PCM 녹음 한 건: 캡처(pcm-capture) + 청크 업로드(chunk-upload) + 음량 이벤트를 묶는다.
// recording.js는 이 세션을 시작·정지·결과 대기·취소하는 것만 한다.
//
// core/store.js·core/dom.js를 import하지 않는다(recording.js 상단 경고).
import { startPcmCapture } from './pcm-capture.js';
import { createChunkUploader, makeChunkSender, makeChunkCanceller } from './chunk-upload.js';

/** 음량 이벤트: 상태 패널(app.js 번들)이 'vt:voice-level'을 받아 미터를 그린다. 두 번들은 이벤트로만 만난다. */
function emitLevel(g, detail) {
  try { g.dispatchEvent(new g.CustomEvent('vt:voice-level', { detail })); } catch { /* 표시 실패는 녹음에 영향 없다 */ }
}

/**
 * @returns {Promise<{ stop():Promise<void>, result():Promise<{res,data}>, cancel():void, sampleRate:number }>}
 *   시작에 실패하면 던진다 — 호출부가 MediaRecorder 경로로 폴백한다. 실패 시 ctx는 이미 닫혀 있다.
 */
export async function startPcmSession({ ctx, stream, apiFetch, base, voiceId, token, sessionId = '', onLimit = null, onFatal = null, g = globalThis }) {
  const uploader = createChunkUploader({
    send: makeChunkSender({ apiFetch, base, voiceId, token, sessionId }),
    cancel: makeChunkCanceller({ apiFetch, base, voiceId }),
    onFatal,
  });
  const cap = await startPcmCapture(ctx, stream, {
    onChunk: (bytes) => uploader.push(bytes),
    onLevel: (lv) => emitLevel(g, lv),
    onLimit,
  }, g);
  let stopping = null;
  return {
    sampleRate: cap.sampleRate,
    stop() {
      if (!stopping) {
        stopping = cap.stop().then((rest) => {
          emitLevel(g, { level: 0, clip: false });
          uploader.finish(rest);
        });
      }
      return stopping;
    },
    result: () => uploader.result(),
    cancel() {
      cap.stop().catch(() => {});
      emitLevel(g, { level: 0, clip: false });
      uploader.cancel();
    },
  };
}
