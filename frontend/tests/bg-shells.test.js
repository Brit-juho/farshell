// 백그라운드 셸(shell/bg-shells.ts · RailRow.tsx ShellsMark·메뉴 요약 · 호버 카드) — 서버 bg_shells.py가 주는 `shells`를 읽는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
let _m = null;
async function mod() { if (!_m) { _m = await loadSolidBuild(); _envs.push(_m); } return _m; }
const NOW = 1_800_000_000_000;
const sec = NOW / 1000;
const raw = {
  checked_at: sec - 60,
  items: [
    { id: 'b1', state: 'running', description: 'e2e 전체 실행', command: 'pytest tests/e2e', started_at: sec - 300, ended_at: null, pending: false },
    { id: 'pending-t1', state: 'running', description: '', command: 'npm run dev', started_at: sec - 5, ended_at: null, pending: true },
    { id: 'b0', state: 'done', description: '빌드', started_at: sec - 900, ended_at: sec - 120, pending: false },
    { id: '', state: 'running' }, { id: 'x', state: 'weird' }, null,
  ],
};
const row = (over = {}) => ({
  sessionId: '', tmuxName: 'farshell', name: 'farshell', open: false, status: 'working', unseen: false,
  since: null, tool: null, kind: null, badge: null, diffFiles: null, origin: 'local', viewLabel: null,
  otherScreens: 0, otherClients: 0, ...over,
});

test('정규화 — 실행 중(오래된 순)·끝남(최근 순), 모양이 틀린 항목은 버린다, 이름은 설명 → 명령줄 → 기본값', async () => {
  const m = await mod();
  const s = m.normalizeShells(raw);
  assert.deepStrictEqual(Array.from(s.running, (x) => x.id), ['b1', 'pending-t1']);
  assert.deepStrictEqual(Array.from(s.ended, (x) => x.id), ['b0']);
  assert.strictEqual(s.checkedAt, sec - 60);
  assert.strictEqual(m.shellLabel(s.running[0]), 'e2e 전체 실행');
  assert.strictEqual(m.shellLabel(s.running[1]), 'npm run dev');
  assert.strictEqual(m.shellLabel({ description: '', command: null }), '백그라운드 셸');
  assert.strictEqual(m.shellCounts(s), '실행 중 2 · 끝남 1');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(m.normalizeShells(undefined))), { running: [], ended: [], checkedAt: null });
  assert.strictEqual(m.normalizeShells({ items: 'x' }).running.length, 0);
});

test('칸 표식 — 실행 중인 셸이 있을 때만 「셸 N」, 이름은 화면 읽기로 읽힌다', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountSessionNode(root, { row: row({ shells: raw }), active: false, geometry: { lane: 1, lines: '' }, onOpen() {}, onContext() {} });
  const mark = root.querySelector('.vt-shells-mark');
  assert.ok(mark, '표식');
  assert.strictEqual(mark.getAttribute('aria-label'), '백그라운드 셸 2개 실행 중');
  assert.strictEqual(mark.textContent.trim(), '2');
  assert.match(mark.getAttribute('data-tip-sub'), /e2e 전체 실행 · npm run dev/);
  const root2 = m.document.createElement('div');
  m.document.body.appendChild(root2);
  m.mountSessionNode(root2, { row: row({ shells: { items: [raw.items[2]], checked_at: sec } }), active: false, geometry: { lane: 1, lines: '' }, onOpen() {}, onContext() {} });
  assert.strictEqual(root2.querySelector('.vt-shells-mark'), null, '끝난 셸만 있으면 표식 없음');
});

test('메뉴 요약 — 「백그라운드 셸」 구역: 개수·마지막 확인·셸별 줄, 설명이 있으면 명령줄을 아래에', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  const summary = m.rowSummaryDetails({ ...row({ shells: raw }) });
  assert.match(summary.accessible, /백그라운드 셸 2개 실행 중/);
  m.mountMenu(root, { x: 10, y: 10, onClose() {}, items: [], summary });
  const sec2 = root.querySelector('.vt-shells-summary');
  assert.ok(sec2);
  assert.match(sec2.textContent, /실행 중 2 · 끝남 1/);
  assert.match(sec2.textContent, /마지막 확인 .+ 전/);
  const rows = Array.from(sec2.querySelectorAll('.vt-shells-row'));
  assert.strictEqual(rows.length, 3);
  assert.strictEqual(rows[0].querySelector('.vt-shells-cmd').textContent, 'pytest tests/e2e');
  assert.strictEqual(rows[1].querySelector('.vt-shells-when').textContent, '확인 전');
  assert.strictEqual(rows[1].querySelector('.vt-shells-cmd'), null, '설명이 없으면 이름이 곧 명령줄 — 두 번 쓰지 않는다');
  assert.ok(rows[2].classList.contains('ended'));
  const none = m.rowSummaryDetails(row());
  assert.strictEqual(none.shells, undefined, '셸이 없으면 구역 없음');
});

test('호버 카드 — 셸 구역은 머리 + 실행 중인 셸, 넘치면 셸 줄을 줄인다', async () => {
  const m = await mod();
  const card = m.buildHoverCard(row({ shells: raw }), { now: NOW });
  const s = card.sections.find((x) => x.id === 'shells');
  assert.ok(s);
  assert.deepStrictEqual(Array.from(s.rows, (r) => r.key), ['shells', 'shell:b1', 'shell:pending-t1']);
  assert.strictEqual(s.rows[0].text, '백그라운드 셸 · 실행 중 2 · 끝남 1');
  assert.strictEqual(s.rows[2].extra, '확인 전');
  const tight = m.buildHoverCard(row({ shells: raw }), { now: NOW, maxLines: 2 });
  const ts = tight.sections.find((x) => x.id === 'shells');
  assert.ok(ts.rows.length <= 3);
});
