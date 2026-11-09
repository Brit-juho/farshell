// 파일 내용 검색(파일 개편 단계 6) — grep-data.ts 순수 로직(접두어 파싱·요청 주소·결과 묶기·강조 조각·상태 문구)과
// 팔레트 `?` 모드·사이드바 「내용 검색」 마운트. 서버 계약은 server/tests/test_fs_grep.py가 본다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });

let _mod = null;
async function mod() {
  if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); }
  return _mod;
}
async function fresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  return env;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
async function waitFor(fn, timeoutMs = 3000, stepMs = 15) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() >= deadline) throw new Error('waitFor timed out');
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

// ── 1부: 순수 로직 ───────────────────────────────────────────────────────────────

test('parseQuery — `?`는 내용 검색 모드, 접두어 뒤 공백은 떼고, 빈 질의도 모드는 유지된다', async () => {
  const { parseQuery, MODE_PREFIX, PREFIX_MODE, PLACEHOLDER, MODE_HINT } = await mod();
  const p = parseQuery('? groupByZone');
  assert.strictEqual(p.mode, 'grep');
  assert.strictEqual(p.query, 'groupByZone');
  assert.strictEqual(parseQuery('?').mode, 'grep');
  assert.strictEqual(parseQuery('?').query, '');
  // 「?」로 시작하는 글자는 한 번 더 쓴다(시안 Q2 「가」).
  assert.strictEqual(parseQuery('? ?.').query, '?.');
  assert.strictEqual(MODE_PREFIX.grep, '?');
  assert.strictEqual(PREFIX_MODE['?'], 'grep');
  assert.ok(PLACEHOLDER.includes('? 내용') && MODE_HINT.includes('? 내용'));
});

test('globParam — 포함·제외를 서버 `glob` 한 값으로, 중괄호 안 쉼표는 나누지 않는다', async () => {
  const { splitGlobs, globParam } = await mod();
  assert.deepStrictEqual(plain(splitGlobs('frontend/**, *.{ts,tsx}\n docs/ ,')), ['frontend/**', '*.{ts,tsx}', 'docs/']);
  assert.strictEqual(globParam('frontend/**, *.{ts,tsx}', '**/*.test.*, !docs/'), 'frontend/**,*.{ts,tsx},!**/*.test.*,!docs/');
  assert.strictEqual(globParam('', ''), '');
});

test('grepUrl — 기본은 글자 그대로, 옵션·범위·sid를 쿼리로', async () => {
  const { grepUrl, GREP_DEFAULTS } = await mod();
  const u = new URL(`http://x${grepUrl('a b&c', { ...GREP_DEFAULTS }, '', 's1')}`);
  assert.strictEqual(u.pathname, '/api/fs/grep');
  assert.deepStrictEqual([u.searchParams.get('q'), u.searchParams.get('mode'), u.searchParams.has('path'), u.searchParams.get('sid')], ['a b&c', 'literal', false, 's1']);
  const v = new URL(`http://x${grepUrl('x(', { regex: true, case: true, word: true, include: 'src/**', exclude: '*.md' }, '/r')}`);
  assert.deepStrictEqual([v.searchParams.get('mode'), v.searchParams.get('case'), v.searchParams.get('word'), v.searchParams.get('glob'), v.searchParams.get('path')],
    ['regex', '1', '1', 'src/**,!*.md', '/r']);
});

test('groupByFile — 파일별로 묶고 처음 나온 순서를 지키며, 평탄 목록의 시작 위치를 붙인다', async () => {
  const { groupByFile } = await mod();
  const h = (path, line) => ({ path, rel: path.slice(3), line, col: 1, text: 'x', ranges: [[0, 1]] });
  const { groups, flat } = groupByFile([h('/r/a/b.ts', 1), h('/r/c.md', 4), h('/r/a/b.ts', 9)]);
  assert.deepStrictEqual(plain(groups.map((g) => [g.name, g.dir, g.hits.map((x) => x.line), g.start])), [['b.ts', 'a/', [1, 9], 0], ['c.md', '', [4], 2]]);
  assert.deepStrictEqual(plain(flat.map((x) => x.line)), [1, 9, 4]);
});

test('segments — 일치를 조각으로, 겹치거나 벗어난 범위는 정리하고, 앞 공백을 걷으면 범위를 당긴다', async () => {
  const { segments } = await mod();
  const s = (text, ranges, trim) => plain(segments(text, ranges, trim)).map((x) => (x.hit ? `[${x.text}]` : x.text)).join('');
  assert.strictEqual(s('x needle y needle', [[2, 8], [11, 17]]), 'x [needle] y [needle]');
  assert.strictEqual(s('abcdef', [[1, 4], [2, 5], [9, 12]]), 'a[bcd][e]f');
  assert.strictEqual(s('    return x', [[11, 12]], true), 'return [x]');
  assert.strictEqual(s('', []), '');
});

test('summary·skippedLine — 기한·한도는 경고로 먼저 말하고, 거부 경로 수는 말하지 않는다', async () => {
  const { summary, skippedLine } = await mod();
  const r = (n) => Array.from({ length: n }, (_, i) => ({ path: `/r/${i % 2}`, rel: `${i % 2}`, line: i, col: 1, text: '', ranges: [] }));
  assert.strictEqual(summary({ results: r(8), files: 4, elapsed_ms: 812 }).text, '결과 8 · 파일 4 · 0.8초');
  assert.strictEqual(summary({ results: [], total: 812, elapsed_ms: 900 }).text, '파일 812개 검색 · 0.9초');
  const to = summary({ results: r(3), timeout: true, searched: 3120, total: 9804 });
  assert.strictEqual(to.tone, 'warn');
  assert.ok(to.text.includes('3120 / 9804'));
  assert.strictEqual(summary({ results: r(500), limit: 'results', elapsed_ms: 2100 }).text, '결과 500개에서 멈췄습니다 · 2.1초');
  assert.strictEqual(skippedLine({ skipped: { large: 3, binary: 12, excluded: 41, denied: 7 } }), '건너뜀: 2 MB 넘는 파일 3 · 바이너리 12 · 제외 패턴 41');
  assert.strictEqual(skippedLine({ skipped: { denied: 7 } }), '');
});

// ── 2부: 팔레트 `?` 모드 ──────────────────────────────────────────────────────────

const RESULT = {
  root: '/r', results: [
    { path: '/r/a.ts', rel: 'a.ts', line: 2, col: 5, text: 'let needle = 1', ranges: [[4, 10]] },
    { path: '/r/sub/b.md', rel: 'sub/b.md', line: 7, col: 1, text: 'needle here', ranges: [[0, 6]] },
  ], files: 2, total: 10, searched: 10, skipped: { binary: 1 }, elapsed_ms: 120, limit: null, timeout: false,
};

function paletteDeps(doc, fetchImpl) {
  const calls = { fetch: [], openFile: [], openFileInPane: [], sidebar: [] };
  return {
    calls,
    vtFetch: async (path, opts) => {
      if (!path.startsWith('/api/fs/grep')) return {};
      calls.fetch.push({ path, opts });
      return fetchImpl(path, opts);
    },
    getAction: () => undefined, gateOk: () => true, listSessions: () => [], getSession: () => null,
    switchTo: () => {}, loadViewer: async () => ({ _loadRecent: () => [] }),
    openFile: (p, o) => calls.openFile.push({ p, o }), openFileInPane: (p, o) => calls.openFileInPane.push({ p, o }),
    splitActivePane: () => 'pane-2', setPaneSession: () => {},
    buildSessionCard: () => doc.createElement('div'), updateSessionCard: () => {},
    setVtSkin: () => {}, vtSkins: () => [], vtSkinLabel: (s) => s,
    listKeymapActions: () => [], invokeKeymapAction: () => {}, displayCombo: (c) => c, showToast: () => {},
    host: 'm5',
    openSidebarSearch: (q, o) => calls.sidebar.push({ q, o: { ...o } }),
  };
}

async function openGrep(fetchImpl) {
  const env = await fresh();
  const root = env.document.createElement('div');
  env.document.body.appendChild(root);
  const deps = paletteDeps(env.document, fetchImpl);
  const api = env.mountPalette(root, deps);
  api.open('grep');
  await waitFor(() => env.document.getElementById('vt-qo-input'));
  const input = env.document.getElementById('vt-qo-input');
  assert.strictEqual(input.value, '? ', 'open("grep")은 「? 」로 연다');
  const type = (v) => { input.value = v; input.dispatchEvent(new env.window.Event('input', { bubbles: true })); };
  const key = (k, extra = {}) => env.document.dispatchEvent(new env.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra }));
  return { env, api, deps, type, key, input };
}

test('팔레트 `?` — 디바운스 뒤 /api/fs/grep을 부르고 파일별로 묶어 그린다, ↓ ↵로 그 줄을 그 호스트로 연다', async () => {
  const { env, api, deps, type, key } = await openGrep(async () => RESULT);
  type('? needle');
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row').length === 2);
  const call = deps.calls.fetch.find((c) => c.path.startsWith('/api/fs/grep'));
  const u = new URL(`http://x${call.path}`);
  assert.deepStrictEqual([u.searchParams.get('q'), u.searchParams.get('mode')], ['needle', 'literal']);
  assert.ok(u.searchParams.get('sid'), '탭마다 sid를 보낸다');
  assert.ok(call.opts.signal && call.opts.timeout >= 15000, '취소 signal과 서버 기한보다 긴 마감');
  const files = Array.from(env.document.querySelectorAll('.vt-gr-file')).map((f) => f.querySelector('.vt-gr-fn').textContent);
  assert.deepStrictEqual(files, ['a.ts', 'b.md']);
  assert.strictEqual(env.document.querySelector('.vt-gr-row mark').textContent, 'needle');
  assert.ok(env.document.querySelector('.vt-gr-prog').textContent.includes('결과 2 · 파일 2'));
  assert.ok(env.document.querySelector('.vt-gr-skip').textContent.includes('바이너리 1'));
  assert.ok(env.document.querySelector('.vt-qo-card-grep'), '카드가 내용 검색 크기로 바뀐다');
  key('ArrowDown');
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row')[1].classList.contains('on'));
  key('Enter');
  assert.deepStrictEqual(plain(deps.calls.openFile), [{ p: '/r/sub/b.md', o: { line: 7, col: 1, host: 'm5' } }]);
  assert.strictEqual(api.isOpen(), false, '열면 팔레트가 닫힌다');
});

test('팔레트 `?` — ⌥R은 정규식으로 다시 찾고, 정규식 오류는 이유와 「검색하지 않았습니다」를 보인다', async () => {
  const { env, deps, type, key } = await openGrep(async (path) => {
    if (path.includes('mode=regex')) {
      const e = new Error('정규식 오류 · 8번째 글자: missing )');
      e.status = 400; e.data = { error: 'bad_regex', reason: '정규식 오류 · 8번째 글자: missing )', pos: 7 };
      throw e;
    }
    return RESULT;
  });
  type('? groupBy(Zone');
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row').length === 2);
  key('r', { altKey: true, code: 'KeyR' });
  await waitFor(() => env.document.querySelector('.vt-gr-prog.err'));
  assert.ok(deps.calls.fetch.some((c) => c.path.includes('mode=regex')));
  assert.strictEqual(env.document.querySelector('.vt-gr-opt[aria-pressed="true"]').getAttribute('aria-label'), '정규식');
  assert.ok(env.document.querySelector('.vt-gr-prog').textContent.includes('8번째 글자'));
  assert.ok(env.document.querySelector('.vt-gr-empty').textContent.includes('검색하지 않았습니다'));
});

test('팔레트 `?` — 503은 「지금은 검색할 수 없습니다」와 다시 시도, 파일 이름 검색 안내', async () => {
  let n = 0;
  const { env, type } = await openGrep(async () => {
    n += 1;
    if (n === 1) {
      const e = new Error('busy'); e.status = 503; e.data = { error: 'grep_unavailable', reason: '검색 프로세스를 띄우지 못했습니다' };
      throw e;
    }
    return RESULT;
  });
  type('? needle');
  await waitFor(() => env.document.querySelector('.vt-gr-prog.err'));
  assert.ok(env.document.querySelector('.vt-gr-prog').textContent.includes('지금은 검색할 수 없습니다'));
  assert.ok(env.document.querySelector('.vt-gr-empty').textContent.includes('파일 이름 검색(/)'));
  const retry = Array.from(env.document.querySelectorAll('.vt-gr-prog button')).find((b) => b.textContent.includes('다시 시도'));
  retry.click();
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row').length === 2);
});

test('팔레트 `?` — 새 입력은 진행 중 요청을 끊고(abort), ⌥↵는 같은 질의·옵션을 사이드바로 넘긴다', async () => {
  const pending = [];
  const { env, deps, type, key, api } = await openGrep((path, opts) => new Promise((resolve, reject) => {
    pending.push({ path, opts });
    opts.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
    if (!path.includes('q=slow')) resolve(RESULT);
  }));
  type('? slow');
  await waitFor(() => pending.length === 1);
  assert.ok(env.document.querySelector('.vt-gr-bar'), '진행 막대');
  type('? fast');
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row').length === 2);
  assert.strictEqual(pending[0].opts.signal.aborted, true, '이전 요청은 끊겼다');
  key('w', { altKey: true, code: 'KeyW' });
  key('Enter', { altKey: true });
  assert.deepStrictEqual(plain(deps.calls.sidebar), [{ q: 'fast', o: { regex: false, case: false, word: true, include: '', exclude: '' } }]);
  assert.strictEqual(api.isOpen(), false);
});

test('팔레트 `?` — 진행 중 「취소」를 누르면 요청을 끊는다', async () => {
  const pending = [];
  const { env, type } = await openGrep((path, opts) => new Promise((resolve, reject) => {
    pending.push(opts);
    opts.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
  }));
  type('? needle');
  const cancel = await waitFor(() => Array.from(env.document.querySelectorAll('.vt-gr-prog button')).find((b) => b.textContent.includes('취소')));
  cancel.click();
  assert.strictEqual(pending[0].signal.aborted, true);
  await waitFor(() => !env.document.querySelector('.vt-gr-bar'));
});

// ── 3부: 사이드바 「파일 › 내용 검색」 ─────────────────────────────────────────────────

test('사이드바 — 대기 중인 요청(⌘⇧F)이 있으면 「내용 검색」으로 열고, 범위는 트리 루트, 결과를 그 줄로 연다', async () => {
  const env = await fresh();
  const { window, document } = env;
  const calls = { fetch: [], open: [] };
  window.vtFetch = async (p, o) => { calls.fetch.push({ p, o }); return RESULT; };
  window.openFile = (p, o) => calls.open.push({ p, o });
  window.__vtFilesSearchPending = { q: 'needle', opts: { case: true }, focus: true };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mounted = env.mountFileTree(host, {
    host: 'local', remote: false,
    fetchJSON: async () => ({ entries: [], total: 0, next: null }), watch: () => () => {},
    fetchHostLabel: async () => 'Mac', openFile: () => {}, insertPath: () => true, copyPath: async () => true, sharePath: () => {},
    terminalCwd: async () => null, sessionName: () => '', recent: () => [], clearRecent: () => {}, toast: () => {},
    getDense: () => false, subscribeSettings: () => () => {},
  });
  await mounted.store.open('/r');
  assert.strictEqual(window.__vtFilesSearchPending, null, '요청을 꺼내 썼다');
  const tabs = Array.from(host.querySelectorAll('.vt-fs-seg .vt-segment'));
  assert.deepStrictEqual(tabs.map((b) => b.getAttribute('aria-selected')), ['false', 'true']);
  await waitFor(() => host.querySelectorAll('.vt-gr-row').length === 2);
  const u = new URL(`http://x${calls.fetch[0].p}`);
  assert.deepStrictEqual([u.searchParams.get('q'), u.searchParams.get('case'), u.searchParams.get('path')], ['needle', '1', '/r']);
  host.querySelectorAll('.vt-gr-row')[0].click();
  assert.deepStrictEqual(plain(calls.open), [{ p: '/r/a.ts', o: { line: 2, col: 5, host: 'local' } }]);
  // 파일 머리를 누르면 그 파일의 줄을 접는다.
  host.querySelector('.vt-gr-file').click();
  await waitFor(() => host.querySelectorAll('.vt-gr-row').length === 1);
  // 「트리」로 돌아가면 트리가 그대로 있다(검색 결과는 숨겨질 뿐).
  tabs[0].click();
  await waitFor(() => tabs[0].getAttribute('aria-selected') === 'true');
  assert.ok(host.querySelector('.vt-ft'));
  // 이미 열린 사이드바에는 이벤트로 — 다시 「내용 검색」으로 바뀐다.
  window.dispatchEvent(new window.CustomEvent('vt:files-search', { detail: { q: 'other' } }));
  await waitFor(() => tabs[1].getAttribute('aria-selected') === 'true');
  await waitFor(() => calls.fetch.some((c) => c.p.includes('q=other')));
  mounted.dispose();
});

// ── 4부: 진행 · 「결과가 오래됨」 ─────────────────────────────────────────────────────

test('changeTouches — 검색 범위 안의 폴더가 바뀌었을 때만, 목록이 없거나 넘치면 언제나', async () => {
  const { changeTouches } = await mod();
  assert.strictEqual(changeTouches('/r', '/r', { dirs: ['sub'] }), true);
  assert.strictEqual(changeTouches('/r', '/r', { dirs: [] }), false);
  assert.strictEqual(changeTouches('/r', '', { dirs: [''] }), true, '시작 루트 전부를 찾았으면 트리 안 어디든');
  assert.strictEqual(changeTouches('/r', '/r/sub', { dirs: ['sub/deep'] }), true);
  assert.strictEqual(changeTouches('/r', '/r/sub', { dirs: ['subway', 'other'] }), false, '이름 앞부분만 같은 폴더는 아니다');
  assert.strictEqual(changeTouches('/r', '/elsewhere', { dirs: ['x'] }), false);
  assert.strictEqual(changeTouches('/r', '/r', { overflow: true }), true);
  assert.strictEqual(changeTouches('/r', '/r', { reconnect: true }), true);
  assert.strictEqual(changeTouches('/r', '/r', undefined), true);
});

test('팔레트 `?` — 오래 걸리는 검색은 진행(읽은 파일 / 후보)을 물어 보이고 막대를 채운다', async () => {
  let release = null;
  const prog = [];
  const { env, type } = await openGrep((path) => {
    if (path.startsWith('/api/fs/grep/progress')) {
      prog.push(path);
      return { running: true, total: 40, searched: 12 };
    }
    return new Promise((resolve) => { release = () => resolve(RESULT); });
  });
  type('? needle');
  const line = await waitFor(() => {
    const el = env.document.querySelector('.vt-gr-prog');
    return el && el.textContent.includes('파일 12 / 40') ? el : null;
  }, 4000);
  assert.ok(new URL(`http://x${prog[0]}`).searchParams.get('sid'), '같은 sid로 묻는다');
  const bar = line.querySelector('.vt-gr-bar.det');
  assert.ok(bar, '후보 수를 알면 채워지는 막대');
  assert.strictEqual(bar.style.width, '30%');
  const asked = prog.length;
  release();
  await waitFor(() => env.document.querySelectorAll('.vt-gr-row').length === 2);
  await new Promise((r) => setTimeout(r, 700));
  assert.strictEqual(prog.length, asked, '끝나면 더 묻지 않는다');
});

test('팔레트 `?` — 진행 경로가 없는 옛 서버(404)면 묻기를 그만두고 걸린 시간만 보인다', async () => {
  let release = null;
  const prog = [];
  const { env, type } = await openGrep((path) => {
    if (path.startsWith('/api/fs/grep/progress')) {
      prog.push(path);
      const e = new Error('nf'); e.status = 404; throw e;
    }
    return new Promise((resolve) => { release = () => resolve(RESULT); });
  });
  type('? needle');
  await waitFor(() => prog.length === 1, 4000);
  await new Promise((r) => setTimeout(r, 700));
  assert.strictEqual(prog.length, 1);
  assert.match(env.document.querySelector('.vt-gr-prog').textContent, /검색 중 · \d+\.\d초/);
  assert.ok(env.document.querySelector('.vt-gr-bar:not(.det)'));
  release();
});

test('사이드바 — 결과가 온 뒤 트리 감시가 범위 안 변경을 알리면 「결과가 오래됨 · 다시 찾기」, 누르면 다시 찾고 지운다', async () => {
  const env = await fresh();
  const { window, document } = env;
  const calls = [];
  let hold = null;
  window.vtFetch = async (p) => {
    if (p.startsWith('/api/fs/grep/progress')) return { running: false };
    calls.push(p);
    if (hold) await hold;
    return RESULT;
  };
  window.openFile = () => {};
  window.__vtFilesSearchPending = { q: 'needle' };
  const watchers = [];
  const host = document.createElement('div');
  document.body.appendChild(host);
  const mounted = env.mountFileTree(host, {
    host: 'local', remote: false,
    fetchJSON: async () => ({ entries: [], total: 0, next: null }),
    watch: (kind, p, fn) => { const w = { kind, p, fn, off: false }; watchers.push(w); return () => { w.off = true; }; },
    fetchHostLabel: async () => 'Mac', openFile: () => {}, insertPath: () => true, copyPath: async () => true, sharePath: () => {},
    terminalCwd: async () => null, sessionName: () => '', recent: () => [], clearRecent: () => {}, toast: () => {},
    getDense: () => false, subscribeSettings: () => () => {},
  });
  await mounted.store.open('/r');
  await waitFor(() => host.querySelectorAll('.vt-gr-row').length === 2);
  const live = () => watchers.filter((w) => !w.off && w.kind === 'tree' && w.p === '/r');
  await waitFor(() => live().length >= 2, 2000);   // 트리 + 검색(같은 키 — 서버 구독은 하나)
  const fire = (detail) => { for (const w of live()) w.fn(detail); };
  assert.strictEqual(host.querySelector('.vt-gr-stale'), null);
  fire({ dirs: [] });
  await new Promise((r) => setTimeout(r, 20));
  assert.strictEqual(host.querySelector('.vt-gr-stale'), null, '보이는 폴더 변경이 없으면 띄우지 않는다');
  fire({ dirs: ['sub'] });
  const banner = await waitFor(() => host.querySelector('.vt-gr-stale'));
  assert.match(banner.textContent, /결과가 오래됨/);
  const n = calls.length;
  banner.querySelector('button').click();
  await waitFor(() => calls.length === n + 1);
  await waitFor(() => !host.querySelector('.vt-gr-stale'));
  // 찾는 동안 바뀐 것도 결과가 오면 오래됨으로 띄운다.
  let open = null;
  hold = new Promise((r) => { open = r; });
  (await waitFor(() => host.querySelector('.vt-gr-acts button'))).click();   // 결과 머리의 「다시 찾기」
  await waitFor(() => host.querySelector('.vt-gr-bar'));
  fire({ overflow: true });
  hold = null;
  open();
  await waitFor(() => host.querySelector('.vt-gr-stale'));
  mounted.dispose();
  assert.ok(watchers.every((w) => w.off), '닫으면 감시를 푼다');
});
