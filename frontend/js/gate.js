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
  var form=document.getElementById('login-form');
  var pass=document.getElementById('login-pass');
  var otpWrap=document.getElementById('login-otp-wrap');
  var otp=document.getElementById('login-otp');
  var btn=document.getElementById('login-submit');
  var err=document.getElementById('login-err');
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
  function hideGate(){ gate.hidden=true; window.__vtAuthed=true; document.dispatchEvent(new Event('vt:authed')); }
  function showForm(){ spin.hidden=true; form.hidden=false;
    setTimeout(function(){ try{ (otpWrap.hidden?pass:otp).focus(); }catch(e){} }, 50); }
  function showErr(m){ err.textContent=m; err.hidden=false; btn.disabled=false; }
  // 자격증명 파라미터를 URL에서 지우고 재로드 — 히스토리/공유 링크에 남지 않게 한다.
  function reloadClean(){
    params.delete('ticket'); params.delete('token');
    var q=params.toString();
    location.replace(location.pathname+(q?'?'+q:'')+location.hash);
  }

  // 1회용 기기 등록 티켓(QR로 들어온 경우) — 스캔 자체가 등록 승인이다.
  var ticket=params.get('ticket');
  if(ticket){
    fetch('/api/auth',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket:ticket})})
    .then(function(r){
      if(r.ok){ reloadClean(); }
      else { showForm(); showErr('등록 링크가 만료되었습니다. 비밀번호로 접속하세요.'); }
    }).catch(function(){ showForm(); });
    return;
  }

  // URL에 ?token= 이 있으면(레거시 링크) 그 토큰으로 프로브 → 통과 시 terminal.js가 쿠키 교환
  var urlTok=params.get('token');
  var probe='/api/capabilities'+(urlTok?('?token='+encodeURIComponent(urlTok)):'');
  fetch(probe,{credentials:'include'}).then(function(r){
    if(r.status===401){ showForm(); } else { hideGate(); }
  }).catch(function(){ hideGate(); });

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
      if(code.length!==6){ showErr('6자리 코드를 입력하세요'); return; }
      payload.otp=code;
    }
    btn.disabled=true; err.hidden=true;
    fetch('/api/auth',{method:'POST',credentials:'include',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(function(r){
      if(r.ok){ location.reload(); return; }
      return r.json().catch(function(){ return {}; }).then(function(d){
        if(d.error==='otp_required'){
          otpWrap.hidden=false; btn.disabled=false; err.hidden=true;
          setTimeout(function(){ try{ otp.focus(); }catch(e){} },50);
          return;
        }
        if(d.error==='otp_invalid'){
          otpWrap.hidden=false;
          showErr('인증 코드가 올바르지 않습니다'+(d.remaining!=null?' (남은 시도 '+d.remaining+'회)':''));
          try{ otp.value=''; otp.focus(); }catch(e){}
          return;
        }
        if(d.error==='otp_locked'){
          showErr('시도 횟수를 초과했습니다. '+Math.ceil((d.retry_after||600)/60)+'분 후 다시 시도하세요.');
          return;
        }
        if(d.error==='ticket_invalid'){ showErr('등록 링크가 만료되었습니다'); return; }
        showErr('비밀번호가 올바르지 않습니다');
        try{ pass.select(); }catch(e){}
      });
    }).catch(function(){ showErr('연결 오류'); });
  });
})();
