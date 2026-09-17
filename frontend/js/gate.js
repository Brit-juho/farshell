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
  var gate=document.getElementById('login-gate');
  var spin=document.getElementById('login-spinner');
  var spinLabel=document.getElementById('login-spin-label');
  var form=document.getElementById('login-form');
  var host=document.getElementById('login-host');
  var nextNote=document.getElementById('login-next-note');
  var pass=document.getElementById('login-pass');
  var otpWrap=document.getElementById('login-otp-wrap');
  var otp=document.getElementById('login-otp');
  var btn=document.getElementById('login-submit');
  var btnLabel=document.getElementById('login-submit-label');
  var err=document.getElementById('login-err');
  var errMsg=document.getElementById('login-err-msg');
  var errDetail=document.getElementById('login-err-detail');
  var params=new URLSearchParams(location.search);

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
  // open-redirect 방지: 같은 출처의 `/s/`로 시작하는 경로만 허용한다.
  function safeNext(){
    var n=params.get('next');
    return (n && n.charAt(0)==='/' && n.indexOf('//')!==0 && n.indexOf('/s/')===0) ? n : null;
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
    setTimeout(function(){ try{ (otpWrap.hidden?pass:otp).focus(); }catch(e){} }, 50);
  }
  // 제출 중 상태. disabled만 걸면 "눌렀는데 아무 일도 없는" 화면이라, 라벨과
  // aria-busy를 같이 바꿔 CSS가 링을 붙이고 스크린리더도 진행 중임을 안다.
  function setBusy(on){
    btn.disabled=on;
    if(on){ btn.setAttribute('aria-busy','true'); btnLabel.textContent='확인하는 중'; }
    else { btn.removeAttribute('aria-busy'); btnLabel.textContent='접속'; }
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
  // 자격증명 파라미터를 URL에서 지우고 재로드 — 히스토리/공유 링크에 남지 않게 한다.
  // next가 있으면 정리 후 재로드하는 대신 곧장 그리로 옮긴다.
  function reloadClean(){
    var n=safeNext();
    if(n){ location.replace(n); return; }
    params.delete('ticket'); params.delete('token');
    var q=params.toString();
    location.replace(location.pathname+(q?'?'+q:'')+location.hash);
  }

  // 1회용 기기 등록 티켓(QR로 들어온 경우) — 스캔 자체가 등록 승인이다.
  var ticket=params.get('ticket');
  if(ticket){
    // 같은 링이지만 기다리는 일이 다르다 — 프로브는 수백 ms, 기기 등록은 서버가
    // 기기 목록에 쓰고 세션까지 발급하는 동안이라 체감이 길다.
    spinLabel.textContent='기기를 등록하는 중';
    fetch('/api/auth',{method:'POST',credentials:'include',
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
      fetch('/api/capabilities',{credentials:'include'}).then(function(p){
        if(p.status!==401){ reloadClean(); return; }
        showForm(); showErr('등록 링크가 만료되었습니다','비밀번호로 접속하거나, 맥에서 fsh mobile로 QR을 다시 받으세요.');
      }).catch(function(){
        showForm(); showErr('등록 링크가 만료되었습니다','비밀번호로 접속하거나, 맥에서 fsh mobile로 QR을 다시 받으세요.');
      });
    }).catch(function(){ showForm(); });
    return;
  }

  // URL에 ?token= 이 있으면(레거시 링크) 그 토큰으로 프로브 → 통과 시 terminal.js가 쿠키 교환
  var urlTok=params.get('token');
  var probe='/api/capabilities'+(urlTok?('?token='+encodeURIComponent(urlTok)):'');
  fetch(probe,{credentials:'include'}).then(function(r){
    if(r.status===401){ showForm(); } else { goNextOrHide(); }
  }).catch(function(){ goNextOrHide(); });

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
    if(!otpWrap.hidden){
      var code=(otp.value||'').replace(/\D/g,'');
      if(code.length!==6){ showErr('인증 코드를 6자리로 입력하세요'); return; }
      payload.otp=code;
    }
    setBusy(true); err.hidden=true;
    fetch('/api/auth',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){
      if(r.ok){ var n=safeNext(); if(n){ location.replace(n); } else { location.reload(); } return; }
      return r.json().catch(function(){ return {}; }).then(function(d){
        if(d.error==='otp_required'){
          otpWrap.hidden=false; setBusy(false); err.hidden=true;
          setTimeout(function(){ try{ otp.focus(); }catch(e){} },50);
          return;
        }
        if(d.error==='otp_invalid'){
          otpWrap.hidden=false;
          showErr('인증 코드가 올바르지 않습니다', d.remaining!=null?('남은 시도 '+d.remaining+'회'):'');
          try{ otp.value=''; otp.focus(); }catch(e){}
          return;
        }
        if(d.error==='otp_locked'){
          showErr('시도 횟수를 초과했습니다', Math.ceil((d.retry_after||600)/60)+'분 뒤에 다시 시도할 수 있습니다.');
          return;
        }
        if(d.error==='ticket_invalid'){ showErr('등록 링크가 만료되었습니다','비밀번호로 접속하세요.'); return; }
        showErr('비밀번호가 올바르지 않습니다');
        try{ pass.select(); }catch(e){}
      });
    // 네트워크 실패는 비밀번호가 틀린 것과 전혀 다른 일이다 — 같은 자리에 뜨더라도
    // 무엇을 해야 하는지가 달라서 문장을 나눠 둔다.
    }).catch(function(){ showErr('서버에 연결하지 못했습니다','네트워크 상태를 확인한 뒤 다시 시도하세요.'); });
  });
})();
