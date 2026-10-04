// 녹음 형식 선택·마이크 제약·에러 분류·재시도 — recording.js에서 쓰는 순수 함수 모음.
//
// DOM/전역(window, navigator) 접근이 없다 — 단위 테스트 가능. voice 번들에 같이 들어가도
// 상태가 없는 모듈이라 안전하다(core/store.js류와 달리 사본이 생겨도 덮어쓸 게 없다).

// 선호 순서. ''는 "형식 지정 없이 브라우저 기본"(iOS Safari는 보통 audio/mp4).
export const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', ''];

// 서버(/voice/input)가 받는 Content-Type 목록.
export const SERVER_CONTENT_TYPES = ['audio/webm', 'audio/mp4', 'audio/ogg', 'audio/wav'];

// isTypeSupported(mime) → bool. 지원 형식이 없거나 함수가 없으면 ''(브라우저 기본).
export function pickRecorderMime(isTypeSupported) {
  if (typeof isTypeSupported !== 'function') return '';
  for (const m of MIME_CANDIDATES) {
    if (m === '') return '';
    try { if (isTypeSupported(m)) return m; } catch { /* 다음 후보 */ }
  }
  return '';
}

// "audio/webm;codecs=opus" → "audio/webm". 알 수 없거나 비면 audio/webm으로 간주.
export function contentTypeOf(mimeType) {
  const base = String(mimeType || '').split(';')[0].trim().toLowerCase();
  const alias = {
    'audio/x-wav': 'audio/wav', 'audio/wave': 'audio/wav', 'audio/vnd.wave': 'audio/wav',
    'audio/x-m4a': 'audio/mp4', 'audio/m4a': 'audio/mp4', 'audio/aac': 'audio/mp4',
    'video/webm': 'audio/webm', 'video/mp4': 'audio/mp4',
    'application/ogg': 'audio/ogg',
  };
  const n = alias[base] || base;
  return SERVER_CONTENT_TYPES.includes(n) ? n : 'audio/webm';
}

export function buildAudioConstraints() {
  return {
    channelCount: 1,
    sampleRate: { ideal: 16000 },
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
}

// 음성용 낮은 비트레이트. mimeType ''이면 키를 넣지 않는다(기본 형식 선택을 방해하지 않게).
export function buildRecorderOptions(mimeType) {
  const o = { audioBitsPerSecond: 32000 };
  if (mimeType) o.mimeType = mimeType;
  return o;
}

export const MIC_ERROR_MESSAGES = {
  '권한': '마이크 권한이 필요합니다 — 브라우저 설정에서 허용해 주세요',
  '마이크 없음': '마이크를 찾을 수 없습니다 — 연결을 확인하세요',
  '이 브라우저는 녹음 형식을 지원하지 않음': '이 브라우저는 녹음 형식을 지원하지 않습니다',
  '마이크를 열 수 없음': '마이크를 열 수 없습니다 — 다른 앱이 쓰는 중인지 확인하세요',
};

export function classifyMicError(err) {
  const name = err && err.name;
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') return '권한';
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return '마이크 없음';
  if (name === 'NotSupportedError' || name === 'TypeError') return '이 브라우저는 녹음 형식을 지원하지 않음';
  return '마이크를 열 수 없음';
}

export function micErrorMessage(err) {
  return MIC_ERROR_MESSAGES[classifyMicError(err)];
}

// fn()을 실행하고 실패 시 delays[i]ms 쉬었다 재시도(총 1 + delays.length회).
// fn이 { retry:false } 성격의 에러를 던지면(err.noRetry) 즉시 포기. sleep은 주입(기본 setTimeout).
// 성공하면 fn 결과, 끝내 실패하면 마지막 에러를 던진다.
/**
 * 녹음 하나의 식별자 — 같은 녹음을 다시 보낼 때 서버가 중복 입력을 막는 데 쓴다(X-Voice-Id).
 * 영숫자·`-`·`_`만 쓴다(서버가 그 밖의 값은 무시한다). randomUUID는 보안 컨텍스트에서만 있어 대체 경로를 둔다.
 */
export function newVoiceId(rand = Math.random, cryptoObj = globalThis.crypto) {
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') return cryptoObj.randomUUID();
  let s = '';
  for (let i = 0; i < 24; i++) s += Math.floor(rand() * 36).toString(36);
  return s;
}

export async function retryWithBackoff(fn, delays = [1000, 3000], sleep = (ms) => new Promise((r) => setTimeout(r, ms))) {
  let lastErr;
  for (let i = 0; i <= delays.length; i++) {
    try {
      return await fn(i);
    } catch (e) {
      lastErr = e;
      if (e && e.noRetry) throw e;
      if (i < delays.length) await sleep(delays[i]);
    }
  }
  throw lastErr;
}
