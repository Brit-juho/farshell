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
  const chips = buildHudChips({ port: '7801' });
  assert.ok(!ids(chips).includes('server'));
});

test('서버 칩은 포트를 그대로 싣는다', async () => {
  const { buildHudChips } = await mod();
  // 기본 배치에는 서버 칩이 없다(2026-10-23) — 켠 배치에서 본다.
  const chips = buildHudChips({ port: '7801', caps: {} }, { left: ['server'], right: [] });
  const server = chips.find((c) => c.id === 'server');
  assert.ok(server);
  assert.strictEqual(server.value, ':7801');
  assert.strictEqual(server.dot, true);
  assert.strictEqual(server.side, 'left');
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
  assert.strictEqual(usage.hint, '· 4h12m');
  assert.strictEqual(usage.tooltip, '· 4h12m 후 초기화');
  assert.strictEqual(usage.side, 'right');
});

test('사용량 칩: 툴팁에 출처와 갱신 시각을 싣고, 모르는 출처는 이름을 지어내지 않는다', async () => {
  const { buildHudChips } = await mod();
  const at = Date.now() / 1000 - 300;
  const tip = (source) => buildHudChips({
    caps: {}, usage: { available: true, profiles: [{ name: 'A', source, checked_at: at, windows: [{ pct: 40, resets_in_sec: 3600 }] }] },
  }).find((c) => c.id === 'usage:A').tooltip;
  assert.strictEqual(tip('statusline'), '· 1h0m 후 초기화\n출처 statusline · 5분 전');
  assert.match(tip('app-server'), /출처 app-server · 5분 전$/);
  assert.strictEqual(tip('something-new'), '· 1h0m 후 초기화');
  assert.strictEqual(tip(undefined), '· 1h0m 후 초기화');
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
    usage: { available: true, profiles: [{ name: 'alpha', windows: [{ pct: 30 }] }] },
  });
  assert.ok(chips.length > 0);
  for (const c of chips) {
    assert.strictEqual(typeof c.priority, 'number', `${c.id}에 순위가 없다`);
  }
});

test('서버 칩이 버전보다 끝까지 남는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({ caps: { version: '2.1.0' }, port: '7777' }, { left: ['server'], right: ['version'] });
  const server = chips.find((c) => c.id === 'server');
  const version = chips.find((c) => c.id === 'version');
  assert.ok(server.priority < version.priority);
});

test('사용량은 한도가 임박할 때만 앞자리로 올라온다', async () => {
  const { buildHudChips } = await mod();
  const at = (pct) => buildHudChips({
    caps: {}, usage: { available: true, profiles: [{ name: 'alpha', windows: [{ pct }] }] },
  }).find((c) => c.id === 'usage:alpha');
  // 평상시에는 "다른 화면이 붙음" 경고보다 뒤, 위험할 때는 그보다 앞.
  const screensAlert = buildHudChips({ caps: {}, screens: 2 }).find((c) => c.id === 'screens');
  assert.ok(at(30).priority > screensAlert.priority, '평상시 사용량이 화면 경고보다 앞이면 안 된다');
  assert.ok(at(95).priority < screensAlert.priority, '위험한 사용량은 화면 경고보다 앞이어야 한다');
});

test('연결된 화면 — 나 혼자일 때보다 여럿일 때 더 오래 남는다', async () => {
  const { buildHudChips } = await mod();
  const p = (n) => buildHudChips({ caps: {}, screens: n }).find((c) => c.id === 'screens').priority;
  assert.ok(p(3) < p(1));
});

// ── 시스템 사용률 칩(CPU·RAM·네트워크) ───────────────────────────────────────
test('formatRate — 초당 바이트를 한 글자 단위로 줄인다', async () => {
  const { formatRate } = await mod();
  assert.strictEqual(formatRate(0), '0');
  assert.strictEqual(formatRate(null), '0');
  assert.strictEqual(formatRate(512), '512B');
  assert.strictEqual(formatRate(1536), '1.5K');
  assert.strictEqual(formatRate(340 * 1024), '340K');
  assert.strictEqual(formatRate(1.2 * 1024 * 1024), '1.2M');
  assert.strictEqual(formatRate(250 * 1024 * 1024), '250M');
});

test('시스템 칩 — 값이 있는 항목만 그린다(읽지 못한 항목은 칩이 없다)', async () => {
  const { buildHudChips } = await mod();
  const all = buildHudChips({ stats: { cpu: { pct: 12.4, cores: 8 }, ram: { used: 1, total: 2, pct: 55.2 }, net: { rx_bps: 2048, tx_bps: 100 } } });
  assert.deepStrictEqual(ids(all), ['stats:cpu', 'stats:ram', 'stats:net']);
  const byId = Object.fromEntries(Array.from(all, (c) => [c.id, c]));
  assert.strictEqual(byId['stats:cpu'].value, '12%');
  assert.strictEqual(byId['stats:ram'].value, '55%');
  assert.strictEqual(byId['stats:net'].value, '↓2K ↑100B');
  assert.strictEqual(byId['stats:cpu'].action, 'system.open');

  assert.deepStrictEqual(ids(buildHudChips({ stats: { cpu: null, ram: { used: 1, total: 2, pct: 10 }, net: null } })), ['stats:ram']);
  assert.deepStrictEqual(ids(buildHudChips({ stats: { cpu: null, ram: null, net: null } })), []);
});

test('시스템 칩 — 임계를 넘으면 색이 바뀌고 자리가 모자랄 때 더 오래 버틴다', async () => {
  const { buildHudChips } = await mod();
  const chip = (stats, id) => Array.from(buildHudChips({ stats }), (c) => c).find((c) => c.id === id);
  assert.strictEqual(chip({ cpu: { pct: 50, cores: 4 } }, 'stats:cpu').tone, 'plain');
  assert.strictEqual(chip({ cpu: { pct: 80, cores: 4 } }, 'stats:cpu').tone, 'warn');
  assert.strictEqual(chip({ cpu: { pct: 95, cores: 4 } }, 'stats:cpu').tone, 'err');
  assert.strictEqual(chip({ ram: { used: 1, total: 2, pct: 82 } }, 'stats:ram').tone, 'warn');
  assert.strictEqual(chip({ ram: { used: 1, total: 2, pct: 91 } }, 'stats:ram').tone, 'err');
  const calm = chip({ cpu: { pct: 10, cores: 4 } }, 'stats:cpu').priority;
  const hot = chip({ cpu: { pct: 95, cores: 4 } }, 'stats:cpu').priority;
  assert.ok(hot < calm, '위험하면 우선순위 숫자가 작아진다(끝까지 남는다)');
});

test('시스템 칩 — GPU 칩은 값이 있을 때만, 임계는 CPU와 같다', async () => {
  const { buildHudChips } = await mod();
  const chip = (stats) => Array.from(buildHudChips({ stats }), (c) => c).find((c) => c.id === 'stats:gpu');
  assert.strictEqual(chip({ gpu: null }), undefined);
  const named = chip({ gpu: { pct: 50, name: 'Sample GPU' } });
  assert.strictEqual(named.tooltip, undefined);
  assert.strictEqual(named.hint, undefined);
  assert.strictEqual(chip({ gpu: { pct: 50.4 } }).value, '50%');
  assert.strictEqual(chip({ gpu: { pct: 80 } }).tone, 'warn');
  assert.strictEqual(chip({ gpu: { pct: 95 } }).tone, 'err');
});


// ── 배치(2026-10-23): 어떤 칩을 어느 쪽에 어떤 순서로 ──────────────────────

const sides = (chips) => Array.from(chips, (c) => `${c.side}:${c.id}`);

test('기본 배치에는 서버 칩이 없다 — 지금 이 화면이 보이면 서버는 떠 있다', async () => {
  const { buildHudChips, DEFAULT_HUD_LAYOUT } = await mod();
  assert.ok(!DEFAULT_HUD_LAYOUT.left.includes('server') && !DEFAULT_HUD_LAYOUT.right.includes('server'));
  assert.ok(!ids(buildHudChips({ port: '7777', caps: { version: '2.6.0' } })).includes('server'));
});

test('배치가 칩의 구역과 순서를 정한다 (칩이 스스로 정한 side는 무시)', async () => {
  const { buildHudChips } = await mod();
  const input = {
    port: '7777', caps: { version: '2.6.0' }, screens: 1,
    stats: { cpu: { pct: 10 }, ram: { pct: 20 } },
  };
  const chips = buildHudChips(input, { left: ['cpu', 'version'], right: ['screens', 'server', 'ram'] });
  assert.deepStrictEqual(sides(chips), [
    'left:stats:cpu', 'left:version', 'right:screens', 'right:server', 'right:stats:ram',
  ]);
});

test('배치에 없는 종류는 값이 있어도 그리지 않는다', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({ caps: { version: '2.6.0' }, screens: 2, stats: { cpu: { pct: 10 } } },
    { left: [], right: ['cpu'] });
  assert.deepStrictEqual(ids(chips), ['stats:cpu']);
});

test('배치에 있어도 값이 없으면 그리지 않는다 (값 없음 → 숨김 규칙이 위에 그대로)', async () => {
  const { buildHudChips } = await mod();
  assert.deepStrictEqual(ids(buildHudChips({}, { left: ['screens', 'server'], right: ['cpu', 'usage', 'version'] })), []);
});

test('사용량 프로필이 여럿이어도 「사용량」 하나로 함께 움직인다', async () => {
  const { buildHudChips } = await mod();
  const usage = { available: true, profiles: [
    { name: 'alpha', windows: [{ pct: 10 }] }, { name: 'beta', windows: [{ pct: 20 }] },
  ] };
  assert.deepStrictEqual(sides(buildHudChips({ caps: {}, usage }, { left: ['usage'], right: [] })),
    ['left:usage:alpha', 'left:usage:beta']);
});

test('parseHudLayout: 비었거나 깨졌으면 기본 배치, 모르는 종류·중복은 버린다', async () => {
  const { parseHudLayout, DEFAULT_HUD_LAYOUT } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  for (const bad of [undefined, null, '', '   ', '{', '[]', '{"left":1}', '{"left":[]}', 42]) {
    assert.deepStrictEqual(plain(parseHudLayout(bad)), plain(DEFAULT_HUD_LAYOUT), `입력: ${String(bad)}`);
  }
  assert.deepStrictEqual(
    plain(parseHudLayout('{"v":4,"left":["cpu","nope",7,"cpu"],"right":["cpu","version","version"]}')),
    { left: ['cpu'], right: ['version'] },
    '먼저 나온 쪽만 남고 모르는 종류는 버려진다');
  assert.deepStrictEqual(plain(parseHudLayout('{"left":[],"right":[]}')), { left: [], right: [] },
    '전부 끈 배치는 그대로 존중한다 (기본으로 되돌리지 않는다)');
  assert.deepStrictEqual(plain(parseHudLayout('{"v":2,"left":[],"right":[]}')), { left: [], right: [] });
});

test('parseHudLayout: 메뉴바 이전(세대 표시 없음)에 저장된 배치는 새 종류를 기본 자리에 한 번 얹는다', async () => {
  const { parseHudLayout, serializeHudLayout } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  const legacy = '{"left":["screens"],"right":["cpu","version"]}';
  assert.deepStrictEqual(plain(parseHudLayout(legacy)),
    { left: ['screens'], right: ['cpu', 'version', 'push', 'clock', 'account'] },
    'push·clock·account는 오른쪽 끝(에이전트는 배치 밖 고정이라 얹지 않는다)');
  // 이미 직접 배치해 둔 새 종류는 건드리지 않는다.
  assert.deepStrictEqual(plain(parseHudLayout('{"left":["screens"],"right":["clock","cpu"]}')),
    { left: ['screens'], right: ['clock', 'cpu', 'push', 'account'] });
  // 한 번 저장하면 세대가 적혀서 다시 얹지 않는다 — 사용자가 새 칩을 끈 선택이 되살아나지 않는다.
  const saved = serializeHudLayout(parseHudLayout(legacy));
  assert.strictEqual(JSON.parse(saved).v, 4);
  const off = serializeHudLayout({ left: ['screens'], right: ['cpu'] });
  assert.deepStrictEqual(plain(parseHudLayout(off)), { left: ['screens'], right: ['cpu'] });
});

test('세대 4: 계정 칩은 세대 3 이하 배치를 읽을 때 「사용량」 앞에 한 번 얹힌다 — 전부 끈 배치·이미 4인 배치는 그대로', async () => {
  const { parseHudLayout, serializeHudLayout } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  assert.deepStrictEqual(plain(parseHudLayout('{"v":3,"left":["screens"],"right":["cpu","usage","clock"]}')),
    { left: ['screens'], right: ['cpu', 'account', 'usage', 'clock'] });
  assert.deepStrictEqual(plain(parseHudLayout('{"v":3,"left":[],"right":["cpu"]}')), { left: [], right: ['cpu', 'account'] }, '사용량을 끈 배치는 오른쪽 끝');
  assert.deepStrictEqual(plain(parseHudLayout('{"v":3,"left":[],"right":[]}')), { left: [], right: [] }, '전부 끈 배치는 사용자의 선택이다');
  assert.deepStrictEqual(plain(parseHudLayout('{"v":4,"left":[],"right":["cpu","usage"]}')), { left: [], right: ['cpu', 'usage'] }, '세대 4에서 계정 칩을 끈 선택은 되살아나지 않는다');
  assert.deepStrictEqual(plain(parseHudLayout('{"v":3,"left":["account"],"right":["usage"]}')), { left: ['account'], right: ['usage'] }, '이미 둔 자리는 건드리지 않는다');
  assert.strictEqual(JSON.parse(serializeHudLayout({ left: [], right: ['account'] })).v, 4);
});

// 계정 칩 — 기본 계정 이름 + 가장 많이 쓴 창 사용률. 계정이 없으면 칩이 없다.
const ACC = (over = {}) => ({
  ok: true,
  profiles: [
    { id: 'a1', name: 'alice', tool: 'claude', auth: 'ok', selectable: true, reason: null,
      usage: { windows: [{ id: 'five_hour', label: '5h', pct: 23.4, resetsInSec: 11400 }, { id: 'seven_day', label: 'weekly', pct: 9, resetsInSec: 90000 }], checkedAt: 1000, stale: false, source: 'statusline' } },
    { id: 'b1', name: 'work', tool: 'codex', auth: 'ok', selectable: true, reason: null,
      usage: { windows: [{ id: 'primary', label: '5h', pct: 91, resetsInSec: 600 }], checkedAt: 1000, stale: false, source: 'app-server' } },
    { id: 'c1', name: 'spare', tool: 'claude', auth: 'missing', selectable: false, reason: 'not signed in', usage: null },
  ],
  defaults: {},
  ...over,
});

test('계정 칩: 계정이 없거나 목록을 못 받았으면 칩이 없다', async () => {
  const { buildHudChips } = await mod();
  assert.deepStrictEqual(ids(buildHudChips({ accounts: null })), []);
  assert.deepStrictEqual(ids(buildHudChips({ accounts: { ok: false, profiles: [], defaults: {} } })), []);
  assert.deepStrictEqual(ids(buildHudChips({ accounts: { ok: true, profiles: [], defaults: {} } })), []);
});

test('계정 칩: 계정은 있는데 기본이 없으면 「계정 지정 안 함」, 누르면 선택기', async () => {
  const { buildHudChips } = await mod();
  const [chip, ...rest] = Array.from(buildHudChips({ accounts: ACC() }));
  assert.strictEqual(rest.length, 0);
  assert.strictEqual(chip.id, 'account');
  assert.strictEqual(chip.label, '계정 지정 안 함');
  assert.strictEqual(chip.action, 'account.pick');
  assert.strictEqual(chip.caret, true);
  assert.strictEqual(chip.tone, 'plain');
  assert.ok(!chip.value);
});

test('계정 칩: 기본이 하나면 칩 하나 — 이름 · 가장 많이 쓴 창 사용률 · 리셋, 도구 이름은 앞에 안 붙인다', async () => {
  const { buildHudChips } = await mod();
  const [chip, ...rest] = Array.from(buildHudChips({ accounts: ACC({ defaults: { claude: { id: 'a1', valid: true } } }), now: 1000 * 1000 }, { left: [], right: ['account'] }));
  assert.strictEqual(rest.length, 0);
  assert.strictEqual(chip.id, 'account:claude');
  assert.strictEqual(chip.label, 'alice');
  assert.strictEqual(chip.value, '5h 23%');
  assert.strictEqual(chip.hint, '· 3h10m');
  assert.strictEqual(chip.tone, 'plain');
  assert.match(chip.tooltip, /Claude 기본 계정: alice/);
  assert.match(chip.tooltip, /출처 statusline/);
  assert.match(chip.tooltip, /누르면 기본 계정을 바꿉니다/);
});

test('계정 칩: Claude·Codex 둘 다 기본이면 칩 둘 — 도구 이름을 앞에 붙이고 칩마다 자기 사용률 색을 쓴다', async () => {
  const { buildHudChips } = await mod();
  const chips = Array.from(buildHudChips({ accounts: ACC({ defaults: { claude: { id: 'a1', valid: true }, codex: { id: 'b1', valid: true } } }) }));
  assert.deepStrictEqual(chips.map((c) => `${c.id}|${c.label}|${c.value}|${c.tone}|${c.priority}`),
    ['account:claude|Claude alice|5h 23%|plain|4', 'account:codex|Codex work|5h 91%|err|2']);
});

test('계정 칩: 사용률 색은 사용량 칩과 같다 — 75% 주의, 90% 경고, 값이 없으면 이름만', async () => {
  const { buildHudChips } = await mod();
  const withPct = (pct) => {
    const acc = ACC({ defaults: { claude: { id: 'a1', valid: true } } });
    acc.profiles[0].usage.windows = [{ id: 'five_hour', label: '5h', pct, resetsInSec: null }];
    return Array.from(buildHudChips({ accounts: acc }))[0];
  };
  assert.strictEqual(withPct(74.4).tone, 'plain');
  assert.strictEqual(withPct(75).tone, 'warn');
  assert.strictEqual(withPct(89.4).tone, 'warn', '사용량 칩처럼 반올림한 값으로 본다');
  assert.strictEqual(withPct(90).tone, 'err');
  assert.strictEqual(withPct(75).priority, 2, '임박하면 순위가 앞으로 온다');
  const none = ACC({ defaults: { claude: { id: 'a1', valid: true } } });
  none.profiles[0].usage = null;
  const [chip] = Array.from(buildHudChips({ accounts: none }));
  assert.strictEqual(chip.label, 'alice');
  assert.ok(!chip.value && !chip.hint, '값을 지어내지 않는다');
});

test('계정 칩: 기본 계정이 로그인 안 됐거나 사라졌으면 주의 색으로 사유를 적는다', async () => {
  const { buildHudChips } = await mod();
  const [out] = Array.from(buildHudChips({ accounts: ACC({ defaults: { claude: { id: 'c1', valid: true } } }) }));
  assert.deepStrictEqual([out.label, out.value, out.tone], ['spare', '로그인 안 됨', 'warn']);
  const [gone] = Array.from(buildHudChips({ accounts: ACC({ defaults: { claude: { id: 'zzz', valid: false } } }) }));
  assert.deepStrictEqual([gone.label, gone.value, gone.tone], ['알 수 없음', '기본 계정을 찾을 수 없음', 'warn']);
});

test('계정 칩: 배치(account 종류)를 따른다 — 기본은 오른쪽 「사용량」 앞, 배치에서 빼면 사라진다', async () => {
  const { buildHudChips, DEFAULT_HUD_LAYOUT } = await mod();
  const input = { accounts: ACC({ defaults: { claude: { id: 'a1', valid: true } } }), usage: { available: true, profiles: [{ name: 'alice', windows: [{ label: '5h', pct: 10 }] }] } };
  assert.deepStrictEqual(ids(buildHudChips(input)), ['account:claude', 'usage:alice']);
  assert.ok(Array.from(DEFAULT_HUD_LAYOUT.right).includes('account'));
  assert.deepStrictEqual(ids(buildHudChips(input, { left: [], right: ['usage'] })), ['usage:alice']);
});

test('세대 3: 에이전트 칩은 배치 밖 고정 — 옛 배치의 agents는 버리고, 옮길 수 없고, 늘 맨 왼쪽이다', async () => {
  const { parseHudLayout, moveHudKind, stepHudKind, buildHudChips, HUD_KINDS, PINNED_HUD_KINDS } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  assert.deepStrictEqual(Array.from(PINNED_HUD_KINDS), ['agents']);
  assert.ok(!Array.from(HUD_KINDS).includes('agents'), '설정 화면이 옮기는 종류에 없다');
  // 세대 2에서 사용자가 오른쪽 끝으로 옮겼거나 껐던 agents — 읽을 때 버린다(그래도 칩은 맨 왼쪽에 나온다).
  assert.deepStrictEqual(plain(parseHudLayout('{"v":4,"left":["screens"],"right":["cpu","agents"]}')),
    { left: ['screens'], right: ['cpu'] });
  const base = { left: ['screens'], right: ['cpu'] };
  assert.strictEqual(moveHudKind(base, 'agents', 'right', 0), base, '고정 종류는 어떤 경로로도 안 움직인다');
  assert.strictEqual(stepHudKind(base, 'agents', 1), base);
  const chips = buildHudChips({ screens: 1, stats: { cpu: { pct: 5 } }, agents: { waiting: 1, working: 0, done: 0 } },
    { left: ['screens'], right: ['cpu', 'agents'] });
  assert.deepStrictEqual(sides(chips), ['left:agents', 'left:screens', 'right:stats:cpu'], '맨 왼쪽 한 번만');
  // 배치를 전부 꺼도 에이전트 칩은 남는다.
  assert.deepStrictEqual(ids(buildHudChips({ agents: { waiting: 0, working: 2, done: 0 } }, { left: [], right: [] })), ['agents']);
});

test('기본 배치 왼쪽: 에이전트(고정) 다음 E2E · 세이프모드 · 연결된 화면', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({
    caps: {}, e2e: true, safeMode: { enabled: true }, screens: 2, agents: { waiting: 0, working: 1, done: 0 },
  });
  assert.deepStrictEqual(ids(chips), ['agents', 'e2e', 'safe-mode', 'screens']);
});

test('parseHudLayout 은 기본 배치 객체를 공유하지 않는다 (호출자가 바꿔도 기본이 안 변한다)', async () => {
  const { parseHudLayout, DEFAULT_HUD_LAYOUT } = await mod();
  const a = parseHudLayout('');
  a.left.push('server');
  assert.ok(!DEFAULT_HUD_LAYOUT.left.includes('server'));
});

test('serializeHudLayout ∘ parseHudLayout 는 항등이다', async () => {
  const { parseHudLayout, serializeHudLayout } = await mod();
  const raw = serializeHudLayout({ left: ['version', 'cpu'], right: ['usage'] });
  assert.strictEqual(serializeHudLayout(parseHudLayout(raw)), raw);
});

test('moveHudKind: 구역 사이를 옮기고, 원본은 건드리지 않으며, 자리는 범위로 자른다', async () => {
  const { moveHudKind, hudZoneOf } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  const base = { left: ['screens', 'e2e'], right: ['cpu', 'ram'] };
  const moved = moveHudKind(base, 'cpu', 'left', 1);
  assert.deepStrictEqual(plain(moved), { left: ['screens', 'cpu', 'e2e'], right: ['ram'] });
  assert.deepStrictEqual(plain(base), { left: ['screens', 'e2e'], right: ['cpu', 'ram'] }, '입력은 그대로');
  assert.deepStrictEqual(plain(moveHudKind(base, 'cpu', 'right', 99)), { left: ['screens', 'e2e'], right: ['ram', 'cpu'] });
  assert.deepStrictEqual(plain(moveHudKind(base, 'cpu', 'right', -5)), { left: ['screens', 'e2e'], right: ['cpu', 'ram'] });
  const off = moveHudKind(base, 'e2e', 'off');
  assert.strictEqual(hudZoneOf(off, 'e2e'), 'off');
  assert.deepStrictEqual(plain(moveHudKind(off, 'e2e', 'right')), { left: ['screens'], right: ['cpu', 'ram', 'e2e'] });
});

test('stepHudKind: 같은 구역에서 한 칸씩, 끝에서는 그대로, 꺼진 칩은 움직이지 않는다', async () => {
  const { stepHudKind } = await mod();
  const plain = (l) => JSON.parse(JSON.stringify(l));
  const base = { left: ['screens', 'e2e'], right: ['cpu'] };
  assert.deepStrictEqual(plain(stepHudKind(base, 'e2e', -1)), { left: ['e2e', 'screens'], right: ['cpu'] });
  assert.strictEqual(stepHudKind(base, 'screens', -1), base);
  assert.strictEqual(stepHudKind(base, 'cpu', 1), base);
  assert.strictEqual(stepHudKind(base, 'version', 1), base);
});

test('모든 종류는 기본 배치가 정확히 한 번만 배정하거나 비워 둔다 (서버만 비어 있다)', async () => {
  const { HUD_KINDS, DEFAULT_HUD_LAYOUT, hudZoneOf } = await mod();
  const off = Array.from(HUD_KINDS).filter((k) => hudZoneOf(DEFAULT_HUD_LAYOUT, k) === 'off');
  assert.deepStrictEqual(off, ['server']);
  const all = [...DEFAULT_HUD_LAYOUT.left, ...DEFAULT_HUD_LAYOUT.right];
  assert.strictEqual(new Set(all).size, all.length);
});

test('buildHudChips 가 만드는 모든 칩의 kind 는 HUD_KINDS 안에 있다', async () => {
  const { buildHudChips, HUD_KINDS } = await mod();
  const everything = { left: [...HUD_KINDS], right: [] };
  const chips = buildHudChips({
    port: '7777', caps: { version: '2.6.0' }, e2e: true, safeMode: { enabled: true }, screens: 2,
    usage: { available: true, profiles: [{ name: 'a', windows: [{ pct: 1 }] }] },
    stats: { cpu: { pct: 1 }, gpu: { pct: 1 }, ram: { pct: 1 }, net: { rx_bps: 1, tx_bps: 1 } },
  }, everything);
  assert.strictEqual(chips.length, 10, '종류 10개가 전부 한 번씩');
  for (const c of chips) assert.ok(Array.from(HUD_KINDS).includes(c.kind), c.id);
});


test('에이전트 칩: 개입이 있으면 주의 색에 클릭하면 이동하고, 없으면 클릭할 곳이 없다', async () => {
  const { buildHudChips } = await mod();
  const lay = { left: ['agents'], right: [] };
  const hot = buildHudChips({ agents: { waiting: 2, working: 1, done: 1 } }, lay)[0];
  assert.strictEqual(hot.label, '개입');
  assert.strictEqual(hot.value, '2');
  assert.strictEqual(hot.hint, '· 작업 1 · 완료 1', '0인 항목은 적지 않는다');
  assert.strictEqual(hot.tone, 'warn');
  assert.strictEqual(hot.action, 'agents.jump');
  assert.strictEqual(hot.priority, 1, '개입이 필요하면 끝까지 남는다');

  const calm = buildHudChips({ agents: { waiting: 0, working: 3, done: 0 } }, lay)[0];
  assert.strictEqual(calm.label, '작업');
  assert.strictEqual(calm.tone, 'plain');
  assert.strictEqual(calm.action, undefined);
  assert.strictEqual(calm.hint, undefined);
});

test('에이전트 칩: 셋 다 0이거나 정보가 없으면 칩이 없다', async () => {
  const { buildHudChips } = await mod();
  const lay = { left: ['agents'], right: [] };
  assert.deepStrictEqual(ids(buildHudChips({ agents: { waiting: 0, working: 0, done: 0 } }, lay)), []);
  assert.deepStrictEqual(ids(buildHudChips({ agents: null }, lay)), []);
  assert.deepStrictEqual(ids(buildHudChips({}, lay)), []);
});

test('푸시 칩: 켜짐·꺼짐만 그리고, 못 쓰는 브라우저면 칩이 없다', async () => {
  const { buildHudChips } = await mod();
  const lay = { left: [], right: ['push'] };
  const on = buildHudChips({ push: 'on' }, lay)[0];
  assert.strictEqual(on.value, '켜짐');
  assert.strictEqual(on.tone, 'ok');
  assert.strictEqual(on.action, 'push.toggle');
  assert.strictEqual(buildHudChips({ push: 'off' }, lay)[0].tone, 'plain');
  assert.deepStrictEqual(ids(buildHudChips({ push: 'unavailable' }, lay)), []);
  assert.deepStrictEqual(ids(buildHudChips({ push: null }, lay)), []);
});

test('시계 칩: now가 있을 때만, 24시간제로 그린다', async () => {
  const { buildHudChips, formatClock } = await mod();
  const lay = { left: [], right: ['clock'] };
  assert.deepStrictEqual(ids(buildHudChips({}, lay)), []);
  const d = new Date(2026, 10, 3, 14, 5);   // 로컬 시각 기준 — 시간대에 상관없이 14:05
  const chip = buildHudChips({ now: d.getTime(), locale: 'en-US' }, lay)[0];
  assert.ok(/14:05$/.test(chip.value), chip.value);
  assert.ok(/Nov/.test(chip.value) && /3/.test(chip.value), chip.value);
  assert.strictEqual(formatClock(NaN), '');
  assert.ok(/오후|PM/.test(formatClock(d.getTime(), 'ko-KR')) === false, '12시간제(오후/PM)로 새지 않는다');
});

test('새 칩은 모두 순위를 갖는다 (agents·push·clock)', async () => {
  const { buildHudChips } = await mod();
  const chips = buildHudChips({
    agents: { waiting: 1, working: 0, done: 0 }, push: 'on', now: Date.now(),
  }, { left: ['agents'], right: ['push', 'clock'] });
  assert.strictEqual(chips.length, 3);
  for (const c of chips) assert.ok(Number.isInteger(c.priority) && c.priority > 0, `${c.id} 순위`);
});

test('훅 점검 칩: 이상이 있는 CLI만 적고, 정상·absent면 칩이 없다 — 배치 밖이라 배치를 꺼도 남는다', async () => {
  const { buildHudChips, moveHudKind, HUD_KINDS, ALERT_HUD_KINDS } = await mod();
  const none = { left: [], right: [] };
  assert.deepStrictEqual(ids(buildHudChips({ hooks: { claude: { state: 'ok' }, codex: { state: 'absent' } } }, none)), []);
  assert.deepStrictEqual(ids(buildHudChips({ hooks: null }, none)), []);
  const chips = buildHudChips({
    hooks: { claude: { state: 'missing', events: ['Stop'] }, codex: { state: 'unreadable', events: [] } },
    agents: { waiting: 1, working: 0, done: 0 },
    screens: 2,
  }, { left: ['screens'], right: [] });
  assert.deepStrictEqual(sides(chips), ['left:agents', 'left:hooks', 'left:screens']);
  const hooks = chips.find((c) => c.id === 'hooks');
  assert.strictEqual(hooks.label, '훅 점검 필요');
  assert.strictEqual(hooks.value, 'Claude · Codex');
  assert.strictEqual(hooks.tone, 'warn');
  assert.strictEqual(hooks.action, 'settings.section');
  assert.strictEqual(hooks.actionArg, 'about');
  const only = buildHudChips({ hooks: { claude: { state: 'ok' }, codex: { state: 'mismatch', events: ['Stop'] } } }, none);
  assert.strictEqual(only.find((c) => c.id === 'hooks').value, 'Codex');
  const base = { left: ['screens'], right: [] };
  assert.ok(!Array.from(HUD_KINDS).includes('hooks') && Array.from(ALERT_HUD_KINDS).includes('hooks'), '설정 목록에는 없고 경고 종류다');
  assert.strictEqual(moveHudKind(base, 'hooks', 'right', 0), base, '배치 밖 종류는 옮길 수 없다');
});
