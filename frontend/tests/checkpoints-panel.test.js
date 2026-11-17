// dock 소스컨트롤의 「에이전트 체크포인트」 절 — 순수 규칙(묶기·태그·문구·상태)과 DOM(켜기 안내·목록·턴 하위 화면).
// 서버 쪽은 server/tests/test_checkpoints_routes.py가 맡는다. 여기서는 가짜 fetch로 화면만 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const CK_JS = path.join(__dirname, '../js/panels/viewer/checkpoints.js');
const SCM_JS = path.join(__dirname, '../js/panels/viewer/scm.js');
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(fetchImpl) {
  const env = createDomEnv('<!doctype html><html><body><div id="host"></div></body></html>');
  _doms.push(env.dom);
  const w = env.window;
  w.API_BASE = ''; w.WS_BASE = 'ws://x'; w.VT_TOKEN = '';
  const calls = [];
  w.fetch = async (url, opts = {}) => {
    calls.push({ url: String(url), method: (opts.method || 'GET'), body: opts.body });
    const r = await fetchImpl(String(url), opts);
    const st = r.status || 200;
    return { ok: st < 400, status: st, headers: { get: () => null }, json: async () => r.body };
  };
  const mod = await importFresh(CK_JS, env.context, new Map());
  return { mod, calls, host: w.document.getElementById('host'), w };
}

const row = (o) => ({ id: 'v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000001', kind: 'turn-end', turn: 1, seq: 1, created: 1_000_000, skipped: 0, trusted: true, preTrusted: true, ...o });

test('sessionGroups — turn-end만, 최신 턴부터, 세션은 최근 순, 라벨은 에이전트·tmux', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  const g = mod.sessionGroups([
    { id: 'a', agent: 'claude', last: 10, checkpoints: [row({ seq: 1, turn: 0, kind: 'baseline' }), row({ seq: 2, turn: 1 }), row({ seq: 4, turn: 2 }), row({ seq: 3, turn: 2, kind: 'pre-turn' })] },
    { id: 'b', agent: 'codex', tmux: 'work', last: 99, checkpoints: [row({ seq: 1, turn: 1 })] },
    { id: 'c', agent: 'claude', last: 5, checkpoints: [row({ seq: 1, kind: 'baseline', turn: 0 })] },
  ]);
  assert.deepStrictEqual([...g.map((x) => x.id)], ['b', 'a']);                      // 턴 기록이 없는 세션 c는 뺀다
  assert.deepStrictEqual([...g[1].turns.map((c) => c.seq)], [4, 2]);
  assert.strictEqual(g[0].label, 'codex · work');
  assert.strictEqual(mod.sessionGroups(null).length, 0);
});

test('sessionGroups — 라벨의 세션 이름은 별칭(모르면 ID)', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  const sessions = [{ id: 'b', agent: 'codex', tmux: 's-1a2b3c4d', last: 9, checkpoints: [row({ seq: 1, turn: 1 })] }];
  assert.strictEqual(mod.sessionGroups(sessions, (id) => (id === 's-1a2b3c4d' ? '배포' : id))[0].label, 'codex · 배포');
  assert.strictEqual(mod.sessionGroups(sessions)[0].label, 'codex · s-1a2b3c4d', '지도가 없으면 ID');
});

test('sessionGroups — 한 세션에 많으면 30개까지만 보이고 나머지 수를 센다', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  const cps = Array.from({ length: 35 }, (_, i) => row({ seq: i + 1, turn: i + 1 }));
  const [g] = mod.sessionGroups([{ id: 'a', agent: 'claude', last: 1, checkpoints: cps }]);
  assert.strictEqual(g.turns.length, 30); assert.strictEqual(g.more, 5); assert.strictEqual(g.turns[0].seq, 35);
});

test('turnTags — HEAD 이동·일부 제외·턴 사이 변경 포함 가능, 모두 낱말로', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  assert.strictEqual(mod.turnTags(row({})).length, 0);
  const all = mod.turnTags(row({ skipped: 2, preTrusted: false, summary: { headMoved: true } }));
  assert.deepStrictEqual([...all.map((x) => x.text)], ['HEAD 이동', '일부 제외', '턴 사이 변경 포함 가능']);
  assert.ok(all.every((x) => x.text && x.tip));
});

test('turnLines — 「턴 N · 시각」과 「파일 N개 · +a −d」, 요약이 없으면 둘째 줄은 비운다', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  const now = 1_000_000_000 + 3 * 60 * 1000;
  const l = mod.turnLines(row({ turn: 7, created: 1_000_000, summary: { files: 4, added: 20, deleted: 3 } }), now);
  assert.strictEqual(l.first, '턴 7 · 3분'); assert.strictEqual(l.second, '파일 4개 · +20 −3');
  assert.strictEqual(mod.turnLines(row({ turn: 7, created: 1_000_000 }), now).second, '');
  assert.match(mod.turnLines(row({ turn: null, created: 0 }), now).first, /^턴 \?$/);
});

test('sectionState — 서버가 꺼졌거나 못 읽으면 숨기고, 저장소 아님·꺼짐·훅 없음·비어 있음·목록을 가른다', async () => {
  const { mod } = await load(async () => ({ body: {} }));
  assert.strictEqual(mod.sectionState(null, false), 'hidden');
  assert.strictEqual(mod.sectionState({ error: 'disabled' }, false), 'hidden');
  assert.strictEqual(mod.sectionState({ repo: false }, false), 'norepo');
  assert.strictEqual(mod.sectionState({ repo: true, enabled: false }, true), 'off');
  assert.strictEqual(mod.sectionState({ repo: true, enabled: true, sessions: [] }, true), 'nohook');
  assert.strictEqual(mod.sectionState({ repo: true, enabled: true, sessions: [] }, false), 'empty');
  assert.strictEqual(mod.sectionState({ repo: true, enabled: true, sessions: [{}] }, false), 'list');
});

test('꺼진 저장소 — 안내와 「이 저장소에서 켜기」만 보이고, 누르면 PUT 한 번 뒤 다시 읽는다', async () => {
  let enabled = false;
  const { mod, host, calls } = await load(async (url, opts) => {
    if (url.includes('/api/checkpoints/settings')) { enabled = JSON.parse(opts.body).enabled; return { body: { ok: true, enabled } }; }
    if (url.includes('/api/checkpoints')) return { body: { repo: true, enabled, sessions: [] } };
    if (url.includes('/api/agents/coverage')) return { body: [{ cli: 'claude', path: 'hook' }] };
    return { body: {} };
  });
  const reload = async () => mod.renderCheckpoints(host, '/r/proj', { reload });
  await reload();
  assert.match(host.textContent, /에이전트 체크포인트/);
  assert.match(host.textContent, /숨은 기록으로 남깁니다/);
  assert.strictEqual(host.querySelector('.vt-ckpt-row'), null);
  const btn = [...host.querySelectorAll('button')].find((b) => b.textContent === '이 저장소에서 켜기');
  assert.ok(btn);
  btn.click();
  await new Promise((r) => setTimeout(r, 20));
  const puts = calls.filter((c) => c.method === 'PUT');
  assert.strictEqual(puts.length, 1);
  assert.deepStrictEqual(JSON.parse(puts[0].body), { repo: '/r/proj', enabled: true });
  assert.match(host.textContent, /아직 기록이 없습니다/);                       // 켠 뒤 다시 읽었다
  assert.ok(host.querySelector('.vt-ckpt-off'), '끄기 버튼이 생긴다');
});

test('켠 저장소 — 세션 묶음과 턴 행(두 줄·태그)이 보이고, 훅이 없으면 안내가 뜬다', async () => {
  const body = { repo: true, enabled: true, sessions: [{ id: 'a', agent: 'claude', tmux: 'dev', last: 5, checkpoints: [
    row({ id: 'v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000002', seq: 2, turn: 2, created: Math.floor(Date.now() / 1000) - 120, preTrusted: false, skipped: 1,
      summary: { files: 2, added: 5, deleted: 1, headMoved: false } }),
  ] }] };
  const { mod, host } = await load(async (url) => {
    if (url.includes('/api/agents/coverage')) return { body: [{ cli: 'claude', path: 'pty' }, { cli: 'codex', path: 'pty' }] };
    return { body };
  });
  await mod.renderCheckpoints(host, '/r/proj', {});
  assert.match(host.textContent, /fsh hooks install/);
  assert.match(host.querySelector('.vt-ckpt-sess').textContent, /claude · dev/);
  const r = host.querySelector('.vt-ckpt-row');
  assert.match(r.textContent, /턴 2/); assert.match(r.textContent, /파일 2개 · \+5 −1/);
  assert.deepStrictEqual([...r.querySelectorAll('.vt-ptag')].map((x) => x.textContent), ['일부 제외', '턴 사이 변경 포함 가능']);
  assert.ok(r.classList.contains('two') && r.classList.contains('vt-prow'));
});

test('서버가 꺼졌거나(404 disabled) 읽지 못하면 절이 아예 없다', async () => {
  const { mod, host } = await load(async () => ({ status: 404, body: { error: 'disabled' } }));
  await mod.renderCheckpoints(host, '/r/proj', {});
  assert.strictEqual(host.children.length, 0);
});

test('턴 하위 화면 — 파일 목록(+/−·바이너리)과 신뢰 못 한 시작 경고, 파일을 누르면 그 파일 diff', async () => {
  const id = 'v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000002';
  const { mod, host, calls } = await load(async (url) => {
    if (url.includes('&file=')) return { body: { diff: 'diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n x\n+y\n', truncated: false } };
    return { body: { from: 'f', to: id, head_moved: true, files: [{ path: 'src/a.py', added: 1, deleted: 0, binary: false }, { path: 'img.png', added: 0, deleted: 0, binary: true }] } };
  });
  let backed = 0;
  await mod.renderTurn(host, '/r/proj', row({ id, turn: 2, preTrusted: false }), { onBack: () => { backed++; } });
  assert.match(host.textContent, /턴 2/);
  assert.match(host.textContent, /변경된 파일/);
  assert.match(host.textContent, /믿을 수 없어/);
  assert.match(host.textContent, /HEAD가 움직여/);
  const rows = [...host.querySelectorAll('.vt-prow')];
  assert.deepStrictEqual([...rows.map((x) => x.querySelector('.lab').textContent)], ['+1 −0', '바이너리']);
  host.querySelector('.vt-psub .vt-vw-cback').click();
  assert.strictEqual(backed, 1);
  rows[0].click();
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(calls.some((c) => c.url.includes('file=src%2Fa.py') && c.url.includes(encodeURIComponent(id))));
  assert.match(host.textContent, /a\.py/);
});

test('턴 화면에서 기다리는 사이 뒤로 가서 다른 화면이 그려지면, 늦게 온 응답이 그 화면을 덮지 않는다', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const { mod, host } = await load(async () => { await gate; return { body: { files: [{ path: 'a.py', added: 1, deleted: 0, binary: false }] } }; });
  const p = mod.renderTurn(host, '/r/proj', row({ turn: 2 }), { onBack: () => {} });
  host.replaceChildren();                                     // 바깥이 다른 화면(목록)을 그렸다
  host.textContent = '목록 화면';
  release();
  await p;
  assert.strictEqual(host.textContent, '목록 화면');
});

test('배선 — scm.js가 이 절을 상태 본문 아래에 달고, checkpoints_changed로 이 절만 다시 읽고, 닫을 때 구독을 푼다', () => {
  const src = fs.readFileSync(SCM_JS, 'utf8');
  assert.match(src, /from '\.\/checkpoints\.js'/);
  assert.match(src, /onWorkspaceEvent\('checkpoints_changed'/);
  assert.match(src, /offCk\(\)/);
  const ck = fs.readFileSync(CK_JS, 'utf8');
  assert.doesNotMatch(ck, /#[0-9a-fA-F]{3,8}\b/, '색은 토큰만(계약 9)');
  assert.doesNotMatch(ck, /innerHTML\s*=\s*[`'"][^`'"]*\$\{/, '동적 문자열을 innerHTML에 넣지 않는다');
});
