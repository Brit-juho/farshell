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

/** 부분 결과 이벤트: 상태 패널이 'vt:voice-partial'을 받아 흐린 글자로 보여 준다(입력하지 않는다). */
function emitPartial(g, detail) {
  try { g.dispatchEvent(new g.CustomEvent('vt:voice-partial', { detail })); } catch { /* 표시 실패는 녹음에 영향 없다 */ }
}

/**
 * 청크 ack 응답의 녹음 중 값을 처리하는 함수를 만든다(순수 — 테스트용으로 export).
 * - partial {text, seq, clipped}: 더 최근(seq가 큰) 결과만 내보낸다. 응답 순서가 뒤바뀌어도 화면이 뒤로 가지 않는다.
 * - endpoint: 서버 VAD가 끝점을 냈다 — onEndpoint를 녹음당 한 번만 부른다(isActive()가 거짓이면 부르지 않는다).
 */
export function makeAckHandler({ onPartial, onEndpoint, isActive = () => true }) {
  let lastSeq = -1;
  let ended = false;
  return (data) => {
    if (!data || typeof data !== 'object') return;
    const p = data.partial;
    if (p && typeof p.text === 'string' && Number.isInteger(p.seq) && p.seq > lastSeq) {
      lastSeq = p.seq;
      onPartial?.({ text: p.text, clipped: p.clipped === true, seq: p.seq });
    }
    if (data.endpoint === true && !ended && isActive()) {
      ended = true;
      onEndpoint?.();
    }
  };
}

/**
 * @returns {Promise<{ stop():Promise<void>, result():Promise<{res,data}>, cancel():void, sampleRate:number }>}
 *   시작에 실패하면 던진다 — 호출부가 MediaRecorder 경로로 폴백한다. 실패 시 ctx는 이미 닫혀 있다.
 */
export async function startPcmSession({ ctx, stream, apiFetch, base, voiceId, token, sessionId = '', onLimit = null, onEndpoint = onLimit, onFatal = null, g = globalThis }) {
  let stopping = null;
  let cancelled = false;
  // 서버 자동 종료(endpoint)는 사용자가 「녹음 종료」를 누른 것과 같다 — 기본은 onLimit(=recording.js의 stopRecording).
  const onAck = makeAckHandler({
    onPartial: (p) => emitPartial(g, p),
    onEndpoint: () => { console.debug('[voice] 서버 끝점(침묵) — 자동 종료'); onEndpoint?.(); },
    isActive: () => !stopping && !cancelled,
  });
  const uploader = createChunkUploader({
    send: makeChunkSender({ apiFetch, base, voiceId, token, sessionId }),
    cancel: makeChunkCanceller({ apiFetch, base, voiceId }),
    onFatal, onAck,
  });
  const cap = await startPcmCapture(ctx, stream, {
    onChunk: (bytes) => uploader.push(bytes),
    onLevel: (lv) => emitLevel(g, lv),
    onLimit,
  }, g);
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
      cancelled = true;
      cap.stop().catch(() => {});
      emitLevel(g, { level: 0, clip: false });
      uploader.cancel();
    },
  };
}
