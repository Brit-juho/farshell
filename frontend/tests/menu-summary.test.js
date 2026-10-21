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

test('summary 가 있으면 첫 줄에 비활성 헤더로 나오고 aria 로 읽힌다, 항목 클릭은 그대로 동작한다', async () => {
  const ran = [];
  const { root, env } = await mount({
    summary: '입력 대기 · 승인 요청 · 웹 외 1 · 이 화면에서 만듦 · main',
    items: [{ label: '닫기', run: () => ran.push('close') }],
  });
  const menu = root.querySelector('.vt-menu');
  const first = menu.firstElementChild;
  assert.ok(first.classList.contains('vt-menu-summary'), '맨 위');
  assert.ok(!first.classList.contains('vt-menu-item'), '항목이 아니다(선택 불가)');
  assert.strictEqual(first.getAttribute('role'), 'note');
  assert.match(first.getAttribute('aria-label'), /^요약: 입력 대기 · 승인 요청/);
  assert.strictEqual(first.textContent, '입력 대기 · 승인 요청 · 웹 외 1 · 이 화면에서 만듦 · main');
  first.dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(ran, [], '요약 줄을 눌러도 아무 항목도 실행되지 않는다');
  menu.querySelector('.vt-menu-item').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.deepStrictEqual(ran, ['close']);
});

test('summary 가 없으면 헤더 행도 없다(호스트 메뉴 등 다른 메뉴는 그대로)', async () => {
  const { root } = await mount({ items: [{ label: 'a', run() {} }] });
  assert.strictEqual(root.querySelector('.vt-menu-summary'), null);
});

test('레일과 Fleet 메뉴가 구조화 요약을 공유하고 말줄임 없이 줄바꿈한다(CSS)', () => {
  const read = (p) => fs.readFileSync(path.join(__dirname, '..', '..', p), 'utf8');
  assert.match(read('frontend/js/shell/Rail.tsx'), /summary=\{rowSummaryDetails\(m\(\)\.row\)\}/);
  assert.match(read('frontend/js/shell/Fleet.tsx'), /MenuSummary summary=\{rowSummaryDetails\(row\)\}/);
  const css = read('styles/layers/components.css');
  const m = css.match(/\.vt-menu-summary\s*\{([^}]*)\}/);
  assert.ok(m);
  assert.match(m[1], /white-space:\s*normal/);
  assert.doesNotMatch(m[1], /text-overflow|nowrap/);
});


test('구조화 요약은 상태, 프로젝트, 전체 경로, 브랜치를 분리하고 빈 필드는 숨긴다', async () => {
  const { root } = await mount({ summary: {
    status: '작업 중 · 이 화면에서 만듦', project: 'sample-repo', cwd: '/workspace/sample-repo',
    branch: 'feat/sample', accessible: '작업 중 · /workspace/sample-repo · feat/sample',
  }, items: [{ label: '닫기', run() {} }] });
  assert.strictEqual(root.querySelector('.vt-menu-summary-project').textContent, 'sample-repo');
  assert.strictEqual(root.querySelector('.vt-menu-summary-path').textContent, '/workspace/sample-repo');
  assert.strictEqual(root.querySelector('.vt-menu-summary-branch').textContent, 'feat/sample');
  const empty = await mount({ summary: { status: '대기', accessible: '대기' }, items: [] });
  assert.strictEqual(empty.root.querySelector('.vt-menu-summary-section'), null);
});
