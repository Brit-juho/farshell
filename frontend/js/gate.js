// 로그인 게이트 — F3(d)에서 index.html 인라인 <script>(구 :107-245)를 그대로
// 옮겼다. classic script로 유지한다: ES 모듈은 defer라 앱 크롬보다 늦게
// 실행되므로, 모듈 로드 전에 인증 여부를 확정해야 하는 이 스크립트는 인라인/
// classic 둘 중 하나여야 한다 — index.html에서의 위치(로그인 게이트 마크업
// 바로 뒤, 벤더/앱 모듈보다 앞)가 곧 실행 순서 보장이다.
//
// /api/capabilities로 인증 여부 확인.
//  - 200 → 인증됨(또는 서버에 인증 미설정) → 게이트 숨김
//  - 401 → 비밀번호 폼 노출 → POST /api/auth 성공 시 24h 쿠키 발급 후 새로고침
//
// 처음 보는 기기이고 서버에서 'fsh otp setup'으로 OTP를 연동해 둔 경우에만
// 401 {error:"otp_required"}가 돌아오고, 그때 6자리 입력칸이 추가로 열린다.
// OTP 미연동이면 이 분기가 아예 발생하지 않아 기존 동작과 동일하다.
(function(){
  // 번역 — i18n-boot.js가 먼저 로드된다. 없으면 원문 그대로.
  // i18n-boot.js가 없을 때(시험 하네스 등)의 대체 — 번역은 못 해도 {n} 같은 자리는 채운다(안 채우면 「{n}회」가 그대로 보인다).
  var T=window.vtT||function(s,v){ return v ? String(s).replace(/\{(\w+)\}/g,function(m,k){ return Object.prototype.hasOwnProperty.call(v,k) ? String(v[k]) : m; }) : s; };
  var gate=document.getElementById('login-gate');
  if(window.vtApplyI18n) window.vtApplyI18n(gate);
  var spin=document.getElementById('login-spinner');
  var spinLabel=document.getElementById('login-spin-label');
  var form=document.getElementById('login-form');
  var host=document.getElementById('login-host');
  var nextNote=document.getElementById('login-next-note');
  var pass=document.getElementById('login-pass');
  var otpWrap=document.getElementById('login-otp-wrap');
  var otp=document.getElementById('login-otp');
  var otpBoxes=Array.prototype.slice.call(document.querySelectorAll('#login-otp-boxes .login-otp-box'));
  var rememberWrap=document.getElementById('login-remember');
  var passState=document.getElementById('login-pass-state');
  // 포커스 전까지 readonly(Chrome 자동완성 억제, index.html 주석). 인라인 핸들러는 CSP(script-src 'self')가 막는다.
  if(pass) pass.addEventListener('focus',function(){ pass.removeAttribute('readonly'); });
  var btn=document.getElementById('login-submit');
  var btnLabel=document.getElementById('login-submit-label');
  var err=document.getElementById('login-err');
  var errMsg=document.getElementById('login-err-msg');
  var errDetail=document.getElementById('login-err-detail');
  var probe=document.getElementById('login-probe');
  var probeMsg=document.getElementById('login-probe-msg');
  var probeDetail=document.getElementById('login-probe-detail');
  var retryBtn=document.getElementById('login-retry');
  // 경고 아이콘은 비밀번호 오류 줄(#login-err)의 것을 그대로 쓴다 — 정적 사본을 하나 더 두지 않는다.
  (function(){
    var ic=err.querySelector('svg'), box=probe.querySelector('.login-err');
    if(ic && box) box.insertBefore(ic.cloneNode(true), box.firstChild);
  })();
  var params=new URLSearchParams(location.search);

  // 게이트의 모든 요청에 마감을 건다. 서버가 연결은 받는데 답을 안 하던 사고에서
  // 이 화면이 「연결을 확인하는 중」에 영원히 멈춰 있었다 — fetch는 거부도 응답도
  // 없으면 끝나지 않는다. 앱 쪽(core/api.js)보다 짧은 8초: 로그인 전이라 기다리게 할
  // 다른 화면이 없고, 정상 서버는 이 확인에 수백 ms면 답한다.
  var GATE_TIMEOUT_MS=8000;
  function timeoutError(){ var e=new Error('timeout'); e.timeout=true; e.status=0; return e; }
  // p가 마감 안에 끝나지 않으면 timeout 오류로 거부한다. onExpire는 abort용.
  function withDeadline(p,onExpire){
    return new Promise(function(resolve,reject){
      var settled=false;
      var timer=setTimeout(function(){
        if(settled) return; settled=true;
        try{ if(onExpire) onExpire(); }catch(_){}
        reject(timeoutError());
      },GATE_TIMEOUT_MS);
      p.then(function(v){ if(settled) return; settled=true; clearTimeout(timer); resolve(v); },
        function(e){ if(settled) return; settled=true; clearTimeout(timer); reject(e); });
    });
  }
  function gateFetch(url,opts){
    var ctl=typeof AbortController==='function'?new AbortController():null;
    var o={}; for(var k in opts){ if(Object.prototype.hasOwnProperty.call(opts,k)) o[k]=opts[k]; }
    if(ctl) o.signal=ctl.signal;
    var p; try{ p=fetch(url,o); }catch(e){ p=Promise.reject(e); }
    return withDeadline(p,function(){ if(ctl) ctl.abort(); });
  }
  function isTimeout(e){ return !!(e && e.timeout===true); }

  // 비밀번호 입력창은 한/영 전환 상태와 무관하게 항상 영문으로 들어가야 한다.
  // 한글 IME가 켜진 채로 입력하면 실제 눌린 물리 키와 다른 완성형 한글이 조합되므로,
  // 두벌식 표준 자판 매핑으로 조합된 한글(완성형 음절 + 홑자모)을 분해해 원래 눌렀을
  // 영문 키로 역변환한다. 조합이 끝나는 시점(compositionend)에만 되돌린다 —
  // 조합 중간(input)에 값을 바꾸면 IME 조합 상태 자체가 깨진다.
  var CHO=['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
  var JUNG=['ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ'];
  var JONG=['','ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ','ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
  var KEY={
    'ㄱ':'r','ㄲ':'R','ㄴ':'s','ㄷ':'e','ㄸ':'E','ㄹ':'f','ㅁ':'a','ㅂ':'q','ㅃ':'Q',
    'ㅅ':'t','ㅆ':'T','ㅇ':'d','ㅈ':'w','ㅉ':'W','ㅊ':'c','ㅋ':'z','ㅌ':'x','ㅍ':'v','ㅎ':'g',
    'ㅏ':'k','ㅐ':'o','ㅑ':'i','ㅒ':'O','ㅓ':'j','ㅔ':'p','ㅕ':'u','ㅖ':'P',
    'ㅗ':'h','ㅛ':'y','ㅜ':'n','ㅠ':'b','ㅡ':'m','ㅣ':'l',
    'ㅘ':'hk','ㅙ':'ho','ㅚ':'hl','ㅝ':'nj','ㅞ':'np','ㅟ':'nl','ㅢ':'ml'
  };
  // 종성(받침)은 겹받침 조합 시 두 키 입력이고, 쌍자음 받침은 초성과 달리 shift 없이
  // 같은 키를 두 번 눌러 만들어진다(ㄲ=rr, ㅆ=tt) — 초성 KEY(R/T)와 다르다.
  var JONG_KEY={
    '':'','ㄱ':'r','ㄲ':'rr','ㄴ':'s','ㄷ':'e','ㄹ':'f','ㅁ':'a','ㅂ':'q',
    'ㅅ':'t','ㅆ':'tt','ㅇ':'d','ㅈ':'w','ㅊ':'c','ㅋ':'z','ㅌ':'x','ㅍ':'v','ㅎ':'g',
    'ㄳ':'rt','ㄵ':'sw','ㄶ':'sg','ㄺ':'fr','ㄻ':'fa','ㄼ':'fq','ㄽ':'ft','ㄾ':'fx','ㄿ':'fv','ㅀ':'fg','ㅄ':'qt'
  };
  function hangulCharToKeys(ch){
    var code=ch.codePointAt(0);
    if(code>=0xAC00 && code<=0xD7A3){
      var s=code-0xAC00;
      var l=CHO[Math.floor(s/588)], v=JUNG[Math.floor((s%588)/28)], t=JONG[s%28];
      return (KEY[l]||'')+(KEY[v]||'')+(t===''?'':(JONG_KEY[t]||''));
    }
    if(KEY[ch]!==undefined) return KEY[ch]; // 조합 안 끝난 홑자모(ㄱ, ㅏ 등)
    return ch;
  }
  function hasHangul(str){ return /[ㄱ-ㆎ가-힣]/.test(str); }
  function fixKoreanInput(str){
    if(!hasHangul(str)) return str;
    var out='';
    for(var i=0;i<str.length;i++){ out+=hangulCharToKeys(str[i]); }
    return out;
  }

  // grid.js 등 다른 번들 스크립트가 "로그인 확정 전에는 인증이 필요한 호출을
  // 하나도 내보내지 않도록" 기다릴 수 있는 유일한 신호. 이게 없으면 각 스크립트가
  // 로그인 폼이 떠 있는 내내(사용자가 비밀번호를 입력하는 시간만큼) 401/403을
  // 반복 재시도하게 된다 — 자연 치유되긴 해도 불필요한 노이즈다.
  window.__vtAuthed=false;
  // 공유 링크(N21) device 모드가 미등록 세션을 `/?next=/s/<token>`으로 보낸다 —
  // 로그인/등록이 끝나면 원래 열려던 공유 다운로드로 자동 재진입해야 한다.
  // 노출 포트 session 모드(2.2)도 같은 패턴으로 `/?next=/p/authorize?...`를
  // 보낸다(server/routes/system.py의 port_share_authorize) — 실기기 검증
  // 2026-09-22에서 이 화이트리스트가 `/s/`만 허용해 로그인 후 메인 화면에
  // 멈추는 걸 발견했다(원래 노출 앱으로 못 돌아감).
  // open-redirect 방지: 같은 출처의 정해진 접두사로 시작하는 경로만 허용한다.
  function safeNext(){
    var n=params.get('next');
    if(!n || n.charAt(0)!=='/' || n.indexOf('//')===0) return null;
    return (n.indexOf('/s/')===0 || n.indexOf('/p/authorize')===0) ? n : null;
  }
  function goNextOrHide(){
    var n=safeNext();
    if(n){ location.replace(n); return; }
    hideGate();
  }
  function hideGate(){ gate.hidden=true; window.__vtAuthed=true; document.dispatchEvent(new Event('vt:authed')); }
  function showForm(){
    spin.hidden=true; form.hidden=false;
    // 터널 URL은 자주 바뀌고 맥이 둘 이상일 수도 있다 — 지금 어디에 로그인하는지를
    // 주소창 밖에서도 한 번 보여 준다. 호스트만 쓴다(경로·쿼리에는 티켓이 섞인다).
    try{ host.textContent=location.host; host.hidden=!location.host; }catch(e){}
    // 공유 링크가 로그인으로 우회된 경우에만 안내를 켠다(그 외에는 거짓말이 된다).
    nextNote.hidden=!safeNext();
    setTimeout(function(){ try{ if(otpWrap.hidden) pass.focus(); else focusOtpBox(firstEmptyOtpBox()); }catch(e){} }, 50);
    // 기기 기억 기간은 처음 보는 기기에서만 고른다. 등록된 기기는 서버가 그 값을
    // 무시하므로(main.py auth_login) 보여 주면 거짓말이 된다. 상태를 못 읽으면 숨긴
    // 채로 둔다 — 그때는 값을 안 보내고 서버 기본(90일)이 적용된다.
    gateFetch('/api/auth/status',{credentials:'include'}).then(function(r){ return withDeadline(r.json()); })
    .then(function(d){ if(d && d.protected && !d.device_known){ rememberWrap.hidden=false; } })
    .catch(function(){});
  }
  // 숨겨져 있으면 null → payload에 넣지 않는다.
  function rememberValue(){
    if(rememberWrap.hidden) return null;
    var c=rememberWrap.querySelector('input[name="vt-remember"]:checked');
    return c ? c.value : null;
  }
  // 제출 중 상태. disabled만 걸면 "눌렀는데 아무 일도 없는" 화면이라, 라벨과
  // aria-busy를 같이 바꿔 CSS가 링을 붙이고 스크린리더도 진행 중임을 안다.
  function setBusy(on){
    btn.disabled=on;
    if(on){ btn.setAttribute('aria-busy','true'); btnLabel.textContent=T('확인하는 중'); }
    else { btn.removeAttribute('aria-busy'); btnLabel.textContent=T('접속'); }
  }
  // 제목은 사람이 읽을 한 문장, 보조 줄은 서버가 덧붙인 사실(남은 시도·대기 시간).
  // 둘을 한 줄에 이어 붙이면 같은 무게로 읽혀서 정작 무슨 일인지가 묻힌다.
  function showErr(m,detail){
    errMsg.textContent=m;
    errDetail.textContent=detail||'';
    errDetail.hidden=!detail;
    err.hidden=false;
    setBusy(false);
  }
  // 확인 자체가 마감을 넘겼다 — 서버가 연결은 받는데 답을 안 한다. 게이트를 숨기면
  // 앱이 같은 서버에 요청을 쏟아 붓고 화면은 정상인 척하므로, 여기서 멈춰 사실을 말하고
  // 같은 확인을 다시 할 수 있게 한다. 비밀번호 폼은 보여 주지 않는다(비밀번호 문제가 아니다).
  var retryFn=null;
  function showProbeTimeout(retry){
    retryFn=retry;
    spin.hidden=true; form.hidden=true;
    probeMsg.textContent=T('서버가 응답하지 않습니다');
    probeDetail.textContent=T('잠시 뒤 다시 시도하세요. 계속되면 맥에서 fsh restart를 실행하세요.');
    probe.hidden=false;
    try{ retryBtn.focus(); }catch(_){}
  }
  retryBtn.addEventListener('click',function(){
    var f=retryFn; if(!f) return;
    retryFn=null; probe.hidden=true; spin.hidden=false;
    f();
  });

  // 자격증명 파라미터를 URL에서 지우고 재로드 — 히스토리/공유 링크에 남지 않게 한다.
  // next가 있으면 정리 후 재로드하는 대신 곧장 그리로 옮긴다.
  function reloadClean(){
    try { sessionStorage.setItem('vt.restore-after-login', '1'); } catch (_) {}
    var n=safeNext();
    if(n){ location.replace(n); return; }
    params.delete('ticket'); params.delete('token');
    var q=params.toString();
    location.replace(location.pathname+(q?'?'+q:'')+location.hash);
  }

  // 열린 페이지의 인증 만료도 같은 로그인 폼으로 처리한다. 기존 뷰는 뒤에서 유지된다.
  document.addEventListener('vt:auth-required', function(){
    window.__vtAuthed=false; gate.hidden=false; probe.hidden=true;
    showForm();
  });

  // 1회용 기기 등록 티켓(QR로 들어온 경우) — 스캔 자체가 등록 승인이다.
  var ticket=params.get('ticket');
  // 티켓 제출이 마감을 넘기면 서버가 이미 받아 처리했는지 알 수 없다. 다시 시도는 같은
  // 티켓을 다시 내는데, 이미 쓰인 티켓이면 아래 401 분기가 "지금 인증돼 있는지"를 물어
  // 그대로 들여보낸다 — 그래서 재시도해도 안전하다.
  function runTicket(){
    // 같은 링이지만 기다리는 일이 다르다 — 프로브는 수백 ms, 기기 등록은 서버가
    // 기기 목록에 쓰고 세션까지 발급하는 동안이라 체감이 길다.
    spinLabel.textContent=T('기기를 등록하는 중');
    gateFetch('/api/auth',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket:ticket})})
    .then(function(r){
      if(r.ok){ reloadClean(); return; }
      // 티켓은 **1회용**이라 같은 티켓이 두 번 제출되면 두 번째는 반드시 401이다.
      // 그런데 첫 제출이 이미 성공해 기기가 등록되고 세션 쿠키까지 받은 뒤라면,
      // 그 401은 "실패"가 아니라 "이미 됐다"는 뜻이다. 그걸 구분하지 않고
      // 에러를 띄우는 바람에 **로그인에 성공한 사용자에게 「등록 링크가
      // 만료되었습니다」가 뜨는** 일이 있었다(서버 로그로 확인: POST 200 →
      // 재로드 → POST 401 순서). QR 온보딩의 유일한 경로라 치명적이다.
      //
      // 그래서 401을 받으면 곧바로 좌절하지 말고 **지금 인증돼 있는지 먼저
      // 물어본다.** 되어 있으면 그대로 들여보낸다. 중복 제출이 왜 생기는지와
      // 무관하게 옳은 동작이다 — 재시도·뒤로가기·프리렌더 어느 쪽이든 같다.
      gateFetch('/api/capabilities',{credentials:'include'}).then(function(p){
        if(p.status!==401){ reloadClean(); return; }
        showForm(); showErr(T('등록 링크가 만료되었습니다'),T('비밀번호로 접속하거나, 맥에서 fsh mobile로 QR을 다시 받으세요.'));
      }).catch(function(e){
        if(isTimeout(e)){ showProbeTimeout(runTicket); return; }
        showForm(); showErr(T('등록 링크가 만료되었습니다'),T('비밀번호로 접속하거나, 맥에서 fsh mobile로 QR을 다시 받으세요.'));
      });
    }).catch(function(e){
      if(isTimeout(e)){ showProbeTimeout(runTicket); return; }
      showForm();
    });
  }
  if(ticket){
    runTicket();
    return;
  }

  // URL의 ?token= 은 더 이상 인증 수단이 아니다 — 값을 쓰지 않고 주소창에서만 지운다. 로그인 쿠키가 없으면 평소처럼 로그인 화면이 뜬다.
  if(params.has('token')){
    try {
      params.delete('token');
      var cleaned=params.toString();
      history.replaceState({},'',location.pathname+(cleaned?'?'+cleaned:'')+location.hash);
    } catch (_) {}
  }
  // 첫 확인. 네트워크 오류(거부)에 게이트를 숨기는 건 의도다 — 오프라인이면 sw.js가
  // 캐시한 셸을 돌려주는데, 그 셸 위에서 core/reachability.js가 "서버에 연결할 수
  // 없습니다"를 말한다(게이트가 가리고 있으면 그 배너도 못 본다). 마감 초과는 다르다:
  // 서버가 연결은 받는데 답을 안 하는 상태라, 숨기면 앱이 같은 서버에 요청을 더 쌓을
  // 뿐이다. 그래서 여기서 멈추고 다시 확인할 수 있게 한다.
  function runProbe(){
    spinLabel.textContent=T('연결을 확인하는 중');
    gateFetch('/api/capabilities',{credentials:'include'}).then(function(r){
      if(r.status===401){ showForm(); } else { goNextOrHide(); }
    }).catch(function(e){
      if(isTimeout(e)){ showProbeTimeout(runProbe); return; }
      goNextOrHide();
    });
  }
  runProbe();

  // 입력 상태 표시(2026-10, 사용자 요청: 한/영·Caps Lock이 안 보여 자꾸 틀린다).
  // - Caps Lock: 키 이벤트의 getModifierState. 모바일 가상 키보드는 알려 주지 않는다.
  // - 한글: 조합 중인 글자나 칸의 값에 **실제로 한글이 있을 때만** 켠다. 모바일
  //   키보드는 영문 입력도 isComposing/keyCode 229로 보고해서 그것만으로는 틀린다.
  //   ASCII 글자 키가 조합 없이 들어오면 끈다. 값은 compositionend가 영문 키로
  //   되돌리므로(아래) 이 표시는 "바꿔 입력된다"는 사실을 알려 주는 역할이다.
  // 화면 폭이 아니라 기기 종류로 구분한다(iPad 데스크톱 모드 포함).
  var mobileInput=/Android|iPhone|iPad|iPod/i.test(navigator.userAgent||'')
    || (/Macintosh/i.test(navigator.userAgent||'') && navigator.maxTouchPoints>1);
  var capsOn=false, koOn=false;
  function renderPassState(){
    var parts=[];
    if(capsOn) parts.push(T('Caps Lock이 켜져 있습니다'));
    if(koOn) parts.push(T('한글 입력 중 · 영문 키로 바꿔 입력됩니다'));
    passState.textContent=parts.join(' · ');
    passState.hidden=!parts.length;
  }
  function trackKey(e){
    if(e.getModifierState){ capsOn=!!e.getModifierState('CapsLock'); }
    if(e.key==='HangulMode'){ koOn=true; }
    else if(!e.isComposing && e.key && e.key.length===1 && /[A-Za-z]/.test(e.key)){ koOn=false; }
    renderPassState();
  }
  if(!mobileInput){
    pass.addEventListener('keydown',trackKey);
    pass.addEventListener('keyup',trackKey);
    pass.addEventListener('compositionupdate',function(e){
      if(hasHangul(e.data||'')){ koOn=true; renderPassState(); }
    });
    pass.addEventListener('input',function(){
      if(hasHangul(pass.value)){ koOn=true; renderPassState(); }
    });
    // 칸을 떠나면 지운다 — 다른 창에서 Caps Lock을 바꿨을 수 있어 돌아올 때 다시 읽는다.
    pass.addEventListener('blur',function(){ capsOn=false; koOn=false; renderPassState(); });
  }

  // OTP 6칸(시안 M10). 칸은 한 자리씩 보이는 입력이고, 제출 값은 숨은 #login-otp 하나다(submit은 그것만 읽는다).
  // - 숫자를 치면 다음 칸으로, 빈 칸에서 Backspace는 앞 칸으로 가서 그 자리를 지운다.
  // - 여러 자리가 한꺼번에 들어오면(붙여넣기 · 문자/인증 앱 자동 채우기 — 첫 칸이 one-time-code) 그 칸부터 나눠 담는다.
  //   여섯 자리를 붙여넣으면 어느 칸에서든 처음부터 채운다.
  // - 칸에 들어가면 내용을 골라 둔다 — 다시 치면 그 자리를 바꾼다(뒤에 덧붙여 밀리지 않게).
  function otpDigits(s){ return String(s||'').replace(/\D/g,''); }
  function syncOtp(){ otp.value=otpBoxes.map(function(b){ return b.value; }).join(''); }
  function clearOtp(){ otpBoxes.forEach(function(b){ b.value=''; }); syncOtp(); }
  function firstEmptyOtpBox(){
    for(var i=0;i<otpBoxes.length;i++){ if(!otpBoxes[i].value) return i; }
    return otpBoxes.length-1;
  }
  function focusOtpBox(i){
    var b=otpBoxes[Math.max(0,Math.min(otpBoxes.length-1,i))];
    if(!b) return;
    b.focus();
    try{ b.select(); }catch(e){}
  }
  function fillOtp(from,digits){
    if(digits.length>=otpBoxes.length) from=0;
    for(var k=0;k<digits.length && from+k<otpBoxes.length;k++){ otpBoxes[from+k].value=digits.charAt(k); }
    syncOtp();
    focusOtpBox(Math.min(from+digits.length, otpBoxes.length-1));
  }
  otpBoxes.forEach(function(box,i){
    box.setAttribute('aria-label',T('인증 코드 {n}번째 자리',{n:i+1}));
    box.addEventListener('focus',function(){ try{ box.select(); }catch(e){} });
    box.addEventListener('input',function(e){
      var d=otpDigits(box.value);
      if(!d){ box.value=''; syncOtp(); return; }
      // 한 글자 타이핑인데 칸에 이미 숫자가 있었다(고르기가 안 먹은 브라우저) — 새로 친 자리만 남긴다.
      if(d.length===2 && e && e.inputType==='insertText'){ d=otpDigits(e.data)||d.charAt(1); }
      if(d.length===1){
        box.value=d; syncOtp();
        if(i<otpBoxes.length-1) focusOtpBox(i+1);
        return;
      }
      box.value='';
      fillOtp(i,d.slice(0,otpBoxes.length));
    });
    box.addEventListener('keydown',function(e){
      if(e.key==='Backspace' && !box.value && i>0){
        e.preventDefault();
        otpBoxes[i-1].value=''; syncOtp(); focusOtpBox(i-1);
      } else if(e.key==='ArrowLeft' && i>0){ e.preventDefault(); focusOtpBox(i-1); }
      else if(e.key==='ArrowRight' && i<otpBoxes.length-1){ e.preventDefault(); focusOtpBox(i+1); }
    });
    box.addEventListener('paste',function(e){
      var data=e.clipboardData||window.clipboardData;
      var d=otpDigits(data && data.getData ? data.getData('text') : '');
      if(!d) { e.preventDefault(); return; }
      e.preventDefault();
      fillOtp(i,d.slice(0,otpBoxes.length));
    });
  });

  // IME 조합이 끝날 때마다(음절 하나가 완성될 때마다) 한글이 섞여 있으면 즉시 되돌린다.
  pass.addEventListener('compositionend',function(){
    var fixed=fixKoreanInput(pass.value);
    if(fixed!==pass.value){ pass.value=fixed; }
  });

  form.addEventListener('submit',function(e){
    e.preventDefault();
    var v=fixKoreanInput(pass.value); if(!v) return;
    if(v!==pass.value) pass.value=v; // 조합 중 상태로 제출된 경우 대비한 마지막 안전망
    var payload={token:v};
    // OTP 2단계에서 다시 제출할 때도 같은 선택이 실린다(라디오는 그대로 남아 있다).
    var rem=rememberValue(); if(rem) payload.remember=rem;
    if(!otpWrap.hidden){
      var code=(otp.value||'').replace(/\D/g,'');
      if(code.length!==6){ showErr(T('인증 코드를 6자리로 입력하세요')); return; }
      payload.otp=code;
    }
    setBusy(true); err.hidden=true;
    gateFetch('/api/auth',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){
      if(r.ok){ var n=safeNext(); if(n){ location.replace(n); } else { location.reload(); } return; }
      return withDeadline(r.json()).catch(function(e){ if(isTimeout(e)) throw e; return {}; }).then(function(d){
        if(d.error==='otp_required'){
          otpWrap.hidden=false; setBusy(false); err.hidden=true;
          setTimeout(function(){ try{ focusOtpBox(firstEmptyOtpBox()); }catch(e){} },50);
          return;
        }
        if(d.error==='otp_invalid'){
          otpWrap.hidden=false;
          showErr(T('인증 코드가 올바르지 않습니다'), d.remaining!=null?(T('남은 시도 {n}회',{n:d.remaining})):'');
          try{ clearOtp(); focusOtpBox(0); }catch(e){}
          return;
        }
        if(d.error==='otp_locked'){
          showErr(T('시도 횟수를 초과했습니다'), T('{n}분 뒤에 다시 시도할 수 있습니다.',{n:Math.ceil((d.retry_after||600)/60)}));
          return;
        }
        if(d.error==='ticket_invalid'){ showErr(T('등록 링크가 만료되었습니다'),T('비밀번호로 접속하세요.')); return; }
        // 화면의 선택지와 서버 허용값이 어긋난 경우(구버전 캐시 화면 등). 비밀번호 문제가 아니다.
        if(d.error==='invalid_remember'){ showErr(T('기기 기억 기간을 처리하지 못했습니다'),T('페이지를 새로고침한 뒤 다시 시도하세요.')); return; }
        // 비밀번호 5회 실패 → IP 단위 10분 잠금(429 password_locked). 이걸 따로
        // 다루지 않으면 잠긴 동안 **정답을 쳐도** "비밀번호가 올바르지 않습니다"가
        // 뜬다 — 실제로 맞는 비밀번호를 놔두고 비밀번호를 새로 바꾸게 만든 문구다.
        if(d.error==='password_locked'){
          showErr(T('시도 횟수를 초과했습니다'), T('{n}분 뒤에 다시 시도할 수 있습니다. 비밀번호를 바꿀 필요는 없습니다.',{n:Math.ceil((d.retry_after||600)/60)}));
          return;
        }
        showErr(T('비밀번호가 올바르지 않습니다'));
        try{ pass.select(); }catch(e){}
      });
    // 네트워크 실패는 비밀번호가 틀린 것과 전혀 다른 일이다 — 같은 자리에 뜨더라도
    // 무엇을 해야 하는지가 달라서 문장을 나눠 둔다.
    // 마감 초과는 또 다르다 — 닿기는 했는데 답이 없다. 다시 누르면 된다(showErr가 버튼을 푼다).
    }).catch(function(e){
      if(isTimeout(e)){ showErr(T('서버가 응답하지 않습니다'),T('잠시 뒤 다시 시도하세요. 계속되면 맥에서 fsh restart를 실행하세요.')); return; }
      showErr(T('서버에 연결하지 못했습니다'),T('네트워크 상태를 확인한 뒤 다시 시도하세요.'));
    });
  });
})();
