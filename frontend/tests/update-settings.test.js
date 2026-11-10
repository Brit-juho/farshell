// 설정 > 「업데이트」 — 상태 표시, 「지금 확인」, 설정 변경(승격 필요), 확인 끔(환경변수) 처리. 서버 계약은 API.md «Update».
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { answerPrompt } = require('./helpers/dialog-stub');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const J = (p) => path.join(__dirname, p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

const status = (over = {}) => ({
  checked_at: 1760000000, channel: 'stable', current: '2.10.1', latest: '2.10.2', latest_tag: 'v2.10.2', available: true,
  applicable: true, signature_ok: true, signer: 'SHA256:abc', error: null, blocked: null,
  attempts: { version: null, count: 0 }, auto: { reason: null }, last_apply: null,
  settings: { check: true, auto: 'off', auto_hour: 4 }, check_enabled: true, env_off: false, in_progress: false, ...over,
});

async function build(initial = status(), { needElevation = false } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = ''; window._tokenQuery = ''; window.fitAndResize = () => {};
  const calls = []; const toasts = [];
  let current = initial; let elevated = !needElevation;
  window.showToast = (m, type) => toasts.push([m, type]);
  window.fetch = (url, opts = {}) => {
    const u = String(url).split('?')[0];
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ u, method, body });
    const res = (b, code = 200) => Promise.resolve({ ok: code < 400, status: code, json: () => Promise.resolve(b) });
    if (u === '/api/update/status') return res(current);
    if (u === '/api/update/check') return res({ ran: true, status: (current = { ...current, checked_at: 1760009999, available: false, latest: '2.10.1', latest_tag: 'v2.10.1' }) });
    if (u === '/api/auth/elevate') { if (body.password === 'pw') { elevated = true; return res({ ok: true }); } return res({ error: 'bad' }, 401); }
    if (u === '/api/update/settings') {
      if (!elevated) return res({ error: 'elevation_required', reason: '비밀번호 재확인이 필요합니다' }, 401);
      current = { ...current, settings: { ...current.settings, ...body } };
      return res(current);
    }
    if (u === '/api/hooks/status') return res({ ok: true, events: {} });
    return res({});
  };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(J('../js/ui/toast.js'), env.context, cache);
  await importFresh(J('../js/core/settings.js'), env.context, cache);
  const P = await importFresh(J('../js/panels/settings.js'), env.context, cache);
  const document = window.document;
  P.showSettings('update');
  await flush();
  return { document, window, calls, toasts, setStatus: (s) => { current = s; } };
}

const rowOf = (d, label) => Array.from(d.querySelectorAll('.vt-set-row')).find((r) => r.querySelector('.vt-set-label').textContent.startsWith(label));
const writes = (calls) => calls.filter((c) => c.u === '/api/update/settings');

test('업데이트 섹션이 상태를 보여 준다 (현재·최신·서명·마지막 확인)', async () => {
  const { document } = await build();
  assert.ok(Array.from(document.querySelectorAll('.vt-set-navitem')).some((b) => b.textContent === '업데이트'), '섹션 목록에 있다');
  assert.strictEqual(document.querySelector('.vt-set-title').textContent, '업데이트');
  assert.match(rowOf(document, '현재 버전').textContent, /2\.10\.1/);
  const latest = rowOf(document, '최신 버전');
  assert.match(latest.textContent, /v2\.10\.2/);
  assert.match(latest.textContent, /서명 검증을 통과했습니다/);
  assert.match(document.body.textContent, /적용하려면 터미널에서 fsh update/);
  assert.match(rowOf(document, '자동 업데이트').textContent, /기본은 꺼짐/);
  assert.strictEqual(rowOf(document, '자동 업데이트').querySelector('select').value, 'off');
  assert.ok(rowOf(document, '적용 시각').querySelector('select').disabled, '자동이 꺼져 있으면 시각은 잠겨 있다');
});

test('「지금 확인」은 POST 하고 결과로 화면을 갱신한다', async () => {
  const { document, calls } = await build();
  const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === '지금 확인');
  btn.click(); await flush();
  assert.ok(calls.some((c) => c.u === '/api/update/check' && c.method === 'POST'));
  assert.match(rowOf(document, '최신 버전').textContent, /최신입니다/);
});

test('설정 변경은 비밀번호 재확인을 거쳐 같은 요청을 다시 보낸다', async () => {
  const { document, calls } = await build(status(), { needElevation: true });
  const sel = rowOf(document, '자동 업데이트').querySelector('select');
  sel.value = 'stable-only';
  sel.dispatchEvent(new document.defaultView.Event('change'));
  await flush();
  await answerPrompt(document, { value: 'pw' });
  await flush();
  assert.deepStrictEqual(writes(calls).map((c) => c.body), [{ auto: 'stable-only' }, { auto: 'stable-only' }], '401 뒤에 승격하고 같은 본문을 다시 보낸다');
  assert.ok(calls.some((c) => c.u === '/api/auth/elevate'));
  assert.strictEqual(rowOf(document, '자동 업데이트').querySelector('select').value, 'stable-only');
  assert.ok(!rowOf(document, '적용 시각').querySelector('select').disabled, '켜지면 시각을 고를 수 있다');
});

test('비밀번호 입력을 취소하면 아무것도 바뀌지 않는다', async () => {
  const { document, calls } = await build(status(), { needElevation: true });
  const sel = rowOf(document, '자동 업데이트').querySelector('select');
  sel.value = 'stable-only';
  sel.dispatchEvent(new document.defaultView.Event('change'));
  await flush();
  await answerPrompt(document, { accept: false });
  await flush();
  assert.strictEqual(writes(calls).length, 1);
  assert.strictEqual(rowOf(document, '자동 업데이트').querySelector('select').value, 'off', '화면도 서버 값(꺼짐)으로 돌아온다');
});

test('적용 시각과 확인 끄기도 같은 API 로 저장한다', async () => {
  const { document, calls } = await build(status({ settings: { check: true, auto: 'stable-only', auto_hour: 4 } }));
  const hour = rowOf(document, '적용 시각').querySelector('select');
  hour.value = '3';
  hour.dispatchEvent(new document.defaultView.Event('change'));
  await flush();
  rowOf(document, '새 버전 확인').querySelector('[role="switch"]').click();
  await flush();
  assert.deepStrictEqual(writes(calls).map((c) => c.body), [{ auto_hour: 3 }, { check: false }]);
});

test('환경변수로 꺼져 있으면 스위치와 「지금 확인」이 잠긴다 (설명 포함)', async () => {
  const { document } = await build(status({ env_off: true, check_enabled: false, available: false, settings: { check: true, auto: 'off', auto_hour: 4 } }));
  const sw = rowOf(document, '새 버전 확인').querySelector('[role="switch"]');
  assert.ok(sw.disabled && sw.getAttribute('aria-checked') === 'false');
  assert.match(rowOf(document, '새 버전 확인').textContent, /VT_UPDATE_CHECK=0/);
  assert.ok(Array.from(document.querySelectorAll('button')).find((b) => b.textContent === '지금 확인').disabled);
  assert.match(rowOf(document, '최신 버전').textContent, /확인 끔/);
});

test('자동 적용이 미뤄진 이유·마지막 결과(실패는 되돌림 표시)를 보여 준다', async () => {
  const { document } = await build(status({
    settings: { check: true, auto: 'stable-only', auto_hour: 4 },
    auto: { reason: 'busy_agent_busy' },
    last_apply: { result: 'failed', at: 1760000100, from: '2.10.0', to: '2.10.1', reason: '프런트엔드 빌드 실패:\nboom', rolled_back: true },
  }));
  const text = document.body.textContent;
  assert.match(text, /작업 중이거나 입력을 기다리는 에이전트가 있어 미뤘습니다/);
  assert.match(text, /마지막 자동 적용 실패: 프런트엔드 빌드 실패: \(되돌림\)/);
});

test('서명 검증 실패·키 미고정은 이유가 보이고 「있음」으로 속이지 않는다', async () => {
  const { document } = await build(status({ available: false, signature_ok: false, error: 'v9.0.0 의 서명이 신뢰하는 키와 맞지 않습니다' }));
  assert.match(rowOf(document, '최신 버전').textContent, /맞지 않습니다/);
  const { document: d2 } = await build(status({ signature_ok: null, blocked: 'no_trusted_key', applicable: false }));
  assert.match(rowOf(d2, '최신 버전').textContent, /fsh update trust/);
});

test('상태를 못 읽으면 오류 한 줄만 보이고 깨지지 않는다', async () => {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = ''; window._tokenQuery = ''; window.fitAndResize = () => {};
  window.fetch = () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({ error: 'human_session_required', reason: '브라우저 로그인 필요' }) });
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(J('../js/ui/toast.js'), env.context, cache);
  await importFresh(J('../js/core/settings.js'), env.context, cache);
  const P = await importFresh(J('../js/panels/settings.js'), env.context, cache);
  P.showSettings('update');
  await flush();
  assert.match(window.document.querySelector('.vt-set-content').textContent, /브라우저 로그인 필요/);
});
