// AudioWorklet 프로세서의 이름·블록 크기·주소. 프로세서 본체는 정적 파일 pcm-worklet.js다.
//
// 예전에는 소스를 문자열로 들고 Blob URL로 addModule 했다. 문서 CSP가 `script-src 'self'`가 된 뒤로
// Blob 모듈은 막히므로(워클릿은 script-src를 따른다) 같은 출처 정적 파일로 서빙한다. 번들(vite) 밖이라
// 산출물·서비스 워커 목록은 그대로고, 파일은 녹음을 시작할 때만 요청된다(미설치 0바이트 계약 유지).
export const WORKLET_NAME = 'fsh-pcm-capture';
export const WORKLET_BLOCK = 2048;

export const WORKLET_URL = '/static/js/voice/pcm-worklet.js';
