// 에이전트 세션 공유 — 공유 대화상자(share/share-dialog.js)와 순수 규칙(share/share-data.js).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const open = async (routes, name = 'dev', share = null) => {
  const t = await loadShare(routes);
  _wins.push(t.window);
  const p = t.mod.openShareDialog(name, share);
  const dlg = await waitFor(() => t.document.querySelector('.vt-dialog'));
  return { ...t, p, dlg, nick: dlg.querySelector('.vt-share-nick'), ok: dlg.querySelector('.vt-share-ok'), msg: dlg.querySelector('.vt-share-msg') };
};
const typeNick = (t, v) => { t.nick.value = v; t.nick.dispatchEvent(new t.window.Event('input', { bubbles: true })); };
const pickMode = (t, v) => {
  const r = t.dlg.querySelector(`input[type=radio][value="${v}"]`);
  r.checked = true; r.dispatchEvent(new t.window.Event('change', { bubbles: true }));
};

test('경고 문구 네 문장이 그대로 실리고, 기본 닉네임은 세션 이름에서 제안된다', async () => {
  const t = await open({}, 'my.proj');
  const text = t.dlg.querySelector('.vt-share-warn').textContent;
  assert.ok(text.includes('공유하면 에이전트가 이 세션의 화면에 보이는 모든 내용을 읽을 수 있습니다.'));
  assert.ok(text.includes('읽기·쓰기는 에이전트가 이 터미널에서 명령을 실행할 수 있다는 뜻입니다.'));
  assert.ok(text.includes('화면에 비밀번호·토큰이 있으면 공유하지 마세요.'));
  assert.ok(text.includes('처음 쓰려는 에이전트마다 승인을 묻습니다.'));
  assert.strictEqual(t.nick.value, 'my-proj', '허용 밖 글자는 - 로');
  assert.strictEqual(t.dlg.querySelector('input[type=radio]:checked').value, 'read', '기본은 읽기 전용');
  assert.strictEqual(t.document.activeElement, t.nick, '입력창에 포커스');
});

test('접근성: 라벨-입력 연결, 모드는 radiogroup, 오류는 aria-live/aria-invalid', async () => {
  const t = await open({});
  assert.strictEqual(t.dlg.querySelector(`label[for="${t.nick.id}"]`).textContent, '닉네임');
  assert.ok(t.dlg.querySelector('[role=radiogroup]'));
  assert.strictEqual(t.msg.getAttribute('aria-live'), 'polite');
  typeNick(t, 'bad nick');
  assert.strictEqual(t.nick.getAttribute('aria-invalid'), 'true');
  typeNick(t, 'good');
  assert.strictEqual(t.nick.getAttribute('aria-invalid'), null);
});

test('닉네임 규칙을 즉시 검증하고 틀리면 공유 버튼이 꺼진다', async () => {
  const t = await open({});
  for (const bad of ['bad nick', 'a/b', '한글', 'x'.repeat(33)]) {
    typeNick(t, bad);
    assert.ok(t.ok.disabled, `${bad}: 버튼 꺼짐`);
    assert.ok(t.msg.classList.contains('error'), `${bad}: 오류 표시`);
  }
  typeNick(t, '');
  assert.ok(t.ok.disabled, '빈 값은 막되 빨간 오류는 띄우지 않는다');
  assert.ok(!t.msg.classList.contains('error'));
  typeNick(t, 'ok_1-A');
  assert.ok(!t.ok.disabled);
  assert.ok(!t.msg.classList.contains('error'));
});

test('모드 선택이 요청 본문에 실리고, 성공하면 닫히고 알린다', async () => {
  let seen;
  const t = await open({ 'POST /api/tmux/dev/share': (b) => { seen = b; return { json: { ok: true, name: 'dev', share: b } }; } });
  typeNick(t, 'builder');
  pickMode(t, 'readwrite');
  t.ok.click();
  const res = await t.p;
  assert.deepStrictEqual(JSON.parse(JSON.stringify(seen)), { nick: 'builder', mode: 'readwrite' });
  assert.strictEqual(res.nick, 'builder');
  assert.strictEqual(t.document.querySelector('.vt-dialog'), null, '닫힘');
  assert.match(t.toasts[0][0], /공유함 · builder · 읽기·쓰기/);
});

test('읽기 전용(기본)도 mode:read 로 간다', async () => {
  let seen;
  const t = await open({ 'POST /api/tmux/dev/share': (b) => { seen = b; return { json: { ok: true, share: b } }; } });
  t.ok.click();
  await t.p;
  assert.strictEqual(seen.mode, 'read');
});

test('409 nick_taken 은 「이미 쓰는 닉네임입니다」를 보이고 열린 채 다시 고칠 수 있다', async () => {
  const t = await open({ 'POST /api/tmux/dev/share': () => ({ status: 409, json: { error: 'nick_taken', reason: 'x' } }) });
  t.ok.click();
  await waitFor(() => t.msg.classList.contains('error'));
  assert.strictEqual(t.msg.textContent, '이미 쓰는 닉네임입니다');
  assert.ok(t.document.querySelector('.vt-dialog'), '열려 있다');
  assert.ok(!t.ok.disabled, '다시 시도 가능');
  assert.strictEqual(t.document.activeElement, t.nick);
});

test('Enter 로 제출, Esc 로 취소(요청 없음)', async () => {
  let n = 0;
  const t = await open({ 'POST /api/tmux/dev/share': (b) => { n++; return { json: { ok: true, share: b } }; } });
  t.nick.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await t.p;
  assert.strictEqual(n, 1);
  const u = await open({});
  u.document.dispatchEvent(new u.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.strictEqual(await u.p, null);
  assert.strictEqual(u.calls.length, 0);
});

test('공유 설정: 기존 닉네임·모드로 시작하고, 읽기 전용으로 낮추면 안내가 뜨며, 해제 안내가 있다', async () => {
  const t = await open({}, 'dev', { nick: 'prod', mode: 'readwrite' });
  assert.strictEqual(t.nick.value, 'prod');
  assert.strictEqual(t.dlg.querySelector('input[type=radio]:checked').value, 'readwrite');
  assert.strictEqual(t.ok.textContent, '저장');
  const lower = t.dlg.querySelector('.vt-share-lower');
  assert.ok(lower.hidden);
  pickMode(t, 'read');
  assert.ok(!lower.hidden);
  assert.ok(t.dlg.querySelector('.vt-share-note').textContent.includes('즉시 지웁니다'));
});

test('공유 해제: DELETE 를 보내고 지운 권한 수를 알린다', async () => {
  const t = await loadShare({ 'DELETE /api/tmux/dev/share': () => ({ json: { ok: true, revokedGrants: 2, clearedRequests: 0 } }) });
  _wins.push(t.window);
  assert.strictEqual(await t.mod.unshareSession('dev'), true);
  assert.strictEqual(t.calls[0].method, 'DELETE');
  assert.match(t.toasts[0][0], /권한 2개 삭제/);
});

test('요청은 쿠키만 보낸다 — Authorization·?token= 이 없어야 서버가 사람 요청으로 인정한다', async () => {
  const t = await open({ 'POST /api/tmux/dev/share': (b) => ({ json: { ok: true, share: b } }) });
  t.window.VT_TOKEN = 'abc'; t.window._tokenQuery = '?token=abc';
  t.ok.click();
  await t.p;
  const c = t.calls[0];
  assert.ok(!/token=/.test(c.url));
  assert.ok(!(c.opts.headers && c.opts.headers.Authorization));
  assert.strictEqual(c.opts.credentials, 'same-origin');
});

test('순수 규칙: suggestNick·validateNick·lastUsedOf', async () => {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _wins.push(env.window);
  const d = await importFresh(path.join(__dirname, '../js/share/share-data.js'), env.context, new Map());
  assert.strictEqual(d.suggestNick('한글'), 'session');
  assert.strictEqual(d.suggestNick('a b c'), 'a-b-c');
  assert.strictEqual(d.suggestNick('x'.repeat(40)).length, 32);
  assert.strictEqual(d.validateNick('a'), null);
  assert.ok(d.validateNick('a b'));
  const g = { agent: { id: '$1' }, target: { nick: 'n' } };
  const ents = [
    { by: 'agent', action: 'read', result: 'ok', agent: { id: '$1' }, nick: 'n', ts: 10 },
    { by: 'agent', action: 'send', result: 'ok', agent: { id: '$1' }, nick: 'n', ts: 30 },
    { by: 'agent', action: 'read', result: 'rate_limited', agent: { id: '$1' }, nick: 'n', ts: 99 },
    { by: 'agent', action: 'read', result: 'ok', agent: { id: '$2' }, nick: 'n', ts: 77 },
  ];
  assert.strictEqual(d.lastUsedOf(g, ents), 30);
  assert.strictEqual(d.lastUsedOf({ agent: { id: '$9' }, target: { nick: 'n' } }, ents), null);
  void tick;
});
