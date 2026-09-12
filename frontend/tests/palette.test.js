// N5/N40/N46(60-settings-palette.md §3) — 팔레트 전면 통합 검증.
// 구 quickopen.test.js(vanilla DOM 하네스)를 대체한다. shell/Palette.tsx는
// Solid + deps 주입 구조라 예전처럼 진짜 core/store.js·term/session.js 등을
// vm-esm으로 실행할 필요가 없다 — helpers/solid-env(vite build --mode test)로
// 컴파일한 산출물을 로드하고, deps는 이 파일이 직접 스텁한다(Hud.tsx 계열과
// 같은 테스트 전략, hud.test.js 참고).
//
// 1부: palette-data.ts 순수 로직(접두사 파싱 7종 + 레거시 `>`·fuzzy match·
// 명령 목록 구조). 2부: mountPalette() DOM 마운트 + 상호작용(quickopen.test.js가
// 지키던 시나리오 — 토글 열기/닫기, 세션 클릭 전환, 설정 토글, capability 게이팅,
// 파일 업로드 명령 — 를 deps 스텁으로 재현).
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

// ── 1부: palette-data.ts ────────────────────────────────────────────────

test('parseQuery — 접두사 없음은 default 모드', async () => {
  const { parseQuery } = await mod();
  // cross-realm 객체라 deepStrictEqual 대신 필드별로 비교한다(위 DEFAULT_COMMANDS
  // 테스트와 같은 이유).
  const p = parseQuery('claude');
  assert.strictEqual(p.mode, 'default');
  assert.strictEqual(p.query, 'claude');
  assert.strictEqual(p.raw, 'claude');
});

test('parseQuery — 7개 접두사 + 레거시 `>`가 각자 모드로 매핑된다', async () => {
  const { parseQuery } = await mod();
  assert.strictEqual(parseQuery('/needle').mode, 'file');
  assert.strictEqual(parseQuery('@dev').mode, 'session');
  assert.strictEqual(parseQuery(':search').mode, 'keymap');
  assert.strictEqual(parseQuery('#foo').mode, 'queue');
  assert.strictEqual(parseQuery('!3000').mode, 'port');
  assert.strictEqual(parseQuery('~error').mode, 'scrollback');
  assert.strictEqual(parseQuery('>theme').mode, 'settings');
});

test('parseQuery — 접두사 뒤 공백은 트리밍되고, query에는 접두사가 안 남는다', async () => {
  const { parseQuery } = await mod();
  const p = parseQuery('  /  needle.py  ');
  assert.strictEqual(p.mode, 'file');
  assert.strictEqual(p.query, 'needle.py');
  assert.strictEqual(p.raw, '/  needle.py');
});

test('fuzzyMatch — 빈 질의는 항상 매치, 대소문자 무시 부분 문자열', async () => {
  const { fuzzyMatch } = await mod();
  assert.strictEqual(fuzzyMatch('anything', ''), true);
  assert.strictEqual(fuzzyMatch('Hello World', 'wor'), true);
  assert.strictEqual(fuzzyMatch('Hello World', 'zzz'), false);
});

test('DEFAULT_COMMANDS — 패널 명령(.show/.open) 5개가 rail과 짝지을 action을 갖는다', async () => {
  const { DEFAULT_COMMANDS, panelCommands } = await mod();
  // vm 모듈이 만든 배열은 이 realm의 Array와 생성자가 달라 deepStrictEqual이
  // "구조는 같은데 참조가 다르다"로 실패한다(cross-realm, hud.test.js의 ids()
  // 헬퍼와 같은 문제) — 이 realm 배열로 복사한다.
  const panels = Array.from(panelCommands(DEFAULT_COMMANDS), (c) => c.action).sort();
  assert.deepStrictEqual(panels, ['files.show', 'ports.show', 'queue.show', 'snippets.show', 'usage.open'].sort());
});

// ── 2부: mountPalette() DOM 마운트 ──────────────────────────────────────

// doc: 반드시 mountFresh()가 반환한 **그 테스트의** jsdom document를 넘긴다.
// buildSessionCard 기본 구현이 DOM을 만들어야 하는데, 이 함수는 모듈 최상단
// (테스트 함수 밖)에서 정의돼 있어 bare `document`를 쓰면 Node 프로세스의
// 전역 스코프(정의 안 됨)를 가리킨다 — Solid 이펙트 안에서 그 ReferenceError가
// 조용히 삼켜져(에러 바운더리) "카드가 안 나온다"로만 드러나는 버그를 실제로
// 겪었다(디버깅 기록: 세션이 tmux 라이브 프리뷰 후보인 테스트만 실패했다).
function fakeDeps(doc, overrides = {}) {
  const calls = { switchTo: [], showToast: [], setVtSkin: [], openFileInPane: [] };
  const gated = new Set(overrides.gatedOff || []);
  return {
    calls,
    vtFetch: overrides.vtFetch || (async () => ({})),
    getAction: overrides.getAction || (() => undefined),
    gateOk: (gate) => !gated.has(gate),
    listSessions: overrides.listSessions || (() => []),
    getSession: overrides.getSession || (() => null),
    switchTo: (id) => calls.switchTo.push(id),
    loadViewer: overrides.loadViewer || (async () => ({ _loadRecent: () => [], _hl: (t) => t })),
    openFileInPane: (p) => calls.openFileInPane.push(p),
    splitActivePane: overrides.splitActivePane || (() => 'pane-2'),
    setPaneSession: overrides.setPaneSession || (() => {}),
    buildSessionCard: overrides.buildSessionCard || ((sess, onSelect) => {
      const el = doc.createElement('div');
      el.dataset.name = sess.name;
      el.innerHTML = '<div class="card-title"></div><div class="card-preview"></div>';
      el.querySelector('.card-title').textContent = sess.name;
      el.onclick = onSelect;
      return el;
    }),
    updateSessionCard: overrides.updateSessionCard || (() => {}),
    ensurePreviewWs: overrides.ensurePreviewWs || (() => {}),
    setVtSkin: (skin) => calls.setVtSkin.push(skin),
    listKeymapActions: overrides.listKeymapActions || (() => []),
    invokeKeymapAction: overrides.invokeKeymapAction || (() => {}),
    displayCombo: overrides.displayCombo || ((c) => c),
    showToast: (msg, type, opts) => calls.showToast.push({ msg, type, opts }),
    ...overrides.extra,
  };
}

// DOM 마운트 테스트는 각자 **독립된** jsdom 문서가 필요하다 — mod()의 캐시를
// 공유하면 이전 테스트가 close() 없이 남긴 팔레트 오버레이가 document 전체를
// querySelectorAll하는 다음 테스트에 섞여든다(실제로 재현됨: 세션 'alpha'가
// 엉뚱한 테스트의 결과에 나타났다). loadSolidBuild()를 매번 새로 호출해
// 새 창을 받는다(빌드 산출물 자체는 buildOnce()가 캐싱하므로 비용은 jsdom
// 창 하나뿐).
async function mountFresh() {
  const env = await loadSolidBuild();
  _doms.push(env);
  const { mountPalette, document, window } = env;
  const root = document.createElement('div');
  document.body.appendChild(root);
  return { mountPalette, document, window, root };
}

async function flush() {
  await new Promise((r) => setImmediate(r));
  await new Promise((r) => setImmediate(r));
}

// 고정 setTimeout(N)은 npm test가 모든 *.test.js를 동시에 돌릴 때 CPU 경합으로
// 플레이키해진다(실측 — palette.test.js만 단독 실행하면 항상 통과하는데
// 전체 스위트에서는 가끔 실패했다). 조건이 실제로 참이 될 때까지 짧은 간격으로
// 폴링한다 — 느린 환경에서는 더 기다리고, 빠른 환경에서는 더 빨리 끝난다.
async function waitFor(fn, timeoutMs = 3000, stepMs = 15) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() >= deadline) throw new Error('waitFor timed out');
    await new Promise((r) => setTimeout(r, stepMs));
  }
}

test('open()/close() 토글 — 같은 모드로 다시 열면 닫힌다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const api = mountPalette(root, fakeDeps(document));
  assert.strictEqual(api.isOpen(), false);
  api.open();
  assert.strictEqual(api.isOpen(), true);
  assert.ok(document.getElementById('vt-qo-input'), '입력창이 DOM에 있어야 한다');
  api.open();
  assert.strictEqual(api.isOpen(), false);
  assert.strictEqual(document.getElementById('vt-qo-input'), null);
});

test('접두사 없음 — 세션·명령이 함께 나열되고, capability 게이팅이 적용된다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    listSessions: () => [{ id: 'a', name: 'alpha' }],
    getAction: (name) => (name === 'queue.show' ? () => {} : (name === 'search.toggle' ? () => {} : undefined)),
    gatedOff: ['ports', 'usage', 'voice'],
  });
  const api = mountPalette(root, deps);
  api.open();
  const rows = await waitFor(() => {
    const list = Array.from(document.querySelectorAll('.vt-qo-row, .vt-qo-session-card'));
    return list.some((r) => r.textContent.includes('alpha')) ? list : null;
  });
  assert.ok(rows.some((r) => r.textContent.includes('alpha')), '세션 행이 보여야 한다');
  assert.ok(rows.some((r) => r.textContent.includes('터미널 내 검색')), '명령 행이 보여야 한다');
  assert.ok(!rows.some((r) => r.textContent.includes('포트 대시보드')), 'ports 게이트가 꺼져 있으면 숨어야 한다');
});

test('세션 클릭 — 전환되고 팔레트가 닫힌다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, { listSessions: () => [{ id: 'a', name: 'alpha' }] });
  const api = mountPalette(root, deps);
  api.open();
  // waitFor가 반환한 참조로 바로 클릭하지 않는다 — await로 넘어가는 사이
  // (onMount의 loadViewer/refreshTmux 이펙트가 정리되며) Solid가 이 행의 DOM을
  // 다시 만들면, 들고 있던 참조는 문서에서 떨어져 나가 클릭 이벤트가 document
  // 레벨 위임 리스너까지 버블링하지 못한다(실측 — switchTo가 안 불렸다).
  // await 직후 **그 자리에서 다시 조회**해 항상 살아있는 노드를 클릭한다.
  const findRow = () => Array.from(document.querySelectorAll('.vt-vw-row')).find((r) => r.textContent.trim() === 'alpha');
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '세션 행을 찾아야 한다');
  row.click();
  assert.deepStrictEqual(deps.calls.switchTo, ['a']);
  assert.strictEqual(api.isOpen(), false);
});

test('세션이 tmux 라이브 프리뷰 후보면 카드로 나온다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    listSessions: () => [{ id: 'a', name: 'dev' }],
    vtFetch: async (path) => {
      if (path.includes('/api/tmux/sessions')) return [{ name: 'dev', command: 'claude', web_session_id: 'a' }];
      if (path.includes('/api/agents')) return {};
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open();
  const card = await waitFor(() => document.querySelector('.vt-qo-session-card'));
  assert.ok(card, '라이브 프리뷰 카드가 나와야 한다');
});

test('`/` 접두사 — 최근 파일 + 서버 검색 결과(디바운스 후)', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    loadViewer: async () => ({ _loadRecent: () => ['/repo/old.py'], _hl: (t) => t }),
    vtFetch: async (path) => {
      if (path.includes('/api/fs/search')) return { results: [{ path: '/repo/src/needle.py', name: 'needle.py' }] };
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '/needle';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const rows = await waitFor(() => {
    const list = Array.from(document.querySelectorAll('.vt-qo-row'));
    return list.some((r) => r.textContent.includes('needle.py')) ? list : null;
  });
  assert.ok(rows.some((r) => r.textContent.includes('needle.py')), '서버 검색 결과가 보여야 한다');
});

test('`!` 접두사 — 포트 기능이 꺼져 있으면 안내 문구만 보인다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, { gatedOff: ['ports'] });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '!';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  await waitFor(() => /사용할 수 없는/.test(document.getElementById('vt-qo-body').textContent));
  assert.match(document.getElementById('vt-qo-body').textContent, /사용할 수 없는/);
});

test('`!` 접두사 — 포트 목록을 보여주고, 선택하면 포트 대시보드를 연다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  let portsShown = 0;
  const deps = fakeDeps(document, {
    vtFetch: async (path) => (path.includes('/api/ports') ? { ports: [{ port: 3000, cmd: 'next dev' }] } : {}),
    getAction: (name) => (name === 'ports.show' ? () => { portsShown++; } : undefined),
  });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '!3000';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('3000'));
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '포트 3000 행이 나와야 한다');
  row.click();
  assert.strictEqual(portsShown, 1);
  assert.strictEqual(api.isOpen(), false);
});

test('`#` 접두사 — 큐 항목을 보여주고, 선택하면 큐 패널을 연다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  let queueShown = 0;
  const deps = fakeDeps(document, {
    vtFetch: async (path) => (path.includes('/api/queue') ? { items: [{ id: '1', text: '테스트 지시', status: 'pending' }] } : {}),
    getAction: (name) => (name === 'queue.show' ? () => { queueShown++; } : undefined),
  });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '#테스트';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('테스트 지시'));
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '큐 항목 행이 나와야 한다');
  row.click();
  assert.strictEqual(queueShown, 1);
});

test('`~` 접두사 — 스크롤백 검색 결과(디바운스 후)가 나온다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    vtFetch: async (path) => {
      if (path.includes('/api/search/scrollback')) {
        return { results: [{ session_id: 's1', session_name: 'dev', line_no: 3, line: 'ERROR boom', context_before: ['a'], context_after: ['b'] }] };
      }
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '~ERROR';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const rows = await waitFor(() => {
    const list = Array.from(document.querySelectorAll('.vt-qo-row'));
    return list.some((r) => r.textContent.includes('ERROR boom')) ? list : null;
  });
  assert.ok(rows.some((r) => r.textContent.includes('ERROR boom')), '스크롤백 매치가 보여야 한다');
});

test('`:` 접두사 — 키맵 액션을 검색해 실행한다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  let invoked = null;
  const deps = fakeDeps(document, {
    listKeymapActions: () => [
      { id: 'search', label: '스크롤백 검색', combo: 'Mod+F', passthrough: false, unavailable: false },
      { id: 'palette', label: '커맨드 팔레트', combo: 'Mod+K', passthrough: false, unavailable: false },
    ],
    invokeKeymapAction: (id) => { invoked = id; },
  });
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = ':스크롤백';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('스크롤백 검색'));
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '키맵 행이 나와야 한다');
  row.click();
  assert.strictEqual(invoked, 'search');
});

test('`>` 접두사(레거시 설정) — 테마 칩을 읽어 선택하면 setVtSkin이 불린다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const chip = document.createElement('button');
  chip.className = 'theme-chip';
  chip.dataset.skin = 'farshell';
  chip.textContent = 'FarShell';
  document.body.appendChild(chip);

  const deps = fakeDeps(document);
  const api = mountPalette(root, deps);
  api.open();
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = '> farshell';
  input.dispatchEvent(new document.defaultView.Event('input', { bubbles: true }));
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('FarShell'));
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '테마 행이 나와야 한다');
  row.click();
  assert.deepStrictEqual(deps.calls.setVtSkin, ['farshell']);
});

test('파일 업로드 명령 — file-input을 클릭한다(네이티브 피커 트리거)', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const fileInput = document.createElement('input');
  fileInput.id = 'file-input';
  fileInput.type = 'file';
  document.body.appendChild(fileInput);
  let clicked = false;
  fileInput.addEventListener('click', () => { clicked = true; });

  const api = mountPalette(root, fakeDeps(document));
  api.open();
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('파일 업로드'));
  await waitFor(findRow);
  const row = findRow();
  assert.ok(row, '파일 업로드 행이 나와야 한다');
  row.click();
  assert.ok(clicked, '#file-input이 클릭돼야 한다');
  assert.strictEqual(api.isOpen(), false, '선택하면 팔레트가 닫혀야 한다');
});

test('Tab — 파일/스크롤백 결과를 큐로 보낸다', async () => {
  const { mountPalette, document, window, root } = await mountFresh();
  const queuePosts = [];
  const deps = fakeDeps(document, {
    loadViewer: async () => ({ _loadRecent: () => ['/repo/notes.md'], _hl: (t) => t }),
    vtFetch: async (path, opts) => {
      if (opts && opts.method === 'POST' && path.includes('/api/queue')) {
        queuePosts.push(JSON.parse(opts.body));
        return { ok: true };
      }
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open('file');
  // 부하가 큰 환경(전체 스위트 동시 실행)에서 loadViewer()의 비동기 응답이
  // Tab 입력보다 늦게 도착하면 행이 아직 없어 Tab이 조용히 no-op한다(실측) —
  // 행이 실제로 나타난 뒤에 Tab을 누른다.
  await waitFor(() => Array.from(document.querySelectorAll('.vt-qo-row')).some((r) => r.textContent.includes('notes.md')));
  const kd = window.KeyboardEvent
    ? new window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    : new document.defaultView.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
  document.dispatchEvent(kd);
  await waitFor(() => queuePosts.length > 0);
  assert.deepStrictEqual(queuePosts, [{ text: '/repo/notes.md' }]);
});
