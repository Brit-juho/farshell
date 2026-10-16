// 통합 승인함 — 모달(authz/inbox.js): 종류별 표시, 결정 선택지, 400ms 무장, Esc=나중에, 포커스, 일괄 승인 없음, 높은 권한 흐름.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { item, promptInput } = require('./helpers/authz-fixtures');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });
const ARM = 450;   // inbox.js ARM_MS(400) + 여유
const J = (x) => JSON.parse(JSON.stringify(x));

async function setup(routes = {}) {
  const t = await loadShare(routes, { files: ['authz/entry.js', 'authz/inbox.js'] });
  _wins.push(t.window);
  const [entry, inbox] = t.mods;
  const modal = () => t.document.querySelector('.vt-authz-approval');
  const act = (a) => modal().querySelector(`[data-act="${a}"]`);
  const text = () => modal().querySelector('.vt-authz-approval-body').textContent;
  return { ...t, entry, inbox, modal, act, text };
}
const decideRoute = (log, json = { ok: true, request: {}, grant: { id: 'g' } }) => (b, url) => { log.push({ url, body: J(b) }); return { json }; };
const esc = (t) => t.document.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

test('share 요청: 문장에 프리셋 이름, 상세 줄에 정확한 scope, 종류·대상·시각이 보이고 기본 포커스는 「거부」다', async () => {
  const t = await setup();
  t.entry.setPending([item('r1', 'share', { age: 180, expiresIn: 7200 })]);
  assert.strictEqual(t.modal().querySelector('.vt-viewer-card').getAttribute('role'), 'alertdialog');
  assert.match(t.text(), /「agent-a」이\(가\) 「build」에 「보기」 권한을 요청합니다/);
  assert.match(t.text(), /세션 공유/);
  assert.match(t.text(), /session\.read, session\.view/);
  assert.match(t.text(), /session:local\/\$2:2/, '대상 id 를 그대로');
  assert.match(t.text(), /3분 전 요청 · 2시간 뒤 만료/);
  assert.strictEqual(t.document.activeElement, t.act('deny'));
});

test('프리셋이 일부만 덮으면 「… 범위 안」(영어 「Part of」 대신 뜻이 분명한 말), 조건은 상세 줄에 그대로', async () => {
  const t = await setup();
  t.entry.setPending([item('r1', 'share', {
    preset: { id: 'drive', label: '운전', exact: false },
    scopes: ['session.input', 'session.read'], conds: { 'session.input': { modes: ['line'], via: ['direct'] } },
  })]);
  assert.match(t.text(), /「운전 범위 안」 권한/);
  assert.match(t.text(), /session\.input\[modes=line; via=direct\]/);
});

test('종류별: msg·peer·generic 도 같은 모달에 뜨고 종류 이름이 다르다', async () => {
  const t = await setup();
  t.entry.setPending([
    item('m1', 'msg', { created: 1, preset: null, scopes: ['msg.send'] }),
    item('p1', 'peer', { created: 2, resource: { id: 'host:mac-b', label: 'mac-b' }, preset: { id: 'control', label: '제어', exact: true }, scopes: ['session.input'], elevated: ['session.input[raw]'] }),
    item('g1', 'generic', { created: 3, reason: '배포 로그를 읽어야 합니다' }),
  ]);
  assert.match(t.text(), /메시지를 보내려 합니다/);
  assert.match(t.text(), /메시지/);
  assert.strictEqual(t.modal().querySelector('.vt-authz-count').textContent, '1 / 3');
  t.inbox.showPending('p1');
  assert.match(t.text(), /다른 맥/);
  assert.match(t.text(), /「제어」/);
  t.inbox.showPending('g1');
  assert.match(t.text(), /일반 요청/);
  assert.match(t.text(), /에이전트가 적은 사유/);
  assert.match(t.text(), /배포 로그를 읽어야 합니다/);
});

test('에이전트가 쓴 사유는 이스케이프된다(마크업이 되지 않는다)', async () => {
  const t = await setup();
  t.entry.setPending([item('g1', 'generic', { reason: '<img src=x onerror=alert(1)><b>x</b>' })]);
  assert.strictEqual(t.modal().querySelector('.vt-authz-reason img'), null);
  assert.strictEqual(t.modal().querySelector('.vt-authz-reason b'), null);
  assert.match(t.modal().querySelector('.vt-authz-reason').textContent, /<img src=x onerror=alert\(1\)><b>x<\/b>/);
});

test('위임된 하위 세션이면 계보를 보인다', async () => {
  const t = await setup();
  t.entry.setPending([item('r1', 'share', { lineage: { delegation: 'dg-77' } })]);
  assert.match(t.text(), /위임된 하위 세션 · 위임 dg-77/);
});

test('선택지: 한 번만 / 이 세션 동안 / 거부 가 choice once|session|deny 로 간다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/a/decision': decideRoute(log), 'POST /api/authz/approvals/b/decision': decideRoute(log), 'POST /api/authz/approvals/c/decision': decideRoute(log) });
  t.entry.setPending([item('a', 'share', { created: 1 }), item('b', 'share', { created: 2 }), item('c', 'generic', { created: 3 })]);
  assert.deepStrictEqual([...t.modal().querySelectorAll('[data-act]')].map((b) => b.dataset.act), ['deny', 'once', 'session']);
  assert.deepStrictEqual([...t.modal().querySelectorAll('[data-act]')].map((b) => b.textContent), ['거부', '한 번만', '이 세션 동안']);
  await tick(ARM);
  t.act('once').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(log[0].body, { decision: 'allow', choice: 'once' });
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => log.length === 2);
  assert.deepStrictEqual(log[1].body, { decision: 'allow', choice: 'session' });
  await tick(ARM);
  t.act('deny').click();
  await waitFor(() => log.length === 3);
  assert.deepStrictEqual(log[2].body, { decision: 'deny' });
  await waitFor(() => !t.modal());
});

test('msg·peer: 「한 번만」은 막혀 있고 이유가 보인다 — 눌러도 요청이 나가지 않는다', async () => {
  const t = await setup();
  t.entry.setPending([item('m1', 'msg', { preset: null, scopes: ['msg.send'] })]);
  const once = t.act('once');
  assert.ok(once.disabled);
  assert.strictEqual(once.getAttribute('aria-disabled'), 'true');
  assert.match(t.text(), /한 번만: 이 종류의 요청은 허용·거부만 받습니다/);
  assert.ok(!t.act('session').disabled);
  await tick(ARM);
  once.click();
  await tick(20);
  assert.strictEqual(t.calls.length, 0);
});

test('share 의 「한 번만」이 서버에서 거절되면(choice_unavailable) 이유를 보이고 요청은 남는다', async () => {
  const t = await setup({ 'POST /api/authz/approvals/r1/decision': () => ({ status: 409, json: { error: 'choice_unavailable', reason: '한 번만·기간 허용은 VT_AUTHZ_ENFORCE=share 가 켜져 있을 때만 됩니다' } }) });
  t.entry.setPending([item('r1')]);
  await tick(ARM);
  t.act('once').click();
  await waitFor(() => /VT_AUTHZ_ENFORCE=share/.test(t.modal().querySelector('.vt-authz-err').textContent));
  assert.ok(t.modal());
  assert.ok(!t.act('deny').disabled, '다시 결정할 수 있다');
});

test('오조작 방지: 뜬 직후에는 어떤 결정 버튼을 눌러도 요청이 나가지 않는다(치던 Enter·Space)', async () => {
  const t = await setup();
  t.entry.setPending([item('r1')]);
  t.act('session').click();
  t.act('deny').click();
  await tick(20);
  assert.strictEqual(t.calls.length, 0);
  assert.ok(t.modal(), '그대로 떠 있다');
});

test('Enter 한 번(= 포커스된 버튼 클릭)은 거부 쪽이다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/r1/decision': decideRoute(log) });
  t.entry.setPending([item('r1')]);
  await tick(ARM);
  t.document.activeElement.click();
  await waitFor(() => log.length === 1);
  assert.strictEqual(log[0].body.decision, 'deny');
});

test('여러 요청은 한 번에 하나씩, 오래된 것부터 — 처리하면 다음이 뜨고 다시 무장 대기·포커스는 거부', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/a/decision': decideRoute(log), 'POST /api/authz/approvals/b/decision': decideRoute(log) });
  t.entry.setPending([item('b', 'generic', { created: 2, resource: { id: 'x', label: 'two' } }), item('a', 'share', { created: 1, resource: { id: 'y', label: 'one' } })]);
  assert.strictEqual(t.modal().querySelector('.vt-authz-count').textContent, '1 / 2');
  assert.match(t.text(), /「one」/);
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => /「two」/.test(t.text()));
  assert.strictEqual(t.document.activeElement, t.act('deny'));
  t.act('session').click();          // 새로 그려진 직후 연타는 다음 요청을 허용하지 않는다
  await tick(20);
  assert.strictEqual(log.length, 1);
});

test('일괄 승인은 없다 — 결정 버튼은 거부·한 번만·이 세션 동안 셋뿐이고 「모두」 문구가 없다', async () => {
  const t = await setup();
  t.entry.setPending([item('a'), item('b', 'msg', { created: 2 }), item('c', 'generic', { created: 3 })]);
  const labels = [...t.modal().querySelectorAll('button')].map((b) => b.textContent.trim());
  assert.ok(!labels.some((l) => /모두|전체|all/i.test(l)), labels.join('|'));
  assert.strictEqual(t.modal().querySelectorAll('.vt-authz-act').length, 3);
});

test('Esc·「나중에」는 결정이 아니다 — 요청은 안 나가고 안내가 뜨며, 새 요청이 오면 다시 뜬다', async () => {
  const t = await setup();
  t.entry.setPending([item('r1', 'share', { created: 1 })]);
  esc(t);
  assert.strictEqual(t.modal(), null);
  assert.strictEqual(t.calls.length, 0);
  assert.match(t.toasts[0][0], /승인 대기 1건 — 설정의 「권한」에서 다시/);
  t.entry.setPending([item('r1', 'share', { created: 1 })]);
  assert.strictEqual(t.modal(), null, '같은 요청이면 나중에 상태 유지');
  t.entry.setPending([item('r1', 'share', { created: 1 }), item('r2', 'msg', { created: 2 })]);
  assert.ok(t.modal(), '새 요청은 다시 띄운다');
  t.modal().querySelector('.vt-authz-snooze').click();
  assert.strictEqual(t.modal(), null);
  t.entry.showPending();
  assert.ok(t.modal(), '설정 「권한」에서 다시 열 수 있다');
});

test('다른 화면에서 처리되면 자동으로 닫히고, 현재 것이 아니면 목록에서만 빠진다', async () => {
  const t = await setup();
  t.entry.setPending([item('r1', 'share', { created: 1 }), item('r2', 'msg', { created: 2 })]);
  t.entry.resolveRequest('r2');
  assert.strictEqual(t.modal().querySelector('.vt-authz-count').textContent, '');
  t.entry.resolveRequest('r1');
  assert.strictEqual(t.modal(), null);
  assert.strictEqual(t.calls.length, 0);
});

test('결정 오류: 404 는 목록에서 빼고, 그 밖의 오류는 이유를 보이며 요청을 남긴다', async () => {
  const t = await setup({
    'POST /api/authz/approvals/gone/decision': () => ({ status: 404, json: { error: 'not_found' } }),
    'POST /api/authz/approvals/dec/decision': () => ({ status: 409, json: { error: 'already_decided' } }),
  });
  t.entry.setPending([item('gone', 'share', { created: 1 }), item('dec', 'share', { created: 2 })]);
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => t.modal().querySelector('.vt-authz-count').textContent === '');   // gone 이 빠지고 dec 만 남는다
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => /이미 다른 결정/.test(t.modal().querySelector('.vt-authz-err').textContent));
  assert.ok(t.modal());
});

test('포커스 트랩 — Tab 이 터미널로 새지 않는다', async () => {
  const t = await setup();
  const term = t.document.createElement('textarea');
  t.document.body.appendChild(term);
  term.focus();
  t.entry.setPending([item('r1')]);
  assert.notStrictEqual(t.document.activeElement, term);
  assert.ok(t.modal().contains(t.document.activeElement));
});

// ── 높은 권한 ─────────────────────────────────────────────────────────────
const elev = (over = {}) => item('e1', 'share', { elevated: ['session.input[raw]'], preset: { id: 'control', label: '제어', exact: true }, scopes: ['session.input'], ...over });

test('높은 권한 요청은 눈에 띄게 구분되고 사유 목록이 그대로 보인다', async () => {
  const t = await setup();
  t.entry.setPending([elev()]);
  const body = t.modal().querySelector('.vt-authz-approval-body');
  assert.strictEqual(body.dataset.elevated, '1');
  assert.match(t.text(), /높은 권한이 필요한 요청/);
  assert.match(t.text(), /비밀번호를 다시 확인한 뒤에만 허용됩니다/);
  assert.match(body.querySelector('.vt-authz-elevated').textContent, /session\.input\[raw\]/);
  t.entry.setPending([item('n1')]);
  assert.strictEqual(t.modal().querySelector('.vt-authz-approval-body').dataset.elevated, '0');
});

test('높은 권한 허용: 서버가 재인증을 요구하면 비밀번호를 묻고 승격한 뒤 같은 결정을 다시 보낸다', async () => {
  let n = 0; const log = [];
  const t = await setup({
    'POST /api/authz/approvals/e1/decision': (b) => { log.push(J(b)); return (n++ === 0) ? { status: 403, json: { error: 'elevation_required', reason: 'x' } } : { json: { ok: true, grant: { id: 'g' } } }; },
    'POST /api/auth/elevate': (b) => { log.push({ elevate: J(b) }); return { json: { ok: true } }; },
  });
  t.entry.setPending([elev()]);
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => promptInput(t.document));
  assert.match(promptInput(t.document).closest('.vt-dialog').textContent, /높은 권한을 허용하려면 비밀번호를 다시 확인/);
  promptInput(t.document).value = 'pw';
  promptInput(t.document).closest('.vt-dialog').querySelector('.vt-dialog-ok').click();
  await waitFor(() => log.length === 3);
  assert.deepStrictEqual(log, [{ decision: 'allow', choice: 'session' }, { elevate: { password: 'pw' } }, { decision: 'allow', choice: 'session' }]);
  await waitFor(() => !t.modal());
});

test('재인증을 취소하면 결정은 다시 나가지 않고 요청은 남는다', async () => {
  const t = await setup({ 'POST /api/authz/approvals/e1/decision': () => ({ status: 403, json: { error: 'elevation_required' } }) });
  t.entry.setPending([elev()]);
  await tick(ARM);
  t.act('session').click();
  await waitFor(() => promptInput(t.document));
  promptInput(t.document).closest('.vt-dialog').querySelector('.vt-dialog-cancel').click();
  await waitFor(() => !promptInput(t.document));
  await tick(20);
  assert.strictEqual(t.calls.filter((c) => c.url.includes('/decision')).length, 1);
  assert.ok(t.modal());
});

test('거부는 재인증이 필요 없다 — 곧바로 deny 만 간다', async () => {
  const log = [];
  const t = await setup({ 'POST /api/authz/approvals/e1/decision': decideRoute(log) });
  t.entry.setPending([elev()]);
  await tick(ARM);
  t.act('deny').click();
  await waitFor(() => log.length === 1);
  assert.deepStrictEqual(log[0].body, { decision: 'deny' });
  assert.strictEqual(promptInput(t.document), null);
});

// ── 설정 안의 목록 ────────────────────────────────────────────────────────
test('대기 목록: 모든 종류가 한 목록에 종류·요청 문장·상세 줄·시각과 함께 보이고, 「결정하기」가 그 요청의 모달을 연다', async () => {
  const t = await setup();
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  t.inbox.mountInboxList(host);
  assert.match(host.textContent, /승인 대기 중인 요청이 없습니다/);
  t.inbox.showPending();   // 빈 목록이면 아무것도 안 뜬다
  assert.strictEqual(t.modal(), null);
  t.entry.setPending([item('a', 'share', { created: 1 }), item('m', 'msg', { created: 2, scopes: ['msg.send'], preset: null }), item('e', 'generic', { created: 3, elevated: ['browser.js'] })]);
  esc(t);                                  // 새 요청으로 뜬 모달을 나중에로
  const rows = [...host.querySelectorAll('.vt-authz-row')];
  assert.strictEqual(rows.length, 3);
  assert.deepStrictEqual(rows.map((r) => r.dataset.kind), ['share', 'msg', 'generic']);
  assert.ok(rows[2].querySelector('.vt-authz-flag'), '높은 권한 표시');
  assert.match(rows[0].textContent, /session\.read, session\.view/);
  rows[1].querySelector('.vt-authz-open').click();
  assert.ok(t.modal());
  assert.match(t.text(), /메시지를 보내려 합니다/);
});
