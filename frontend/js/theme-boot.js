// 테마 부팅 — 페인트 전에 data-skin 확정(FOUC 방지). classic script, <head>에서 동기 로드.
// 예전에는 index.html 인라인 스크립트였다. CSP `script-src 'self'`가 인라인을 막으므로 파일로 뺐다.
//
// D1(ADR-3): 기본값은 farshell. 이미 vt-skin을 저장해 둔 사용자는 그 값을 그대로 쓴다(마이그레이션 없음).
// 저장된 값이 없을 때만 새 기본값이 적용되고, 그때도 시스템이 라이트 모드면 다크 스킨을 들이밀지 않고
// 유일한 라이트 스킨인 notepad로 떨어진다(prefers-color-scheme 추종). 사용자가 명시적으로 고르면 그게 이긴다.
(function(){
  try {
    var stored = localStorage.getItem('vt-skin');
    var valid = ['farshell','macos','catppuccin','windows','vscode','notepad','warp','linear','vercel'];
    var s;
    if (stored && valid.indexOf(stored) >= 0) {
      s = stored;
    } else {
      var prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      s = prefersLight ? 'notepad' : 'farshell';
    }
    document.documentElement.setAttribute('data-skin', s);
  } catch(e){}
})();
