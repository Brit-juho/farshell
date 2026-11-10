// 새 세션 계정 선택(핫스왑 H1) — 순수 로직(term/accounts-data.js), 선택기 메뉴·요청(term/accounts.js),
// 레일 행 배지, 팔레트 줄. 자동 선택은 없다: 정렬은 목록 순서만 바꾸고, 값이 없으면 「알 수 없음」이다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { (e.dom || e).window.close(); } catch (_) {} } });
const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SERVER = {
  profiles: [
    { id: 'a'.repeat(32), name: 'alice', tool: 'claude', purpose: 'work', auth: 'ok', auth_kind: 'login', selectable: true, reason: null, default: true,
      usage: { windows: [{ id: 'five_hour', label: '5h', pct: 80, resets_at: 'x', resets_in_sec: 7200 }, { id: 'seven_day', label: 'weekly', pct: 20, resets_at: 'x', resets_in_sec: 90000 }],
        checked_at: 1000, stale: false, source: 'oauth-usage' } },
    { id: 'b'.repeat(32), name: 'work', tool: 'claude', purpose: 'work', auth: 'ok', auth_kind: 'token', selectable: true, reason: null, default: false, usage: null },
    { id: 'c'.repeat(32), name: 'personal', tool: 'codex', purpose: 'work', auth: 'ok', auth_kind: 'login', selectable: true, reason: null, default: false,
      usage: { windows: [{ id: 'primary', label: '5h', pct: 10, resets_at: 'x', resets_in_sec: 60 }], checked_at: 1000, stale: true, source: 'codex-usage' } },
    { id: 'd'.repeat(32), name: 'blank', tool: 'claude', purpose: 'work', auth: 'missing', auth_kind: null, selectable: false, reason: 'not signed in here', default: false, usage: null },
  ],
  defaults: { claude: { id: 'a'.repeat(32), valid: true } },
};

async function dataModule() {
  const env = createDomEnv('<body></body>');
  _envs.push(env);
  const mod = await importFresh(path.join(__dirname, '../js/term/accounts-data.js'), env.context, new Map());
  return { mod, env };
}

test('서버 목록을 정규화한다 — 모양이 틀린 줄은 버리고 비밀·경로 필드는 아예 읽지 않는다', async () => {
  const { mod } = await dataModule();
  const acc = mod.normalizeAccounts({ ...SERVER, profiles: [...SERVER.profiles, { id: 5 }, null, { id: 'x', name: 'n', tool: 'shell' }] });
  assert.equal(acc.profiles.length, 4);
  assert.equal(acc.ok, true);
  assert.equal(mod.normalizeAccounts(null).ok, false);
  assert.equal(mod.normalizeAccounts({ profiles: 3 }).profiles.length, 0);
  assert.ok(!('root' in acc.profiles[0]) && !('path' in acc.profiles[0]));
});

test('줄 글: 도구 · 인증 · 사용률/리셋 · 갱신 시각, 값이 없으면 「알 수 없음」, 쓸 수 없으면 사유', async () => {
  const { mod } = await dataModule();
  const acc = mod.normalizeAccounts(SERVER);
  const [alice, work, personal, blank] = acc.profiles;
  const now = 1000 * 1000 + 180 * 1000; // checked_at + 3분
  assert.equal(mod.rowSub(alice, now), 'Claude · 정상 · 5시간 80% · 2시간 뒤 리셋 · 주간 20% · 1일 뒤 리셋 · 출처 OAuth · 3분 전 갱신');
  assert.match(mod.rowSub(work, now), /^Claude · 정상 · 사용량 알 수 없음$/, '값이 없으면 지어내지 않는다');
  assert.match(mod.rowSub(personal, now), /Codex · 정상 · 5시간 10% · 1분 뒤 리셋 · 출처 Codex API · 3분 전 갱신 · 오래된 값/);
  assert.equal(mod.rowSub(blank, now), 'Claude · 로그인 안 됨 · not signed in here');
});

test('줄 글: statusline·app-server 출처는 이름과 갱신 시각을 함께 보이고, 모르는 출처는 이름을 지어내지 않는다', async () => {
  const { mod } = await dataModule();
  const base = SERVER.profiles[0];
  const rowFor = (source) => mod.normalizeAccounts({ ...SERVER, profiles: [{ ...base, usage: { ...base.usage, source } }] }).profiles[0];
  const now = 1000 * 1000 + 600 * 1000;
  assert.match(mod.rowSub(rowFor('statusline'), now), /주간 20% · 1일 뒤 리셋 · 출처 statusline · 10분 전 갱신$/);
  assert.match(mod.rowSub(rowFor('app-server'), now), /출처 app-server · 10분 전 갱신$/);
  assert.match(mod.sourceText(rowFor('statusline')), /statusline/);
  assert.equal(mod.sourceShort(rowFor('something-new')), '');
  assert.doesNotMatch(mod.rowSub(rowFor('something-new'), now), /출처/);
});

test('「여유 많은 순」은 목록 순서만 바꾼다 — 여유 = 100 − 가장 많이 쓴 창, 모르면 뒤, 자동 선택 없음', async () => {
  const { mod } = await dataModule();
  const acc = mod.normalizeAccounts(SERVER);
  assert.deepEqual(Array.from(mod.sortRows(acc.profiles, 'server'), (p) => p.name), ['alice', 'work', 'personal', 'blank']);
  assert.deepEqual(Array.from(mod.sortRows(acc.profiles, 'headroom'), (p) => p.name), ['personal', 'alice', 'blank', 'work']);
  assert.equal(mod.headroom(acc.profiles[0]), 20);
  assert.equal(mod.headroom(acc.profiles[1]), null);
  assert.equal(acc.profiles.length, 4, '정렬은 원본을 바꾸거나 줄이지 않는다');
});

test('선택기 줄: once는 「계정 지정 없이」를, default는 도구별 「기본 계정 해제」를 더하고 쓸 수 없는 계정은 disabled로 남긴다', async () => {
  const { mod } = await dataModule();
  const acc = mod.normalizeAccounts(SERVER);
  const once = mod.chooserEntries(acc, { mode: 'once', sort: 'server' });
  assert.equal(once.at(-1).kind, 'none');
  assert.equal(once.at(-1).id, 'none');
  assert.equal(once.find((e) => e.name === 'blank').disabled, true);
  const def = mod.chooserEntries(acc, { mode: 'default', sort: 'server' });
  assert.deepEqual(Array.from(def.filter((e) => e.kind === 'clear'), (e) => e.tool), ['claude']);
  assert.equal(mod.defaultLine(acc), '기본: alice(Claude)');
  assert.equal(mod.defaultLine({ profiles: [], defaults: {} }), '기본 계정 없음');
});

// --- accounts.js: 요청과 메뉴 ---------------------------------------------------------

async function accountsModule({ reply, relayed = false } = {}) {
  const env = createDomEnv('<body></body>');
  _envs.push(env);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  const calls = [];
  env.window.fetch = async (url, options = {}) => {
    const route = url.split('?')[0];
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ route, body });
    const answer = reply ? await reply(route, body) : { status: 200, data: route.endsWith('/picker') ? SERVER : {} };
    return { ok: answer.status < 400, status: answer.status, json: async () => answer.data };
  };
  const cache = new Map();
  await importFresh(path.join(__dirname, '../js/ui/toast.js'), env.context, cache);
  if (relayed) env.window.localStorage.setItem('vt.relayHost', 'm5');   // 부팅 때 한 번 읽는다(core/host-route.js)
  const mod = await importFresh(path.join(__dirname, '../js/term/accounts.js'), env.context, cache);
  return { ...env, calls, mod };
}

test('기본 계정이 정해져 있을 때만 생성 요청에 default를 싣는다 — 없거나 목록을 못 받으면 요청이 예전과 같다', async () => {
  const marked = await accountsModule();
  assert.equal(await marked.mod.profileForCreate(), 'default');
  assert.equal(await marked.mod.profileForCreate('none'), 'none');
  assert.equal(await marked.mod.profileForCreate('b'.repeat(32)), 'b'.repeat(32));
  const none = await accountsModule({ reply: async () => ({ status: 200, data: { profiles: [SERVER.profiles[0]], defaults: {} } }) });
  assert.equal(await none.mod.profileForCreate(), undefined);
  const denied = await accountsModule({ reply: async () => ({ status: 403, data: { detail: {} } }) });
  assert.equal(await denied.mod.profileForCreate(), undefined, '사람 세션이 아닌 서버는 계정 기능이 아예 없다');
  const relayed = await accountsModule({ relayed: true });
  assert.equal(await relayed.mod.profileForCreate(), undefined, '다른 맥을 보는 중에는 계정을 보내지 않는다');
  assert.equal(relayed.calls.length, 0);
});

test('「이번만 다른 계정」 메뉴: 계정 줄·사유·정렬 토글, 고른 id를 돌려주고 기본은 바뀌지 않는다', async () => {
  const { mod, document, calls } = await accountsModule();
  const picked = [];
  await mod.openAccountMenu({ mode: 'once', onPick: (id) => picked.push(id) });
  const menu = document.getElementById('account-menu');
  assert.ok(menu);
  const items = () => [...menu.querySelectorAll('.vt-menu-item.two')];
  assert.deepEqual(items().map((i) => i.querySelector('.vt-menu-item-label').textContent), ['alice', 'work', 'personal', 'blank', '계정 지정 없이']);
  assert.equal(items()[3].getAttribute('aria-disabled'), 'true');
  assert.match(items()[3].textContent, /not signed in here/);
  items()[3].click();
  assert.deepEqual(picked, [], '쓸 수 없는 계정은 눌러도 아무 일도 없다');
  await new Promise((r) => setTimeout(r, 5));   // 바깥 클릭 감시는 한 틱 뒤에 붙는다
  menu.querySelector('[role="menuitemcheckbox"]').click();
  assert.ok(document.getElementById('account-menu'), '정렬을 켜도 메뉴는 열려 있다(줄을 다시 그려도 안쪽 클릭이다)');
  assert.deepEqual(items().map((i) => i.querySelector('.vt-menu-item-label').textContent), ['personal', 'alice', 'blank', 'work', '계정 지정 없이']);
  items()[1].click();
  assert.deepEqual(picked, ['a'.repeat(32)]);
  assert.equal(document.getElementById('account-menu'), null, '고르면 닫힌다');
  assert.ok(!calls.some((c) => c.route.endsWith('/default')), '「이번만」은 기본 계정을 바꾸지 않는다');
});

test('「기본 계정 바꾸기」 메뉴: 도구별 기본 변경·해제 요청을 보내고 실패 사유를 토스트로 보인다', async () => {
  const { mod, document, calls } = await accountsModule();
  await mod.openAccountMenu({ mode: 'default' });
  let menu = document.getElementById('account-menu');
  const find = (name) => [...menu.querySelectorAll('.vt-menu-item.two')].find((i) => i.querySelector('.vt-menu-item-label').textContent === name);
  find('work').click();
  await flush();
  const set = calls.find((c) => c.route.endsWith('/default'));
  assert.deepEqual(set.body, { tool: 'claude', profile_id: 'b'.repeat(32) });
  await mod.openAccountMenu({ mode: 'default' });
  menu = document.getElementById('account-menu');
  find('Claude 기본 계정 해제').click();
  await flush();
  assert.deepEqual(calls.filter((c) => c.route.endsWith('/default')).at(-1).body, { tool: 'claude', profile_id: null });

  const failing = await accountsModule({ reply: async (route) => (route.endsWith('/default') ? { status: 409, data: { error: 'profile_refused', reason: '프로필 사유' } } : { status: 200, data: SERVER }) });
  await failing.mod.openAccountMenu({ mode: 'default' });
  failing.document.getElementById('account-menu').querySelectorAll('.vt-menu-item.two')[1].click();
  await flush();
  assert.match(failing.document.body.textContent, /프로필 사유/);
});

test('계정이 하나도 없으면 메뉴를 열지 않는다', async () => {
  const { mod, document } = await accountsModule({ reply: async () => ({ status: 200, data: { profiles: [], defaults: {} } }) });
  await mod.openAccountMenu({ mode: 'once' });
  assert.equal(document.getElementById('account-menu'), null);
});

test('서버 거절 문구는 기본 환경으로 대체하지 않았다고 알린다', async () => {
  const { mod } = await accountsModule();
  assert.match(mod.refusalText({ error: 'not_logged_in', reason: '로그인 안 됨' }), /세션을 만들지 않았습니다: 로그인 안 됨/);
});

// --- 레일 행 배지·정규화 ---------------------------------------------------------------

const base = {
  sessionId: 's1', tmuxName: 'api', name: 'api', open: true, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, changed: null,
  agent: null, branch: null, isMainWorktree: false, origin: 'local', viewLabel: null, otherScreens: 0, otherClients: 0,
};

test('레일 행 배지: 서버가 기록한 계정만 이름으로 보이고, 툴팁은 「생성 시 계정」, 없으면 배지가 없다', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const mount = (profile) => {
    const root = env.document.createElement('div');
    env.document.body.appendChild(root);
    env.mountRow(root, { row: { ...base, profile }, active: false, onOpen() {}, onContext() {} });
    return root;
  };
  const withProfile = mount({ id: 'a'.repeat(32), name: 'alice', tool: 'claude' });
  const badge = withProfile.querySelector('.vt-srow-profile');
  assert.ok(badge);
  assert.equal(badge.querySelector('.vt-srow-profile-name').textContent, 'alice');
  assert.equal(badge.dataset.tip, '생성 시 계정');
  assert.equal(badge.dataset.tipSub, 'alice');
  assert.equal(mount(null).querySelector('.vt-srow-profile'), null);
  assert.equal(mount(undefined).querySelector('.vt-srow-profile'), null);
  assert.equal(mount({ id: 'z'.repeat(32), name: null, tool: null }).querySelector('.vt-srow-profile-name').textContent, '알 수 없는 계정', '이름을 못 읽으면 지어내지 않는다');
});

test('normalizeProfile: 모양이 맞는 값만 받는다(옛 서버·깨진 값은 null)', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  assert.equal(env.normalizeProfile(undefined), null);
  assert.equal(env.normalizeProfile({ id: '' }), null);
  assert.equal(env.normalizeProfile('alice'), null);
  assert.deepEqual({ ...env.normalizeProfile({ id: 'x', name: 'alice', tool: 'claude' }) }, { id: 'x', name: 'alice', tool: 'claude' });
});

// --- 팔레트 ---------------------------------------------------------------------------

test('팔레트: 계정이 있으면 「<이름> 계정으로 새 세션」과 「기본 계정 바꾸기」가 나오고, 쓸 수 없는 계정은 빠진다', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { mountPalette, document } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const created = []; const opened = [];
  const deps = {
    vtFetch: async () => ({}), getAction: () => undefined, gateOk: () => true, listSessions: () => [], getSession: () => null,
    switchTo() {}, loadViewer: async () => ({ _loadRecent: () => [] }), openFile() {}, openFileInPane() {},
    splitActivePane: () => null, setPaneSession() {}, buildSessionCard: () => document.createElement('div'), updateSessionCard() {},
    setVtSkin() {}, vtSkins: () => ['farshell'], vtSkinLabel: (s) => s, listKeymapActions: () => [], invokeKeymapAction() {},
    displayCombo: (c) => c, showToast() {},
    loadAccounts: async () => ({ ok: true, profiles: [
      { id: 'a'.repeat(32), name: 'alice', tool: 'claude', selectable: true },
      { id: 'd'.repeat(32), name: 'blank', tool: 'claude', selectable: false }] }),
    newSessionWithAccount: (id) => created.push(id),
    openAccountMenu: (o) => opened.push(o.mode),
  };
  const api = mountPalette(root, deps);
  api.open();
  const rows = async () => { await flush(10); return [...document.querySelectorAll('.vt-qo-row')]; };
  let list = await rows();
  const labels = list.map((r) => r.textContent);
  assert.ok(labels.some((l) => l.includes('alice 계정으로 새 세션')));
  assert.ok(!labels.some((l) => l.includes('blank 계정으로 새 세션')));
  assert.ok(labels.some((l) => l.includes('기본 계정 바꾸기')));
  list.find((r) => r.textContent.includes('alice 계정으로 새 세션')).click();
  await flush();
  assert.deepEqual(created, ['a'.repeat(32)]);
  api.open();
  list = await rows();
  list.find((r) => r.textContent.includes('기본 계정 바꾸기')).click();
  await flush();
  assert.deepEqual(opened, ['default']);
});

test('팔레트: 계정이 없거나 deps가 없으면 계정 줄이 없다(예전 그대로)', async () => {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { mountPalette, document } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  const deps = {
    vtFetch: async () => ({}), getAction: () => undefined, gateOk: () => true, listSessions: () => [], getSession: () => null,
    switchTo() {}, loadViewer: async () => ({ _loadRecent: () => [] }), openFile() {}, openFileInPane() {},
    splitActivePane: () => null, setPaneSession() {}, buildSessionCard: () => document.createElement('div'), updateSessionCard() {},
    setVtSkin() {}, vtSkins: () => ['farshell'], vtSkinLabel: (s) => s, listKeymapActions: () => [], invokeKeymapAction() {},
    displayCombo: (c) => c, showToast() {},
    loadAccounts: async () => ({ ok: true, profiles: [] }), newSessionWithAccount() {}, openAccountMenu() {},
  };
  const api = mountPalette(root, deps);
  api.open();
  await flush(10);
  assert.ok(![...document.querySelectorAll('.vt-qo-row')].some((r) => /계정/.test(r.textContent)));
});
