// 97번 1단계 4/n — 설정 →「MCP」 섹션(지연 청크 panels/mcp.js).
//
// 이 화면의 값어치는 목록을 그리는 것보다 **거짓말을 하지 않는 것**에 있다
// (97번 §4). 네 도구 다 설정 파일을 세션 시작 시 한 번만 읽으므로 "껐다"가
// "지금 안 불린다"를 뜻하지 않는데, 토글만 덜렁 그리면 사용자는 그렇게 믿는다.
// 그래서 여기서 고정하는 것은 주로 다음 셋이다:
//   1. env/헤더 **값**이 화면 어디에도 나타나지 않는다
//   2. 반영 시점을 항상 같이 보여주고, **모르는 건 모른다고** 쓴다
//   3. 쓰기 결과가 `unknown`이면 켠 것처럼 보이지 않는다
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const MCP_JS = path.join(__dirname, '../js/panels/mcp.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

const flush = () => new Promise((r) => setImmediate(r));

const FACTS = {
  claude: { hot_reload: false, apply: '다음 세션부터 — 실행 중인 창은 재시작해야 반영된다', off_is_immediate: null },
  agy: { hot_reload: null, apply: '반영 시점이 확인되지 않았다 — 실행 중인 창은 재시작을 권한다', off_is_immediate: null, scopes: ['global'] },
};

function scanPayload(over = {}) {
  return {
    worktree: { id: 'wt1', path: '/repo', label: 'repo/main' },
    facts: FACTS,
    errors: [],
    servers: [],
    groups: [{
      name: 'notion',
      entries: [{
        name: 'notion', tool: 'claude', scope: 'global', shared: false,
        enabled: true, transport: 'stdio', worktree_id: null,
        env: [{ key: 'NOTION_TOKEN', ref: null, literal: true }],
        headers: [], notes: [],
      }],
    }],
    ...over,
  };
}

async function build({ scan = scanPayload(), toggle } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  const posts = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    if (opts && opts.method === 'POST' && u.includes('/api/mcp/toggle')) {
      posts.push(JSON.parse(opts.body));
      const r = toggle || { status: 'ok', changed: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(r) });
    }
    if (u.includes('/api/mcp')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(scan) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  const M = await importFresh(MCP_JS, env.context, cache);
  const host = window.document.createElement('div');
  window.document.body.appendChild(host);
  M.mount(host, 'wt1');
  await flush();
  await flush();
  return { window, document: window.document, host, posts, M };
}

const texts = (host, sel) => Array.from(host.querySelectorAll(sel)).map((n) => n.textContent);


test('목록 — 서버 이름으로 묶어 스코프 토글을 그린다', async () => {
  const { host } = await build();
  assert.ok(texts(host, '.vt-set-label').includes('notion'));
  assert.deepEqual(texts(host, '.vt-mcp-toggle'), ['전역 · 켬']);
  assert.ok(texts(host, '.vt-mcp-tool').includes('Claude'));
});

test('env 값은 화면 어디에도 나타나지 않는다 — 이름만 보여준다', async () => {
  // 서버가 값을 안 내려주는 건 라우트 테스트가 잡는다. 여기서는 화면이
  // 실수로라도 값을 그리지 않는지를 본다(두 겹으로 막는다).
  const scan = scanPayload();
  scan.groups[0].entries[0].env = [{ key: 'NOTION_TOKEN', ref: 'MY_TOKEN', literal: false }];
  const { host } = await build({ scan });
  assert.match(host.textContent, /필요: MY_TOKEN/);
  assert.doesNotMatch(host.textContent, /sk-|Bearer /);
});

test('반영 시점을 항상 같이 보여준다', async () => {
  const { host } = await build();
  assert.match(host.textContent, /Claude: 다음 세션부터/);
});

test('모르는 것은 모른다고 쓴다 — off 즉시성이 null이면 단정하지 않는다', async () => {
  const { host } = await build();
  assert.match(host.textContent, /즉시 적용되는지는 확인되지 않았습니다/);
});

test('파싱 실패는 숨기지 않고, 건드리지 않는다고 알린다', async () => {
  const scan = scanPayload({ errors: [{ source: '/x/.mcp.json', reason: 'JSON 파싱 실패' }] });
  const { host } = await build({ scan });
  assert.match(host.textContent, /JSON 파싱 실패/);
  assert.match(host.textContent, /건드리지 않습니다/);
});

test('저장소 공유 파일임을 표시한다 — 키가 커밋될 수 있는 자리다', async () => {
  const scan = scanPayload();
  scan.groups[0].entries[0].shared = true;
  scan.groups[0].entries[0].notes = ['이 파일은 저장소에 커밋된다 — `${VAR}` 참조를 쓸 것'];
  const { host } = await build({ scan });
  assert.ok(texts(host, '.vt-mcp-meta').includes('저장소 공유'));
  assert.match(host.textContent, /커밋된다/);
});

test('토글 — 목표 상태를 보내고(뒤집기가 아니다) 화면에 반영한다', async () => {
  const { host, posts } = await build();
  host.querySelector('.vt-mcp-toggle').click();
  await flush();
  await flush();
  assert.deepEqual(posts, [{
    tool: 'claude', name: 'notion', enabled: false,
    scope: 'global', shared: false, worktree: 'wt1',
  }]);
  assert.deepEqual(texts(host, '.vt-mcp-toggle'), ['전역 · 끔']);
});

test('unknown — 켠 것처럼 보이면 안 된다', async () => {
  // 썼는지 확인하지 못한 경우다. 여기서 상태를 뒤집어 보여주면 사용자는
  // "껐다"고 믿게 되는데 실제로는 그대로일 수 있다.
  const { host } = await build({ toggle: { status: 'unknown', reason: '확인 실패', changed: null } });
  host.querySelector('.vt-mcp-toggle').click();
  await flush();
  await flush();
  assert.deepEqual(texts(host, '.vt-mcp-toggle'), ['전역 · 켬'], '상태를 바꾸면 안 된다');
});

test('서버가 하나도 없으면 공식 추가 방법을 안내한다', async () => {
  const { host } = await build({ scan: scanPayload({ groups: [] }) });
  assert.match(host.textContent, /claude mcp add/);
});

test('읽기 실패를 조용히 빈 화면으로 두지 않는다', async () => {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  window.fetch = () => Promise.resolve({
    ok: false, status: 500, json: () => Promise.resolve({ error: 'scan_failed' }),
  });
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  const M = await importFresh(MCP_JS, env.context, cache);
  const host = window.document.createElement('div');
  window.document.body.appendChild(host);
  M.mount(host, null);
  await flush();
  await flush();
  assert.match(host.textContent, /읽지 못했습니다/);
});
