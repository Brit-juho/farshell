// 행 메뉴의 맨 위 요약 줄(shell/RailRow.tsx Menu `summary`) — 선택 불가 헤더 행이고 aria 로 읽힌다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });

async function mount(props) {
  const env = await loadSolidBuild();
  _envs.push(env);
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  env.mountMenu(root, { x: 10, y: 10, onClose: () => {}, ...props });
  return { root, env };
}

const sessionRow = (over = {}) => ({
  sessionId: 's1', tmuxName: 'farshell', name: 'farshell', open: true, status: 'waiting', kind: 'approval', unseen: false,
  since: null, tool: null, badge: null, diffFiles: null, origin: 'local', originHost: null, viewLabel: null,
  otherScreens: 0, otherClients: 0, cwd: '/Users/alice/code/acme/tools/farshell', branch: 'dev',
  changed: { files: 2, add: 12, del: 3 }, ...over,
});

test('summary(카드 모델)가 있으면 첫 줄에 선택 불가 헤더로 나오고 aria 로 읽힌다, 항목 클릭은 그대로 동작한다', async () => {
  const ran = [];
  const env0 = await loadSolidBuild(); _envs.push(env0);
  const model = env0.buildHoverCard(sessionRow(), { commands: true });
  const { root, env } = await mount({ summary: model, items: [{ label: '닫기', run: () => ran.push('close') }] });
  const menu = root.querySelector('.vt-menu');
  const first = menu.firstElementChild;
  assert.ok(first.classList.contains('vt-menu-summary'), '맨 위');
  assert.ok(!first.classList.contains('vt-menu-item'), '항목이 아니다(선택 불가)');
  assert.strictEqual(first.getAttribute('role'), 'note');
  assert.match(first.getAttribute('aria-label'), /^요약: 입력 대기 · 승인 요청 · ~\/code\/acme\/tools\/farshell · dev/);
  assert.ok(first.querySelector('.vt-hc-chip'), '상태 칩');
  assert.strictEqual(first.querySelector('.vt-hc-head'), null, '이름·경과 머리는 메뉴를 연 칸에 이미 있어 빼다');
  first.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(ran, [], '요약을 눌러도 아무 항목도 실행되지 않는다');
  menu.querySelector('.vt-menu-item').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(ran, ['close']);
});

test('summary 가 없으면 헤더 행도 없다(호스트 메뉴 등 다른 메뉴는 그대로)', async () => {
  const { root } = await mount({ items: [{ label: 'a', run() {} }] });
  assert.strictEqual(root.querySelector('.vt-menu-summary'), null);
});

test('레일·Fleet 메뉴와 접힌 띠 호버 카드가 한 모델·한 그리기를 쓴다, 글자는 골라 복사할 수 있다(CSS)', () => {
  const read = (p) => fs.readFileSync(path.join(__dirname, '..', '..', p), 'utf8');
  assert.match(read('frontend/js/shell/Rail.tsx'), /summary=\{summaryOf\(m\(\)\.row\)\}/);
  assert.match(read('frontend/js/shell/Rail.tsx'), /buildHoverCard\(row, \{[^}]*commands: true/);
  assert.match(read('frontend/js/shell/Fleet.tsx'), /MenuSummary model=\{summaryOf\(row\)\}/);
  assert.match(read('frontend/js/shell/RailRow.tsx'), /<CardBody model=\{props\.model\} \/>/);
  assert.doesNotMatch(read('frontend/js/shell/rail-data.ts'), /rowSummaryDetails/, '옛 요약 함수는 없앴다');
  const css = read('styles/layers/components.css');
  const m = css.match(/\.vt-menu-summary\s*\{([^}]*)\}/);
  assert.ok(m);
  assert.match(m[1], /user-select:\s*text/);
  assert.match(m[1], /width:\s*300px/);
  assert.doesNotMatch(css, /\.vt-menu-summary-(project|path|branch|label)/, '옛 요약 칸 스타일은 없앴다');
});

test('메뉴 요약 — 위치는 ~로 시작하는 한 줄(길면 가운데를 줄이고 전체 경로는 title), 브랜치 옆 +N −N, 빈 정보는 줄이 없다', async () => {
  const env0 = await loadSolidBuild(); _envs.push(env0);
  const { root } = await mount({ summary: env0.buildHoverCard(sessionRow(), { commands: true }), items: [] });
  const rows = Array.from(root.querySelectorAll('.vt-menu-summary .vt-hc-row'));
  const dir = rows.find((r) => r.dataset.key === 'dir');
  assert.strictEqual(dir.querySelector('.vt-hc-text').textContent, '~/code/acme/tools/farshell');
  assert.strictEqual(dir.querySelector('.vt-hc-val').getAttribute('title'), '/Users/alice/code/acme/tools/farshell');
  const br = rows.find((r) => r.dataset.key === 'branch');
  assert.strictEqual(br.querySelector('.vt-hc-text').textContent, 'dev');
  assert.match(br.querySelector('.vt-hc-extra').textContent, /\+12\s*−3/);
  assert.ok(!root.textContent.includes('farshell/Users'), '이름과 절대 경로를 두 번 쓰지 않는다');
  const bare = await mount({ summary: env0.buildHoverCard(sessionRow({ status: 'idle', kind: null, cwd: null, branch: null, changed: null }), {}), items: [] });
  assert.strictEqual(bare.root.querySelector('.vt-menu-summary .vt-hc-row'), null, '유휴·위치 없음이면 줄이 없다');
});

test('메뉴 요약에만 세션 ID 한 줄(흐린 고정폭, 골라 복사) — 호버 카드에는 없다, 넘쳐도 자르지 않는다', async () => {
  const env0 = await loadSolidBuild(); _envs.push(env0);
  const row = sessionRow({ tmuxName: 's-1a2b3c4d', name: '배포' });
  const { root } = await mount({ summary: env0.buildHoverCard(row, { commands: true, showId: true }), items: [] });
  const id = root.querySelector('.vt-menu-summary .vt-hc-row[data-key="id"]');
  assert.ok(id, 'ID 줄');
  assert.strictEqual(id.querySelector('.vt-hc-text').textContent, 'ID · s-1a2b3c4d');
  assert.ok(id.classList.contains('muted'));
  assert.ok(id.querySelector('.vt-hc-text').classList.contains('mono'));
  const card = env0.buildHoverCard(row, {});
  assert.ok(!card.sections.some((s) => s.rows.some((r) => r.key === 'id')), '호버 카드(showId 없음)에는 없다');
  const tight = env0.buildHoverCard(sessionRow({ tmuxName: 's-1', otherScreens: 2, unseen: true, share: null }), { showId: true, shareable: true, maxLines: 1 });
  assert.ok(tight.sections.some((s) => s.rows.some((r) => r.key === 'id')), '줄 상한에 걸려도 ID는 남는다');
  const read = (p) => fs.readFileSync(path.join(__dirname, '..', '..', p), 'utf8');
  assert.match(read('frontend/js/shell/Rail.tsx'), /buildHoverCard\(row, \{[^}]*showId: true/);
  assert.match(read('frontend/js/shell/Fleet.tsx'), /commands: true, showId: true/);
  assert.doesNotMatch(read('frontend/js/shell/RailHoverCard.tsx'), /showId/, '접힌 띠 호버 카드는 ID를 싣지 않는다');
});
