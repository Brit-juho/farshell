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

async function build({ scan = scanPayload(), toggle, group,
                       credData = { creds: [], refs: [] },
                       pluginData = { plugins: [] } } = {}) {
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
    if (opts && opts.method === 'POST' && u.includes('/api/mcp/group')) {
      posts.push({ _url: '/api/mcp/group', ...JSON.parse(opts.body) });
      const r = group || { status: 'ok', changed: 1, results: [] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(r) });
    }
    if (opts && opts.method === 'POST' && u.includes('/api/mcp/tags')) {
      posts.push({ _url: '/api/mcp/tags', ...JSON.parse(opts.body) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    if (u.includes('/api/mcp/plugins')) {
      if (opts && opts.method === 'POST') {
        posts.push({ _url: '/api/mcp/plugins/toggle', ...JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'ok' }) });
      }
      if (pluginData === null) return Promise.reject(new Error('plugins unavailable'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve(pluginData) });
    }
    if (u.includes('/api/mcp/creds')) {
      if (opts && opts.method === 'POST') {
        // vtFetch가 빈 토큰 쿼리(`?`)를 붙인다 — 경로만 남긴다.
        posts.push({ _url: u.replace(/^.*\/api/, '/api').split('?')[0],
                     ...JSON.parse(opts.body) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve(
          { ok: true, cred: { id: 'c1', env: 'FSH_MCP_X_TOKEN' } }) });
      }
      if (credData === null) return Promise.reject(new Error('creds unavailable'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve(credData) });
    }
    if (opts && opts.method === 'POST' && u.includes('/api/mcp/deploy')) {
      posts.push({ _url: '/api/mcp/deploy', ...JSON.parse(opts.body) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        { status: 'ok', changed: true, target: '/x/.claude.json' }) });
    }
    if (u.includes('/api/mcp')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(scan) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  // 모듈은 bare `prompt(...)`를 부른다 — window뿐 아니라 vm 컨텍스트의
  // 전역에도 심어야 잡힌다(jsdom 기본 prompt는 항상 null을 준다).
  const setPrompt = (fn) => { window.prompt = fn; env.context.prompt = fn; };
  setPrompt(() => null);
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  const M = await importFresh(MCP_JS, env.context, cache);
  const host = window.document.createElement('div');
  window.document.body.appendChild(host);
  M.mount(host, 'wt1');
  await flush();
  await flush();
  return { window, document: window.document, host, posts, M, setPrompt };
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

test('Codex MCP 로그인 필요 상태와 명령을 표시한다', async () => {
  const scan = scanPayload();
  scan.groups[0].entries.push({
    name: 'private-mcp', tool: 'codex', scope: 'global', shared: false,
    enabled: true, transport: 'http', auth_status: 'not_logged_in',
    env: [], headers: [], notes: [],
  });
  const { host } = await build({ scan });
  const warning = Array.from(host.querySelectorAll('.vt-mcp-authwarn'))[0];
  assert.equal(warning.textContent, '로그인 필요');
  assert.equal(warning.title, 'codex mcp login private-mcp');
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
  // 모듈은 bare `prompt(...)`를 부른다 — window뿐 아니라 vm 컨텍스트의
  // 전역에도 심어야 잡힌다(jsdom 기본 prompt는 항상 null을 준다).
  const setPrompt = (fn) => { window.prompt = fn; env.context.prompt = fn; };
  setPrompt(() => null);
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


// ── 그룹 태그 (97번 2단계) ─────────────────────────────────────────────────

/** 태그가 붙은 서버 두 개 — 하나는 켬, 하나는 끔(=섞임 상태). */
function taggedScan({ aOn = true, bOn = true } = {}) {
  const mk = (name, enabled) => ({
    name, tool: 'claude', scope: 'global', shared: false,
    enabled, transport: 'stdio', worktree_id: null,
    env: [], headers: [], notes: [],
  });
  const a = mk('alpha', aOn);
  const b = mk('beta', bOn);
  return scanPayload({
    servers: [a, b],
    groups: [{ name: 'alpha', entries: [a] }, { name: 'beta', entries: [b] }],
    tags: { alpha: ['검증용'], beta: ['검증용'] },
    allTags: ['검증용'],
  });
}

test('그룹 칩은 상태를 색이 아니라 글자로도 말한다', async () => {
  const { host } = await build({ scan: taggedScan({ aOn: true, bOn: true }) });
  assert.deepEqual(texts(host, '.vt-mcp-groupchip'), ['검증용 · 전부 켬']);
});

test('일부만 켜져 있으면 「섞임」으로 보여준다 — 켬으로 뭉개지 않는다', async () => {
  const { host } = await build({ scan: taggedScan({ aOn: true, bOn: false }) });
  assert.deepEqual(texts(host, '.vt-mcp-groupchip'), ['검증용 · 섞임']);
  assert.strictEqual(host.querySelector('.vt-mcp-groupchip').dataset.state, 'mixed');
});

test('섞인 상태에서 누르면 전부 **켜기**를 보낸다 (§1-3)', async () => {
  const { host, posts } = await build({ scan: taggedScan({ aOn: true, bOn: false }) });
  host.querySelector('.vt-mcp-groupchip').click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/group');
  assert.deepEqual({ tag: sent.tag, enabled: sent.enabled }, { tag: '검증용', enabled: true });
});

test('전부 켜져 있을 때 누르면 전부 **끄기**를 보낸다', async () => {
  const { host, posts } = await build({ scan: taggedScan({ aOn: true, bOn: true }) });
  host.querySelector('.vt-mcp-groupchip').click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/group');
  assert.strictEqual(sent.enabled, false);
});

test('그룹 요청은 뒤집기가 아니라 목표 상태를 보낸다 — 항목 목록을 안 만든다', async () => {
  // 화면이 멤버를 세어 토글을 여러 번 보내면 부분 실패 때 상태가 갈라진다.
  const { host, posts } = await build({ scan: taggedScan({ aOn: false, bOn: false }) });
  host.querySelector('.vt-mcp-groupchip').click();
  await flush(); await flush();
  assert.strictEqual(posts.filter((p) => p._url === '/api/mcp/group').length, 1);
  assert.strictEqual(posts.filter((p) => String(p._url).includes('toggle')).length, 0);
});

test('부분 실패는 성공으로 뭉개지 않고 실패한 이름을 말한다', async () => {
  const { host, document } = await build({
    scan: taggedScan({ aOn: true, bOn: true }),
    group: {
      status: 'partial', changed: 1,
      results: [{ name: 'alpha', tool: 'claude', status: 'ok', changed: true },
                { name: 'beta', tool: 'claude', status: 'failed', reason: 'x' }],
    },
  });
  host.querySelector('.vt-mcp-groupchip').click();
  await flush(); await flush();
  assert.match(document.body.textContent, /beta/);
  assert.match(document.body.textContent, /다시 눌러도 안전/);
});

test('태그가 하나도 없으면 그룹 줄을 아예 안 그린다', async () => {
  const { host } = await build();   // 기본 payload엔 tags/allTags가 없다
  assert.strictEqual(host.querySelector('.vt-mcp-groupbar'), null);
});

test('태그 칩을 떼면 남은 목록 전체를 보낸다 — 부분 갱신이 아니다', async () => {
  const scan = taggedScan();
  scan.tags.alpha = ['검증용', '앱개발용'];
  scan.allTags = ['검증용', '앱개발용'];
  const { host, posts } = await build({ scan });
  // alpha 서버 박스의 첫 태그 칩(=검증용)을 뗀다.
  host.querySelector('.vt-mcp-tags .vt-mcp-tagchip').click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/tags');
  assert.deepEqual(sent, { _url: '/api/mcp/tags', name: 'alpha', tags: ['앱개발용'] });
});

test('그룹 조작은 CLI 토글 API를 부르지 않는다', async () => {
  const { host, posts } = await build({ scan: taggedScan({ aOn: false, bOn: false }) });
  host.querySelector('.vt-mcp-groupchip').click();
  await flush(); await flush();
  assert.ok(!posts.some((p) => String(p._url || '').includes('toggle')));
});


// ── 자격증명 · 가져오기 (97번 3단계) ───────────────────────────────────────

test('보관된 값은 마스킹으로만 보인다 — 원문은 화면에 다시 안 나타난다', async () => {
  const { host } = await build({
    credData: { creds: [{ id: 'c1', server: 'notion', key: 'NOTION_TOKEN',
                          env: 'FSH_MCP_NOTION_TOKEN', masked: 'sk-1…9f2a' }], refs: [] },
  });
  assert.match(host.textContent, /sk-1…9f2a/);
  assert.match(host.textContent, /FSH_MCP_NOTION_TOKEN/);
});

test('값 보관 요청에 서버·키가 함께 간다', async () => {
  const { host, posts, setPrompt } = await build();
  setPrompt(() => 'sk-typed-by-user');
  const add = Array.from(host.querySelectorAll('.vt-mcp-tagadd'))
    .find((b) => b.textContent === '값 보관');
  add.click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/creds');
  assert.strictEqual(sent.server, 'notion');
  assert.strictEqual(sent.key, 'NOTION_TOKEN');
});

test('가져오기는 보관된 자격증명의 참조만 싣는다 — 값은 안 싣는다', async () => {
  const { host, posts } = await build({
    credData: { creds: [{ id: 'c1', server: 'notion', key: 'NOTION_TOKEN',
                          env: 'FSH_MCP_NOTION_TOKEN', masked: 'sk…a' }], refs: [] },
  });
  const btn = Array.from(host.querySelectorAll('.vt-mcp-tagadd'))
    .find((b) => b.textContent === 'agy 전역');
  btn.click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/deploy');
  assert.deepEqual(sent.env_map, { env: { NOTION_TOKEN: 'FSH_MCP_NOTION_TOKEN' } });
  assert.ok(!JSON.stringify(sent).includes('sk-'), '값이 요청에 실렸다');
});

test('이미 그 도구·스코프에 있으면 가져오기 버튼이 잠긴다', async () => {
  const { host } = await build();   // notion은 claude/global에 이미 있다
  const btn = Array.from(host.querySelectorAll('.vt-mcp-tagadd'))
    .find((b) => b.textContent === 'Claude 전역');
  assert.strictEqual(btn.disabled, true);
});

test('심어둔 참조의 위치를 보여준다 — 회수의 유일한 근거다', async () => {
  const { host } = await build({
    credData: { creds: [], refs: [{ server: 'notion', env: 'FSH_MCP_NOTION_TOKEN',
                                    source: '/home/u/.claude.json', tool: 'claude' }] },
  });
  assert.match(host.textContent, /심어둔 참조/);
  assert.match(host.textContent, /\/home\/u\/\.claude\.json/);
});

test('자격증명 조회가 실패해도 MCP 목록은 그려진다', async () => {
  // 둘은 출처가 다르다 — 한쪽이 죽었다고 다른 쪽을 숨기면 안 된다.
  const { host } = await build({ credData: null });
  assert.ok(texts(host, '.vt-set-label').includes('notion'));
});


// ── opencode · OAuth · 플러그인 (97번 4단계) ───────────────────────────────

test('OAuth 서버는 가져오기 버튼 대신 이유를 보여준다', async () => {
  // 버튼을 그려놓고 누르면 409를 주는 것보다, 왜 안 되는지 먼저 말한다.
  const scan = scanPayload();
  scan.groups[0].entries[0].oauth = true;
  const { host } = await build({ scan });
  assert.match(host.textContent, /OAuth 서버는 다른 스코프로 복제할 수 없습니다/);
  assert.ok(!Array.from(host.querySelectorAll('.vt-mcp-tagadd'))
    .some((b) => b.textContent === 'agy 전역'));
});

test('가져오기 대상에 opencode가 있다', async () => {
  const { host } = await build();
  const labels = Array.from(host.querySelectorAll('.vt-mcp-tagadd')).map((b) => b.textContent);
  assert.ok(labels.includes('opencode 전역'));
});

test('플러그인이 없으면 설치 안내를 보여준다 — 빈 목록을 그냥 두지 않는다', async () => {
  const { host } = await build();
  assert.match(host.textContent, /설치된 플러그인이 없습니다/);
  assert.match(host.textContent, /claude plugin install/);
});

test('플러그인 토글은 이름·스코프를 그대로 보낸다', async () => {
  const { host, posts } = await build({
    pluginData: { plugins: [{ name: 'p@m', plugin: 'p', marketplace: 'm',
                              tool: 'claude', scope: 'global', enabled: true,
                              explicit: true }] },
  });
  const btn = Array.from(host.querySelectorAll('.vt-mcp-toggle'))
    .find((b) => b.textContent.startsWith('p ·'));
  btn.click();
  await flush(); await flush();
  const sent = posts.find((p) => p._url === '/api/mcp/plugins/toggle');
  assert.deepEqual({ name: sent.name, enabled: sent.enabled, scope: sent.scope },
                   { name: 'p@m', enabled: false, scope: 'global' });
});

test('Codex 플러그인은 포함된 스킬과 MCP를 함께 표시한다', async () => {
  const { host } = await build({
    pluginData: { plugins: [{ name: 'bundle@m', plugin: 'bundle', marketplace: 'm',
                              tool: 'codex', scope: 'global', enabled: true,
                              explicit: true, version: '1.2.3', skill_count: 2,
                              skills: ['review', 'ship'], bundles_mcp: true }] },
  });
  assert.match(host.textContent, /Codex/);
  assert.match(host.textContent, /스킬 2/);
  assert.match(host.textContent, /MCP 포함/);
  const skill = Array.from(host.querySelectorAll('.vt-mcp-meta'))
    .find((el) => el.textContent === '스킬 2');
  assert.equal(skill.title, 'review, ship');
});

test('플러그인 조회가 실패해도 MCP 목록은 그려진다', async () => {
  const { host } = await build({ pluginData: null });
  assert.ok(texts(host, '.vt-set-label').includes('notion'));
});
