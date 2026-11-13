// 접힌 세션 띠 호버 카드 — 내용 구성(rail-hover-card.ts, 순수 함수)과 열고 닫는 규칙(RailHoverCard.tsx, jsdom).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
let _mod = null;
async function mod() { if (!_mod) { _mod = await loadSolidBuild(); _envs.push(_mod); } return _mod; }
// vm 컨텍스트가 만든 객체는 프로토타입이 달라 deepStrictEqual이 실패한다 — JSON으로 비교한다(rail-compact.test.js와 같다).
const same = (a, b, msg) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b), msg);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const css = (f) => fs.readFileSync(path.join(__dirname, '../../styles/screens', f), 'utf8');

const NOW = 1_700_000_000_000;
const base = {
  sessionId: 's1', tmuxName: 'api', name: 'api', open: true, status: 'idle', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, changed: null,
  agent: null, branch: null, isMainWorktree: false, origin: 'local', viewLabel: null, otherScreens: 0, otherClients: 0,
};
const row = (over = {}) => ({ ...base, ...over });
const sub = (...specs) => ({
  parent_id: 'p',
  items: specs.map((s, i) => ({ id: 'a' + i, state: s.state || 'working', label: s.label || 'worker', started_at: s.at ?? NOW / 1000 - 600 + i, stale: !!s.stale })),
});
const rowsOf = (m, sec) => (m.sections.find((s) => s.id === sec)?.rows) || [];
const keys = (m) => m.sections.flatMap((s) => s.rows.map((r) => r.key));

// ── 경로 ────────────────────────────────────────────────────────────────

test('shortenPath — 홈은 ~, 짧으면 그대로, 길면 첫 조각 + … + 마지막 3 → 2 → 1 조각', async () => {
  const { shortenPath, tildePath } = await mod();
  assert.strictEqual(tildePath('/Users/alice/work/app'), '~/work/app');
  assert.strictEqual(tildePath('/home/alice'), '~');
  assert.strictEqual(tildePath('/root/x'), '~/x');
  assert.strictEqual(tildePath('/Usersx/alice'), '/Usersx/alice', '접두만 같은 다른 경로는 홈이 아니다');
  assert.strictEqual(shortenPath('/Users/alice/work/app'), '~/work/app');
  assert.strictEqual(shortenPath('/Users/alice/work/app/'), '~/work/app', '끝 슬래시는 뗀다');
  assert.strictEqual(shortenPath('/'), '/');
  const long = '/Users/alice/GitHub/side_project/tools/farshell/frontend/js/shell';
  const s = shortenPath(long);
  assert.strictEqual(s, '~/…/frontend/js/shell');
  assert.match(s, /^~\/…\//, '앞을 줄인다');
  assert.ok(s.length <= 34);
  // 마지막 3조각이 안 들어가면 2조각으로
  assert.strictEqual(shortenPath('/Users/alice/a/bbbbbbbbbbbb/cccccccccccc/dddddddddddd', 34), '~/…/cccccccccccc/dddddddddddd');
  assert.strictEqual(shortenPath('/Users/alice/a/bbbbbbbbbbbbbbbb/cccccccccccccccc/dddddddddddddddd', 34), '~/…/dddddddddddddddd', '둘도 안 들어가면 한 조각');
  // 한 조각도 안 들어가면 그 조각의 앞을 줄인다
  const one = shortenPath('/Users/alice/a/' + 'x'.repeat(60), 34);
  assert.strictEqual(one.length, 34);
  assert.match(one, /^~\/…x+$/);
  // 절대 경로(홈 밖)는 루트 기호 없이 …로 시작
  const abs = shortenPath('/opt/projects/very-long-organisation-name/another-long-directory/leaf', 34);
  assert.match(abs, /^\/…\//);
  assert.ok(abs.endsWith('leaf'));
});

// ── 머리 · 상태 ─────────────────────────────────────────────────────────

test('머리 — 이름 · 에이전트 · 상태 글자 · 경과, 비어 있으면 구역이 없다', async () => {
  const { buildHoverCard } = await mod();
  const m = buildHoverCard(row({ name: 'dev', agent: 'claude', status: 'working', since: NOW / 1000 - 120 }), { now: NOW });
  assert.strictEqual(m.name, 'dev');
  assert.strictEqual(m.agent, 'claude');
  assert.strictEqual(m.state, 'working');
  assert.strictEqual(m.stateText, '작업 중');
  assert.strictEqual(m.since, '2분');
  same(m.sections, [], '아무 값도 없으면 구역 없음 — 「정보 갱신 대기」 같은 빈 문구도 없다');
  const none = buildHoverCard(row(), { now: NOW });
  assert.strictEqual(none.since, '');
  assert.strictEqual(none.agent, null);
  assert.strictEqual(none.stateText, '', '유휴는 쓰지 않는다(칸·메뉴 요약과 같은 기준) — 상태 줄 자체가 빠진다');
  assert.strictEqual(none.note, null);
});

test('입력 대기 — 종류(승인·선택·계획·질문)가 note, 모르는 값은 질문. working·done은 보조 표시, idle은 없음', async () => {
  const { buildHoverCard } = await mod();
  const note = (r) => buildHoverCard(row(r), { now: NOW }).note;
  assert.strictEqual(note({ status: 'waiting', kind: 'approval' }), '승인 요청');
  assert.strictEqual(note({ status: 'waiting', kind: 'choice' }), '선택 요청');
  assert.strictEqual(note({ status: 'waiting', kind: 'plan' }), '계획 확인');
  assert.strictEqual(note({ status: 'waiting', kind: 'question' }), '질문');
  assert.strictEqual(note({ status: 'waiting', kind: null }), '질문');
  assert.strictEqual(note({ status: 'waiting', kind: 'weird' }), '질문');
  assert.strictEqual(buildHoverCard(row({ status: 'waiting' }), { now: NOW }).stateText, '입력 대기');
  assert.strictEqual(note({ status: 'done', badge: 'question' }), '질문 있음');
  assert.strictEqual(note({ status: 'working', badge: 'plan' }), '계획 있음');
  assert.strictEqual(note({ status: 'idle', badge: 'plan' }), null);
  assert.strictEqual(buildHoverCard(row({ status: 'error' }), { now: NOW }).state, 'idle', '모르는 상태는 유휴');
});

test('활동 — 도구 이름은 working일 때만, 호출부가 준 값 그대로(카드가 스스로 읽지 않는다)', async () => {
  const { buildHoverCard } = await mod();
  const a = buildHoverCard(row({ status: 'working', tool: 'Edit' }), { now: NOW, tool: 'Bash' });
  same(rowsOf(a, 'activity').map((r) => r.text), ['Bash'], 'row.tool(Edit)이 아니라 넘긴 값');
  assert.strictEqual(rowsOf(a, 'activity')[0].mono, true);
  same(rowsOf(buildHoverCard(row({ status: 'working', tool: 'Edit' }), { now: NOW }), 'activity'), [], '넘기지 않으면 없다');
  same(rowsOf(buildHoverCard(row({ status: 'waiting', tool: 'Bash' }), { now: NOW, tool: 'Bash' }), 'activity'), []);
  same(rowsOf(buildHoverCard(row({ status: 'working' }), { now: NOW, tool: '' }), 'activity'), []);
});

// ── 위치 ────────────────────────────────────────────────────────────────

test('위치 — 디렉터리(~ 단축 · title은 전체) · 브랜치 · 워크트리 표기 · diff, 값이 없으면 줄이 없다', async () => {
  const { buildHoverCard } = await mod();
  const m = buildHoverCard(row({
    cwd: '/Users/alice/work/app', locationTip: '/Users/alice/work/app · 터미널: /tmp', branch: 'feat/x', workspaceKind: 'WT',
    changed: { files: 3, add: 12, del: 4 },
  }), { now: NOW });
  const [dir, br] = rowsOf(m, 'location');
  same([dir.key, dir.icon, dir.text, dir.mono], ['dir', 'folder', '~/work/app', true]);
  assert.strictEqual(dir.title, '/Users/alice/work/app · 터미널: /tmp');
  same([br.key, br.icon, br.text, br.tag], ['branch', 'git-branch', 'feat/x', '워크트리']);
  same(br.diff, { add: 12, del: 4 });
  assert.strictEqual(br.extra, undefined);
  // 메인 워크트리·종류 모름은 표기 없음
  assert.strictEqual(rowsOf(buildHoverCard(row({ cwd: '/a', branch: 'main', workspaceKind: '메인' }), { now: NOW }), 'location')[1].tag, undefined);
  // 브랜치 없음 → 디렉터리만
  same(rowsOf(buildHoverCard(row({ cwd: '/Users/alice/x' }), { now: NOW }), 'location').map((r) => r.key), ['dir']);
  // 디렉터리 없음(맥락 정보 없음) → 브랜치만
  same(rowsOf(buildHoverCard(row({ branch: 'dev' }), { now: NOW }), 'location').map((r) => r.key), ['branch']);
  // 줄 수 0이고 파일만 → 「파일 3」
  const files = rowsOf(buildHoverCard(row({ branch: 'dev', changed: { files: 3, add: 0, del: 0 } }), { now: NOW }), 'location')[0];
  assert.strictEqual(files.diff, undefined);
  assert.strictEqual(files.extra, '파일 3');
  // 한쪽만 있으면 그쪽만
  same(rowsOf(buildHoverCard(row({ branch: 'dev', changed: { files: 1, add: 5, del: 0 } }), { now: NOW }), 'location')[0].diff, { add: 5, del: 0 });
});

test('위치 — 원격 호스트면 호스트 이름과 응답 상태(속도), 꺼졌으면 이유', async () => {
  const { buildHoverCard } = await mod();
  const on = rowsOf(buildHoverCard(row({ cwd: '/home/alice/x' }), { now: NOW, host: { label: 'studio', online: true, latencyMs: 12 } }), 'location');
  const h = on.find((r) => r.key === 'host');
  same([h.icon, h.text, h.extra], ['server', 'studio', '응답 중 · 12ms']);
  assert.strictEqual(on[0].key, 'dir', '호스트 줄은 디렉터리·브랜치 뒤');
  const noMs = rowsOf(buildHoverCard(row(), { now: NOW, host: { label: 'studio', online: true } }), 'location')[0];
  assert.strictEqual(noMs.extra, '응답 중');
  const off = rowsOf(buildHoverCard(row(), { now: NOW, host: { label: 'studio', online: false, reason: 'timeout' } }), 'location')[0];
  assert.strictEqual(off.icon, 'wifi-off');
  assert.ok(off.extra && !/응답 중/.test(off.extra));
  assert.strictEqual(rowsOf(buildHoverCard(row(), { now: NOW, host: null }), 'location').length, 0, '로컬이면 호스트 줄 없음');
});

// ── 서브에이전트 ──────────────────────────────────────────────────────────

test('내부 에이전트 — 머리 「N개 · 작업 중 M」과 줄(작업 중이 위), 줄에는 상태 모양 · 이름 · 경과', async () => {
  const { buildHoverCard } = await mod();
  const m = buildHoverCard(row({
    subagents: sub({ state: 'done', label: 'a' }, { state: 'working', label: 'b' }, { state: 'idle', label: 'c' }, { state: 'working', label: 'd' }),
  }), { now: NOW });
  const r = rowsOf(m, 'subagents');
  assert.strictEqual(r[0].text, '내부 에이전트 4개 · 작업 중 2');
  assert.strictEqual(r[0].icon, 'bot');
  same(r.slice(1).map((x) => [x.text, x.glyph]), [['b', 'working'], ['d', 'working'], ['c', 'idle'], ['a', 'done']]);
  assert.strictEqual(r[1].extra, '10분', '경과 = 시작 후');
  // 작업 중이 없으면 개수만
  assert.strictEqual(rowsOf(buildHoverCard(row({ subagents: sub({ state: 'idle' }, { state: 'done' }) }), { now: NOW }), 'subagents')[0].text, '내부 에이전트 2개');
  // 한도 도달
  const lim = rowsOf(buildHoverCard(row({ subagents: { ...sub({ state: 'working' }), limited: true } }), { now: NOW }), 'subagents')[0];
  assert.strictEqual(lim.text, '내부 에이전트 · 표시 한도 도달');
  // 실패는 모양이 따로
  assert.strictEqual(rowsOf(buildHoverCard(row({ subagents: sub({ state: 'failed' }) }), { now: NOW }), 'subagents')[1].glyph, 'failed');
});

test('내부 에이전트 6개 → 줄 5개 + 「외 1개」', async () => {
  const { buildHoverCard, NATIVE_MAX } = await mod();
  assert.strictEqual(NATIVE_MAX, 5);
  const six = sub(...Array.from({ length: 6 }, (_, i) => ({ label: 'w' + i })));
  const r = rowsOf(buildHoverCard(row({ subagents: six }), { now: NOW }), 'subagents');
  assert.strictEqual(r[0].text, '내부 에이전트 6개 · 작업 중 6');
  assert.strictEqual(r.filter((x) => x.key.startsWith('native:')).length, 5);
  same([r[6].key, r[6].text, r[6].muted], ['native-more', '외 1개', true]);
  assert.strictEqual(r.length, 7);
  // 5개면 「외」가 없다
  const five = rowsOf(buildHoverCard(row({ subagents: sub(...Array.from({ length: 5 }, () => ({}))) }), { now: NOW }), 'subagents');
  assert.ok(!five.some((x) => x.key === 'native-more'));
});

test('같은 이름은 시작 순 #N(NativeChildren과 같은 규칙) — 자른 뒤에도 번호가 안 바뀐다, 이름이 하나면 번호 없음', async () => {
  const { buildHoverCard } = await mod();
  const items = {
    parent_id: 'p', items: [
      { id: 'late', state: 'working', label: 'general-purpose', started_at: 300 },
      { id: 'early', state: 'working', label: 'general-purpose', started_at: 100 },
      { id: 'mid', state: 'done', label: 'general-purpose', started_at: 200 },
      { id: 'solo', state: 'working', label: 'explorer', started_at: 50 },
    ],
  };
  const r = rowsOf(buildHoverCard(row({ subagents: items }), { now: NOW * 0 + 1000 * 1000 }), 'subagents');
  const byKey = Object.fromEntries(r.filter((x) => x.key.startsWith('native:')).map((x) => [x.key, x.text]));
  assert.strictEqual(byKey['native:early'], 'general-purpose #1');
  assert.strictEqual(byKey['native:mid'], 'general-purpose #2');
  assert.strictEqual(byKey['native:late'], 'general-purpose #3');
  assert.strictEqual(byKey['native:solo'], 'explorer');
  // 잘라도 번호는 전체 기준
  const six = { parent_id: 'p', items: [{ id: 'x1', state: 'idle', label: 'g', started_at: 1 }, { id: 'x2', state: 'done', label: 'g', started_at: 2 },
    ...Array.from({ length: 4 }, (_, i) => ({ id: 'w' + i, state: 'working', label: 'g', started_at: 10 + i }))] };
  const cut = rowsOf(buildHoverCard(row({ subagents: six }), { now: NOW }), 'subagents');
  assert.ok(cut.some((x) => x.key === 'native-more'));
  assert.strictEqual(cut.find((x) => x.key === 'native:w0').text, 'g #3');
});

test('내부 에이전트 — 상태 확인 필요(stale)는 흐리게 + 경과 대신 글, 값 없는 항목은 정규화에서 빠진다', async () => {
  const { buildHoverCard } = await mod();
  const r = rowsOf(buildHoverCard(row({ subagents: sub({ state: 'working', stale: true }) }), { now: NOW }), 'subagents')[1];
  assert.strictEqual(r.dim, true);
  assert.strictEqual(r.extra, '상태 확인 필요');
  same(rowsOf(buildHoverCard(row({ subagents: { items: [{ id: 'bad id', state: 'working' }, null, { id: 'ok', state: 'weird' }] } }), { now: NOW }), 'subagents'), []);
  same(rowsOf(buildHoverCard(row({ subagents: undefined }), { now: NOW }), 'subagents'), []);
});

test('하위 세션(위임) — 「하위 세션 N · 보고 M/N」 + 줄(입력 대기 → 작업 중 순) 3개까지 + 「외 K개」, 닫힌 것은 세지 않는다', async () => {
  const { buildHoverCard } = await mod();
  const kid = (name, status, state, outcome = null) => row({ name, tmuxName: name, status, lineage: { delegation: 'd-' + name, parent: 'api', state, outcome, createdAt: 1, task: 't', summary: '' } });
  const kids = [kid('k1', 'working', 'running'), kid('k2', 'idle', 'reported', 'succeeded'), kid('k3', 'waiting', 'running'), kid('k4', 'done', 'reported', 'failed'), kid('k5', 'idle', 'closed')];
  const r = rowsOf(buildHoverCard(row({ tmuxName: 'api' }), { now: NOW, kids }), 'subagents');
  assert.strictEqual(r[0].text, '하위 세션 4 · 보고 2/4');
  assert.strictEqual(r[0].icon, 'corner-down-right');
  same(r.slice(1, 4).map((x) => [x.text, x.glyph, x.extra]), [['k3', 'waiting', '진행 중'], ['k1', 'working', '진행 중'], ['k4', 'done', '보고 · 실패']]);
  same([r[4].key, r[4].text], ['kids-more', '외 1개']);
  assert.ok(!r.some((x) => x.text === 'k5'), '닫힌 하위는 줄에 없다');
  same(rowsOf(buildHoverCard(row(), { now: NOW, kids: [kid('only-closed', 'idle', 'closed')] }), 'subagents'), [], '열린 하위가 없으면 구역도 없다');
});

test('부모 — 위임 하위 세션이면 「부모: 이름」 + 위임 상태 + 맡은 일, 고아·부모 이름 없음이면 없다', async () => {
  const { buildHoverCard } = await mod();
  const lin = (over) => ({ delegation: 'd', parent: 'planner', state: 'running', outcome: null, createdAt: 1, task: '테스트 고치기', summary: '', ...over });
  const r = rowsOf(buildHoverCard(row({ lineage: lin() }), { now: NOW }), 'subagents');
  same([r[0].key, r[0].icon, r[0].text, r[0].extra], ['parent', 'corner-up-left', '부모: planner', '진행 중']);
  same([r[1].key, r[1].text], ['task', '테스트 고치기']);
  same(rowsOf(buildHoverCard(row({ lineage: lin({ task: '' }) }), { now: NOW }), 'subagents').map((x) => x.key), ['parent']);
  same(rowsOf(buildHoverCard(row({ lineage: lin({ state: 'orphaned' }) }), { now: NOW }), 'subagents'), []);
  same(rowsOf(buildHoverCard(row({ lineage: lin({ parent: null }) }), { now: NOW }), 'subagents'), []);
});

// ── 메타 ────────────────────────────────────────────────────────────────

test('메타 — 다른 화면 · 터미널 · 공유 · 만든 곳 · 읽지 않음, 없으면 줄이 없다', async () => {
  const { buildHoverCard } = await mod();
  same(rowsOf(buildHoverCard(row(), { now: NOW }), 'meta'), [], '공유할 수 없는 세션은 「공유 안 함」도 말하지 않는다');
  const m = buildHoverCard(row({
    otherScreens: 2, otherClients: 1, origin: 'peer', originHost: '맥미니', unseen: true, status: 'done',
    share: { nick: 'mina', mode: 'readwrite' },
  }), { now: NOW, shareable: true });
  const meta = rowsOf(m, 'meta');
  same(meta.map((x) => x.key), ['screens', 'share', 'origin', 'unseen']);
  assert.strictEqual(meta[0].text, '다른 화면 2 · 터미널 1');
  assert.strictEqual(meta[1].text, '공유 · mina · 읽기·쓰기');
  assert.strictEqual(meta[1].icon, 'share-write');
  assert.strictEqual(meta[2].text, '맥미니에서 만듦');
  assert.strictEqual(meta[3].text, '아직 확인 안 함');
  // 읽기 전용 아이콘, 로컬·외부(CLI·tmux) 출처는 줄 없음 — 다른 맥에서 만든 세션만 말한다
  const ro = rowsOf(buildHoverCard(row({ share: { nick: 'a', mode: 'read' }, origin: 'external' }), { now: NOW }), 'meta');
  same(ro.map((x) => [x.key, x.icon]), [['share', 'share-read']]);
  // 이름 모르는 peer
  assert.strictEqual(rowsOf(buildHoverCard(row({ origin: 'peer' }), { now: NOW }), 'meta')[0].text, '다른 맥에서 만듦');
  // idle은 unseen을 안 말한다(칸의 점과 같은 규칙)
  same(rowsOf(buildHoverCard(row({ unseen: true, status: 'idle' }), { now: NOW }), 'meta'), []);
  // 한 가지만 있어도 그것만
  same(rowsOf(buildHoverCard(row({ otherClients: 2 }), { now: NOW }), 'meta').map((x) => x.text), ['터미널 2']);
});

test('메타 — 공유할 수 있는 세션이 공유 중이 아니면 「공유 안 함」(마지막 줄), 공유 중이면 그것 하나', async () => {
  const { buildHoverCard, canShareName } = await mod();
  const m = rowsOf(buildHoverCard(row({ otherScreens: 1 }), { now: NOW, shareable: true }), 'meta');
  same(m.map((x) => x.text), ['다른 화면 1', '공유 안 함']);
  assert.strictEqual(m[1].muted, true);
  assert.ok(!rowsOf(buildHoverCard(row({ share: { nick: 'a', mode: 'read' } }), { now: NOW, shareable: true }), 'meta').some((x) => x.text === '공유 안 함'));
  assert.strictEqual(canShareName('api'), true);
  assert.strictEqual(canShareName('내-세션'), false, '한글 이름은 공유 못 한다');
  assert.strictEqual(canShareName(null), false);
});

// ── 높이 상한 ───────────────────────────────────────────────────────────

test('높이 상한 — 넘치면 메타 → 하위 세션 줄 → 내부 에이전트 줄 순으로 자르고 위치는 남긴다', async () => {
  const { buildHoverCard, BODY_MAX_LINES } = await mod();
  assert.strictEqual(BODY_MAX_LINES, 14);
  const kid = (n) => row({ name: 'k' + n, tmuxName: 'k' + n, status: 'working', lineage: { delegation: 'd' + n, parent: 'api', state: 'running', outcome: null, createdAt: n, task: '', summary: '' } });
  const full = row({
    cwd: '/Users/alice/x', branch: 'dev', status: 'working', otherScreens: 1, origin: 'peer', originHost: 'm', unseen: true,
    subagents: sub(...Array.from({ length: 6 }, (_, i) => ({ label: 'w' + i }))),
  });
  const opts = { now: NOW, tool: 'Bash', kids: [kid(1), kid(2), kid(3), kid(4)], shareable: true, host: { label: 'h', online: true, latencyMs: 3 } };
  const n = (m) => m.sections.reduce((a, s) => a + s.rows.length, 0);
  const big = buildHoverCard(full, { ...opts, maxLines: 99 });
  assert.ok(n(big) > BODY_MAX_LINES, '전부 그리면 상한을 넘는 입력');
  const m = buildHoverCard(full, opts);
  assert.ok(n(m) <= BODY_MAX_LINES, '상한 안: ' + n(m));
  same(rowsOf(m, 'location').map((r) => r.key), ['dir', 'branch', 'host'], '위치는 그대로');
  assert.strictEqual(rowsOf(m, 'activity').length, 1);
  assert.strictEqual(rowsOf(m, 'meta').length, 0, '메타부터 잘렸다');
  assert.ok(rowsOf(m, 'subagents').some((r) => r.key === 'kids'), '하위 세션 머리는 남는다');
  // 더 좁히면 하위 세션 줄 → 내부 에이전트 줄 순서로 줄고 위치는 끝까지 남는다
  const tight = buildHoverCard(full, { ...opts, maxLines: 8 });
  assert.ok(n(tight) < n(m), '더 줄어든다');
  same(rowsOf(tight, 'location').map((r) => r.key), ['dir', 'branch', 'host']);
  const tightKids = rowsOf(tight, 'subagents').filter((r) => r.key.startsWith('kid:')).length;
  const tightNative = rowsOf(tight, 'subagents').filter((r) => r.key.startsWith('native:')).length;
  assert.ok(tightKids <= 1);
  assert.strictEqual(tightNative, 1, '내부 에이전트 줄도 마지막에는 줄어든다(바닥 1줄 + 「외 K개」)');
  // 메타는 마지막 줄부터 빠진다 — 「공유 안 함」이 먼저 사라진다
  const light = buildHoverCard(row({ otherScreens: 1, origin: 'peer' }), { now: NOW, maxLines: 2 });
  same(rowsOf(light, 'meta').map((r) => r.key), ['screens', 'origin']);
  same(rowsOf(buildHoverCard(row({ otherScreens: 1, origin: 'peer' }), { now: NOW, maxLines: 1 }), 'meta').map((r) => r.key), ['screens']);
});

test('빈 구역·빈 문구가 없다 — 모든 줄에 글이 있고 모든 구역에 줄이 있다', async () => {
  const { buildHoverCard } = await mod();
  const m = buildHoverCard(row({
    cwd: '/a', branch: 'b', status: 'working', subagents: sub({}), otherScreens: 1,
  }), { now: NOW, tool: 'Bash' });
  for (const s of m.sections) {
    assert.ok(s.rows.length > 0, s.id);
    for (const r of s.rows) assert.ok(r.text && r.text.trim(), r.key);
  }
  assert.ok(!JSON.stringify(m).includes('정보 갱신 대기'));
  same(new Set(keys(m)).size, keys(m).length, '키는 겹치지 않는다');
});

// ── 열고 닫는 규칙(DOM) ─────────────────────────────────────────────────

async function setup(extra = {}, sources) {
  const env = await loadSolidBuild();
  _envs.push(env);
  const { mountHoverCard, document, window } = env;
  const root = document.createElement('aside');
  root.id = 'vt-wgrail';
  const mk = (key) => { const el = document.createElement('div'); el.setAttribute('data-card-key', key); el.tabIndex = 0; el.setAttribute('role', 'button'); root.appendChild(el); return el; };
  const a = mk('api'), b = mk('web');
  document.body.appendChild(root);
  const srcs = sources || {
    api: { row: row({ name: 'api', tmuxName: 'api', status: 'working', tool: 'Bash', cwd: '/Users/alice/app', branch: 'dev' }), kids: [], host: null, shareable: false },
    web: { row: row({ name: 'web', tmuxName: 'web', status: 'waiting', kind: 'approval' }), kids: [], host: null, shareable: false },
  };
  const h = mountHoverCard(root, { sources: srcs, openDelay: 25, closeGrace: 40, ...extra });
  const card = () => document.getElementById('vt-rail-card');
  const ptr = (el, type, pointerType = 'mouse') => {
    const ev = new window.Event(type, { bubbles: type !== 'pointerleave' && type !== 'pointerenter', cancelable: true });
    ev.pointerType = pointerType;
    el.dispatchEvent(ev);
  };
  return { env, root, a, b, h, card, ptr, document, window };
}
const isOpen = (c) => c.classList.contains('open');

test('기본 상수 — 올림 지연 250ms · 닫기 유예 160ms(떠 있는 동안 가로지르기용)', async () => {
  const { HC_OPEN_DELAY, HC_CLOSE_GRACE, HC_CARD_ID } = await mod();
  assert.strictEqual(HC_OPEN_DELAY, 250);
  assert.ok(HC_CLOSE_GRACE >= 100 && HC_CLOSE_GRACE <= 300);
  assert.strictEqual(HC_CARD_ID, 'vt-rail-card');
});

test('hover — 지연 뒤에 뜨고, 그 전에 나가면 안 뜬다 · 칸에 aria-describedby가 열린 동안만 걸린다', async () => {
  const { a, root, card, ptr, h } = await setup();
  assert.strictEqual(card().getAttribute('role'), 'tooltip');
  assert.strictEqual(card().getAttribute('aria-hidden'), 'true');
  ptr(a, 'pointerover');
  assert.ok(!isOpen(card()), '바로는 안 뜬다');
  await sleep(10);
  ptr(root, 'pointerleave');
  await sleep(80);
  assert.ok(!isOpen(card()), '스쳐 지나가면 안 뜬다');
  ptr(a, 'pointerover');
  await sleep(60);
  assert.ok(isOpen(card()));
  assert.strictEqual(card().getAttribute('aria-hidden'), 'false');
  assert.strictEqual(a.getAttribute('aria-describedby'), 'vt-rail-card');
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'api');
  assert.strictEqual(card().querySelector('.vt-hc-chip').textContent.trim(), '작업 중');
  ptr(root, 'pointerleave');
  await sleep(90);
  assert.ok(!isOpen(card()));
  assert.strictEqual(a.getAttribute('aria-describedby'), null, '닫히면 연결을 푼다');
  h.dispose();
});

test('hover — 터치 포인터에는 뜨지 않는다', async () => {
  const { a, card, ptr } = await setup();
  ptr(a, 'pointerover', 'touch');
  await sleep(70);
  assert.ok(!isOpen(card()));
});

test('hover — 이미 떠 있으면 다른 칸으로 지연 없이 옮겨 뜬다(내용도 바뀐다)', async () => {
  const { a, b, card, ptr } = await setup();
  ptr(a, 'pointerover');
  await sleep(60);
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'api');
  ptr(b, 'pointerover');
  assert.ok(isOpen(card()), '유지');
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'web', '즉시 바뀐다(지연 없음)');
  assert.strictEqual(card().querySelector('.vt-hc-note').textContent, '승인 요청', '입력 대기는 종류 한 줄');
  assert.strictEqual(a.getAttribute('aria-describedby'), null);
  assert.strictEqual(b.getAttribute('aria-describedby'), 'vt-rail-card');
});

test('hoverable — 칸에서 카드로 건너가는 동안 닫히지 않고, 카드에서 나가면 닫힌다', async () => {
  const { a, root, card, ptr } = await setup();
  ptr(a, 'pointerover');
  await sleep(60);
  ptr(root, 'pointerleave');            // 칸을 떠남(유예 시작)
  await sleep(15);
  ptr(card(), 'pointerenter');          // 카드에 도착
  await sleep(90);
  assert.ok(isOpen(card()), '카드 위에 있는 동안 유지(persistent)');
  ptr(card(), 'pointerleave');
  await sleep(90);
  assert.ok(!isOpen(card()));
});

test('dismissable — Esc로 닫히고, 같은 칸에 포인터가 그대로여도 다시 안 뜬다(떠났다 돌아오면 뜬다)', async () => {
  const { a, root, card, ptr, document, window } = await setup();
  ptr(a, 'pointerover');
  await sleep(60);
  assert.ok(isOpen(card()));
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.ok(!isOpen(card()));
  assert.strictEqual(a.getAttribute('aria-describedby'), null);
  ptr(a, 'pointerover');
  await sleep(60);
  assert.ok(!isOpen(card()), '같은 칸 위 — 다시 안 뜬다');
  ptr(root, 'pointerleave');
  await sleep(10);
  ptr(a, 'pointerover');
  await sleep(60);
  assert.ok(isOpen(card()), '떠났다 돌아오면 다시 뜬다');
  // 카드가 안 떠 있을 때의 Esc는 아무 일도 안 한다
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.ok(!isOpen(card()));
});

test('키보드 포커스 — 지연 없이 같은 카드가 뜨고 aria-describedby로 이어지며, 포커스가 떠나면 닫힌다', async () => {
  const { a, b, card, ptr, document, window } = await setup();
  a.focus();
  assert.ok(isOpen(card()), '지연 없음');
  assert.strictEqual(a.getAttribute('aria-describedby'), 'vt-rail-card');
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'api');
  b.focus();
  assert.ok(isOpen(card()), '칸 사이 이동 — 닫힌 채로 남지 않는다');
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'web');
  assert.strictEqual(a.getAttribute('aria-describedby'), null);
  assert.strictEqual(b.getAttribute('aria-describedby'), 'vt-rail-card');
  // 포인터가 스쳐도 포커스로 열린 카드는 닫히지 않는다
  ptr(document.body, 'pointerover');
  await sleep(80);
  assert.ok(isOpen(card()));
  b.blur();
  assert.ok(!isOpen(card()), '포커스가 떠나면 닫힌다');
  assert.strictEqual(b.getAttribute('aria-describedby'), null);
  // Esc 후에는 같은 칸에 포커스가 남아 있어도 다시 안 뜬다
  a.focus();
  assert.ok(isOpen(card()));
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.ok(!isOpen(card()));
  assert.strictEqual(document.activeElement, a, 'Esc는 포커스를 옮기지 않는다');
  // jsdom은 세 번째 focus()부터 :focus-visible을 거짓으로 답한다 — 그 질문만 참으로 고정하고 focusin을 직접 보낸다.
  const realMatches = b.matches.bind(b);
  b.matches = (sel) => (sel === ':focus-visible' ? true : realMatches(sel));
  b.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }));
  assert.ok(isOpen(card()), '포커스가 다른 칸으로 가면 그 칸의 카드가 뜬다');
  assert.strictEqual(card().querySelector('.vt-hc-name').textContent, 'web');
});

test('칸의 기존 aria-describedby는 건드리지 않는다', async () => {
  const { a, card } = await setup();
  a.setAttribute('aria-describedby', 'mine');
  a.focus();
  assert.ok(isOpen(card()));
  assert.strictEqual(a.getAttribute('aria-describedby'), 'mine');
  a.blur();
  assert.strictEqual(a.getAttribute('aria-describedby'), 'mine');
});

test('눌렀을 때·스크롤·접힘 해제 — 닫힌다', async () => {
  const { a, card, ptr, document, window, h, root } = await setup();
  ptr(a, 'pointerover'); await sleep(60);
  assert.ok(isOpen(card()));
  document.body.dispatchEvent(new window.Event('pointerdown', { bubbles: true }));
  assert.ok(!isOpen(card()), '누르면(= 세션을 연다) 닫힌다');
  ptr(root, 'pointerleave'); await sleep(5);
  ptr(a, 'pointerover'); await sleep(60);
  assert.ok(isOpen(card()));
  window.dispatchEvent(new window.Event('scroll'));
  assert.ok(!isOpen(card()), '스크롤하면 닫힌다');
  ptr(root, 'pointerleave'); await sleep(5);
  ptr(a, 'pointerover'); await sleep(60);
  assert.ok(isOpen(card()));
  h.setEnabled(false);
  assert.ok(!isOpen(card()), '띠가 펼쳐지면 닫힌다');
});

test('열린 카드는 도구 이름을 열릴 때 한 번 찍고, 상태가 바뀔 때만 갱신한다 · 세션이 사라지면 닫는다', async () => {
  const { a, card, ptr, h } = await setup();
  const tool = () => card().querySelector('[data-key="tool"] .vt-hc-text')?.textContent;
  ptr(a, 'pointerover'); await sleep(60);
  assert.strictEqual(tool(), 'Bash');
  h.update('api', { tool: 'Edit' });
  assert.strictEqual(tool(), 'Bash', '같은 상태에서 도구가 바뀌어도 그대로(깜빡임 방지)');
  h.update('api', { branch: 'feat/y' });
  assert.strictEqual(card().querySelector('[data-key="branch"] .vt-hc-text').textContent, 'feat/y', '그 밖의 값은 살아 있다');
  assert.strictEqual(tool(), 'Bash');
  h.update('api', { status: 'idle' });
  assert.strictEqual(tool(), undefined, '상태가 바뀌면 갱신(작업 중이 아니면 줄이 없다)');
  h.update('api', { status: 'working', tool: 'Read' });
  assert.strictEqual(tool(), 'Read', '상태가 바뀌었으니 새 값');
  h.remove('api');
  assert.ok(!isOpen(card()), '세션이 사라지면 닫는다');
});

test('카드 DOM — 모든 줄이 같은 3열 구조(아이콘 열 · 값 열)이고 상태는 모양 + 글자로 말한다', async () => {
  const { a, card, ptr } = await setup({}, {
    api: {
      row: row({
        name: 'api', tmuxName: 'api', agent: 'claude', status: 'waiting', kind: 'plan', cwd: '/Users/alice/app', branch: 'dev', workspaceKind: 'WT',
        changed: { files: 1, add: 3, del: 1 }, otherScreens: 1, subagents: sub({ label: 'g' }, { label: 'g' }),
      }),
      kids: [], host: null, shareable: true,
    },
  });
  ptr(a, 'pointerover'); await sleep(60);
  const c = card();
  const rows = [...c.querySelectorAll('.vt-hc-row')];
  assert.ok(rows.length >= 8);
  for (const r of rows) {
    assert.ok(r.querySelector(':scope > .vt-hc-ico'), '아이콘 열: ' + r.className);
    assert.ok(r.querySelector(':scope > .vt-hc-val'), '값 열: ' + r.className);
  }
  const chip = c.querySelector('.vt-hc-chip');
  assert.ok(chip.querySelector('svg'), '상태 모양(기호)');
  assert.strictEqual(chip.textContent.trim(), '입력 대기', '상태 글자');
  assert.strictEqual(c.querySelector('.vt-hc-note').textContent, '계획 확인');
  assert.strictEqual(c.querySelector('.vt-hc-tag').textContent, '워크트리');
  assert.match(c.querySelector('[data-key="branch"] .vt-hc-extra').textContent, /\+3\s*−1/);
  assert.ok(c.querySelector('[data-key="native"] .vt-hc-ico svg'), '내부 에이전트 머리 아이콘');
  same([...c.querySelectorAll('[data-key^="native:"] .vt-hc-text')].map((e) => e.textContent), ['g #1', 'g #2']);
  assert.ok(c.querySelector('[data-key="unshared"]'), '공유할 수 있는 세션 — 공유 안 함');
  assert.ok(c.querySelector('.vt-hc-ico[aria-hidden="true"] svg'), '아이콘은 장식(글이 읽힌다)');
});

// ── CSS 계약 ────────────────────────────────────────────────────────────

test('CSS — 폭·높이 고정(메뉴 크기 변수를 안 쓴다), 줄 20 · 구역 8, 등장은 opacity뿐이고 모션 줄이기에서 전환 없음, 글자색은 txt·sub만', () => {
  const c = css('71-rail-hover-card.css');
  const body = c.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(body, /\.vt-hc \{[^}]*width:300px/);
  assert.match(body, /max-height:min\(380px, calc\(100vh - 16px\)\)/);
  assert.ok(!/--vt-cell|--vt-act-w|--vt-side|data-vt-side-scale/.test(body), '크기 설정과 무관');
  assert.match(body, /\.vt-hc-row \{[^}]*grid-template-columns:14px minmax\(0, 1fr\) auto[^}]*min-height:20px/);
  assert.match(body, /\.vt-hc-sec \{ margin-top:8px; \}/);
  assert.match(body, /background:var\(--color-bg-raised\)/);
  assert.match(body, /border:1px solid var\(--color-line-strong\)/);
  assert.match(body, /border-radius:8px/);
  assert.ok(!/transform\s*:/.test(body), 'transform 없음');
  assert.match(body, /transition:opacity var\(--dur-fast\) linear/);
  assert.match(body, /@media \(prefers-reduced-motion: reduce\) \{ \.vt-hc \{ transition:none; \} \}/);
  // 글자색(color:)은 txt · sub · 상태 기호(--color-st-*) · diff만 — muted·faint는 쓰지 않는다
  const colors = [...body.matchAll(/(?<![-\w])color\s*:\s*([^;}]+)/g)].map((m) => m[1].trim());
  for (const v of colors) assert.match(v, /^var\(--color-(txt|sub|st-[a-z]+)\)$/, v);
  assert.ok(!/--color-(muted|faint)/.test(body));
});

test('CSS — main.css가 71 파일을 70 바로 뒤에 불러온다', () => {
  const main = fs.readFileSync(path.join(__dirname, '../../styles/main.css'), 'utf8');
  const i70 = main.indexOf('70-worktree-rail.css'), i71 = main.indexOf('71-rail-hover-card.css'), i80 = main.indexOf('80-dock.css');
  assert.ok(i70 > 0 && i70 < i71 && i71 < i80);
});

test('공용 툴팁은 그대로 — tooltip.js는 data-tip만 보고 카드는 data-card-key만 본다', () => {
  const tip = fs.readFileSync(path.join(__dirname, '../js/ui/tooltip.js'), 'utf8');
  assert.ok(!tip.includes('data-card-key'));
  const card = fs.readFileSync(path.join(__dirname, '../js/shell/RailHoverCard.tsx'), 'utf8');
  assert.ok(!/['"`]\[data-tip\]|data-tip=/.test(card.replace(/\/\/.*$/gm, '')), '카드는 data-tip을 읽지도 달지도 않는다');
});

test('유휴는 눈에 보이는 메뉴 요약·호버 카드에 쓰지 않고, 화면 읽기용 요약에는 남긴다', async () => {
  const m = await mod();
  const base = { status: 'idle', kind: null, otherClients: 0, origin: 'local', originHost: null, branch: 'main' };
  const d = m.rowSummaryDetails(base);
  assert.ok(!d.status.includes('유휴'), d.status);
  assert.ok(d.accessible.includes('유휴'), d.accessible);
  assert.ok(m.rowSummaryDetails({ ...base, status: 'working' }).status.includes('작업 중'));
});
