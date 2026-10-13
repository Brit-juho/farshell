// 화면 언어 — classic script. gate.js(로그인 화면)가 ES 모듈보다 먼저 돌기 때문에
// 번역 함수도 모듈 밖에 있어야 한다. 모듈 쪽 진입점은 core/i18n.js(이 전역을 감싼다).
//
// 키는 한국어 원문이다(gettext 방식): t('세션 닫기'). 한국어 화면은 사전 없이 원문을
// 그대로 쓰고, 영어 화면만 서버가 끼워 넣은 locales/en.js의 window.__VT_I18N을 본다.
// 사전에 없는 문구는 원문(한국어)으로 나온다 — 빈칸이나 키 이름이 보이는 것보다 낫다.
//
// 값은 문자열이거나 {one, other} — vars.n이 1이면 one을 쓴다(영어 복수형).
// 자리표시자는 {name}. 원문과 번역이 같은 이름을 써야 한다(scripts/check_i18n.py가 본다).
(function(){
  function lang(){ return document.documentElement.lang === 'en' ? 'en' : 'ko'; }
  function fill(s, vars){
    if(!vars) return s;
    return s.replace(/\{(\w+)\}/g, function(m, k){
      return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }
  function t(src, vars){
    var out = src;
    if(lang() !== 'ko'){
      var dict = window.__VT_I18N || {};
      var v = Object.prototype.hasOwnProperty.call(dict, src) ? dict[src] : null;
      if(typeof v === 'string') out = v;
      else if(v && typeof v === 'object') out = (vars && Number(vars.n) === 1 && v.one) ? v.one : (v.other || v.one || src);
    }
    return fill(out, vars);
  }
  // 정적 HTML 번역. data-i18n이 붙은 요소의 텍스트와
  // data-i18n-attr="placeholder,aria-label"에 적힌 속성을 원문 키로 바꾼다.
  function apply(root){
    if(lang() === 'ko' || !root || !root.querySelectorAll) return;
    var els = root.querySelectorAll('[data-i18n],[data-i18n-attr]');
    for(var i = 0; i < els.length; i++){
      var el = els[i];
      if(el.hasAttribute('data-i18n')){
        if(!el.children.length){
          var src = el.getAttribute('data-i18n') || el.textContent.trim();
          if(src) el.textContent = t(src);
        } else {
          // 아이콘 + 문구처럼 자식 요소가 섞인 경우: 직속 텍스트 노드만 바꾼다.
          for(var c = el.firstChild; c; c = c.nextSibling){
            if(c.nodeType === 3 && c.nodeValue.trim()){
              var raw = c.nodeValue, key = raw.trim();
              c.nodeValue = raw.replace(key, t(key));
            }
          }
        }
      }
      var attrs = (el.getAttribute('data-i18n-attr') || '').split(',');
      for(var j = 0; j < attrs.length; j++){
        var a = attrs[j].trim();
        if(a && el.hasAttribute(a)) el.setAttribute(a, t(el.getAttribute(a)));
      }
    }
  }
  window.vtLang = lang;
  window.vtT = t;
  window.vtApplyI18n = apply;
})();
