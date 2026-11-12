// 세션 로직 점검 단계 6 — 보관함 묶기, 새 세션 메뉴의 저장소 목록, 설정 시트 탭(소스 구조).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
async function mod() { const env = await loadSolidBuild(); _envs.push(env); return env; }
const plain = (v) => JSON.parse(JSON.stringify(v));
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', '..', ...p), 'utf8');

test('보관함 — 저장소(루트 폴더)별로 묶고, 개수 많은 순, 저장소 밖은 맨 아래', async () => {
  const m = await mod();
  const groups = m.groupArchive({
    sessions: [
      { id: 'a', workspace_id: 'WA' }, { id: 'b', workspace_id: 'WB' }, { id: 'c', workspace_id: 'WB' },
      { id: 'd', workspace_id: null }, { id: 'e', workspace_id: 'WD' },
    ],
    workspaces: [{ id: 'WA', project_id: 'PA' }, { id: 'WB', project_id: 'PB' }, { id: 'WD', project_id: 'PD' }],
    projects: [{ id: 'PA', name: 'alpha', path: '/r/alpha', kind: 'git' }, { id: 'PB', name: 'beta', path: '/r/beta', kind: 'git' },
      { id: 'PD', name: 'notes', path: '/r/notes', kind: 'directory' }],
    project_counts: { PA: 1, PB: 7, _none: 2 },
    next: null,
  });
  assert.deepStrictEqual(plain(groups.map((g) => [g.key, g.sessions.map((s) => s.id)])), [['PB', ['b', 'c']], ['PA', ['a']], ['_none', ['d', 'e']]]);
  assert.strictEqual(groups[0].root, '/r/beta');
});

test('새 세션 메뉴 — 세션이 많은 저장소가 먼저, 메인 작업 트리가 그 워크트리보다 먼저, 검색은 이름·브랜치·경로', async () => {
  const m = await mod();
  const list = m.repoChoices({ repos: [
    { name: 'alpha', worktrees: [{ id: 'a1', path: '/r/alpha', branch: 'main', isMain: true, sessions: [] }] },
    { name: 'beta', worktrees: [
      { id: 'b2', path: '/r/beta-wt', branch: 'fix/x', isMain: false, sessions: ['s'] },
      { id: 'b1', path: '/r/beta', branch: 'dev', isMain: true, sessions: ['s', 't'] },
    ] },
  ] });
  assert.deepStrictEqual(plain(list.map((c) => [c.label, c.linked])), [['beta', false], ['beta · 워크트리', true], ['alpha', false]]);
  assert.deepStrictEqual(plain(m.filterChoices(list, 'fix').map((c) => c.key)), ['b2']);
  assert.deepStrictEqual(plain(m.filterChoices(list, '/r/alpha').map((c) => c.key)), ['a1']);
  assert.strictEqual(m.repoChoices(null).length, 0);
});

test('새 세션 메뉴 — 이름은 만들기와 같은 규칙의 미리보기를 묻고, core에는 스텁만 남는다', () => {
  const nsm = read('frontend', 'js', 'shell', 'NewSessionMenu.tsx');
  assert.match(nsm, /\/api\/tmux\/name-preview/);
  const sess = read('frontend', 'js', 'term', 'session.js');
  const fn = sess.slice(sess.indexOf('export function showAddMenu'), sess.indexOf('// 일반(비 tmux) 터미널 세션 생성'));
  assert.ok(fn.length < 2600, '메뉴 본체(항목 그리기)는 core에 없다');
  assert.match(sess, /registerAction\('session\.create-in'/);
});

test('설정 시트 — 저장소 · 보기 · 정리 탭, 정리는 다른 맥을 볼 때 숨기고 바꿀 때 승격', () => {
  const rail = read('frontend', 'js', 'shell', 'Rail.tsx');
  assert.match(rail, /id: 'view', label: t\('보기'\)/);
  assert.match(rail, /viewHostId === LOCAL_HOST \? \[\{ id: 'cleanup', label: t\('정리'\)/);
  const rs = read('frontend', 'js', 'shell', 'RailSettings.tsx');
  assert.doesNotMatch(rs, /저장소별로 묶기|최근 종료 표시 개수/);
  assert.match(rs, /resume_ends_session/);
  assert.match(rs, /auto_name/);
});

test('대화 기록 창은 검색·열람만 — 수집·복제·백업은 설정 › 대화 보관', () => {
  const panel = read('frontend', 'js', 'history', 'panel.js');
  assert.doesNotMatch(panel, /session-history\/settings|replication\/settings|preview\/source/);
  assert.match(panel, /'settings\.section'\)\?\.\('history'\)/);
  const sec = read('frontend', 'js', 'panels', 'settings', 'sections', 'history.js');
  for (const api of ['/api/session-history/settings', '/api/session-history/replication/settings', '/api/session-history/preview/source', "backup: true"]) {
    assert.ok(sec.includes(api), api);
  }
});

test('교차 검토 — 보관함 조건을 바꾸면 커서를 지우고, 다른 맥 보기에서는 이름 미리보기를 묻지 않고, 저장소 탭 패널에 이름이 있다', () => {
  const av = read('frontend', 'js', 'shell', 'ArchiveView.tsx');
  assert.match(av, /const invalidate = \(\) => \{ request\+\+;/);
  assert.match(av, /setQ\(v\); invalidate\(\);/);
  assert.match(av, /setProject\([^;]+\); invalidate\(\);/);
  assert.match(read('frontend', 'js', 'shell', 'NewSessionMenu.tsx'), /if \(p\.deps\.relayed \|\| asked\.has\(key\)\) return;/);
  assert.match(read('frontend', 'js', 'shell', 'RepoVisibility.tsx'), /aria-labelledby=\{\(props\.tabs \|\| \[\]\)\.length \? 'vt-repovis-tab-repos'/);
});
