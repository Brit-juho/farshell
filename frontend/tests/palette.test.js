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

test('DEFAULT_COMMANDS — 패널 명령(.show/.open) 8개가 활동 막대의 도구 활동과 짝지을 action을 갖는다', async () => {
  const { DEFAULT_COMMANDS, panelCommands } = await mod();
  // vm 모듈이 만든 배열은 이 realm의 Array와 생성자가 달라 deepStrictEqual이
  // "구조는 같은데 참조가 다르다"로 실패한다(cross-realm, hud.test.js의 ids()
  // 헬퍼와 같은 문제) — 이 realm 배열로 복사한다.
  const panels = Array.from(panelCommands(DEFAULT_COMMANDS), (c) => c.action).sort();
  assert.deepStrictEqual(panels, ['dir.show', 'files.show', 'ports.show', 'queue.show', 'scm.show', 'snippets.show', 'system.open', 'usage.open'].sort());
});

// ── 2부: mountPalette() DOM 마운트 ──────────────────────────────────────

// doc: 반드시 mountFresh()가 반환한 **그 테스트의** jsdom document를 넘긴다.
// buildSessionCard 기본 구현이 DOM을 만들어야 하는데, 이 함수는 모듈 최상단
// (테스트 함수 밖)에서 정의돼 있어 bare `document`를 쓰면 Node 프로세스의
// 전역 스코프(정의 안 됨)를 가리킨다 — Solid 이펙트 안에서 그 ReferenceError가
// 조용히 삼켜져(에러 바운더리) "카드가 안 나온다"로만 드러나는 버그를 실제로
// 겪었다(디버깅 기록: 세션이 tmux 라이브 프리뷰 후보인 테스트만 실패했다).
function fakeDeps(doc, overrides = {}) {
  const calls = { switchTo: [], showToast: [], setVtSkin: [], openFile: [], openFileInPane: [] };
  const gated = new Set(overrides.gatedOff || []);
  return {
    calls,
    vtFetch: overrides.vtFetch || (async () => ({})),
    getAction: overrides.getAction || (() => undefined),
    gateOk: (gate) => !gated.has(gate),
    listSessions: overrides.listSessions || (() => []),
    getSession: overrides.getSession || (() => null),
    switchTo: (id) => calls.switchTo.push(id),
    loadViewer: overrides.loadViewer || (async () => ({ _loadRecent: () => [] })),
    openFile: (p) => calls.openFile.push(p),
    openFileInPane: (p) => calls.openFileInPane.push(p),
    splitActivePane: overrides.splitActivePane || (() => 'pane-2'),
    setPaneSession: overrides.setPaneSession || (() => {}),
    buildSessionCard: overrides.buildSessionCard || ((sess, onSelect) => {
      const el = doc.createElement('div');
      el.dataset.name = sess.name;
      el.innerHTML = '<div class="card-title"></div><div class="card-meta"></div>';
      el.querySelector('.card-title').textContent = sess.name;
      el.onclick = onSelect;
      return el;
    }),
    updateSessionCard: overrides.updateSessionCard || (() => {}),
    setVtSkin: (skin) => calls.setVtSkin.push(skin),
    vtSkins: overrides.vtSkins || (() => ['farshell', 'macos', 'catppuccin', 'windows', 'vscode', 'notepad']),
    vtSkinLabel: overrides.vtSkinLabel || ((skin) => ({
      farshell: 'FarShell', macos: 'macOS', catppuccin: 'Catppuccin',
      windows: 'Windows', vscode: 'VS Code', notepad: 'Notepad',
    }[skin] || skin)),
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

test('접두사 없음 — 세션은 없고 명령만 나열되며, capability 게이팅이 적용된다', async () => {
  // 2026-09-23(2.1 D6) — 세션 이동/관리는 세션 탭이 맡고, 빠른 열기
  // 기본 화면에는 더 이상 세션이 안 나온다(`@`로만 찾는다).
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    listSessions: () => [{ id: 'a', name: 'alpha' }],
    getAction: (name) => (['queue.show', 'search.toggle', 'settings.show', 'scm.show'].includes(name) ? () => {} : undefined),
    gatedOff: ['ports', 'usage', 'voice'],
  });
  const api = mountPalette(root, deps);
  api.open();
  const rows = await waitFor(() => {
    const list = Array.from(document.querySelectorAll('.vt-qo-row, .vt-qo-session-card'));
    return list.some((r) => r.textContent.includes('터미널 내 검색')) ? list : null;
  });
  assert.ok(!rows.some((r) => r.textContent.includes('alpha')), '세션 행은 기본 화면에 없어야 한다');
  assert.ok(rows.some((r) => r.textContent.includes('터미널 내 검색')), '명령 행이 보여야 한다');
  assert.ok(rows.some((r) => r.textContent.includes('설정')), '「설정」 명령이 「모든 기능」에 있어야 한다');
  assert.ok(rows.some((r) => r.textContent.includes('MCP · 스킬 관리')), '모바일에서도 빠른 열기로 MCP 팝업에 닿아야 한다');
  assert.ok(rows.some((r) => r.textContent.includes('변경')), '「변경」 명령이 「모든 기능」에 있어야 한다');
  assert.ok(!rows.some((r) => r.textContent.includes('포트 대시보드')), 'ports 게이트가 꺼져 있으면 숨어야 한다');
});

test('`@` 접두사 — 세션 클릭 시 전환되고 팔레트가 닫힌다', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, { listSessions: () => [{ id: 'a', name: 'alpha' }] });
  const api = mountPalette(root, deps);
  api.open('session');
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

test('`@` 접두사 — 세션이 tmux 라이브 프리뷰 후보면 카드로 나온다', async () => {
  const { mountPalette, document, window, root } = await mountFresh();
  window.allSessions = () => ({ a: { tmuxName: 'dev' } });
  const deps = fakeDeps(document, {
    listSessions: () => [{ id: 'a', name: 'dev' }],
    vtFetch: async (path) => {
      if (path.includes('/api/tmux/sessions')) return [{ name: 'dev', command: 'claude' }];
      if (path.includes('/api/agents')) return {};
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open('session');
  const card = await waitFor(() => document.querySelector('.vt-qo-session-card'));
  assert.ok(card, '라이브 프리뷰 카드가 나와야 한다');
});

// 리디자인 6단계(시안 O01 · O03) — `@`에서 고른 세션의 화면을 오른쪽 칸에 터미널 색 클래스로 그린다(글자는 textContent).
test('`@` 접두사 — 고른 세션 화면을 터미널 색으로 미리 보이고, 화면 글자는 마크업으로 해석되지 않는다', async () => {
  const { mountPalette, document, window, root } = await mountFresh();
  window.allSessions = () => ({ a: { tmuxName: 'dev' } });
  const asked = [];
  const deps = fakeDeps(document, {
    listSessions: () => [{ id: 'a', name: 'dev' }],
    vtFetch: async (path) => {
      if (path.includes('/api/tmux/sessions')) return [{ name: 'dev', command: 'claude' }];
      if (path.includes('/api/tmux/preview/')) { asked.push(path); return { available: true, content: '\x1b[32m✓ 42 passed\x1b[0m\n\x1b[31m<b>x</b>\x1b[0m' }; }
      return {};
    },
  });
  const api = mountPalette(root, deps);
  api.open('session');
  const term = await waitFor(() => document.querySelector('.vt-qo-preview-pane .vt-qo-term .ta-green'));
  assert.strictEqual(term.textContent, '✓ 42 passed');
  assert.match(asked[0], /^\/api\/tmux\/preview\/dev\?lines=40&ansi=1$/);
  const pane = document.querySelector('.vt-qo-preview-pane');
  assert.strictEqual(pane.querySelector('b'), null);
  assert.strictEqual(pane.querySelector('.ta-red').textContent, '<b>x</b>');
  api.close();
});

test('명령을 실행하면 「최근 사용 기능」으로 기본 화면 위에 올라온다', async () => {
  // window.vtSettingsGet/Set은 core/settings.js가 앱 부팅 시 공개하는
  // 브리지다(테스트는 solid-env가 만든 jsdom window를 직접 스텁한다).
  const { mountPalette, document, window, root } = await mountFresh();
  let stored = '[]';
  window.vtSettingsGet = (key) => (key === 'palette.recentCommands' ? stored : null);
  window.vtSettingsSet = (key, v) => { if (key === 'palette.recentCommands') stored = v; };
  const deps = fakeDeps(document, {
    getAction: (name) => (name === 'search.toggle' ? () => {} : undefined),
  });
  const api = mountPalette(root, deps);
  api.open();
  const findRow = () => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes('터미널 내 검색'));
  await waitFor(findRow);
  findRow().click();
  assert.deepStrictEqual(JSON.parse(stored), ['cmd:터미널 내 검색']);

  api.open();
  const recentSection = await waitFor(() => Array.from(document.querySelectorAll('.vt-qo-sec-label')).find((s) => s.textContent === '최근 사용'));
  assert.ok(recentSection, '「최근 사용」 구획 머리가 나와야 한다');
});

test('`/` 접두사 — 최근 파일 + 서버 검색 결과(디바운스 후)', async () => {
  const { mountPalette, document, root } = await mountFresh();
  const deps = fakeDeps(document, {
    loadViewer: async () => ({ _loadRecent: () => ['/repo/old.py'] }),
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

test('`>` 접두사 — deps.vtSkins()로 테마 행을 만들고 고르면 setVtSkin이 불린다', async () => {
  // 2026-09-18 — DOM의 `.theme-chip`을 읽던 방식은 그 칩이 사는 화면(설정
  // 「모양」탭)이 열려 있지 않으면 항상 실패하는 취약한 결합이었다(실제로
  // 그 화면 자체가 도달 불가능해지는 사고가 났다). deps.vtSkins()를 직접
  // 쓰도록 바꿨으니 DOM에 칩이 전혀 없어도 팔레트 목록엔 떠야 한다.
  const { mountPalette, document, root } = await mountFresh();
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
    loadViewer: async () => ({ _loadRecent: () => ['/repo/notes.md'] }),
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

// ADR-44 — 파일 행 Enter는 표면 설정을 따르는 openFile, Cmd/Ctrl+Enter(「새 pane에
// 열기」)는 **여전히 pane**이어야 한다. 후자가 오버레이로 가면 splitActivePane이
// 만든 빈 pane만 남는다(계획서 §8.1이 짚은 실제 회귀).
async function openFileRow(mods) {
  const { mountPalette, document, window, root } = await mountFresh();
  const splits = [];
  const deps = fakeDeps(document, {
    loadViewer: async () => ({ _loadRecent: () => ['/repo/notes.md'] }),
    splitActivePane: (dir) => { splits.push(dir); return 'pane-2'; },
  });
  const api = mountPalette(root, deps);
  api.open('file');
  await waitFor(() => Array.from(document.querySelectorAll('.vt-qo-row')).some((r) => r.textContent.includes('notes.md')));
  const Kb = window.KeyboardEvent || document.defaultView.KeyboardEvent;
  document.dispatchEvent(new Kb('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...mods }));
  await flush();
  return { calls: deps.calls, splits };
}

test('파일 행 Enter — 표면 설정을 따르는 openFile로 연다', async () => {
  const { calls, splits } = await openFileRow({});
  assert.deepStrictEqual(calls.openFile, ['/repo/notes.md']);
  assert.deepStrictEqual(calls.openFileInPane, []);
  assert.deepStrictEqual(splits, []);
});

test('파일 행 Cmd/Ctrl+Enter(새 pane에 열기) — 칸을 만들고 그 칸에 pane으로 연다', async () => {
  const { calls, splits } = await openFileRow({ ctrlKey: true });
  assert.deepStrictEqual(splits, ['right']);
  assert.deepStrictEqual(calls.openFileInPane, ['/repo/notes.md']);
  assert.deepStrictEqual(calls.openFile, [], '오버레이로 가면 빈 pane만 남는다');
});

// ── 3부: `>` 설정 색인 — 줄 안에서 값을 바꾼다 ────────────────────────────

test('설정 색인 — 터미널 항목은 줄 안 컨트롤, 나머지 섹션은 link, 모바일에선 하단바가 빠진다', async () => {
  const { buildSettingEntries } = await mod();
  const all = buildSettingEntries();
  const byLabel = (l) => all.find((e) => e.label === l);
  assert.strictEqual(byLabel('글자 크기').kind, 'range');
  assert.strictEqual(byLabel('글자 크기').key, 'terminal.fontSize');
  assert.strictEqual(byLabel('커서 모양').kind, 'select');
  assert.strictEqual(byLabel('커서 깜빡임').kind, 'bool');
  assert.strictEqual(byLabel('등록 기기').kind, 'link', '보안 항목은 바꾸지 않고 해당 화면으로 보낸다');
  assert.strictEqual(byLabel('등록 기기').section, 'security');
  assert.strictEqual(byLabel('개입 필요 구역 표시').key, 'rail.attention.show');
  assert.strictEqual(all.filter((e) => e.label === '글자 크기').length, 1, '인라인 항목은 link로 다시 나오지 않는다');
  const ids = new Set(all.map((e) => e.id));
  assert.strictEqual(ids.size, all.length, '항목 id는 겹치지 않는다');
  const mobile = buildSettingEntries(['terminal', 'voice']);
  assert.ok(!mobile.some((e) => e.kind === 'link' && e.section === 'security'), '보이지 않는 섹션의 link는 뺀다');
});

test('matchSettingEntry — 낱말을 전부 포함해야 하고 별칭·경로로도 찾는다', async () => {
  const { buildSettingEntries, matchSettingEntry } = await mod();
  const font = buildSettingEntries().find((e) => e.label === '글자 크기');
  assert.ok(matchSettingEntry(font, ''));
  assert.ok(matchSettingEntry(font, '폰트'), '별칭');
  assert.ok(matchSettingEntry(font, '터미널 표시'), '경로의 낱말');
  assert.ok(!matchSettingEntry(font, '터미널 음성'), '낱말 하나라도 없으면 탈락');
});

test('stepSettingValue — bool 뒤집기, select 순환, range 단계(방향 무관 bool)', async () => {
  const { stepSettingValue, formatSettingValue, buildSettingEntries } = await mod();
  const all = buildSettingEntries();
  const get = (l) => all.find((e) => e.label === l);
  assert.strictEqual(stepSettingValue(get('커서 깜빡임'), true, 1), false);
  assert.strictEqual(stepSettingValue(get('커서 깜빡임'), false, -1), true);
  assert.strictEqual(stepSettingValue(get('커서 모양'), 'block', 1), 'underline');
  assert.strictEqual(stepSettingValue(get('커서 모양'), 'block', -1), 'bar', '앞쪽 끝에서 감싼다');
  assert.strictEqual(stepSettingValue(get('글자 크기'), 14, 1), 15);
  assert.strictEqual(stepSettingValue(get('스크롤백 줄 수'), 2000, 1), 2500, 'step이 있으면 그만큼');
  assert.strictEqual(stepSettingValue(get('글자 크기'), 'x', 1), undefined);
  assert.strictEqual(stepSettingValue(get('등록 기기'), null, 1), undefined, 'link는 바꿀 값이 없다');
  assert.strictEqual(formatSettingValue(get('글자 크기'), 14), '14px');
  assert.strictEqual(formatSettingValue(get('스크롤백 줄 수'), 5000), '5,000 줄');
  assert.strictEqual(formatSettingValue(get('커서 모양'), 'bar'), '막대');
});

function settingsBridge(window, initial) {
  const values = { ...initial };
  const sets = [];
  const listeners = new Set();
  window.vtSettingsGet = (k) => values[k];
  window.vtSettingsSet = (k, v) => { sets.push([k, v]); values[k] = v; for (const f of listeners) f({ [k]: v }); return Promise.resolve(true); };
  window.vtSettingsSubscribe = (f) => { listeners.add(f); return () => listeners.delete(f); };
  return { values, sets };
}

async function openSettingsPalette(initial, query = '>') {
  const { mountPalette, document, window, root } = await mountFresh();
  const bridge = settingsBridge(window, initial);
  const actions = [];
  const deps = fakeDeps(document, {
    getAction: (name) => (name === 'settings.section' ? (...a) => actions.push(a) : undefined),
  });
  const api = mountPalette(root, deps);
  api.open('settings');
  await flush();
  const input = document.getElementById('vt-qo-input');
  input.value = query;
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  const rowOf = (text) => Array.from(document.querySelectorAll('.vt-qo-row')).find((r) => r.textContent.includes(text));
  await waitFor(() => document.querySelector('.vt-qo-row.vt-qo-setting'));
  const press = async (key) => {
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    await flush();
  };
  return { api, document, window, bridge, actions, rowOf, press, input };
}

test('`>` — 설정 줄이 이름·경로·현재 값을 보여주고, 중복되던 토글 명령은 사라진다', async () => {
  const { document, rowOf } = await openSettingsPalette({ 'terminal.cursorBlink': true, 'terminal.cursorStyle': 'bar', 'terminal.fontSize': 14, 'mouse.autocopyOnSelect': true }, '> 커서');
  const blink = rowOf('커서 깜빡임');
  assert.ok(blink.textContent.includes('터미널 › 표시'), '경로가 이름 아래에 보인다');
  assert.strictEqual(blink.querySelector('.vt-qo-switch').getAttribute('aria-checked'), 'true');
  assert.ok(rowOf('커서 모양').querySelector('.vt-qo-val').textContent.includes('막대'));
  assert.strictEqual(document.querySelectorAll('.vt-qo-row.vt-qo-setting').length, 2, '「커서」로 걸리는 줄은 둘');
});

test('`>` — Enter는 선택한 줄의 값을 바꾸되 팔레트를 닫지 않는다', async () => {
  const { api, bridge, press, rowOf } = await openSettingsPalette({ 'terminal.fontSize': 14, 'terminal.cursorBlink': true });
  await press('Enter');  // 첫 설정 줄 = 터미널 › 표시 › 글자 크기
  assert.deepStrictEqual(bridge.sets, [['terminal.fontSize', 15]]);
  assert.strictEqual(api.isOpen(), true, '바꾼 결과를 보며 이어서 바꾸도록 열려 있다');
  await waitFor(() => rowOf('글자 크기').querySelector('.vt-qo-val').textContent.includes('15px'));
});

test('`>` — ←/→는 선택한 줄의 값을 옮기고, 값이 없는 줄에서는 캐럿에 넘긴다', async () => {
  const { bridge, press, rowOf, document, window } = await openSettingsPalette({ 'terminal.cursorStyle': 'block', 'terminal.cursorBlink': true }, '> 커서 모양');
  await waitFor(() => rowOf('커서 모양'));
  await press('ArrowRight');
  assert.deepStrictEqual(bridge.sets, [['terminal.cursorStyle', 'underline']]);
  await waitFor(() => rowOf('커서 모양').querySelector('.vt-qo-val').textContent.includes('밑줄'));
  await press('ArrowLeft');
  assert.deepStrictEqual(bridge.sets.at(-1), ['terminal.cursorStyle', 'block']);

  // 값이 없는 줄(link)에서는 가로채지 않는다 — 입력창 캐럿 이동을 막으면 검색어를 고칠 수 없다.
  const input = document.getElementById('vt-qo-input');
  input.value = '> 등록 기기';
  input.dispatchEvent(new window.Event('input', { bubbles: true }));
  await waitFor(() => rowOf('등록 기기'));
  const ev = new window.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true });
  document.dispatchEvent(ev);
  await flush();
  assert.strictEqual(ev.defaultPrevented, false);
  assert.strictEqual(bridge.sets.length, 2, 'link 줄에서는 값을 쓰지 않는다');
});

test('`>` — 바꿀 수 없는 항목은 해당 섹션으로 보내며 팔레트를 닫는다', async () => {
  const { api, actions, rowOf } = await openSettingsPalette({}, '> 등록 기기');
  await waitFor(() => rowOf('등록 기기'));
  rowOf('등록 기기').click();
  assert.deepStrictEqual(actions, [['security']]);
  assert.strictEqual(api.isOpen(), false);
});
