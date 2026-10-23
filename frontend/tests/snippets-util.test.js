// snippets-util.js — 스니펫의 순수 규칙. 지키는 계약:
//   본문 끝의 줄바꿈 하나가 곧 「마지막 줄까지 Enter」다(서버 snippet_store.normalize_text와 같은 규칙).
//   편집 화면의 (본문, 스위치)와 저장되는 본문이 서로 손실 없이 오가야 한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const UTIL = path.join(__dirname, '../js/snippets-util.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(UTIL, env.context, new Map());
}

test('splitText: 끝 줄바꿈은 스위치로, 본문에서는 빠진다', async () => {
  const u = await load();
  assert.deepStrictEqual({ ...u.splitText('a\nb\n') }, { body: 'a\nb', enter: true });
  assert.deepStrictEqual({ ...u.splitText('a\nb') }, { body: 'a\nb', enter: false });
});

test('composeText ∘ splitText 는 항등이다', async () => {
  const u = await load();
  for (const text of ['ls', 'ls\n', 'a\nb', 'a\nb\n', 'git status\n']) {
    const { body, enter } = u.splitText(text);
    assert.strictEqual(u.composeText(body, enter), text);
  }
});

test('composeText: 앞 빈 줄 제거, 끝 빈 줄은 스위치로 대신, CRLF 정규화, 공백뿐이면 빈 문자열', async () => {
  const u = await load();
  assert.strictEqual(u.composeText('\n\nx\r\ny\n\n\n', true), 'x\ny\n');
  assert.strictEqual(u.composeText('x\n\n', false), 'x');
  assert.strictEqual(u.composeText(' \n ', true), '');
});

test('displayName: 이름이 없으면 본문의 첫 비어 있지 않은 줄', async () => {
  const u = await load();
  assert.strictEqual(u.displayName({ label: '배포', text: 'x' }), '배포');
  assert.strictEqual(u.displayName({ label: '', text: '\n\n  npm test \nnpm run build\n' }), 'npm test');
});

test('matchesQuery: 모든 단어가 이름·본문 어딘가에 있어야 한다(대소문자 무시)', async () => {
  const u = await load();
  const it = { label: 'Deploy', text: 'git push origin main\n' };
  assert.strictEqual(u.matchesQuery(it, ''), true);
  assert.strictEqual(u.matchesQuery(it, 'deploy PUSH'), true);
  assert.strictEqual(u.matchesQuery(it, 'deploy rebase'), false);
});

test('sessionNameFor: 입력 > 스니펫 이름 > 빈 값(서버가 폴더 이름으로)', async () => {
  const u = await load();
  assert.strictEqual(u.sessionNameFor({ label: '배포' }, '  api  '), 'api');
  assert.strictEqual(u.sessionNameFor({ label: ' 배포 ' }, ''), '배포');
  assert.strictEqual(u.sessionNameFor({ label: '' }, ''), '');
});

test('planInput: paste 는 본문을 한 덩어리로 + 끝 Enter 여부, type 은 줄마다 Enter', async () => {
  const u = await load();
  assert.deepStrictEqual({ ...u.planInput('a\nb\n', 'paste') }, { paste: 'a\nb', enter: true });
  assert.deepStrictEqual({ ...u.planInput('a\nb', 'paste') }, { paste: 'a\nb', enter: false });
  assert.deepStrictEqual({ ...u.planInput('a\nb\n', 'type') }, { type: 'a\nb\n' });
  assert.deepStrictEqual({ ...u.planInput('a\nb', 'type') }, { type: 'a\nb' });
});

test('sessionNameAdjusted: 번호만 붙은 건 그대로, 글자가 바뀌면 알린다', async () => {
  const u = await load();
  assert.strictEqual(u.sessionNameAdjusted('deploy', 'deploy'), false);
  assert.strictEqual(u.sessionNameAdjusted('deploy', 'deploy-2'), false);
  assert.strictEqual(u.sessionNameAdjusted('my build', 'my-build'), true);
  assert.strictEqual(u.sessionNameAdjusted('입력만', 'proj'), true);
  assert.strictEqual(u.sessionNameAdjusted('', 'proj'), false);
});
