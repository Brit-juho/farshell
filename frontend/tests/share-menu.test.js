// 에이전트 세션 공유 — 행 메뉴 항목 조건·요약줄·실행 브리지 (shell/rail-data.ts).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
const load = async () => { const e = await loadSolidBuild(); _envs.push(e); return e; };
const ids = (items) => items.map((i) => i.id);
// vm 컨텍스트의 객체는 프로토타입이 달라 deepStrictEqual 이 실패한다 — JSON 으로 건너온 값을 비교한다.
const plain = (v) => JSON.parse(JSON.stringify(v));

test('공유 안 된 이 맥의 tmux 행: 「에이전트와 공유…」와 「에이전트 권한…」, 해제·설정은 없다', async () => {
  const { rowMenuItems } = await load();
  const items = rowMenuItems({ open: true, tmuxName: 'dev', share: null }, { share: true });
  assert.ok(ids(items).includes('share'));
  assert.ok(ids(items).includes('agentPerms'));
  assert.ok(!ids(items).includes('shareSettings'));
  assert.ok(!ids(items).includes('unshare'));
  assert.strictEqual(items.find((i) => i.id === 'share').label, '에이전트와 공유…');
});

test('공유된 행: 「공유 설정…」「공유 해제」「에이전트 권한…」, 공유 항목은 없다', async () => {
  const { rowMenuItems } = await load();
  const items = rowMenuItems({ open: false, tmuxName: 'dev', share: { nick: 'dev', mode: 'read' } }, { share: true });
  assert.deepStrictEqual(plain(ids(items).filter((i) => /share|unshare|agentPerms/i.test(i))), ['shareSettings', 'unshare', 'agentPerms']);
  assert.strictEqual(items.find((i) => i.id === 'unshare').label, '공유 해제');
});

test('다른 맥을 보는 화면(share 옵션 없음)·tmux 아닌 행에는 항목이 없다', async () => {
  const { rowMenuItems } = await load();
  const any = (items) => items.some((i) => ['share', 'shareSettings', 'unshare', 'agentPerms'].includes(i.id));
  assert.ok(!any(rowMenuItems({ open: true, tmuxName: 'dev', share: null })), '옵션 없음 = 원격 화면');
  assert.ok(!any(rowMenuItems({ open: true, tmuxName: 'dev', share: null }, { share: false })));
  assert.ok(!any(rowMenuItems({ open: true, tmuxName: null, share: null }, { share: true })));
});

test('kill 이름의 공유 세션에도 공유 해제 항목이 있다', async () => {
  const { rowMenuItems } = await load();
  const items = rowMenuItems({ open: true, tmuxName: 'kill', share: { nick: 'shell', mode: 'read' } }, { share: true });
  assert.ok(ids(items).includes('unshare'));
});

test('서버가 받지 않는 이름(한글 등)은 공유 항목만 빠지고 권한 패널 진입은 남는다', async () => {
  const { rowMenuItems } = await load();
  const items = rowMenuItems({ open: true, tmuxName: '작업', share: null }, { share: true });
  assert.ok(!ids(items).includes('share'));
  assert.ok(ids(items).includes('agentPerms'));
});

test('종료 항목은 그대로 마지막에 남는다', async () => {
  const { rowMenuItems } = await load();
  const items = rowMenuItems({ open: true, tmuxName: 'dev', share: null }, { share: true });
  assert.strictEqual(items[items.length - 1].id, 'kill');
});

test('요약줄에 「공유 · 닉네임 · 읽기 전용/읽기·쓰기」가 들어가고, 공유 아니면 없다', async () => {
  const { rowSummary } = await load();
  const base = { status: 'idle', kind: null, otherClients: 0, origin: 'local', branch: null };
  assert.match(rowSummary({ ...base, share: { nick: 'build', mode: 'read' } }), /공유 · build · 읽기 전용/);
  assert.match(rowSummary({ ...base, share: { nick: 'build', mode: 'readwrite' } }), /공유 · build · 읽기·쓰기/);
  assert.doesNotMatch(rowSummary({ ...base, share: null }), /공유/);
  assert.doesNotMatch(rowSummary(base), /공유/);
});

test('세션 목록 항목의 share 는 규칙을 지킬 때만 행에 실린다(손으로 심은 값 방어)', async () => {
  const { normalizeShare, shareSentence } = await load();
  assert.deepStrictEqual(plain(normalizeShare({ nick: 'a_b-1', mode: 'readwrite' })), { nick: 'a_b-1', mode: 'readwrite' });
  assert.deepStrictEqual(plain(normalizeShare({ nick: 'x', mode: '???' })), { nick: 'x', mode: 'read' });
  assert.strictEqual(normalizeShare({ nick: 'bad nick', mode: 'read' }), null);
  assert.strictEqual(normalizeShare(null), null);
  assert.strictEqual(normalizeShare(undefined), null);
  assert.strictEqual(shareSentence(null), '');
});

test('메뉴 실행: 브리지로 연결된다(세션 이름·현재 공유값을 넘긴다)', async () => {
  const { runRowMenu } = await load();
  const got = [];
  const w = {
    vtShareOpen: (n, s) => got.push(['open', n, s]),
    vtShareUnshare: (n) => got.push(['unshare', n]),
    vtAgentPerms: () => got.push(['perms']),
  };
  const row = { sessionId: '', tmuxName: 'dev', name: 'dev', share: { nick: 'n', mode: 'read' } };
  runRowMenu('shareSettings', row, w, () => {});
  runRowMenu('share', { ...row, share: null }, w, () => {});
  runRowMenu('unshare', row, w, () => {});
  runRowMenu('agentPerms', row, w, () => {});
  assert.deepStrictEqual(plain(got), [
    ['open', 'dev', { nick: 'n', mode: 'read' }], ['open', 'dev', null], ['unshare', 'dev'], ['perms'],
  ]);
});

test('레일·Fleet 행에 공유 표시(ShareMark)와 메뉴 옵션이 연결돼 있고, 원격 화면에서는 끈다', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const read = (p) => fs.readFileSync(path.join(__dirname, '..', '..', p), 'utf8');
  assert.match(read('frontend/js/shell/RailRow.tsx'), /<ShareMark share=\{props\.row\.share\} \/>/);
  assert.match(read('frontend/js/shell/Fleet.tsx'), /<ShareMark share=\{props\.row\.share\} \/>/);
  assert.match(read('frontend/js/shell/Rail.tsx'), /share: viewHostId === LOCAL_HOST/);
  assert.match(read('frontend/js/shell/Fleet.tsx'), /share: viewHostId === LOCAL_HOST/);
  // 보안 상태라 레일 설정에 표시 토글을 만들지 않는다
  assert.doesNotMatch(read('frontend/js/shell/rail-prefs.ts') + read('frontend/js/shell/RailSettings.tsx'), /share/i);
});
