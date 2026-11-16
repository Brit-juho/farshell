// panels/event-log — 「이슈용 복사」 텍스트(format.js)와 기록 패널(panel.js).
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const JS = (p) => path.join(__dirname, '../js', p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function load(opts) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = 'http://x';
  w.requestAnimationFrame = (fn) => w.setTimeout(fn, 0);
  w.CSS = w.CSS || { escape: (s) => String(s).replace(/"/g, '\\"') };
  w.fetch = (url, o) => (opts && opts.fetch ? opts.fetch(url, o) : Promise.reject(new Error('no fetch')));
  const cache = new Map();
  const F = await importFresh(JS('panels/event-log/format.js'), env.context, cache);
  const E = await importFresh(JS('core/event-log.js'), env.context, cache);
  const P = await importFresh(JS('panels/event-log/panel.js'), env.context, cache);
  return { F, E, P, w, document: env.document };
}

const rows = (document) => [...document.querySelectorAll('.vt-ev-row')];
const jsonRes = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });

test('format: reqLine / eventToText 는 요청·사유·관련(추정)·스택을 이슈용 마크다운으로 만든다', async () => {
  const { F } = await load();
  assert.strictEqual(F.reqLine({ method: 'PUT', path: '/api/fs/file', status: 403, ms: 182 }), 'PUT /api/fs/file → 403 · 182ms');
  assert.strictEqual(F.reqLine({ method: 'GET', path: '/a', status: 0 }), 'GET /a → 응답 없음');
  assert.strictEqual(F.reqLine(null), '');
  const text = F.eventToText({
    level: 'error', msg: '저장 실패', count: 3, ts: 1_700_000_000_000, last: 1_700_000_005_000, source: 'toast',
    req: { method: 'PUT', path: '/api/fs/file', status: 403, code: 'elevation_required', reason: '재확인 필요', host: 'local' },
    relGuess: true, detail: 'Error: x\n  at f (a.js:1:1)', ctx: { view: 'desktop', session: 'main', screen: 'abcdef1234' },
  }, { req: { method: 'GET', path: '/api/other', status: 500 } });
  assert.match(text, /^### \[error\] 저장 실패 ×3/);
  assert.match(text, /요청: PUT \/api\/fs\/file → 403 · code=elevation_required · host=local/);
  assert.match(text, /사유: 재확인 필요/);
  assert.match(text, /관련: GET \/api\/other → 500 \(추정\)/);
  assert.match(text, /session=main · screen=abcdef12/);
  assert.match(text, /```\nError: x\n {2}at f \(a\.js:1:1\)\n```/);
});

test('format: reportText 는 환경 머리말 뒤에 항목을 잇는다', async () => {
  const { F } = await load();
  const t = F.reportText([{ level: 'warn', msg: 'a', count: 1, ts: 1, last: 1, source: 'api' }], { ua: 'Chrome 142 / macOS', lang: 'ko', now: 0 });
  assert.match(t, /UA: Chrome 142 \/ macOS/);
  assert.match(t, /### \[warn\] a/);
});

test('패널: 열면 이 화면 기록(오류·경고·알림)이 최신순으로 보이고 debug 는 기본으로 숨는다', async () => {
  const { E, P, document } = await load();
  E.logEvent({ level: 'error', source: 'toast', msg: '저장 실패' });
  E.logEvent({ level: 'info', source: 'toast', msg: '복사됨' });
  E.logEvent({ level: 'debug', source: 'api', msg: 'GET /a — HTTP 404', req: { method: 'GET', path: '/a', status: 404 } });
  P.showEventLog();
  const msgs = rows(document).map((r) => r.querySelector('.vt-ev-msg').textContent);
  assert.deepStrictEqual(Array.from(msgs), ['복사됨', '저장 실패']);
  assert.ok(document.getElementById('vt-eventlog'));
});

test('패널: 등급 칩으로 요청(debug)을 켜면 나타나고, 오류를 끄면 사라진다', async () => {
  const { E, P, document } = await load();
  E.logEvent({ level: 'error', source: 'toast', msg: '저장 실패' });
  E.logEvent({ level: 'debug', source: 'api', msg: 'GET /a — HTTP 404', req: { method: 'GET', path: '/a', status: 404 } });
  P.showEventLog();
  const chip = (label) => [...document.querySelectorAll('.vt-ev-chip')].find((c) => c.textContent === label);
  chip('요청').click();
  assert.strictEqual(rows(document).length, 2);
  chip('오류').click();
  assert.strictEqual(rows(document).length, 1);
  assert.match(rows(document)[0].textContent, /GET \/a/);
});

test('패널: 한 줄을 누르면 스택·요청·화면 정보가 펼쳐지고 다시 누르면 접힌다', async () => {
  const { E, P, document } = await load();
  const err = new Error('권한 없음');
  err.request = { method: 'PUT', path: '/api/fs/file', status: 403, ms: 9, code: 'elevation_required', host: 'local' };
  E.logEvent({ level: 'error', source: 'toast', msg: '저장 실패', error: err });
  P.showEventLog();
  assert.strictEqual(document.querySelector('.vt-ev-detail'), null);
  document.querySelector('.vt-ev-line').click();
  const d = document.querySelector('.vt-ev-detail');
  assert.ok(d);
  assert.match(d.textContent, /PUT \/api\/fs\/file → 403/);
  assert.match(d.textContent, /elevation_required/);
  assert.match(d.querySelector('.vt-ev-pre').textContent, /권한 없음/);
  document.querySelector('.vt-ev-line').click();
  assert.strictEqual(document.querySelector('.vt-ev-detail'), null);
});

test('패널: 기록 문구는 마크업으로 해석되지 않는다 (서버·스택 문자열)', async () => {
  const { E, P, document } = await load();
  E.logEvent({ level: 'error', source: 'js', msg: '<img src=x onerror=alert(1)>', detail: '<script>alert(1)</script>' });
  P.showEventLog();
  document.querySelector('.vt-ev-line').click();
  assert.strictEqual(document.querySelector('.vt-ev-list img, .vt-ev-list script'), null);
  assert.match(document.querySelector('.vt-ev-pre').textContent, /<script>/);
});

test('패널: 관련 링크는 가려져 있던 요청(debug)을 켜서 그 항목을 펼친다 — 추정이면 표시한다', async () => {
  const { E, P, document } = await load();
  const api = E.logEvent({ level: 'debug', source: 'api', msg: 'POST /api/x — HTTP 409', req: { method: 'POST', path: '/api/x', status: 409 } });
  E.logEvent({ level: 'error', source: 'toast', msg: '열 수 없습니다', rel: api, relGuess: true });
  P.showEventLog();
  document.querySelector('.vt-ev-line').click();
  assert.ok(document.querySelector('.vt-ev-guess'), '추정 표시');
  document.querySelector('.vt-ev-link').click();
  const open = rows(document).filter((r) => r.classList.contains('open'));
  assert.ok(open.some((r) => r.dataset.id === api), '요청 줄이 나타나 펼쳐진다');
});

test('패널: 지우기(이 화면)는 확인 없이 로컬 기록을 비운다', async () => {
  const { E, P, document } = await load();
  E.logEvent({ level: 'error', source: 'js', msg: 'a' });
  P.showEventLog();
  [...document.querySelectorAll('.vt-ev-foot button')].find((b) => b.textContent.includes('지우기')).click();
  assert.strictEqual(E.getEvents().length, 0);
  assert.strictEqual(rows(document).length, 0);
  assert.match(document.querySelector('.vt-ev-empty').textContent, /기록이 없습니다/);
});

test('패널: 열린 채로 새 오류가 생기면 목록이 갱신된다', async () => {
  const { E, P, document } = await load();
  P.showEventLog();
  assert.strictEqual(rows(document).length, 0);
  E.logEvent({ level: 'error', source: 'js', msg: '새 오류' });
  await wait(30);
  assert.strictEqual(rows(document).length, 1);
});

test('패널: 열려 있을 때 같은 id 로 다시 열면 닫지 않고 그 항목으로 옮겨 간다 (토스트 「자세히」)', async () => {
  const { E, P, document } = await load();
  const a = E.logEvent({ level: 'error', source: 'js', msg: 'a' });
  const b = E.logEvent({ level: 'warn', source: 'js', msg: 'b' });
  P.showEventLog();
  P.showEventLog(b);
  assert.ok(document.getElementById('vt-eventlog'), '닫히지 않았다');
  assert.ok(rows(document).find((r) => r.dataset.id === b).classList.contains('open'));
  assert.ok(a);
});

test('패널: 열면 읽음 처리돼 미확인 수가 0 이 된다', async () => {
  const { E, P } = await load();
  E.logEvent({ level: 'error', source: 'js', msg: 'a' });
  assert.strictEqual(E.unseenCount(), 1);
  P.showEventLog();
  assert.strictEqual(E.unseenCount(), 0);
});

test('패널: 「모든 화면」은 서버 기록을 중계 없이(noLog) 읽어 보여 준다', async () => {
  const calls = [];
  const now = Date.now();
  const { P, document } = await load({ fetch: (url, o) => { calls.push(url); return jsonRes({ items: [
    { id: 's1', level: 'error', source: 'js', msg: '폰에서 난 오류', ts: now, last: now, count: 2, ctx: { ua: 'Chrome 142 / Android', screen: 'zzzz' } },
  ] }); } });
  P.showEventLog();
  [...document.querySelectorAll('.vt-ev-segbtn')].find((b) => b.textContent === '모든 화면').click();
  await wait(30);
  assert.ok(calls[0].startsWith('http://x/api/client-log?'), calls[0]);
  assert.strictEqual(rows(document).length, 1);
  assert.match(rows(document)[0].textContent, /폰에서 난 오류/);
  assert.ok(![...document.querySelectorAll('.vt-ev-chip')].some((c) => c.textContent === '요청'), '서버에는 오류·경고만 있다');
});

test('패널: 서버 기록을 쓸 수 없으면(403) 이유를 보이고 다시 시도를 준다', async () => {
  const { P, document } = await load({ fetch: () => jsonRes({ error: 'human_session_required' }, 403) });
  P.showEventLog();
  [...document.querySelectorAll('.vt-ev-segbtn')].find((b) => b.textContent === '모든 화면').click();
  await wait(30);
  assert.match(document.querySelector('.vt-ev-empty.err').textContent, /서버 기록을 쓸 수 없습니다/);
});
