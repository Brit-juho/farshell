// 화면 언어 — js/i18n-boot.js(번역 함수 원본)와 locales/en.js(사전)를 실제 브라우저와
// 같은 순서(사전 → boot)로 jsdom에 올려 본다. 서버가 <html lang>을 박는 부분은
// server/tests/test_i18n.py가 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const BOOT = fs.readFileSync(path.join(__dirname, '../js/i18n-boot.js'), 'utf8');
const EN = fs.readFileSync(path.join(__dirname, '../js/locales/en.js'), 'utf8');
const INDEX = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');

const doms = [];
after(() => { for (const d of doms) d.window.close(); });

function load(lang, body = '', dict = null) {
  const dom = new JSDOM(`<!doctype html><html lang="${lang}"><body>${body}</body></html>`, { runScripts: 'outside-only' });
  doms.push(dom);
  if (dict) dom.window.eval(`window.__VT_I18N = ${JSON.stringify(dict)};`);
  dom.window.eval(BOOT);
  return dom.window;
}

test('ko: 사전 없이 원문 + 자리표시자 치환', () => {
  const w = load('ko');
  assert.strictEqual(w.vtLang(), 'ko');
  assert.strictEqual(w.vtT('남은 시도 {n}회', { n: 2 }), '남은 시도 2회');
  assert.strictEqual(w.vtT('{a} 그대로', {}), '{a} 그대로');
});

test('en: 번역, 복수형, 빠진 키는 원문', () => {
  const w = load('en', '', {
    '닫기': 'Close',
    '남은 시도 {n}회': { one: '1 attempt left', other: '{n} attempts left' },
  });
  assert.strictEqual(w.vtLang(), 'en');
  assert.strictEqual(w.vtT('닫기'), 'Close');
  assert.strictEqual(w.vtT('남은 시도 {n}회', { n: 1 }), '1 attempt left');
  assert.strictEqual(w.vtT('남은 시도 {n}회', { n: 3 }), '3 attempts left');
  assert.strictEqual(w.vtT('없는 문구'), '없는 문구');
  // 사전 키가 Object.prototype 이름이어도 원문으로 떨어진다.
  assert.strictEqual(w.vtT('toString'), 'toString');
});

test('en: 정적 HTML — 잎 텍스트, 아이콘 섞인 텍스트, 속성', () => {
  const w = load('en',
    '<p data-i18n>닫기</p>' +
    '<button data-i18n><svg></svg>  닫기\n</button>' +
    '<input placeholder="닫기" aria-label="닫기" title="그대로" data-i18n-attr="placeholder,aria-label">' +
    '<span>닫기</span>',
    { '닫기': 'Close' });
  w.vtApplyI18n(w.document);
  const d = w.document;
  assert.strictEqual(d.querySelector('p').textContent, 'Close');
  assert.strictEqual(d.querySelector('button').textContent, '  Close\n');
  assert.ok(d.querySelector('button svg'), '아이콘 요소는 남는다');
  const inp = d.querySelector('input');
  assert.strictEqual(inp.placeholder, 'Close');
  assert.strictEqual(inp.getAttribute('aria-label'), 'Close');
  assert.strictEqual(inp.title, '그대로');
  assert.strictEqual(d.querySelector('span').textContent, '닫기', '표식 없는 요소는 건드리지 않는다');
  w.vtApplyI18n(w.document); // 두 번 적용해도 같다(gate.js와 main.js가 각각 부른다)
  assert.strictEqual(d.querySelector('p').textContent, 'Close');
});

test('실제 사전으로 로그인 화면이 영어가 된다', () => {
  const body = INDEX.slice(INDEX.indexOf('<body'), INDEX.indexOf('<!--vt:i18n-->'));
  const dom = new JSDOM(`<!doctype html><html lang="en">${body}</html>`, { runScripts: 'outside-only' });
  doms.push(dom);
  dom.window.eval(EN);
  dom.window.eval(BOOT);
  const gate = dom.window.document.getElementById('login-gate');
  dom.window.vtApplyI18n(gate);
  assert.strictEqual(dom.window.document.querySelector('label[for="login-pass"]').textContent, 'Password');
  assert.strictEqual(dom.window.document.getElementById('login-submit-label').textContent, 'Sign in');
  assert.doesNotMatch(gate.textContent, /[가-힣]/, '로그인 화면에 한국어가 남지 않는다');
});

test('core/i18n.js: 전역이 없으면 원문', async () => {
  const mod = await import(path.join(__dirname, '../js/core/i18n.js'));
  assert.strictEqual(mod.lang(), 'ko');
  assert.strictEqual(mod.t('{n}개', { n: 3 }), '3개');
});
