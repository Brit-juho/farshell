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
    ['터미널', '마우스 · 선택', '접근성', '음성', '키맵', '정보'],
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
