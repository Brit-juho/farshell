// 설정 「음성 및 알림」 — 음성 입력 구역(모델 카드·준비 체크리스트·관리). 서버 계약(JSON 형태)의 픽스처로 본다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const INDEX_HTML = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8')
  .replace(/<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/g, '');
const J = (p) => path.join(__dirname, p);
const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

const bench = (cer, sec, rss) => ({ cer, sec, rss_mb: rss, basis: '한국어 짧은 문장 20개 · M2' });
const mk = (o) => ({
  engine: 'mlx_qwen3', engine_label: 'MLX Qwen3-ASR', tagline: '', repo: 'x/y', size_mb: 1200, tags: [], bench: null,
  license: 'Apache-2.0', notes: [], custom: false, supported: true, unsupported_reason: '',
  requirements: [], model_present: true, ready: true, selected: false, loaded: false, ...o,
});
function catalog(over = {}) {
  return {
    env: { os: 'darwin', arch: 'arm64', key: 'mac-arm64', label: 'Mac (Apple 칩)', macos_version: '15.0', wsl2: false, ram_mb: 16384 },
    recommended: 'rec', selected: 'rec', selected_valid: true, legacy_value: null,
    models: [
      mk({ id: 'rec', label: '권장 모델', tags: ['권장'], tagline: '가장 균형이 좋음', bench: bench(4.2, 1.1, 1500), notes: ['첫 실행이 느립니다'], selected: true, ready: true }),
      mk({ id: 'acc', label: '고정확 모델', tags: ['고정확'], bench: bench(3.1, 2.4, 3000), size_mb: 3200, ready: false, model_present: false,
        requirements: [{ id: 'pkg', label: 'mlx-qwen3 패키지', kind: 'pip', satisfied: false, hint: 'pip install mlx-qwen3', can_install: false, size_mb: 0 }] }),
      mk({ id: 'lite', label: '경량 모델', tags: ['경량'], bench: null, size_mb: 400, ready: false, model_present: false,
        requirements: [{ id: 'bin', label: '실행 파일', kind: 'binary', satisfied: false, hint: '', can_install: true, size_mb: 30 }] }),
      mk({ id: 'ok2', label: '준비된 모델', size_mb: 0, ready: true }),
    ],
    unsupported: [mk({ id: 'apple', label: '애플 음성', engine: 'apple_speech', supported: false, unsupported_reason: 'macOS 26 이상에서만 쓸 수 있습니다' })],
    setup: null, ...over,
  };
}

async function build(cat = catalog()) {
  const env = createDomEnv(INDEX_HTML);
  _doms.push(env.dom);
  const { window } = env;
  window.API_BASE = ''; window._tokenQuery = ''; window.fitAndResize = () => {};
  const puts = []; const posts = []; const dels = [];
  window.fetch = (url, opts) => {
    const u = String(url);
    const res = (b) => Promise.resolve({ ok: true, json: () => Promise.resolve(b) });
    if (opts && opts.method === 'PUT') { puts.push(JSON.parse(opts.body)); return res({ ok: true }); }
    if (opts && opts.method === 'POST') posts.push({ u, body: opts.body ? JSON.parse(opts.body) : null });
    if (opts && opts.method === 'DELETE') { dels.push(JSON.parse(opts.body)); return res({ deleted: true, freed_bytes: 1048576 }); }
    if (u.includes('/voice/stt/catalog')) return res(cat);
    if (u.includes('/voice/stt/status')) return res({ available: true, loaded: false, engine: 'mlx_qwen3', model: 'rec', rss_mb: 0, setup: null, last_error: cat.last_error || null });
    if (u.includes('/voice/stt/setup')) return res({ id: 'j', model: 'acc', state: 'running', steps: [{ id: 'model', label: '모델', state: 'running', done: 1, total: 4 }], error: null });
    if (u.includes('/voice/stt/model')) return res({
      models: [{ model_id: 'rec', label: '권장 모델', engine: 'mlx_qwen3', size_bytes: 1.5 * 1024 ** 3, selected: true },
        { model_id: 'old', label: '옛 모델', engine: 'mlx_qwen3', size_bytes: 300 * 1024 ** 2, selected: false }],
      binaries: [{ id: 'llama', label: 'llama.cpp 실행 파일', path: '/x', size_bytes: 50 * 1024 ** 2 }] });
    if (u.includes('/api/hooks/status')) return res({ ok: true, events: {} });
    return res({});
  };
  window.matchMedia = (q) => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const cache = new Map();
  await importFresh(J('../js/ui/toast.js'), env.context, cache);
  await importFresh(J('../js/core/settings.js'), env.context, cache);
  const P = await importFresh(J('../js/panels/settings.js'), env.context, cache);
  const document = window.document;
  P.showSettings();
  Array.from(document.querySelectorAll('.vt-set-navitem')).find((b) => b.textContent === '음성 및 알림').click();
  await flush();
  return { document, P, puts, posts, dels };
}
const cards = (d) => Array.from(d.querySelectorAll('.vt-vm-card'));
const cardOf = (d, id) => d.querySelector(`[role="radio"][data-id="${id}"]`).closest('.vt-vm-card');
const btnByText = (root, re) => Array.from(root.querySelectorAll('button')).find((b) => re.test(b.textContent));
async function dialog(d, action) { // confirmDialog 안의 버튼
  const b = d.querySelector(action === 'ok' ? '.vt-dialog-ok' : '.vt-dialog-cancel');
  assert.ok(b, '확인 창이 떠야 한다');
  b.click(); await flush();
}

test('카드 — 권장 배지는 하나뿐이고 강조 클래스를 갖는다', async () => {
  const { document } = await build();
  const rec = Array.from(document.querySelectorAll('.vt-vm-card .vt-tag')).filter((t) => t.textContent === '권장');
  assert.strictEqual(rec.length, 1);
  assert.ok(rec[0].classList.contains('vt-vm-tag-rec'));
  assert.strictEqual(cards(document).length, 4);
});

test('카드 — 라디오 역할·aria-checked, 측정 없으면 문구, 디스크 0은 시스템이 관리', async () => {
  const { document } = await build();
  assert.ok(document.querySelector('[role="radiogroup"]'));
  const radios = document.querySelectorAll('[role="radio"]');
  assert.strictEqual(Array.from(radios).filter((r) => r.getAttribute('aria-checked') === 'true').length, 1);
  assert.match(cardOf(document, 'lite').textContent, /아직 측정하지 않음/);
  assert.match(cardOf(document, 'rec').textContent, /4\.2%/);
  assert.match(cardOf(document, 'rec').textContent, /한국어 짧은 문장 20개/);
  assert.match(cardOf(document, 'ok2').textContent, /시스템이 관리/);
  assert.match(cardOf(document, 'rec').querySelector('details.vt-vm-notes').textContent, /자세히/);
  assert.match(cardOf(document, 'rec').textContent, /사용 가능/);
  assert.match(cardOf(document, 'acc').textContent, /설치 필요/);
  assert.match(cardOf(document, 'lite').textContent, /받아야 함/);
});

test('받기 — 확인 창을 거치고, 취소하면 POST가 안 나간다', async () => {
  const { document, posts } = await build();
  cardOf(document, 'lite').querySelector('[role="radio"]').click();
  await flush();
  const get = btnByText(document.querySelector('.vt-vm-check'), /^받기 \(약 30MB\)/);
  assert.ok(get, 'can_install 항목에 받기 버튼');
  get.click(); await flush();
  assert.match(document.querySelector('.vt-dialog-msg').textContent, /30MB/);
  await dialog(document, 'cancel');
  assert.strictEqual(posts.filter((p) => p.u.includes('/voice/stt/setup')).length, 0);
  btnByText(document.querySelector('.vt-vm-check'), /^모델 받기 \(약 400MB\)/).click(); await flush();
  await dialog(document, 'cancel');
  assert.strictEqual(posts.filter((p) => p.u.includes('/voice/stt/setup')).length, 0);
  // 확인하면 그때 나간다
  btnByText(document.querySelector('.vt-vm-check'), /^받기 \(약 30MB\)/).click(); await flush();
  await dialog(document, 'ok');
  const sent = posts.filter((p) => p.u.includes('/voice/stt/setup'));
  assert.strictEqual(sent.length, 1);
  assert.deepEqual(sent[0].body, { model: 'lite', step: 'req:bin' });
});

test('체크리스트 — pip 항목은 복사 가능한 명령만 보이고 받기 버튼이 없다', async () => {
  const { document, posts } = await build();
  cardOf(document, 'acc').querySelector('[role="radio"]').click();
  await flush();
  const chk = document.querySelector('.vt-vm-check');
  assert.strictEqual(chk.querySelector('code').textContent, 'pip install mlx-qwen3');
  assert.ok(btnByText(chk, /^복사$/));
  assert.ok(!btnByText(chk, /^받기/));
  assert.strictEqual(posts.length, 0);
  assert.ok(!btnByText(chk, /이 모델 사용/), 'ready 전에는 사용 확정 버튼 없음');
});

test('선택 — ready 카드는 확인 없이 바로 voice.sttModel 설정', async () => {
  const { document, puts } = await build();
  cardOf(document, 'ok2').querySelector('[role="radio"]').click();
  await flush();
  assert.ok(!document.querySelector('.vt-dialog-ok'), '확인 창 없음');
  assert.ok(puts.some((p) => JSON.stringify(p).includes('ok2')), JSON.stringify(puts));
});

test('legacy 값이면 안내 문구를 보인다', async () => {
  const { document } = await build(catalog({ selected: 'base', selected_valid: false, legacy_value: 'base' }));
  const n = document.querySelector('.vt-vm-legacy');
  assert.strictEqual(n.hidden, false);
  assert.match(n.textContent, /이전에 고른 모델\(‘base’\)은 더 이상 지원하지 않습니다\. 아래에서 모델을 다시 골라 주세요/);
  assert.strictEqual(document.querySelectorAll('[role="radio"][aria-checked="true"]').length, 0);
});

test('못 쓰는 모델은 접힌 영역에 사유와 함께', async () => {
  const { document } = await build();
  const det = Array.from(document.querySelectorAll('details')).find((d) => /이 서버에서는 쓸 수 없는 모델/.test(d.textContent));
  assert.ok(det && !det.open);
  assert.match(det.textContent, /macOS 26 이상/);
  // 못 쓰는 엔진은 직접 입력에서 비활성이 아니다(apple은 목록 대상 아님) — mlx는 사용 가능
  assert.ok(!document.querySelector('.vt-vm-custom option[value="mlx_qwen3"]').disabled);
});

test('직접 입력 — 잘못된 형식은 거부하고, 올바르면 custom id로 준비 흐름', async () => {
  const { document, posts } = await build();
  const box = document.querySelector('.vt-vm-custom');
  const input = box.querySelector('input');
  for (const bad of ['a', 'a/b/c', 'https://x.com/a/b', '../x', 'a/..', 'a b/c']) {
    input.value = bad; btnByText(box, /준비하기/).click();
    assert.match(box.textContent, /형식이 맞지 않습니다/, bad);
  }
  input.value = 'my-org/Qwen3_ASR.v1'; btnByText(box, /준비하기/).click(); await flush();
  btnByText(document.querySelector('.vt-vm-check'), /^모델 받기/).click(); await flush();
  await dialog(document, 'ok');
  const sent = posts.find((p) => p.u.includes('/voice/stt/setup'));
  assert.deepEqual(sent.body, { model: 'custom:mlx_qwen3:my-org/Qwen3_ASR.v1', step: 'model' });
});

test('관리 — 삭제는 확인 창을 거치고 선택 중이면 경고, 취소하면 DELETE 없음', async () => {
  const { document, dels } = await build();
  const items = Array.from(document.querySelectorAll('.vt-vm-manage .vt-set-model-item'));
  assert.strictEqual(items.length, 3);
  btnByText(items[0], /삭제/).click(); await flush();
  assert.match(document.querySelector('.vt-dialog-msg').textContent, /지금 선택 중인 모델입니다/);
  await dialog(document, 'cancel');
  assert.strictEqual(dels.length, 0);
  btnByText(items[2], /삭제/).click(); await flush();
  await dialog(document, 'ok');
  assert.deepEqual(dels, [{ binary: 'llama' }]);
});

test('마지막 오류가 있으면 상태 줄에 보인다', async () => {
  const { document } = await build(catalog({ last_error: '모델을 읽지 못했습니다' }));
  const e = document.querySelector('.vt-vm-error');
  assert.strictEqual(e.hidden, false);
  assert.match(e.textContent, /모델을 읽지 못했습니다/);
});

// 실서버(`GET /voice/stt/catalog`, macOS 27 애플 칩)가 돌려준 **실제 응답**을 고정한 픽스처 — 위 픽스처는 계약을 보고
// 손으로 만든 것이라, 서버 응답 형태가 어긋나면 이 테스트가 먼저 잡는다(2026-10-03 채집).
test('실제 서버 응답 — 모든 카드가 그려지고 권장·선택·특징·측정치가 서버 값 그대로 보인다', async () => {
  const real = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/stt-catalog-server.json'), 'utf8'));
  const { document } = await build(real);
  assert.strictEqual(cards(document).length, real.models.length);
  const recTags = Array.from(document.querySelectorAll('.vt-vm-card .vt-tag')).filter((t) => t.textContent === '권장');
  assert.strictEqual(recTags.length, 1);
  assert.strictEqual(recTags[0].closest('.vt-vm-card'), cardOf(document, real.recommended));
  const checked = Array.from(document.querySelectorAll('[role="radio"]')).filter((r) => r.getAttribute('aria-checked') === 'true');
  assert.strictEqual(checked.length, 1);
  assert.strictEqual(checked[0].getAttribute('data-id'), real.selected);
  for (const m of real.models) {
    const text = cardOf(document, m.id).textContent;
    assert.ok(text.includes(m.label), `${m.id}: 이름`);
    assert.ok(text.includes(m.tagline.slice(0, 12)), `${m.id}: 특징 한 줄`);
    if (m.bench) assert.ok(text.includes(String(m.bench.cer)), `${m.id}: 정확도 수치`);
    else assert.match(text, /아직 측정하지 않음/, `${m.id}: 측정 없음 문구`);
  }
});

