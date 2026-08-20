// D13: 그리드 카드 상태 전이 — toggleGridView(그리드/터미널 뷰 전환)와
// refreshGrid(세션 목록 → 카드 생성/갱신/제거, 빈 상태)를 실제 index.html 마크업
// 위에서 검증한다. ansiToHtml 자체(XSS 방어)는 ansilex.test.js가, grid.js가 그
// 함수를 올바르게 참조하는지는 grid-wiring.test.js가 이미 다룬다 — 여기서는 카드
// DOM의 생성/갱신/제거 전이만 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

// 인라인 부팅 스크립트는 걷어내고 마크업만 쓴다 — terminal-lifecycle.test.js와 같은 이유.
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const ANSILEX_JS = fs.readFileSync(path.join(__dirname, '../js/ansilex.js'), 'utf8');
const GRID_JS = fs.readFileSync(path.join(__dirname, '../js/grid.js'), 'utf8');

class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = FakeWebSocket.CONNECTING; }
  send() {}
  close() { this.readyState = FakeWebSocket.CLOSED; }
}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

// 브라우저의 CSS.escape 최소 폴필 — 테스트에 쓰는 세션 이름은 영숫자/-/_ 뿐이라
// 완전한 스펙 구현일 필요는 없다.
function cssEscape(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c); }

// grid.js는 connectAgentWs()가 최상위에서 즉시 불려 ensurePreviewWs 등에서 30초
// keepalive setInterval을 만든다. window.close()로 정리 안 하면 프로세스가 그
// 타이머들이 다 소진될 때까지 붙잡혀 node --test가 30초씩 느려진다 — 파일 끝의
// after()에서 한꺼번에 닫는다.
const _windows = [];
after(() => { for (const w of _windows) { try { w.close(); } catch (_) {} } });

function buildGridWindow({ tmuxSessions = [], agents = {} } = {}) {
  const dom = new JSDOM(INDEX_HTML, { url: 'http://localhost/', runScripts: 'dangerously' });
  const { window } = dom;
  _windows.push(window);

  window.API_BASE = '';
  window.WS_BASE = 'ws://localhost';
  window.CSS = { escape: cssEscape };
  window.WebSocket = FakeWebSocket;
  window.sessions = {};
  window._tokenQuery = '';

  const state = { tmuxSessions, agents };
  window.__setTmuxSessions = (list) => { state.tmuxSessions = list; };
  window.fetch = (url) => {
    const ok = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    const u = String(url);
    if (u.includes('/api/tmux/sessions')) return ok(state.tmuxSessions);
    if (u.includes('/api/agents')) return ok(state.agents);
    if (u.includes('/api/safe-mode')) return ok({ enabled: false });
    if (u.includes('/api/capabilities')) return ok({});
    return ok({});
  };

  const runScript = (code) => {
    const el = window.document.createElement('script');
    el.textContent = code;
    window.document.body.appendChild(el);
  };
  runScript(ANSILEX_JS);
  runScript(GRID_JS);

  return window;
}

// --- toggleGridView -----------------------------------------------------------

test('toggleGridView: 켜면 grid-view가 보이고 terminal-container는 숨는다', async () => {
  const window = buildGridWindow({ tmuxSessions: [] });
  await window.toggleGridView();

  assert.strictEqual(window.document.getElementById('grid-view').style.display, 'block');
  assert.strictEqual(window.document.getElementById('terminal-container').style.display, 'none');
  assert.ok(window.document.getElementById('grid-toggle').classList.contains('active'));
});

test('toggleGridView: 다시 누르면 원상복구되고 grid-toggle의 active가 빠진다', async () => {
  const window = buildGridWindow({ tmuxSessions: [] });
  await window.toggleGridView();
  await window.toggleGridView();

  assert.strictEqual(window.document.getElementById('grid-view').style.display, 'none');
  assert.strictEqual(window.document.getElementById('terminal-container').style.display, '');
  assert.ok(!window.document.getElementById('grid-toggle').classList.contains('active'));
});

// --- refreshGrid: 빈 상태 ------------------------------------------------------

test('refreshGrid: tmux 세션이 없으면 빈 상태 안내를 렌더링한다', async () => {
  const window = buildGridWindow({ tmuxSessions: [] });
  await window.refreshGrid();

  const cards = window.document.getElementById('grid-cards');
  assert.ok(cards.querySelector('.vt-grid-empty'));
  assert.strictEqual(cards.children.length, 1);
});

// --- refreshGrid: 카드 생성/갱신 -----------------------------------------------

test('refreshGrid: 세션마다 카드를 하나씩 만들고 이름/명령을 채운다', async () => {
  const window = buildGridWindow({
    tmuxSessions: [
      { name: 'dev', command: 'claude', cwd: '/repo' },
      { name: 'ops', command: 'vim', cwd: '/srv' },
    ],
  });
  await window.refreshGrid();

  const cards = window.document.getElementById('grid-cards');
  const names = Array.from(cards.querySelectorAll('.vt-card')).map((c) => c.dataset.name).sort();
  assert.deepStrictEqual(names, ['dev', 'ops']);

  const devCard = cards.querySelector('[data-name="dev"]');
  assert.strictEqual(devCard.querySelector('.card-title').textContent, 'dev');
  assert.strictEqual(devCard.querySelector('.card-cmd').textContent, 'claude');
});

test('refreshGrid: 이미 탭으로 열려 있는 세션은 open-tab 클래스가 붙는다', async () => {
  const window = buildGridWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo', web_session_id: 'w1' }],
  });
  window.sessions = { w1: { term: {} } }; // 이미 탭으로 열려 있는 상태를 흉내

  await window.refreshGrid();

  const card = window.document.querySelector('[data-name="dev"]');
  assert.ok(card.classList.contains('open-tab'));
});

test('refreshGrid: 재호출 시 이미 있는 카드는 재사용하고(중복 생성 X) 값만 갱신한다', async () => {
  const window = buildGridWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo' }],
  });
  await window.refreshGrid();
  const firstCardEl = window.document.querySelector('[data-name="dev"]');

  window.__setTmuxSessions([{ name: 'dev', command: 'claude --continue', cwd: '/repo' }]);
  await window.refreshGrid();

  const cards = window.document.querySelectorAll('[data-name="dev"]');
  assert.strictEqual(cards.length, 1, '같은 세션명이면 카드가 새로 추가되지 않고 재사용돼야 한다');
  assert.strictEqual(cards[0], firstCardEl, '같은 DOM 엘리먼트를 재사용해야 한다(재생성 X)');
  assert.strictEqual(cards[0].querySelector('.card-cmd').textContent, 'claude --continue');
});

test('refreshGrid: 사라진 세션의 카드는 다음 새로고침에서 제거된다', async () => {
  const window = buildGridWindow({
    tmuxSessions: [
      { name: 'dev', command: 'claude', cwd: '/repo' },
      { name: 'ops', command: 'vim', cwd: '/srv' },
    ],
  });
  await window.refreshGrid();
  assert.strictEqual(window.document.querySelectorAll('.vt-card').length, 2);

  window.__setTmuxSessions([{ name: 'dev', command: 'claude', cwd: '/repo' }]);
  await window.refreshGrid();

  const remaining = Array.from(window.document.querySelectorAll('.vt-card')).map((c) => c.dataset.name);
  assert.deepStrictEqual(remaining, ['dev']);
});

test('refreshGrid: 에이전트 배지 정보를 카드에 반영한다', async () => {
  const window = buildGridWindow({
    tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo' }],
    agents: { dev: { icon: '🤖', label: 'Claude Code' } },
  });
  await window.refreshGrid();

  const badge = window.document.querySelector('[data-name="dev"] .card-agent');
  assert.strictEqual(badge.textContent, '🤖');
  assert.strictEqual(badge.getAttribute('title'), 'Claude Code');
});
