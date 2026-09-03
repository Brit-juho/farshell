// 음성 기능 진입점 — F4에서 voice.js(503줄, classic script)를 4개 ES 모듈로
// 쪼갠 뒤의 조립 지점. agent/status.js가 `/api/capabilities`로 음성 설치
// 여부를 확인한 뒤에만 `import('../voice/index.js')`로 동적 로드한다(구
// document.createElement('script') 방식을 대체 — 미설치 환경에서 0바이트
// 비용이라는 목표는 동일하게 유지).
import './recording.js';
import './tts.js';
import './media-session.js';
import './notify.js';
