// 인증 완료 전 복원·저장을 시작하지 않는다. 로그인 게이트가 상태와 완료 이벤트를 소유한다.
export function waitForAuthentication() {
  if (window.__vtAuthed !== false) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = () => { cleanup(); resolve(true); };
    const leaving = () => { cleanup(); resolve(false); };
    const cleanup = () => {
      document.removeEventListener('vt:authed', done);
      window.removeEventListener('pagehide', leaving);
    };
    document.addEventListener('vt:authed', done);
    window.addEventListener('pagehide', leaving);
  });
}

export function requireAuthentication() {
  if (window.__vtAuthed === false) return;
  window.__vtAuthed = false;
  document.dispatchEvent(new Event('vt:auth-required'));
}
