// E2E 암호화(nacl, ~20KB)는 ?e2e=1/#e2e=1일 때만 필요 — 그 경우에만 동기 로드해서
// terminal.js가 실행될 때 이미 window.nacl이 준비돼 있게 한다(P1).
// 인라인 스크립트였으나 CSP `script-src 'self'` 때문에 파일로 뺐다. document.write는 파서가 막힌 동안에만
// 동기 삽입이 보장되므로 classic script(async·defer 아님)로 로드해야 한다.
if (/[?&#]e2e=1(&|$)/.test(location.search + location.hash)) {
  document.write('<scr' + 'ipt src="/static/vendor/nacl.min.js"></scr' + 'ipt>');
  document.write('<scr' + 'ipt src="/static/vendor/nacl-util.min.js"></scr' + 'ipt>');
}
