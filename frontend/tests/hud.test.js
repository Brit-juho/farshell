// N34 §7 — HUD 칩 구성 로직(shell/hud-data.ts). 이 파일이 지키는 계약은
// 하나다: **값이 없는 항목은 숨긴다.** 2.0의 사용량 게이팅과 같은 규칙이고,
// "없음"/"—" 같은 자리표시자를 그리면 24px짜리 띠가 금세 쓸모없어진다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

let _mod = null;
async function mod() {
  if (!_mod) {
    _mod = await loadSolidBuild();
    _doms.push(_mod);
  }
  return _mod;
}

// vm 모듈이 만든 배열은 이 realm의 Array와 생성자가 달라 deepStrictEqual이
// "구조는 같은데 참조가 다르다"로 실패한다(cross-realm) — 이 realm 배열로 복사한다.
const ids = (chips) => Array.from(chips, (c) => c.id);

test('아무 데이터도 없으면 칩이 하나도 없다 (자리표시자를 그리지 않는다)', async () => {
  const { buildHudChips } = await mod();
  assert.deepStrictEqual(ids(buildHudChips({})), []);
});

test('capabilities가 오기 전에는 서버 칩도 안 그린다', async () => {
  const { buildHudChips } = await mod();
  // 부팅 중 "터널 없음" 같은 틀린 상태를 잠깐 보여주는 게 아무것도 안 보여주는
  // 것보다 나쁘다 — caps가 null이면 통째로 비운다.
  const chips = buildHudChips({ port: '7801', tunnel: { running: true, mode: 'named' } });
  assert.ok(!ids(chips).includes('server'));
});

test('서버 칩은 포트를 그대로 싣는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({ port: '7801', caps: {} });
  const server = chips.find((c) => c.id === 'server');
  assert.ok(server);
  assert.strictEqual(server.value, ':7801');
  assert.strictEqual(server.dot, true);
  assert.strictEqual(server.side, 'left');
});

test('터널이 안 돌면 터널 칩이 없다 / 돌면 모드를 싣는다', async () => {
  const { buildHudChips } = await mod();
  assert.ok(!ids(buildHudChips({ caps: {}, tunnel: { running: false } })).includes('tunnel'));

  const named = buildHudChips({ caps: {}, tunnel: { running: true, mode: 'named' } });
  assert.strictEqual(named.find((c) => c.id === 'tunnel').value, 'named');

  const anon = buildHudChips({ caps: {}, tunnel: { running: true, mode: 'anonymous' } });
  assert.strictEqual(anon.find((c) => c.id === 'tunnel').value, '익명');
});

test('E2E·세이프모드는 켜졌을 때만 칩이 생긴다', async () => {
  const { buildHudChips } = await mod();
  const off = buildHudChips({ caps: {}, e2e: false, safeMode: { enabled: false } });
  assert.ok(!ids(off).includes('e2e'));
  assert.ok(!ids(off).includes('safe-mode'));

  const on = buildHudChips({ caps: {}, e2e: true, safeMode: { enabled: true } });
  assert.strictEqual(on.find((c) => c.id === 'e2e').value, 'ON');
  assert.strictEqual(on.find((c) => c.id === 'safe-mode').value, 'ON');
});

test('연결된 화면: 1개여도 보여주고, 2개 이상이면 주의 색', async () => {
  const { buildHudChips } = await mod();
  // 2.0은 clients.length < 2면 패널을 통째로 숨겨서 "누가 더 붙어 있나"를
  // 확인할 방법이 사실상 없었다. HUD는 1개여도 보여준다.
  const one = buildHudChips({ caps: {}, screens: 1 }).find((c) => c.id === 'screens');
  assert.strictEqual(one.value, '1');
  assert.strictEqual(one.tone, 'plain');

  // 여러 화면이 같은 tmux 세션에 붙으면 tmux가 가장 작은 클라이언트에 맞춰
  // 리레이아웃한다 — "화면이 오락가락"의 원인이라 주의 색으로 띄운다.
  const three = buildHudChips({ caps: {}, screens: 3 }).find((c) => c.id === 'screens');
  assert.strictEqual(three.tone, 'warn');
  assert.strictEqual(three.action, 'clients.show');

  // 조회 실패(0/null)는 숨긴다.
  assert.ok(!ids(buildHudChips({ caps: {}, screens: 0 })).includes('screens'));
  assert.ok(!ids(buildHudChips({ caps: {}, screens: null })).includes('screens'));
});

test('사용량 provider가 없으면 사용량 칩이 통째로 없다 (2.0 규칙 유지)', async () => {
  const { buildHudChips } = await mod();
  assert.deepStrictEqual(
    ids(buildHudChips({ caps: {}, usage: { available: false } })).filter((i) => i.startsWith('usage:')),
    [],
  );
});

test('사용량 칩: 창이 여럿이면 가장 많이 쓴 창을 대표로 쓴다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({
    caps: {},
    usage: {
      available: true,
      profiles: [{
        name: 'Claude',
        windows: [
          { label: '5h', pct: 62, resets_in_sec: 15120 },
          { label: '7d', pct: 20, resets_in_sec: 345600 },
        ],
      }],
    },
  });
  const usage = chips.find((c) => c.id === 'usage:Claude');
  assert.strictEqual(usage.value, '62%', '20%가 아니라 더 높은 62%를 써야 한다');
  assert.strictEqual(usage.hint, '· 4h12m 후 초기화');
  assert.strictEqual(usage.side, 'right');
});

test('사용량 칩 색: 75% 주의, 90% 위험', async () => {
  const { buildHudChips } = await mod();
  const toneAt = (pct) => buildHudChips({
    caps: {},
    usage: { available: true, profiles: [{ name: 'X', windows: [{ pct }] }] },
  }).find((c) => c.id === 'usage:X').tone;

  assert.strictEqual(toneAt(10), 'plain');
  assert.strictEqual(toneAt(80), 'warn');
  assert.strictEqual(toneAt(95), 'err');
});

test('창(window)이 없는 프로필은 칩을 만들지 않는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({
    caps: {},
    usage: { available: true, profiles: [{ name: '사용량 없음', windows: [] }] },
  });
  assert.deepStrictEqual(ids(chips).filter((i) => i.startsWith('usage:')), []);
});

test('버전은 있을 때만, v 접두사를 붙여 우측에', async () => {
  const { buildHudChips } = await mod();
  assert.ok(!ids(buildHudChips({ caps: { version: '' } })).includes('version'));
  const v = buildHudChips({ caps: { version: '2.1.0' } }).find((c) => c.id === 'version');
  assert.strictEqual(v.value, 'v2.1.0');
  assert.strictEqual(v.side, 'right');
});

test('formatResetsIn — 시/분/곧/없음', async () => {
  const { formatResetsIn } = await mod();
  assert.strictEqual(formatResetsIn(15120), '4h12m');
  assert.strictEqual(formatResetsIn(720), '12m');
  assert.strictEqual(formatResetsIn(30), '곧');
  assert.strictEqual(formatResetsIn(null), '');
  assert.strictEqual(formatResetsIn(undefined), '');
  assert.strictEqual(formatResetsIn(-5), '');
});

// ── 순위(priority) ─────────────────────────────────────────────────────────
// "값이 없으면 숨긴다"와 다른 축이다 — 보여줄 건 있는데 폭이 없을 때 무엇이
// 마지막까지 남는지. 실제로 덜어내는 건 Hud.tsx의 fitChips지만, 무엇이 먼저
// 양보하는지는 이 순수 함수가 정하므로 여기서 잠근다.

test('모든 칩은 순위를 갖는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({
    caps: { version: '2.1.0' }, port: '7777',
    tunnel: { running: true, mode: 'named' },
    e2e: true, safeMode: { enabled: true }, screens: 2,
    usage: { available: true, profiles: [{ name: 'brit', windows: [{ pct: 30 }] }] },
  });
  assert.ok(chips.length > 0);
  for (const c of chips) {
    assert.strictEqual(typeof c.priority, 'number', `${c.id}에 순위가 없다`);
  }
});

test('서버 칩이 버전보다 끝까지 남는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({ caps: { version: '2.1.0' }, port: '7777' });
  const server = chips.find((c) => c.id === 'server');
  const version = chips.find((c) => c.id === 'version');
  assert.ok(server.priority < version.priority);
});

test('사용량은 한도가 임박할 때만 앞자리로 올라온다', async () => {
  const { buildHudChips } = await mod();
  const at = (pct) => buildHudChips({
    caps: {}, usage: { available: true, profiles: [{ name: 'brit', windows: [{ pct }] }] },
  }).find((c) => c.id === 'usage:brit');
  // 평상시에는 터널보다 뒤, 위험할 때는 터널보다 앞.
  const tunnel = buildHudChips({ caps: {}, tunnel: { running: true } }).find((c) => c.id === 'tunnel');
  assert.ok(at(30).priority > tunnel.priority, '평상시 사용량이 터널보다 앞이면 안 된다');
  assert.ok(at(95).priority < tunnel.priority, '위험한 사용량은 터널보다 앞이어야 한다');
});

test('연결된 화면 — 나 혼자일 때보다 여럿일 때 더 오래 남는다', async () => {
  const { buildHudChips } = await mod();
  const p = (n) => buildHudChips({ caps: {}, screens: n }).find((c) => c.id === 'screens').priority;
  assert.ok(p(3) < p(1));
});
