// N45 — 모바일 헤더 환경 칩("macOS · MacBook Pro M1 Pro"). 세션을 옮겨 다녀도
// 안 바뀌는 값(이 서버가 도는 물리 기기)이라 부팅 시 한 번만 읽는다.
// `/api/capabilities`의 hostname/version과 같은 "값 없으면 숨긴다" 규칙 —
// machine이 빈 문자열이면(Linux/WSL2) os만, os마저 없으면 칩 자체를 숨긴다.
//
// mobile-nav.js처럼 main.js가 정적 import하는 파일이라(지연 청크 아님) core를
// 그대로 import해도 된다.
import { vtFetch } from '../core/api.js';

const _el = document.getElementById('vt-env-chip');

// mobile-nav.js와 달리 폭으로 게이팅하지 않는다 — 데스크톱 폭에서 시작했다가
// 창을 좁혀 compact가 되는 경우(리사이즈, 회전)에도 칩이 비어 있으면 안
// 된다. `/api/capabilities`는 부팅 시 다른 모듈도 이미 부르는 가벼운
// ETag-캐시 가능 GET이라 폭과 무관하게 한 번 부르는 비용이 작다.
async function _init() {
  if (!_el) return;
  let cap;
  try {
    cap = await vtFetch('/api/capabilities');
  } catch (_) {
    return; // 조회 실패 — hidden 유지
  }
  const os = (cap && cap.os) || '';
  const machine = (cap && cap.machine) || '';
  const text = machine ? `${os} · ${machine}` : os;
  if (!text) return;
  _el.textContent = text;
  _el.hidden = false;
}

_init();
