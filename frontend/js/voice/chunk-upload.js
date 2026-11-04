// PCM 청크 업로더 — 순서대로 보내고, 서버의 누적 ack(ackSeq)로 정리하고, 끊기면 같은 seq를 다시 보낸다.
//
// 네트워크(fetch)·시간(sleep/now)은 주입한다 — 상태 기계만 있어 단위 테스트가 된다.
// 규칙:
// - 한 번에 한 요청만 보낸다(가장 앞 미ack 청크). 응답의 ackSeq 이하는 버린다.
// - 일시 실패(네트워크·429/502/503/504)는 retryWithBackoff로 재시도하고, 그래도 안 되면 녹음을 계속 쌓으면서
//   GIVE_UP_MS 동안 더 기다린다. 서버는 30초 무응답이면 녹음을 폐기하므로 그 안에서 포기한다.
// - 서버가 중간 상태를 잃었는지(응답 ackSeq가 방금 보낸 seq보다 작음)는 되돌릴 수 없는 오류로 본다.
// - 응답에 complete:true가 오면(마지막 청크든 늦게 도착한 누락 청크든) 그것이 최종 결과다. 확인 모드면 그 결과가
//   pending_confirm(입력 안 함 + 확인 토큰)일 수 있다 — recording.js가 /voice/input 응답과 같은 분기로 확인 상자를 띄운다.
// - 4xx 등 서버가 거부한 응답은 재시도하지 않고 결과로 돌려준다(recording.js가 data.error를 보여 준다).
// - 완결 전 ack 응답은 onAck(data)로 넘긴다 — 서버가 싣는 녹음 중 값(endpoint 자동 종료 신호, partial 부분 결과)을
//   pcm-session이 읽는다. onAck가 던져도 업로드는 계속한다.
import { retryWithBackoff } from './capture.js';

export const GIVE_UP_MS = 25000;
const TRANSIENT_STATUS = new Set([429, 502, 503, 504]);
const defaultSleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** apiFetch로 POST /voice/chunk 하는 send 함수를 만든다. 일시 실패는 throw, 나머지는 {res, data}.
 *  X-Voice-Confirm: 1 — 이 화면은 확인 상자(confirm.js)를 그릴 수 있다. /voice/input과 같은 뜻이며, 서버는 녹음의
 *  어느 조각에서든 받으면 final 처리에서 확인 정책(voice.confirm)을 거쳐 pending_confirm을 돌려줄 수 있다.
 *  모든 조각에 붙인다 — 녹음을 끝맺는 요청이 final이 아닐 수도 있다(늦게 도착한 앞 조각). */
export function makeChunkSender({ apiFetch, base, voiceId, token, sessionId = '' }) {
  return async function send(seq, bytes, final) {
    const q = `seq=${seq}${final ? '&final=1' : ''}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ''}`;
    const res = await apiFetch(`${base}/voice/chunk?${q}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream', 'X-Voice-Id': voiceId, 'X-Voice-Target': token, 'X-Voice-Confirm': '1' },
      body: bytes,
    });
    let data = null;
    try { data = await res.json(); } catch { /* JSON이 아닌 오류 본문 */ }
    if (TRANSIENT_STATUS.has(res.status)) {
      const e = new Error(`server ${res.status}`);
      e.status = res.status; e.data = data;
      throw e;
    }
    return { res, data };
  };
}

export function makeChunkCanceller({ apiFetch, base, voiceId }) {
  return () => apiFetch(`${base}/voice/chunk/cancel`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Voice-Id': voiceId }, body: '{}', keepalive: true,
  }).catch(() => {});
}

function fatal(message, status = 0) {
  const e = new Error(message);
  e.noRetry = true; e.status = status;
  return e;
}

export function createChunkUploader({
  send, cancel = null, retryDelays = [500, 1000, 2000], giveUpMs = GIVE_UP_MS,
  sleep = defaultSleep, now = () => Date.now(), onFatal = null, onAck = null,
} = {}) {
  const queue = [];           // 미ack 청크 { seq, bytes, final } — seq 오름차순
  let nextSeq = 0;
  let finished = false;
  let outcome = null;         // { res, data }
  let failure = null;
  let cancelled = false;
  let running = false;
  let ackHigh = -1;
  let waiters = [];
  let failStart = 0;

  function settle() {
    if (!outcome && !failure) return;
    const ws = waiters; waiters = [];
    for (const w of ws) outcome ? w.resolve(outcome) : w.reject(failure);
  }

  async function pump() {
    if (running) return;
    running = true;
    try {
      while (!cancelled && !outcome && !failure && queue.length) {
        const item = queue[0];
        let r;
        try {
          r = await retryWithBackoff(() => send(item.seq, item.bytes, item.final), retryDelays, sleep);
          failStart = 0;
        } catch (e) {
          if (e && e.noRetry) { failure = e; break; }
          if (!failStart) failStart = now();
          if (now() - failStart > giveUpMs) { failure = e; break; }
          await sleep(1000);      // 녹음은 계속 쌓이고, 잠시 뒤 같은 seq를 다시 보낸다
          continue;
        }
        const { res, data } = r;
        if (cancelled) break;
        if (!res.ok) { outcome = r; break; }
        if (data && data.complete) { outcome = r; queue.length = 0; break; }
        const ack = data && data.ackSeq;
        if (!Number.isInteger(ack) || ack < item.seq || ack < ackHigh) {
          failure = fatal('server lost the recording', res.status); break;
        }
        ackHigh = ack;
        while (queue.length && queue[0].seq <= ack) queue.shift();
        if (onAck) { try { onAck(data); } catch { /* 표시·자동 종료 처리 실패가 업로드를 막지 않는다 */ } }
        if (item.final && !queue.length) { failure = fatal('final acked without result', res.status); break; }
      }
    } finally {
      running = false;
      if (failure && onFatal) { try { onFatal(failure); } catch {} }
      settle();
    }
  }

  return {
    /** 마이크 청크 하나(비어 있지 않음)를 대기열에 넣는다. */
    push(bytes) {
      if (cancelled || finished || outcome || failure) return;
      queue.push({ seq: nextSeq++, bytes, final: false });
      pump();
    },
    /** 녹음 끝. rest는 청크 크기를 못 채운 나머지(비어도 된다 — 서버는 빈 final을 받는다). */
    finish(rest = new Uint8Array(0)) {
      if (cancelled || finished) return;
      finished = true;
      if (outcome || failure) return;
      queue.push({ seq: nextSeq++, bytes: rest, final: true });
      pump();
    },
    /** 최종 결과 { res, data }. 실패로 끝났다면 거부되고, 다시 부르면(다시 보내기) 남은 청크로 재시도한다. */
    result() {
      if (outcome) return Promise.resolve(outcome);
      if (failure && !cancelled) {
        failure = null; failStart = 0;          // 같은 청크로 새 시도(서버가 잊었으면 곧 같은 오류로 다시 끝난다)
        pump();
      }
      return new Promise((resolve, reject) => { waiters.push({ resolve, reject }); });
    },
    /** 녹음 취소: 더 보내지 않고 서버에 폐기를 알린다(최선 노력). */
    cancel() {
      if (cancelled) return;
      cancelled = true; queue.length = 0;
      if (cancel && !outcome) cancel();
      const e = fatal('cancelled'); failure = e; settle();
    },
    get pendingChunks() { return queue.length; },
    get ackSeq() { return ackHigh; },
  };
}
