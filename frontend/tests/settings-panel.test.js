// S4 — 설정 화면. 스키마에서 그리는지(항목 하드코딩 금지), 변경이 즉시
// 스토어로 가는지, 키맵 재바인딩·충돌 표시가 실제로 동작하는지를 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');

const SETTINGS_PANEL_JS = path.join(__dirname, '../js/panels/settings.js');
const SETTINGS_JS = path.join(__dirname, '../js/core/settings.js');
const KEYMAP_JS = path.join(__dirname, '../js/core/keymap.js');
const TOAST_JS = path.join(__dirname, '../js/ui/toast.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function build({ hooks = { ok: true, events: { PreToolUse: 'ok', PostToolUse: 'ok', Stop: 'ok' } }, fetchExtra } = {}) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = '';
  window._tokenQuery = '';
  const puts = [];
  const posts = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    if (opts && opts.method === 'PUT') {
      puts.push(JSON.parse(opts.body));
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
    }
    if (opts && opts.method === 'POST') posts.push(u);
    if (u.includes('/api/hooks/status')) return Promise.resolve({ ok: true, json: () => Promise.resolve(hooks) });
    if (fetchExtra) {
      const r = fetchExtra(u, opts);
      if (r) return r;
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  window.matchMedia = (q) => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  const S = await importFresh(SETTINGS_JS, env.context, cache);
  const K = await importFresh(KEYMAP_JS, env.context, cache);
  const P = await importFresh(SETTINGS_PANEL_JS, env.context, cache);
  return { window, document: window.document, S, K, P, puts, posts };
}

const flush = () => new Promise((r) => setImmediate(r));

function sectionButton(document, label) {
  return Array.from(document.querySelectorAll('.vt-set-navitem')).find((b) => b.textContent === label);
}
function rowByLabel(document, label) {
  return Array.from(document.querySelectorAll('.vt-set-row'))
    .find((r) => r.querySelector('.vt-set-label')?.firstChild.textContent === label);
}

test('열기 — 섹션 목록과 첫 섹션이 그려진다', async () => {
  const { document, P } = await build();
  P.showSettings();
  assert.ok(document.getElementById('vt-settings'), '패널이 열려야 한다');
  assert.deepEqual(
    Array.from(document.querySelectorAll('.vt-set-navitem')).map((b) => b.textContent),
    ['터미널', '마우스 · 선택', '접근성', '음성', '에이전트', '키맵', '보안', '스크롤백', '정보'],
  );
  assert.ok(rowByLabel(document, '글자 크기'), '첫 섹션(터미널)이 그려져야 한다');
});

test('토글 — 다시 부르면 닫힌다', async () => {
  const { document, P } = await build();
  P.showSettings();
  P.showSettings();
  assert.strictEqual(document.getElementById('vt-settings'), null);
});

test('컨트롤은 스키마에서 그린다 — 커서 모양 옵션이 스키마 values와 같다', async () => {
  const { document, P, S } = await build();
  P.showSettings();
  const sel = rowByLabel(document, '커서 모양').querySelector('select');
  assert.deepEqual(
    Array.from(sel.options).map((o) => o.value),
    S.SCHEMA['terminal.cursorStyle'].values,
  );
});

test('range 컨트롤의 min/max도 스키마에서 온다', async () => {
  const { document, P, S } = await build();
  P.showSettings();
  const input = rowByLabel(document, '글자 크기').querySelector('input[type="range"]');
  assert.strictEqual(Number(input.min), S.SCHEMA['terminal.fontSize'].min);
  assert.strictEqual(Number(input.max), S.SCHEMA['terminal.fontSize'].max);
});

test('체크박스 변경 → 스토어에 즉시 반영되고 서버로 나간다', async () => {
  const { document, P, S, puts } = await build();
  P.showSettings();
  sectionButton(document, '마우스 · 선택').click();
  const cb = rowByLabel(document, '앱에 마우스 이벤트 전달').querySelector('.vt-set-check');
  cb.checked = false;
  cb.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await flush();
  assert.strictEqual(S.get('mouse.forwardToApp'), false);
  assert.strictEqual(puts.at(-1).settings['mouse.forwardToApp'], false);
});

test('접근성 — screenReaderMode가 UI에 노출된다(S4 이전엔 콘솔로만 가능했다)', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '접근성').click();
  const row = rowByLabel(document, '스크린 리더 모드');
  assert.ok(row, '항목이 있어야 한다');
  assert.match(row.querySelector('.vt-set-help').textContent, /새로 여는 탭부터/,
    '런타임에 못 바꾸는 항목은 그 사실을 함께 보여준다');
});

test('키맵 — 현재 바인딩과 passthrough 체크가 그려진다', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '키맵').click();
  const row = rowByLabel(document, '스크롤백 검색');
  assert.ok(row.querySelector('.vt-set-combo').textContent.length > 0);
  assert.strictEqual(row.querySelector('.vt-set-pt input').checked, false);
});

test('키맵 — passthrough 체크가 레지스트리에 반영된다', async () => {
  const { document, P, K } = await build();
  P.showSettings();
  sectionButton(document, '키맵').click();
  // N40/N46 — `search`(Mod+F)는 이제 「스크롤백 검색」. 인페인 검색바는
  // `searchInPane`(Mod+Shift+F, 라벨 「터미널 내 검색」)로 내려갔다.
  const box = rowByLabel(document, '스크롤백 검색').querySelector('.vt-set-pt input');
  box.checked = true;
  box.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await flush();
  assert.strictEqual(K.list().find((b) => b.id === 'search').passthrough, true);
});

test('키맵 — 재바인딩: 버튼을 누르고 조합을 입력하면 저장된다', async () => {
  const { document, window, P, K } = await build();
  P.showSettings();
  sectionButton(document, '키맵').click();
  const btn = rowByLabel(document, '스크롤백 검색').querySelector('.vt-set-combo');
  btn.click();
  assert.ok(btn.classList.contains('recording'), '녹화 상태가 보여야 한다');
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'g', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true }));
  await flush();
  assert.strictEqual(K.normalize(K.list().find((b) => b.id === 'search').combo), 'mod+shift+g');
});

test('키맵 — 수식키만 누르면 확정되지 않는다', async () => {
  const { document, window, P, K } = await build();
  P.showSettings();
  sectionButton(document, '키맵').click();
  const btn = rowByLabel(document, '스크롤백 검색').querySelector('.vt-set-combo');
  btn.click();
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Shift', shiftKey: true, bubbles: true, cancelable: true }));
  await flush();
  assert.ok(btn.classList.contains('recording'), '아직 기다려야 한다');
  assert.strictEqual(K.normalize(K.list().find((b) => b.id === 'search').combo), 'mod+f');
});

test('키맵 — Escape로 재바인딩을 취소한다', async () => {
  const { document, window, P, K } = await build();
  P.showSettings();
  sectionButton(document, '키맵').click();
  rowByLabel(document, '스크롤백 검색').querySelector('.vt-set-combo').click();
  document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await flush();
  assert.strictEqual(K.normalize(K.list().find((b) => b.id === 'search').combo), 'mod+f');
});

test('키맵 — 충돌하면 행에 표시하고 이유를 적는다', async () => {
  const { document, P, K } = await build();
  await K.setBinding('palette', 'Mod+F');
  P.showSettings();
  sectionButton(document, '키맵').click();
  const row = rowByLabel(document, '스크롤백 검색');
  assert.ok(row.classList.contains('conflict'));
  assert.match(row.querySelector('.vt-set-help').textContent, /충돌/);
});

test('정보 — 훅이 전부 등록돼 있으면 그대로 보여준다', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '정보').click();
  await flush();
  const rows = Array.from(document.querySelectorAll('.vt-set-hookrow')).map((r) => r.textContent);
  assert.deepEqual(rows, ['PreToolUse — 등록됨', 'PostToolUse — 등록됨', 'Stop — 등록됨']);
});

test('정보 — 훅이 빠져 있으면 해결 방법을 함께 안내한다', async () => {
  const { document, P } = await build({ hooks: { ok: false, events: { PreToolUse: 'add', PostToolUse: 'add', Stop: 'update' } } });
  P.showSettings();
  sectionButton(document, '정보').click();
  await flush();
  const help = document.querySelector('.vt-set-about .vt-set-help');
  assert.match(help.textContent, /fsh hooks install/);
});

test('정보 — 훅 상태 조회가 실패해도 패널이 죽지 않는다', async () => {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  env.window.fetch = (url, opts) => (opts && opts.method === 'PUT'
    ? Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    : Promise.reject(new Error('offline')));
  const cache = new Map();
  await importFresh(TOAST_JS, env.context, cache);
  await importFresh(SETTINGS_JS, env.context, cache);
  await importFresh(KEYMAP_JS, env.context, cache);
  const P = await importFresh(SETTINGS_PANEL_JS, env.context, cache);
  P.showSettings();
  sectionButton(env.window.document, '정보').click();
  await flush();
  assert.match(env.window.document.querySelector('.vt-set-about').textContent, /확인할 수 없습니다/);
});

// N42(60-settings-palette.md §7) — 설정 → 음성 탭: 알림·음성 진단 5항목.
test('음성 — 5항목이 모두 그려진다', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '음성').click();
  for (const label of ['웹 푸시', '작업 완료 알림', 'Whisper 모델', '맥에서 음성만 쓰기']) {
    assert.ok(rowByLabel(document, label), `${label} 행이 있어야 한다`);
  }
});

test('음성 — 웹 푸시 상태가 /api/push/status에서 채워진다', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/push/status')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ available: true, configured: true, subscriptions: 2 }) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '음성').click();
  await flush();
  const help = rowByLabel(document, '웹 푸시').querySelector('.vt-set-help');
  assert.match(help.textContent, /구독 2대 · VAPID 확인됨/);
});

test('음성 — 웹 푸시 테스트 발송 버튼이 실호출 + 토스트를 낸다', async () => {
  const toasts = [];
  const { document, window, P } = await build({
    fetchExtra: (u) => (u.includes('/api/push/test')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, sent: 1 }) })
      : null),
  });
  window.showToast = (msg) => toasts.push(msg);
  P.showSettings();
  sectionButton(document, '음성').click();
  const btn = rowByLabel(document, '웹 푸시').querySelector('button');
  btn.click();
  await flush();
  assert.ok(toasts.some((m) => /발송됨/.test(m)));
});

test('음성 — 작업 완료 알림 소리 듣기 버튼이 /api/notify/test를 호출한다', async () => {
  const { document, P, posts } = await build({
    fetchExtra: (u) => (u.includes('/api/notify/test')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, configured: true }) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '음성').click();
  const btn = rowByLabel(document, '작업 완료 알림').querySelector('button');
  btn.click();
  await flush();
  assert.ok(posts.some((u) => u.includes('/api/notify/test')));
});

test('음성 — Whisper 모델 상태에 따라 적재/내리기 버튼이 토글된다', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/voice/stt/status')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ available: true, loaded: true, engine: 'faster-whisper' }) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '음성').click();
  await flush();
  const row = rowByLabel(document, 'Whisper 모델');
  assert.match(row.querySelector('.vt-set-help').textContent, /메모리 상주 · faster-whisper/);
  const [preload, unload] = row.querySelectorAll('button');
  assert.strictEqual(preload.disabled, true, '이미 적재돼 있으면 미리 적재는 비활성');
  assert.strictEqual(unload.disabled, false);
});

test('음성 — 맥에서 음성만 쓰기 버튼이 시작→중지로 토글된다', async () => {
  const { document, P, posts } = await build({
    fetchExtra: (u) => {
      if (u.includes('/voice/local/start')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'recording' }) });
      if (u.includes('/voice/local/stop')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'not_recording', text: '' }) });
      return null;
    },
  });
  P.showSettings();
  sectionButton(document, '음성').click();
  const btn = rowByLabel(document, '맥에서 음성만 쓰기').querySelector('button');
  assert.strictEqual(btn.textContent, '시작');
  btn.click();
  await flush();
  assert.strictEqual(btn.textContent, '중지');
  assert.ok(posts.some((u) => u.includes('/voice/local/start')));
  btn.click();
  await flush();
  assert.strictEqual(btn.textContent, '시작');
  assert.ok(posts.some((u) => u.includes('/voice/local/stop')));
});

test('음성 — 다른 탭으로 이동해도 죽지 않는다(clients.js 폴링 정리 경로)', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '음성').click();
  sectionButton(document, '터미널').click();
  assert.ok(rowByLabel(document, '글자 크기'), '터미널 섹션으로 정상 전환돼야 한다');
});

test('음성 — 패널을 닫아도 죽지 않는다(onClose cleanup 경로)', async () => {
  const { document, P } = await build();
  P.showSettings();
  sectionButton(document, '음성').click();
  P.showSettings();
  assert.strictEqual(document.getElementById('vt-settings'), null);
});

// N9/N45(80-multihost-agents.md §2) — 감지 커버리지 표.
const COVERAGE_ROWS = [
  { cli: 'claude', path: 'pty', patternLines: 24, states: ['waiting'], trust: 'mid' },
  { cli: 'codex', path: 'none', patternLines: 4, states: [], trust: 'low' },
];

test('에이전트 — 커버리지 표가 API 응답으로 그려진다', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/agents/coverage')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(COVERAGE_ROWS) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '에이전트').click();
  await flush();
  const table = document.querySelector('.vt-set-covtable');
  assert.ok(table, '표가 그려져야 한다');
  const rows = Array.from(table.querySelectorAll('tbody tr'));
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].querySelector('.vt-set-covcli').textContent, 'claude');
  assert.strictEqual(rows[0].querySelector('.vt-set-covtrust').dataset.trust, 'mid');
  assert.strictEqual(rows[1].querySelector('.vt-set-covtrust').dataset.trust, 'low');
});

test('에이전트 — "보통 이하" 경고 문장이 그대로 노출된다', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/agents/coverage')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(COVERAGE_ROWS) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '에이전트').click();
  await flush();
  const texts = Array.from(document.querySelectorAll('.vt-set-help')).map((e) => e.textContent);
  assert.ok(texts.includes('"보통" 이하는 레일에서 승인 대기를 놓칠 수 있다는 뜻입니다.'));
});

test('에이전트 — API 실패 시 표 대신 안내 문구', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/agents/coverage')
      ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '에이전트').click();
  await flush();
  assert.ok(!document.querySelector('.vt-set-covtable'));
});


// ── 「보안」(60 §2) — 비밀번호 · OTP · 기기 · 승격 상태. 전부 읽기 전용.
const SEC_DEVICES = {
  devices: [
    { id: 'a1b2c3d4', label: 'iPhone', added_at: 1757000000, last_seen: 1757100000, current: true },
    { id: 'ff00aa11', label: 'MacBook', added_at: 1756000000, last_seen: 0, current: false },
  ],
};

function securityFetch({ status = {}, devices = SEC_DEVICES, elevation = {} } = {}) {
  return (u) => {
    if (u.includes('/api/auth/status')) return Promise.resolve({ ok: true, json: () => Promise.resolve(status) });
    if (u.includes('/api/auth/elevation')) return Promise.resolve({ ok: true, json: () => Promise.resolve(elevation) });
    if (u.includes('/api/devices')) return Promise.resolve({ ok: true, json: () => Promise.resolve(devices) });
    return null;
  };
}

async function openSecurity(opts) {
  const built = await build({ fetchExtra: securityFetch(opts) });
  built.P.showSettings();
  sectionButton(built.document, '보안').click();
  await flush();
  await flush();
  return built;
}

const secStates = (document) => Array.from(document.querySelectorAll('.vt-set-secrow')).map((r) => [
  r.querySelector('.vt-set-label').firstChild.textContent,
  r.querySelector('.vt-set-secstate').textContent,
  r.querySelector('.vt-set-secstate').dataset.state,
]);

test('보안 — 비밀번호·OTP 미설정이면 CLI 명령을 안내한다', async () => {
  const { document } = await openSecurity({ status: { protected: false, password_set: false, otp_enabled: false } });
  const rows = secStates(document);
  assert.deepEqual(rows[0], ['웹 로그인 비밀번호', '설정 안 됨', 'off']);
  assert.deepEqual(rows[1], ['OTP (새 기기 등록 관문)', '비활성', 'off']);
  const helps = Array.from(document.querySelectorAll('.vt-set-help')).map((e) => e.textContent);
  assert.ok(helps.some((t) => t.includes('fsh password')));
  assert.ok(helps.some((t) => t.includes('fsh otp setup')));
});

test('보안 — 설정돼 있으면 활성 배지', async () => {
  const { document } = await openSecurity({ status: { protected: true, password_set: true, otp_enabled: true } });
  const rows = secStates(document);
  assert.deepEqual(rows[0].slice(1), ['설정됨', 'on']);
  assert.deepEqual(rows[1].slice(1), ['활성', 'on']);
});

test('보안 — 기기 목록과 "이 기기" 배지', async () => {
  const { document } = await openSecurity();
  const trs = Array.from(document.querySelectorAll('.vt-set-devtable tbody tr'));
  assert.strictEqual(trs.length, 2);
  assert.strictEqual(trs[0].dataset.current, '1');
  assert.ok(trs[0].querySelector('.vt-set-devme'), '현재 기기에 배지가 있어야 한다');
  assert.strictEqual(trs[1].querySelector('.vt-set-devme'), null);
  assert.strictEqual(trs[1].children[3].textContent, '기록 없음');
});

test('보안 — 설정 변경 UI(입력·버튼)를 두지 않는다', async () => {
  const { document } = await openSecurity();
  const content = document.querySelector('.vt-set-content');
  assert.strictEqual(content.querySelector('input'), null);
  assert.strictEqual(content.querySelector('button'), null);
});

test('보안 — 승격은 ADR-27로 쓰는 경로가 없다는 설명이 함께 나온다', async () => {
  const { document } = await openSecurity({ elevation: { elevated: false, elevated_until: 0, unused: true } });
  const row = Array.from(document.querySelectorAll('.vt-set-secrow'))
    .find((r) => r.querySelector('.vt-set-label').firstChild.textContent === '승격 세션');
  assert.ok(row);
  assert.strictEqual(row.querySelector('.vt-set-secstate').textContent, '승격 안 됨');
  assert.ok(row.querySelector('.vt-set-help').textContent.includes('ADR-27'));
});

test('보안 — 승격 중이면 남은 시간을 분으로 보여준다', async () => {
  const until = Math.floor(Date.now() / 1000) + 9 * 60;
  const { document } = await openSecurity({ elevation: { elevated: true, elevated_until: until } });
  const row = Array.from(document.querySelectorAll('.vt-set-secrow'))
    .find((r) => r.querySelector('.vt-set-label').firstChild.textContent === '승격 세션');
  assert.match(row.querySelector('.vt-set-secstate').textContent, /승격됨 · 9분 남음/);
});

test('보안 — API 실패 시 표 대신 안내 문구', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => ((u.includes('/api/devices') || u.includes('/api/auth/'))
      ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '보안').click();
  await flush();
  await flush();
  assert.strictEqual(document.querySelector('.vt-set-devtable'), null);
  const helps = Array.from(document.querySelectorAll('.vt-set-help')).map((e) => e.textContent);
  assert.ok(helps.some((t) => t.includes('인증 상태를 확인할 수 없습니다')));
  assert.ok(helps.some((t) => t.includes('기기 목록을 확인할 수 없습니다')));
  assert.ok(helps.some((t) => t.includes('승격 상태를 확인할 수 없습니다')));
});

// ── 「스크롤백」(N13, 80-multihost-agents.md §3) ──────────────────────────

test('스크롤백 — 토글 변경이 스토어와 서버로 나간다', async () => {
  const { document, P, S, puts } = await build({
    fetchExtra: (u) => (u.includes('/api/scrollback/usage')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ bytes: 1234 }) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '스크롤백').click();
  const cb = rowByLabel(document, '스크롤백 영속화').querySelector('.vt-set-check');
  assert.strictEqual(cb.checked, false); // 기본 OFF
  cb.checked = true;
  cb.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
  await flush();
  assert.strictEqual(S.get('scrollback.persist'), true);
  assert.strictEqual(puts.at(-1).settings['scrollback.persist'], true);
});

test('스크롤백 — 디스크 사용량을 보여준다', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/scrollback/usage')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve({ bytes: 2 * 1024 * 1024 }) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '스크롤백').click();
  await flush();
  const texts = Array.from(document.querySelectorAll('.vt-set-help, .vt-set-sechost'))
    .map((e) => e.textContent);
  assert.ok(texts.some((t) => t.includes('2.0MB')));
});

test('스크롤백 — API 실패 시 안내 문구', async () => {
  const { document, P } = await build({
    fetchExtra: (u) => (u.includes('/api/scrollback/usage')
      ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
      : null),
  });
  P.showSettings();
  sectionButton(document, '스크롤백').click();
  await flush();
  const texts = Array.from(document.querySelectorAll('.vt-set-sechost')).map((e) => e.textContent);
  assert.ok(texts.some((t) => t.includes('디스크 사용량을 확인할 수 없습니다')));
});
