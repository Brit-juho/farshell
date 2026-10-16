// 페어 승인(kind=pair) — 승인함 모달 안의 한 화면: 누가 누구와, 묶음, 역할별 프리셋, 확인 문구 둘, 높은 권한 체크 박스(미리 체크 없음),
// 재인증은 체크가 있을 때만 한 번, 허용 버튼 글자 = 줄 것, 거부가 기본 포커스, 400ms 무장, 서버 거절 이유 표시.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { item, promptInput } = require('./helpers/authz-fixtures');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const ARM = 450;
const J = (x) => JSON.parse(JSON.stringify(x));

const DRIVER = { role: 'driver', preset: { id: 'drive', label: '운전', exact: false }, scopes: ['fs.read', 'fs.write', 'session.input'], conds: { 'session.input': { modes: ['line'] } } };
const NAV = { role: 'navigator', preset: { id: 'collab', label: '협업', exact: false }, scopes: ['fs.read', 'session.read'], conds: {} };
const pairItem = (id = 'pq-1', over = {}, pair = {}) => item(id, 'pair', {
  subject: { kind: 'agent', id: null, name: 'beta' }, resource: { id: 'pair:pr-1', label: 'pair' },
  scopes: ['fs.read', 'fs.write', 'session.input', 'session.read'], preset: null, elevated: [],
  pair: {
    id: 'pr-1', phrases: { initiator: 'orchard river', joiner: 'orchard river' },
    bundle: { sessions: ['alpha', 'beta'], dirs: ['/work/proj'], leases: true, summary: '세션 alpha, beta · 폴더 /work/proj · lease 잡음' },
    roles: { initiator: { name: '', kind: 'human', role: 'navigator' }, joiner: { name: 'beta', kind: 'agent', role: 'driver' } },
    presets: { driver: DRIVER, navigator: NAV }, elevatedItems: [], needsReauth: false, note: '서버 안내',
    ...pair,
  },
  ...over,
});
const ELEV = [
  { id: 'raw_input', label: 'raw 키 입력(세션에 키를 그대로 보냄)', reasons: ['session.input[raw]'] },
  { id: 'session_manage', label: '세션 관리(만들기·종료)', reasons: ['session.manage'] },
];

async function setup(routes = {}) {
  const t = await loadShare(routes, { files: ['authz/entry.js', 'authz/inbox.js'] });
  _wins.push(t.window);
  const [entry, inbox] = t.mods;
  const modal = () => t.document.querySelector('.vt-authz-approval');
  const act = (a) => modal().querySelector(`[data-act="${a}"]`);
  const text = () => modal().querySelector('.vt-authz-approval-body').textContent;
  const ticks = () => [...modal().querySelectorAll('input[data-tick]')];
  const pw = (v = 'pw') => { const i = promptInput(t.document); i.value = v; t.document.querySelectorAll('.vt-dialog-ok')[t.document.querySelectorAll('.vt-dialog-ok').length - 1].click(); };
  return { ...t, entry, inbox, modal, act, text, ticks, pw };
}
const decideLog = (log, json = { ok: true, state: 'allowed' }) => (b, url) => { log.push({ url, body: J(b) }); return { json }; };

test('한 화면: 누가 누구와(닉네임)·묶음을 말로·역할별 프리셋 이름과 정확한 scope+조건', async () => {
  const t = await setup();
  t.entry.setPending([pairItem()]);
  assert.match(t.text(), /「사람\(나\)」와\(과\) 「beta」이\(가\) 같은 작업 묶음을 함께 하려 합니다/);
  assert.match(t.text(), /beta.*에이전트 · 운전자\(driver\)/);
  assert.match(t.text(), /사람\(나\).*사람 · 길잡이\(navigator\)/);
  assert.match(t.text(), /운전 범위 안/);
  assert.match(t.text(), /협업 범위 안/);
  assert.match(t.text(), /fs\.read, fs\.write, session\.input · session\.input\[modes=line\]/);
  assert.match(t.text(), /alpha/);
  assert.match(t.text(), /\/work\/proj/);
  assert.match(t.text(), /폴더마다 운전자 이름으로 잡습니다/);
  assert.match(t.text(), /서버 안내/);
});

test('영어에서 어색한 「Part of」가 아니라 「… 범위 안」으로 나온다', async () => {
  const t = await setup();
  const d = await require('./helpers/vm-esm').importFresh(require('node:path').join(__dirname, '../js/authz/data.js'), t.env.context, new Map());
  assert.strictEqual(d.presetName({ id: 'drive', label: '운전', exact: false }), '운전 범위 안');
  assert.strictEqual(d.presetName({ id: 'drive', label: '운전', exact: true }), '운전');
});

test('확인 문구 둘이 나란히, 비교 안내와 함께 보인다. 둘이 다르면 거부하라는 경고가 더 뜬다', async () => {
  const t = await setup();
  t.entry.setPending([pairItem()]);
  const cells = t.modal().querySelectorAll('.vt-pair-phrase');
  assert.deepStrictEqual([...cells].map((c) => c.textContent), ['orchard river', 'orchard river']);
  assert.match(t.text(), /beta 쪽 문구/);
  assert.match(t.text(), /각자 화면에 보여 준 문구와 위 문구를 하나씩 비교/);
  assert.strictEqual(t.modal().querySelector('.vt-authz-warn'), null);
  t.entry.setPending([pairItem('pq-1', {}, { phrases: { initiator: 'orchard river', joiner: 'maple window' } })]);
  assert.match(t.modal().querySelector('.vt-authz-warn').textContent, /서로 다릅니다/);
});

test('높은 권한: 항목마다 체크 박스, 아무것도 미리 체크돼 있지 않고, 사유 scope 가 그대로 보인다', async () => {
  const t = await setup();
  t.entry.setPending([pairItem('pq-1', { elevated: ['session.input[raw]'] }, { elevatedItems: ELEV, needsReauth: true })]);
  assert.strictEqual(t.ticks().length, 2);
  assert.ok(t.ticks().every((c) => !c.checked));
  assert.match(t.text(), /session\.input\[raw\]/);
  assert.match(t.text(), /체크한 항목은 beta에게만/);
});

test('높은 권한 항목이 없으면 체크 영역도 없다', async () => {
  const t = await setup();
  t.entry.setPending([pairItem()]);
  assert.strictEqual(t.ticks().length, 0);
});

test('허용 버튼 글자는 줄 것을 그대로 말한다 — 체크가 바뀌면 바로 따라 바뀐다', async () => {
  const t = await setup();
  t.entry.setPending([pairItem('pq-1', {}, { elevatedItems: ELEV, needsReauth: true })]);
  assert.match(t.act('allow').textContent, /허용 — 사람\(나\): 길잡이\(navigator\) · 협업 범위 안 \/ beta: 운전자\(driver\) · 운전 범위 안 · 높은 권한 없이/);
  t.ticks()[0].checked = true; t.ticks()[0].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  assert.match(t.act('allow').textContent, /높은 권한 raw 키 입력$/);
  t.ticks()[1].checked = true; t.ticks()[1].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  assert.match(t.act('allow').textContent, /높은 권한 raw 키 입력, 세션 관리$/);
  t.ticks()[0].checked = false; t.ticks()[0].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  assert.match(t.act('allow').textContent, /높은 권한 세션 관리$/);
});

test('선택지는 거부·허용 둘뿐(한 번만·이 세션 동안·모두 허용 없음), 기본 포커스는 거부', async () => {
  const t = await setup();
  t.entry.setPending([pairItem()]);
  assert.deepStrictEqual([...t.modal().querySelectorAll('.vt-authz-act')].map((b) => b.dataset.act), ['deny', 'allow']);
  assert.strictEqual(t.document.activeElement, t.act('deny'));
  const labels = [...t.modal().querySelectorAll('button')].map((b) => b.textContent);
  assert.ok(!labels.some((l) => /모두 허용|한 번만|이 세션 동안/.test(l)));
});

test('400ms 무장: 뜬 직후의 클릭은 요청을 보내지 않는다. Esc 는 결정이 아니라 나중에', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': decideLog(log) });
  t.entry.setPending([pairItem()]);
  t.act('allow').click(); t.act('deny').click();
  await tick(30);
  assert.strictEqual(log.length, 0);
  t.document.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.strictEqual(t.modal(), null);
  assert.strictEqual(log.length, 0);
});

test('체크 없이 허용: 재인증을 묻지 않고 elevated:[] 로 한 번 보낸다(choice 없음)', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': decideLog(log) });
  t.entry.setPending([pairItem('pq-1', {}, { elevatedItems: ELEV, needsReauth: true })]);
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(log[0].body, { decision: 'allow', elevated: [] });
  assert.strictEqual(t.document.querySelector('.vt-dialog-input'), null, '비밀번호 창이 뜨지 않는다');
  await waitFor(() => !t.modal());
});

test('체크하고 허용: 서버가 재인증을 요구하면 비밀번호를 한 번만 묻고 같은 체크로 다시 보낸다', async () => {
  const log = [];
  let n = 0;
  const t = await setup({
    'POST /api/authz/approvals/pq-1/decision': (b, url) => { log.push(J(b)); n += 1; return n === 1 ? { status: 403, json: { error: 'elevation_required' } } : { json: { ok: true, state: 'allowed' } }; },
    'POST /api/auth/elevate': () => ({ json: { ok: true } }),
  });
  t.entry.setPending([pairItem('pq-1', {}, { elevatedItems: ELEV, needsReauth: true })]);
  t.ticks()[1].checked = true; t.ticks()[1].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => promptInput(t.document));
  t.pw('secret');
  await waitFor(() => log.length === 2);
  assert.deepStrictEqual(log[0], { decision: 'allow', elevated: ['session_manage'] });
  assert.deepStrictEqual(log[1], { decision: 'allow', elevated: ['session_manage'] });
  assert.strictEqual(t.calls.filter((c) => c.url.split('?')[0].endsWith('/api/auth/elevate')).length, 1, '재인증은 한 번');
  await waitFor(() => !t.modal());
});

test('재인증을 취소하면 아무것도 허용되지 않고 창은 열려 있고 체크도 남는다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': (b) => { log.push(J(b)); return { status: 403, json: { error: 'elevation_required' } }; } });
  t.entry.setPending([pairItem('pq-1', {}, { elevatedItems: ELEV, needsReauth: true })]);
  t.ticks()[0].checked = true; t.ticks()[0].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => promptInput(t.document));
  t.document.querySelectorAll('.vt-dialog-cancel')[t.document.querySelectorAll('.vt-dialog-cancel').length - 1].click();
  await tick(30);
  assert.strictEqual(log.length, 1);
  assert.ok(t.modal());
  assert.ok(t.ticks()[0].checked);
});

test('거부는 높은 권한이 있어도 재인증 없이 deny 만 보낸다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': decideLog(log, { ok: true, state: 'denied' }) });
  t.entry.setPending([pairItem('pq-1', {}, { elevatedItems: ELEV, needsReauth: true })]);
  t.ticks()[0].checked = true; t.ticks()[0].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  await tick(ARM);
  t.act('deny').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(log[0].body, { decision: 'deny' });
});

test('서버가 거절하면(확인 문구 불일치) 서버의 이유를 그대로 보이고 창은 열려 있다', async () => {
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': () => ({ status: 400, json: { error: 'phrase_mismatch', reason: '확인 문구가 맞지 않습니다 — 두 에이전트가 보고 있는 문구와 같은지 확인하세요', remaining: 2 } }) });
  t.entry.setPending([pairItem()]);
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => /확인 문구가 맞지 않습니다/.test(t.modal().querySelector('.vt-authz-err').textContent));
  assert.ok(t.modal());
  assert.ok(!t.act('deny').disabled);
});

test('wider_than_requested 같은 그 밖의 서버 거절도 이유 그대로, 열린 채', async () => {
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': () => ({ status: 400, json: { error: 'wider_than_requested', reason: '요청하지 않은 elevated 항목은 줄 수 없습니다' } }) });
  t.entry.setPending([pairItem()]);
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => /요청하지 않은 elevated 항목/.test(t.modal().querySelector('.vt-authz-err').textContent));
  assert.ok(t.modal());
});

test('already_decided: 서버의 이유를 보이고 창은 열어 둔 채 목록을 다시 맞춘다', async () => {
  const t = await setup({
    'POST /api/authz/approvals/pq-1/decision': () => ({ status: 409, json: { error: 'already_decided', reason: '이미 처리된 요청입니다 — 먼저 온 결정이 적용됐습니다' } }),
    'GET /api/authz/approvals': () => ({ json: { approvals: [pairItem('pq-1'), pairItem('pq-2', { created: 5 })] } }),
  });
  t.entry.setPending([pairItem('pq-1'), pairItem('pq-2', { created: 5 })]);
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => /이미 처리된 요청입니다/.test(t.modal().querySelector('.vt-authz-err').textContent));
  assert.ok(t.modal());
  await waitFor(() => t.calls.some((c) => c.method === 'GET' && c.url === '/api/authz/approvals'));
});

test('서버가 요청 자체를 닫은 거절(확인 문구 3번 불일치)은 이유를 알림으로 남기고 목록에서 뺀다', async () => {
  const t = await setup({ 'POST /api/authz/approvals/pq-1/decision': () => ({ status: 409, json: { error: 'phrase_mismatch_locked', reason: '확인 문구가 계속 맞지 않아 요청을 거절했습니다' } }) });
  t.entry.setPending([pairItem('pq-1')]);
  await tick(ARM);
  t.act('allow').click();
  await waitFor(() => !t.modal());
  assert.match(t.toasts.map((x) => x[0]).join('|'), /계속 맞지 않아 요청을 거절/);
});

test('같은 요청의 체크는 다시 그려져도 유지되고, 다른 요청은 체크 없이 시작한다', async () => {
  const t = await setup();
  t.entry.setPending([pairItem('pq-1', { created: 1 }, { elevatedItems: ELEV }), pairItem('pq-2', { created: 2 }, { elevatedItems: ELEV })]);
  t.ticks()[0].checked = true; t.ticks()[0].dispatchEvent(new t.window.Event('change', { bubbles: true }));
  t.entry.setPending([pairItem('pq-1', { created: 1 }, { elevatedItems: ELEV }), pairItem('pq-2', { created: 2 }, { elevatedItems: ELEV })]);
  assert.ok(t.ticks()[0].checked, '같은 요청 — 유지');
  t.entry.showPending('pq-2');
  assert.ok(t.ticks().every((c) => !c.checked), '다른 요청 — 미리 체크 없음');
});

test('서버 글자는 이스케이프된다(닉네임·묶음·문구에 마크업 없음)', async () => {
  const t = await setup();
  t.entry.setPending([pairItem('pq-1', {}, {
    bundle: { sessions: ['<img src=x>'], dirs: ['<b>d</b>'], leases: false, summary: '' },
    roles: { initiator: { name: '<i>a</i>', kind: 'agent', role: 'driver' }, joiner: { name: 'beta', kind: 'agent', role: 'navigator' } },
    phrases: { initiator: '<u>x</u>', joiner: '<u>x</u>' },
  })]);
  assert.strictEqual(t.modal().querySelector('img, b, i, u'), null);
  assert.match(t.text(), /<img src=x>/);
});

test('승인 대기 목록 행: 페어는 묶음을 말로 보이고 높은 권한 표시가 붙는다', async () => {
  const t = await setup();
  const host = t.document.createElement('div'); t.document.body.appendChild(host);
  t.entry.setPending([pairItem('pq-1', { elevated: ['session.input[raw]'] })]);
  t.entry.showPending();
  // 모달을 닫고 목록만
  t.document.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  t.inbox.mountInboxList(host);
  assert.match(host.textContent, /페어/);
  assert.match(host.textContent, /세션 alpha, beta · 폴더 \/work\/proj · lease 잡음/);
  assert.match(host.textContent, /높은 권한/);
});
