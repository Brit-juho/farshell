// 설정 「페어」·「폴더 lease」 패널, 권한 목록의 페어 표시, 감사 문장, 페어·lease 이벤트(중복 없이 한 번).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadShare, waitFor, tick } = require('./helpers/share-env');
const { answerConfirm } = require('./helpers/dialog-stub');
const { grant } = require('./helpers/authz-fixtures');

const _wins = [];
after(() => { for (const w of _wins) { try { w.close(); } catch (_) {} } });

const mem = (seat, over = {}) => ({ seat, kind: 'agent', name: seat === 'initiator' ? 'alpha' : 'beta', role: seat === 'initiator' ? 'driver' : 'navigator', joined: true, you: false, driver: seat === 'initiator', ...over });
const pair = (id, state, over = {}) => ({
  id, state, created: 1, generation: 0, members: [mem('initiator'), mem('joiner')],
  bundle: { sessions: [{ name: 'alpha' }, { name: 'beta' }], dirs: ['/work/proj'], leases: true, elevated: { requested: [], granted: [] } },
  approval: { state: 'allowed', id: 'pq-x' }, role: null, isDriver: false, expires: Date.now() / 1000 + 7200, endReason: null, handoff: null,
  ...over,
});

async function mountPairs(pairs, routes = {}, opts = {}) {
  const t = await loadShare({
    'GET /api/pairs': () => (typeof pairs === 'function' ? pairs() : { json: { pairs } }),
    ...routes,
  }, { files: ['authz/pairs.js', 'authz/store.js', 'authz/inbox.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  const api = t.mods[0].mountPairs(host, opts.now);
  const rows = () => [...host.querySelectorAll('.vt-pair-row')];
  const btn = (id, act) => host.querySelector(`.vt-pair-row[data-pair="${id}"] [data-act="${act}"]`);
  return { ...t, host, api, store: t.mods[1], inbox: t.mods[2], rows, btn, gets: () => t.calls.filter((c) => c.method === 'GET' && c.url === '/api/pairs').length };
}

test('목록: 상태·구성원과 역할·묶음(말로)·남은 시간이 한 행에, 끝난 것은 따로(정리 현황)', async () => {
  const t = await mountPairs([
    pair('pr-a', 'active', { generation: 2, bundle: { sessions: [{ name: 'alpha' }], dirs: [], leases: false, elevated: { requested: ['raw_input'], granted: ['raw_input'] } } }),
    pair('pr-b', 'ended', { endReason: 'revoked_by_human', cleanup: { done: false, saga: true, grants: 2, leases: 1 }, expires: null }),
  ]);
  await waitFor(() => t.rows().length === 2);
  const [a, b] = t.rows();
  assert.match(a.textContent, /진행 중/);
  assert.match(a.textContent, /alpha.*운전자/);
  assert.match(a.textContent, /beta.*길잡이/);
  assert.match(a.textContent, /세션 alpha · 점유 안 함/);
  assert.match(a.textContent, /2시간 남음/);
  assert.match(a.textContent, /역할 교체 2회/);
  assert.match(a.textContent, /높은 권한 1개 허용됨/);
  assert.ok(b.dataset.ended);
  assert.match(b.textContent, /사람이 회수/);
  assert.match(b.textContent, /정리 중 — 남은 권한 2개 · 폴더 점유 1개/);
  assert.strictEqual(b.querySelector('.vt-pair-act'), null, '끝난 페어에는 동작이 없다');
  assert.match(t.host.textContent, /최근에 끝난 페어/);
});

test('빈 상태: 시작 방법(에이전트·fsh pair start)과 서버에서 켜야 한다는 설명 — 오류가 아니다', async () => {
  const t = await mountPairs([]);
  await waitFor(() => /진행 중인 페어가 없습니다/.test(t.host.textContent));
  assert.match(t.host.textContent, /에이전트가 시작하거나 터미널에서 fsh pair start 로 시작/);
  assert.match(t.host.textContent, /서버에서 켜야 합니다 — 서버 설정 VT_AUTHZ_ENFORCE 에 pair/);
  assert.strictEqual(t.host.querySelector('.vt-authz-err').textContent, '');
});

test('꺼진 서버(409 pair_disabled): 한 줄 안내이고 오류 문구·재시도는 없다', async () => {
  const t = await mountPairs(null, { 'GET /api/pairs': () => ({ status: 409, json: { error: 'pair_disabled', reason: 'pair 기능이 꺼져 있습니다 — 서버 설정 VT_AUTHZ_ENFORCE 에 pair 를 더해야 합니다' } }) });
  await waitFor(() => t.host.querySelector('[data-disabled]'));
  assert.match(t.host.querySelector('[data-disabled]').textContent, /페어 기능이 꺼져 있습니다\. 서버 설정 VT_AUTHZ_ENFORCE 에 pair 를 더하면 켜집니다/);
  assert.strictEqual(t.host.querySelector('.vt-authz-err').textContent, '');
  assert.strictEqual(t.host.querySelector('.vt-pair-act'), null);
});

test('다른 오류는 불러오지 못했다는 말과 서버 이유, 다시 시도', async () => {
  const t = await mountPairs(null, { 'GET /api/pairs': () => ({ status: 500, json: { error: 'boom', reason: '내부 오류' } }) });
  await waitFor(() => /불러오지 못했습니다/.test(t.host.textContent));
  assert.match(t.host.querySelector('.vt-authz-err').textContent, /내부 오류/);
  assert.ok(t.host.querySelector('.vt-pair-refresh'));
});

test('끝내기: 확인 후 DELETE, 구성원이면 「끝내기」 사람이 구성원이 아니면 「회수」', async () => {
  const t = await mountPairs([
    pair('pr-m', 'active', { members: [mem('initiator', { kind: 'human', name: '', you: true, driver: true }), mem('joiner')], role: 'driver', isDriver: true }),
    pair('pr-o', 'active'),
  ], { 'DELETE /api/pairs/pr-m': () => ({ json: { ok: true } }), 'DELETE /api/pairs/pr-o': () => ({ json: { ok: true } }) });
  await waitFor(() => t.rows().length === 2);
  assert.strictEqual(t.btn('pr-m', 'end').textContent, '끝내기');
  assert.strictEqual(t.btn('pr-o', 'end').textContent, '회수');
  t.btn('pr-o', 'end').click();
  await answerConfirm(t.document, { accept: false });
  await tick(30);
  assert.strictEqual(t.calls.filter((c) => c.method === 'DELETE').length, 0, '취소하면 안 보낸다');
  t.btn('pr-o', 'end').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => t.calls.some((c) => c.method === 'DELETE' && c.url === '/api/pairs/pr-o'));
  await waitFor(() => t.gets() >= 2);
});

test('역할 교체: driver 구성원은 제안, navigator 구성원은 수락(대기 중일 때만), 사람(비구성원)은 확인 후 바로 교체', async () => {
  const hMe = (driver) => [mem('initiator', { kind: 'human', name: '', you: true, driver, role: driver ? 'driver' : 'navigator' }), mem('joiner', { driver: !driver, role: driver ? 'navigator' : 'driver' })];
  const t = await mountPairs([
    pair('pr-d', 'active', { members: hMe(true), role: 'driver', isDriver: true }),
    pair('pr-n', 'handoff_pending', { members: hMe(false), role: 'navigator', handoff: { to: 'initiator', by: 'joiner', expiresIn: 90 } }),
    pair('pr-w', 'active'),
  ], {
    'POST /api/pairs/pr-d/handoff': () => ({ json: { ok: true } }),
    'POST /api/pairs/pr-n/handoff/accept': () => ({ json: { ok: true } }),
    'POST /api/pairs/pr-w/handoff': () => ({ json: { ok: true } }),
  });
  await waitFor(() => t.rows().length === 3);
  assert.strictEqual(t.btn('pr-d', 'handoff').textContent, '역할 넘기기 제안');
  assert.strictEqual(t.btn('pr-d', 'accept'), null);
  assert.strictEqual(t.btn('pr-n', 'accept').textContent, '교체 수락');
  assert.strictEqual(t.btn('pr-n', 'handoff'), null, '수락 대기 중에는 제안 버튼이 없다');
  assert.strictEqual(t.btn('pr-w', 'handoff').textContent, '역할 바꾸기');
  t.btn('pr-d', 'handoff').click();
  await waitFor(() => t.calls.some((c) => c.url === '/api/pairs/pr-d/handoff'));
  await waitFor(() => t.gets() >= 2);          // 동작 뒤 목록을 다시 읽는다 — 그동안 다른 버튼은 잠겨 있다
  t.btn('pr-n', 'accept').click();
  await waitFor(() => t.calls.some((c) => c.url === '/api/pairs/pr-n/handoff/accept'));
  await waitFor(() => t.gets() >= 3);
  t.btn('pr-w', 'handoff').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => t.calls.some((c) => c.url === '/api/pairs/pr-w/handoff'));
});

test('승인 대기 페어: 「승인하기」가 그 승인 요청을 모달로 연다', async () => {
  const t = await mountPairs([pair('pr-j', 'joined', { approval: { state: 'pending', id: 'pq-j' } })]);
  await waitFor(() => t.rows().length === 1);
  const seen = [];
  t.window.document.addEventListener('x', () => {});
  t.store.setPending([{ id: 'pq-j', kind: 'pair', state: 'pending', created: 1, subject: { name: 'beta' }, resource: { id: 'pair:pr-j', label: 'pair' }, scopes: [], conds: {}, pair: { roles: { initiator: { name: 'alpha', kind: 'agent', role: 'driver' }, joiner: { name: 'beta', kind: 'agent', role: 'navigator' } }, presets: {}, phrases: {}, bundle: { sessions: [], dirs: [], leases: false }, elevatedItems: [] } }]);
  t.inbox.wire();
  t.btn('pr-j', 'approve').click();
  assert.ok(t.document.querySelector('.vt-authz-approval'), seen.join());
});

test('코드 보기: 코드·확인 문구가 보이고 남은 시간이 1초마다 줄며 0 이 되면 만료 문구로 바뀐다(가짜 시계)', async () => {
  let clock = 1_000_000;
  const ticks = [];
  const mine = [mem('initiator', { kind: 'human', name: '', you: true }), mem('joiner', { name: '', joined: false, driver: false })];
  const t = await mountPairs(() => ({ json: { pairs: [pair('pr-c', 'code_issued', { members: mine, approval: { state: 'none', id: null }, code: { state: 'issued', expiresIn: 3 } })] } }), {
    'POST /api/pairs/pr-c/code': () => ({ json: { ok: true, code: 'ABCD-EFGH', phrase: 'maple window', expires: 0, ttl: 300 } }),
  }, { now: () => clock });
  const win = t.window;
  win.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
  win.clearInterval = () => {};
  await waitFor(() => t.btn('pr-c', 'code'));
  t.btn('pr-c', 'code').click();
  await waitFor(() => t.host.querySelector('.vt-pair-code-val'));
  assert.strictEqual(t.host.querySelector('.vt-pair-code-val').textContent, 'ABCD-EFGH');
  assert.match(t.host.textContent, /maple window/);
  const cd = () => t.host.querySelector('.vt-pair-countdown[data-kind="code"]');
  assert.match(cd().textContent, /0:03 남음/);
  assert.ok(ticks.length >= 1, '1초 타이머가 돈다');
  clock += 1000; ticks.forEach((f) => f());
  assert.match(cd().textContent, /0:02 남음/);
  clock += 2000; ticks.forEach((f) => f());
  assert.match(cd().textContent, /만료됨/);
  assert.ok(cd().dataset.expired);
});

test('코드는 시작한 쪽만 본다 — 에이전트가 시작한 페어에는 「코드 보기」가 없다', async () => {
  const t = await mountPairs([pair('pr-x', 'code_issued', { approval: { state: 'none', id: null } })]);
  await waitFor(() => t.rows().length === 1);
  assert.strictEqual(t.btn('pr-x', 'code'), null);
});

test('수락 대기 표시의 상대 이름은 자리(joiner)가 아니라 구성원 이름이다', async () => {
  const t = await mountPairs([pair('pr-h', 'handoff_pending', { handoff: { to: 'joiner', by: 'initiator', expiresIn: 100 } })]);
  await waitFor(() => t.rows().length === 1);
  assert.match(t.host.textContent, /beta 의 수락을 기다리는 중/);
  assert.match(t.host.querySelector('.vt-pair-countdown[data-kind="handoff"]').textContent, /1:4\d|1:3\d/);
});

// ── 폴더 lease ───────────────────────────────────────────────────────────

async function mountLeases(res, routes = {}) {
  const t = await loadShare({ 'GET /api/authz/leases': () => ({ json: res }), ...routes }, { files: ['authz/leases.js', 'authz/store.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div');
  t.document.body.appendChild(host);
  const api = t.mods[0].mountLeases(host);
  return { ...t, host, api, store: t.mods[1], rows: () => [...host.querySelectorAll('.vt-lease-row')] };
}
const lease = (id, over = {}) => ({ id, holder: 'alpha', glob: '/work/proj/**', mode: 'write', exclusive: false, reason: '', created: 1, expires: 9e9, remaining: 3000, derivedFrom: null, delegation: null, approvedVia: 'manual', ttl: 1800, ...over });

test('lease 목록: 보유자·경로·모드·배타·만료·상속 표시와 권고 장치 안내(막는 중이 아님)', async () => {
  const t = await mountLeases({ enforced: false, leases: [lease('l1', { exclusive: true, reason: '리팩터링' }), lease('l2', { holder: 'beta', mode: 'read', remaining: 90 }), lease('l3', { derivedFrom: 'l1', holder: 'child' })] });
  await waitFor(() => t.rows().length === 3);
  const [a, b, c] = t.rows();
  assert.match(a.textContent, /쓰기.*배타/);
  assert.match(a.textContent, /alpha.*경로.*\/work\/proj\/\*\*/);
  assert.match(a.textContent, /50분 뒤 만료/);
  assert.match(a.textContent, /사유 · 리팩터링/);
  assert.match(b.textContent, /읽기/);
  assert.match(b.textContent, /beta/);
  assert.match(t.host.querySelector('.vt-lease-note').textContent, /권고 장치입니다\. 지금은 막지 않고/);
  assert.match(c.textContent, /상위에서 상속 · 읽기 전용/);
  assert.match(c.textContent, /상위 점유 l1/);
  assert.strictEqual(c.querySelector('.vt-lease-revoke'), null, '상속된 lease 에는 놓기가 없다');
  assert.ok(a.querySelector('.vt-lease-revoke'));
});

test('enforced 일 때도 FarShell 밖(셸·편집기) 쓰기는 못 막는다고 말한다', async () => {
  const t = await mountLeases({ enforced: true, leases: [] });
  await waitFor(() => /셸이나 편집기에서 직접 쓰는 것은 막지 못하는/.test(t.host.querySelector('.vt-lease-note').textContent));
});

test('빈 lease 목록 안내', async () => {
  const t = await mountLeases({ enforced: false, leases: [] });
  await waitFor(() => /점유 중인 폴더가 없습니다/.test(t.host.textContent));
});

test('놓기: 확인 → DELETE → 다시 읽기. 취소하면 아무것도 안 보낸다. 서버 오류는 이유를 보인다', async () => {
  let fail = false;
  const t = await mountLeases({ enforced: false, leases: [lease('l1')] }, { 'DELETE /api/authz/leases/l1': () => (fail ? { status: 404, json: { error: 'not_found', reason: '그런 폴더 점유가 없습니다' } } : { json: { ok: true } }) });
  await waitFor(() => t.rows().length === 1);
  t.host.querySelector('.vt-lease-revoke').click();
  await answerConfirm(t.document, { accept: false });
  await tick(30);
  assert.strictEqual(t.calls.filter((c) => c.method === 'DELETE').length, 0);
  fail = true;
  t.host.querySelector('.vt-lease-revoke').click();
  await answerConfirm(t.document, { accept: true });
  await waitFor(() => /그런 폴더 점유가 없습니다/.test(t.host.querySelector('.vt-authz-err').textContent));
});

// ── 권한 목록의 페어 표시 ─────────────────────────────────────────────────

test('권한 목록: 페어 권한에 페어 표시가 붙고 누르면 같은 화면의 그 페어 행으로 간다. 기본 규칙은 따로 읽기 전용', async () => {
  const t = await loadShare({
    'GET /api/authz/grants': () => ({ json: { grants: [grant('pp-579eda0de7-i-0', 'pair', { subject: { id: 'x', name: 'alpha' }, resource: 'session:local/a', preset: { id: 'drive', label: '운전', exact: false } }), grant('gg-1', 'generic')] } }),
  }, { files: ['authz/grants.js'] });
  _wins.push(t.window);
  const row = t.document.createElement('li'); row.className = 'vt-pair-row'; row.dataset.pair = 'pr-579eda0de7';
  let focused = 0; row.focus = () => { focused += 1; }; row.scrollIntoView = () => {};
  t.document.body.appendChild(row);
  const host = t.document.createElement('div'); t.document.body.appendChild(host);
  t.mods[0].mountGrants(host);
  await waitFor(() => host.querySelectorAll('.vt-authz-row').length === 2);
  const tag = host.querySelector('.vt-pair-link');
  assert.match(tag.textContent, /페어 579eda/);
  assert.strictEqual(tag.dataset.pairLink, 'pr-579eda0de7');
  assert.strictEqual(host.querySelectorAll('.vt-pair-link').length, 1, '다른 종류에는 붙지 않는다');
  assert.match(host.querySelector('.vt-authz-row[data-kind="pair"]').textContent, /운전 범위 안/);
  tag.click();
  assert.strictEqual(focused, 1);
  assert.ok(host.querySelector('.vt-authz-builtin'), '기본 규칙은 따로');
  assert.ok([...host.querySelectorAll('select option')].some((o) => o.value === 'pair'), '종류 거르기에 페어');
});

test('페어 권한 회수 확인 문구는 페어 전체가 끝난다고 말한다', async () => {
  const t = await loadShare({
    'GET /api/authz/grants': () => ({ json: { grants: [grant('pp-579eda0de7-i-0', 'pair')] } }),
    'DELETE /api/authz/grants/pp-579eda0de7-i-0': () => ({ json: { ok: true } }),
  }, { files: ['authz/grants.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div'); t.document.body.appendChild(host);
  t.mods[0].mountGrants(host);
  await waitFor(() => host.querySelector('.vt-authz-revoke'));
  host.querySelector('.vt-authz-revoke').click();
  await waitFor(() => t.document.querySelector('.vt-dialog'));
  assert.match(t.document.querySelector('.vt-dialog').textContent, /이 페어 전체가 끝납니다/);
  await answerConfirm(t.document, { accept: false });
});

// ── 감사 ─────────────────────────────────────────────────────────────────

test('감사: 페어·lease 줄은 읽기 쉬운 말(개수·상태만), 원래 action 은 title, 내용 필드는 그리지 않는다', async () => {
  const entries = [
    { ts: 5, source: 'chain', principal: 'human:x', action: 'pair.approve', resource: 'pair:pr-1', scopes: ['fs.read'], result: 'ok', reason: 'elevated=2', detail: { state: 'active', generation: 1, sessions: 2, dirs: 1, leases: true, elevated: 2, secret: 'LEAK' } },
    { ts: 4, source: 'chain', principal: 'agent:a', action: 'pair.end', resource: 'pair:pr-1', scopes: [], result: 'ended', reason: 'member_gone', detail: { state: 'ended', sessions: 2, dirs: 0, leases: false, elevated: 0 } },
    { ts: 3, source: 'chain', principal: 'agent:a', action: 'pair.code', resource: 'pair:pr-1', scopes: [], result: 'ok', reason: 'issued', detail: { state: 'code_issued' } },
    { ts: 2, source: 'chain', principal: 'agent:a', action: 'lease.acquire', resource: 'fs:local/work', scopes: ['fs.write'], result: 'ok', detail: { lease: 'l1', mode: 'write', exclusive: true, derived_from: null } },
    { ts: 1, source: 'chain', principal: 'human', action: 'lease.override', resource: 'fs:local', scopes: [], result: 'ok', detail: { lease: 'l1', mode: 'write' } },
  ];
  const t = await loadShare({ 'GET /api/authz/audit': () => ({ json: { entries, chain: 'ok', lines: 5, brokenSeq: null } }) }, { files: ['authz/audit.js'] });
  _wins.push(t.window);
  const host = t.document.createElement('div'); t.document.body.appendChild(host);
  t.mods[0].mountAudit(host);
  await waitFor(() => host.querySelectorAll('.vt-authz-audit-row').length === 5);
  const [a, e, c, l, o] = [...host.querySelectorAll('.vt-authz-audit-row')];
  assert.strictEqual(a.querySelector('.vt-authz-audit-act').textContent, '페어 승인');
  assert.strictEqual(a.querySelector('.vt-authz-audit-act').title, 'pair.approve');
  assert.match(a.textContent, /세션 2개 · 폴더 1개 · 폴더 점유 포함 · 높은 권한 2개 요청 · 상태 진행 중 · 교체 1회/);
  assert.match(a.textContent, /높은 권한 2개 허용/);
  assert.ok(!/LEAK/.test(host.textContent));
  assert.match(e.textContent, /페어 끝/);
  assert.match(e.textContent, /참여 세션이 사라짐/);
  assert.ok(!/issued/.test(c.textContent), '줄 이름이 이미 말하는 reason 은 숨긴다');
  assert.match(l.textContent, /폴더 점유 시작/);
  assert.match(l.textContent, /쓰기 · 배타/);
  assert.match(o.textContent, /사람이 점유 중인 경로에 직접 씀/);
});

// ── 이벤트(중복 없이 한 번) ───────────────────────────────────────────────

async function evStore() {
  const t = await loadShare({ 'GET /api/authz/approvals': () => ({ json: { approvals: [] } }) }, { files: ['authz/store.js'] });
  _wins.push(t.window);
  const seen = [];
  t.mod.onChange((ev) => seen.push(ev.type));
  const count = (k) => seen.filter((x) => x === k).length;
  return { ...t, seen, count };
}
const SETTLE = 150;

test('이벤트: 같은 묶음(요청→결정→회수)이 잇따라 와도 페어·lease 다시 읽기 알림은 종류마다 한 번', async () => {
  const t = await evStore();
  t.mod.onEvent({ type: 'authz_request', id: 'pq-1', kind: 'pair' });
  t.mod.onEvent({ type: 'authz_request', id: 'pq-2', kind: 'pair' });
  t.mod.onEvent({ type: 'authz_resolved', id: 'pq-1', kind: 'pair', state: 'allowed' });
  t.mod.onEvent({ type: 'authz_revoked', id: 'pr-1', kind: 'pair' });
  await tick(SETTLE);
  assert.strictEqual(t.count('pairs'), 1);
  assert.strictEqual(t.count('leases'), 1);
  assert.strictEqual(t.calls.filter((c) => c.url === '/api/authz/approvals').length, 1, '승인함 읽기도 한 번');
});

test('이벤트: 다른 종류의 요청·결정은 페어 알림을 만들지 않는다. 회수는 lease 만 알린다', async () => {
  const t = await evStore();
  t.mod.onEvent({ type: 'authz_request', id: 's1', kind: 'share' });
  t.mod.onEvent({ type: 'authz_resolved', id: 's1', kind: 'share', state: 'allowed' });
  await tick(SETTLE);
  assert.strictEqual(t.count('pairs'), 0);
  assert.strictEqual(t.count('leases'), 0);
  t.mod.onEvent({ type: 'authz_revoked', id: 'ag-1', kind: 'share' });
  await tick(SETTLE);
  assert.strictEqual(t.count('pairs'), 0);
  assert.strictEqual(t.count('leases'), 1);
});

test('이벤트: 비상 정지(id * · kind all)는 페어·lease 도 다시 읽게 하고, lease_notice 는 lease 만', async () => {
  const t = await evStore();
  t.mod.onEvent({ type: 'authz_resolved', id: '*', kind: 'all', state: 'cleared' });
  t.mod.onEvent({ type: 'authz_revoked', id: '*', kind: 'all' });
  await tick(SETTLE);
  assert.strictEqual(t.count('pairs'), 1);
  assert.strictEqual(t.count('leases'), 1);
  t.mod.onEvent({ type: 'lease_notice', id: 'l1', kind: 'override' });
  t.mod.onEvent({ type: 'lease_notice', id: 'l1', kind: 'override' });
  await tick(SETTLE);
  assert.strictEqual(t.count('pairs'), 1);
  assert.strictEqual(t.count('leases'), 2);
});

test('이벤트가 패널을 다시 읽게 한다 — 페어 패널은 알림 한 번에 목록 한 번', async () => {
  const t = await mountPairs([pair('pr-a', 'active')]);
  await waitFor(() => t.rows().length === 1);
  const before = t.gets();
  t.store.onEvent({ type: 'authz_request', id: 'pq-1', kind: 'pair' });
  t.store.onEvent({ type: 'authz_resolved', id: 'pq-1', kind: 'pair', state: 'allowed' });
  await tick(200);
  assert.strictEqual(t.gets(), before + 1);
});

test('share-lazy 배선: lease_notice 도 승인함 청크가 떠 있을 때만 전달한다', async () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(require('node:path').join(__dirname, '../js/share-lazy.js'), 'utf8');
  assert.match(src, /'lease_notice'/);
});
