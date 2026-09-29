// N45 — 모바일 헤더의 기기 이름("MacBook Pro M1 Pro"). 세션을 옮겨 다녀도
// 안 바뀌는 값(이 서버가 도는 물리 기기)이라 부팅 시 한 번만 읽는다. 호스트를
// 전환했으면 vtFetch가 중계하므로 그 맥의 값이 나온다.
// 2026-09-29 — OS 이름("macOS · ")은 뺐다(사용자 요청: 어느 맥인지만 보면 된다).
// machine이 빈 문자열이면(Linux/WSL2) hostname, 그마저 없으면 비워 둔다.
// 2026-09-29(H1) — 따로 있던 칩을 없애고, 세션 버튼의 둘째 줄("기기 · 3/5")로
// 합쳤다. 이 파일은 값만 구해 html[data-vt-machine]에 두고 알린다 —
// picker.js가 그 값을 읽어 그린다.
//
// mobile-nav.js처럼 main.js가 정적 import하는 파일이라(지연 청크 아님) core를
// 그대로 import해도 된다.
import { vtFetch } from '../core/api.js';

// mobile-nav.js와 달리 폭으로 게이팅하지 않는다 — 데스크톱 폭에서 시작했다가
// 창을 좁혀 compact가 되는 경우(리사이즈, 회전)에도 칩이 비어 있으면 안
// 된다. `/api/capabilities`는 부팅 시 다른 모듈도 이미 부르는 가벼운
// ETag-캐시 가능 GET이라 폭과 무관하게 한 번 부르는 비용이 작다.
async function _init() {
  let cap;
  try {
    cap = await vtFetch('/api/capabilities');
  } catch (_) {
    return; // 조회 실패 — 기기 줄 없이 위치만 보인다
  }
  const text = (cap && (cap.machine || cap.hostname)) || '';
  if (!text) return;
  document.documentElement.dataset.vtMachine = text;
  document.dispatchEvent(new CustomEvent('vt:machine', { detail: { machine: text } }));
}

_init();
