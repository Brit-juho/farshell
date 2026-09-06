// D13: 그리드 카드 상태 전이 — refreshGrid(세션 목록 → 카드 생성/갱신/제거,
// 빈 상태)를 검증한다. ansiToHtml 자체(XSS 방어)는 ansilex.test.js가 다룬다 —
// 여기서는 카드 DOM의 생성/갱신/제거 전이만 본다.
// (예전에 grid-wiring.test.js를 함께 가리켰으나 그 파일은 이미 삭제되고 없다.
//  참조만 남아 있던 것을 F0에서 정리했다.)
//
// L3 4단계: 전체화면 그리드 뷰(#grid-view/#grid-toggle, toggleGridView)는
// 폐지됐다(ADR-7) — 그 두 테스트는 함께 지웠다. index.html에도 더 이상
// #grid-cards 컨테이너가 없으므로(5단계가 다시 만들 예정), 여기서는 그
// 계약(id="grid-cards")만 흉내 낸 최소 컨테이너를 직접 만들어 붙인다 —
// refreshGrid()가 실제 화면 마크업과 무관하게 카드 렌더 로직만 순수하게
// 검증되도록.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

// 인라인 부팅 스크립트는 걷어내고 마크업만 쓴다 — terminal-lifecycle.test.js와 같은 이유.
const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const ANSILEX_JS = fs.readFileSync(path.join(__dirname, '../js/lib/ansilex.js'), 'utf8');

// F4: grid.js가 agent/{badges,status,preview}.js 3개의 진짜 ES 모듈로 쪼개졌다.
// terminal-lifecycle.test.js와 같은 기법 — import/export 구문만 지우고 classic
// script로 주입한다(전부 같은 전역 렉시컬 환경을 공유하게 되므로, 지워진 import가
// 가리키던 이름은 아래 window 스텁 또는 나란히 주입되는 다른 파일의 top-level
// 선언으로 resolve된다).
function stripEsm(src) {
  return src
    .replace(/^import .*$/gm, '')
    .replace(/^export (async function|function|const|let)/gm, '$1');
}
const AGENT_FILES = ['badges', 'status', 'preview'].map((name) =>
  stripEsm(fs.readFileSync(path.join(__dirname, `../js/agent/${name}.js`), 'utf8')));

class FakeWebSocket {
  constructor(url) { this.url = url; this.readyState = FakeWebSocket.CONNECTING; }
  send() {}
  close() { this.readyState = FakeWebSocket.CLOSED; }
}
FakeWebSocket.CONNECTING = 0; FakeWebSocket.OPEN = 1; FakeWebSocket.CLOSING = 2; FakeWebSocket.CLOSED = 3;

// 브라우저의 CSS.escape 최소 폴필 — 테스트에 쓰는 세션 이름은 영숫자/-/_ 뿐이라
// 완전한 스펙 구현일 필요는 없다.
function cssEscape(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => '\\' + c); }

// refreshGrid가 부르는 ensurePreviewWs가 세션마다 30초 keepalive setInterval을
// 만든다. window.close()로 정리 안 하면 프로세스가 그 타이머들이 다 소진될
// 때까지 붙잡혀 node --test가 30초씩 느려진다 — 파일 끝의 after()에서 한꺼번에
// 닫는다.
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

  // #grid-cards는 더 이상 index.html에 없다(위 헤더 주석) — refreshGrid()가
  // 기대하는 컨테이너 계약만 최소로 흉내 낸다.
  const gridCards = window.document.createElement('div');
  gridCards.id = 'grid-cards';
  window.document.body.appendChild(gridCards);

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
  // F3(a): apiFetch()를 window.fetch 스텁으로 그대로 감싼다.
  window.apiFetch = (...args) => window.fetch(...args);
  // F3(c): badges/status/preview가 각자 끝에서 registerAction()을 부른다 —
  // no-op 스텁으로 충분하다.
  window.registerAction = () => {};
  // F4: preview.js가 term/session.js의 switchTo/addSession을 부른다. 이 테스트는
  // 카드를 실제로 클릭하지 않으므로(attachTmuxSession 경로 미실행) no-op으로 충분.
  window.switchTo = () => {};
  window.addSession = () => {};

  const runScript = (code) => {
    const el = window.document.createElement('script');
    el.textContent = code;
    window.document.body.appendChild(el);
  };
  // F3(b): grid.js가 core/store.js의 getSession()/allSessions()를 부른다.
  // 매 호출 시점의 window.sessions(테스트가 재할당하기도 함)를 그대로 읽도록
  // bare identifier로 감싼 최소 스텁.
  runScript('function getSession(id) { return sessions[id]; } function allSessions() { return sessions; }');
  runScript(ANSILEX_JS);
  for (const src of AGENT_FILES) runScript(src);

  return window;
}

// --- refreshGrid: 컨테이너 부재(L3 4단계 이후의 실제 상태) ---------------------

test('refreshGrid: #grid-cards가 없으면 조용히 아무 것도 안 한다(TypeError 없음)', async () => {
  const window = buildGridWindow({ tmuxSessions: [{ name: 'dev', command: 'claude', cwd: '/repo' }] });
  window.document.getElementById('grid-cards').remove();
  await assert.doesNotReject(window.refreshGrid());
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
