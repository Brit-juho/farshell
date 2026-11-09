// N19~N21(50-files-share.md §4) — dock 파일 탭의 표시 로직. 행의 두 번째 줄에
// 들어가는 세 값(공유 라벨 · 크기 · 남은 기간)이 계약이다. 특히 "남은 기간"은
// 공유 중/고정 파일이 TTL 정리에서 제외된다는 §1 규칙의 화면 쪽 짝이라,
// 서버가 expires_at을 null로 내려줄 때 기간이 아니라 **이유**를 써야 한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const FILES_JS = path.join(__dirname, '../js/panels/files/files.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

let _mod = null;
async function load() {
  if (_mod) return _mod;
  const env = createDomEnv('<!doctype html><html><body><div id="app"></div></body></html>');
  _doms.push(env.dom);
  _mod = await importFresh(FILES_JS, env.context, new Map());
  return _mod;
}

const NOW = 1_757_000_000; // 고정 기준 시각(초)

test('fmtBytes — 단위가 넘어가는 경계에서 자릿수가 뭉개지지 않는다', async () => {
  const { fmtBytes } = await load();
  assert.strictEqual(fmtBytes(0), '0B');
  assert.strictEqual(fmtBytes(512), '512B');
  assert.strictEqual(fmtBytes(18 * 1024), '18KB');
  assert.strictEqual(fmtBytes(248 * 1024 * 1024), '248MB');
  assert.strictEqual(fmtBytes(2 * 1024 ** 3), '2.0GB');
});

test('fmtUploadDate — 같은 해면 월-일 시:분, 해가 다르면 연도까지', async () => {
  const { fmtUploadDate } = await load();
  const now = new Date(NOW * 1000);
  const sameYear = new Date(now.getFullYear(), 8, 5, 14, 7).getTime() / 1000; // 9월 5일 14:07
  assert.strictEqual(fmtUploadDate({ created: sameYear }, now), '09-05 14:07');
  const lastYear = new Date(now.getFullYear() - 1, 11, 25, 9, 3).getTime() / 1000;
  assert.strictEqual(fmtUploadDate({ created: lastYear }, now), `${now.getFullYear() - 1}-12-25 09:03`);
  assert.strictEqual(fmtUploadDate({ created: 0 }, now), '');
});

test('fmtRemaining — 공유 중·고정 파일은 기간 대신 이유를 쓴다(§1 정리 제외 규칙)', async () => {
  const { fmtRemaining } = await load();
  assert.strictEqual(fmtRemaining({ pin: true, expires_at: null }, NOW), '고정됨');
  assert.strictEqual(fmtRemaining({ shares: [{ shareId: 's' }], expires_at: null }, NOW), '공유 중');
  // 공유도 고정도 아닌데 만료 시각이 없다 = 알 수 없음. 0일로 속이지 않는다.
  assert.strictEqual(fmtRemaining({ shares: [], expires_at: null }, NOW), '—');
});

test('fmtRemaining — 남은 시간은 가장 큰 단위 하나로', async () => {
  const { fmtRemaining } = await load();
  assert.strictEqual(fmtRemaining({ expires_at: NOW + 29 * 86400 }, NOW), '29일');
  assert.strictEqual(fmtRemaining({ expires_at: NOW + 5 * 3600 }, NOW), '5시간');
  assert.strictEqual(fmtRemaining({ expires_at: NOW + 120 }, NOW), '2분');
  // 1분 미만이라도 "0분"이 아니라 최소 1분 — 아직 살아 있다는 뜻이 남아야 한다.
  assert.strictEqual(fmtRemaining({ expires_at: NOW + 10 }, NOW), '1분');
  assert.strictEqual(fmtRemaining({ expires_at: NOW - 1 }, NOW), '만료됨');
});

test('shareLabel — 모드가 섞이면 둘 다 보여준다(PIN 여부가 사라지면 안 된다)', async () => {
  const { shareLabel } = await load();
  assert.strictEqual(shareLabel({ shares: [] }), '');
  assert.strictEqual(shareLabel({ shares: [{ mode: 'device' }] }), '내 기기');
  assert.strictEqual(shareLabel({ shares: [{ mode: 'pin' }] }), 'PIN');
  assert.strictEqual(shareLabel({ shares: [{ mode: 'device' }, { mode: 'pin' }] }), '내 기기 · PIN');
  // 같은 모드가 여러 개면 한 번만.
  assert.strictEqual(shareLabel({ shares: [{ mode: 'pin' }, { mode: 'pin' }] }), 'PIN');
});

test('absoluteShareUrl — 공유 링크는 남에게 보내는 값이라 항상 절대 URL', async () => {
  const { absoluteShareUrl } = await load();
  const url = absoluteShareUrl('/s/v1.123.abc.def.sig');
  assert.match(url, /^https?:\/\/[^/]+\/s\/v1\.123\.abc\.def\.sig$/);
});

// 고정 토글(D5) — 예전에는 `pin` 필드와 정리 제외 규칙만 있고 켜는 길이 없었다. 행의 버튼이 지금 상태를
// aria-pressed로 보여주고, 누르면 반대 값을 서버에 보낸다.
async function loadWithFetch() {
  const env = createDomEnv('<!doctype html><html><body><div id="app"></div></body></html>');
  _doms.push(env.dom);
  const calls = [];
  env.window.API_BASE = '';
  env.window._tokenQuery = '';
  env.window.fetch = (url, opts) => {
    calls.push({ url: String(url), opts });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true, items: [], quota: {} }) });
  };
  const mod = await importFresh(FILES_JS, env.context, new Map());
  return { mod, calls, win: env.window };
}

test('renderRow — 고정 버튼이 상태를 보여주고 누르면 반대 값을 보낸다', async () => {
  const { mod, calls } = await loadWithFetch();
  const row = mod.renderRow({ id: 'ab12', name: 'a.txt', size: 5, pin: false, shares: [], expires_at: NOW + 86400 }, null);
  const btn = row.querySelector('.vt-fl-act.pin');
  assert.ok(btn, '고정 버튼이 없다');
  assert.strictEqual(btn.getAttribute('aria-pressed'), 'false');
  btn.click();
  await new Promise((r) => setTimeout(r, 0));
  const call = calls.find((c) => c.url.startsWith('/api/files/ab12/pin'));
  assert.ok(call, `고정 요청이 없다: ${JSON.stringify(calls.map((c) => c.url))}`);
  assert.strictEqual(call.opts.method, 'POST');
  assert.deepStrictEqual(JSON.parse(call.opts.body), { pin: true });

  const pinned = mod.renderRow({ id: 'cd34', name: 'b.txt', size: 5, pin: true, shares: [], expires_at: null }, null);
  assert.strictEqual(pinned.querySelector('.vt-fl-act.pin').getAttribute('aria-pressed'), 'true');
});

// R6 — 파일 패널 드롭 한 번은 업로드 한 번이다. 패널 안 요소에 놓아도(버블링) 두 번 올라가지 않고,
// 창 수준 안내(upload-lite의 window drop)까지 올라가지 않으며, 터미널에 경로를 넣지 않는다.
test('wireFileDrop — 드롭 한 번에 업로드는 한 번, 경로는 안 꽂는다', async () => {
  const { mod, win } = await loadWithFetch();
  const doc = win.document;
  const root = doc.createElement('div');
  const inner = doc.createElement('div');
  root.appendChild(inner);
  doc.body.appendChild(root);
  mod.wireFileDrop(root);

  const uploads = [];
  win.vtUploadFiles = (dt, opts) => { uploads.push({ dt, opts }); return Promise.resolve([]); };
  let reachedWindow = 0;
  win.addEventListener('drop', () => { reachedWindow += 1; });

  const dt = { types: ['Files'], files: [{ name: 'a.txt' }], dropEffect: 'none' };
  for (const type of ['dragover', 'drop']) {
    const ev = new win.Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dt });
    inner.dispatchEvent(ev);
    assert.strictEqual(ev.defaultPrevented, true, `${type}의 기본 동작(페이지 이동)이 막혀야 한다`);
  }
  await new Promise((r) => setTimeout(r, 0));
  assert.strictEqual(uploads.length, 1, '드롭 한 번에 업로드는 한 번이어야 한다');
  assert.strictEqual(uploads[0].dt, dt);
  assert.deepStrictEqual({ ...uploads[0].opts }, { insertPath: false });
  assert.strictEqual(reachedWindow, 0, '패널이 받은 드롭은 창 수준 안내로 올라가면 안 된다');
  assert.strictEqual(root.dataset.dropfile, undefined, '드롭 뒤 강조 표시가 남으면 안 된다');

  // 파일이 아닌 드래그(탭 이동 등)는 건드리지 않는다.
  const other = new win.Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(other, 'dataTransfer', { value: { types: ['text/vt-tab-id'] } });
  inner.dispatchEvent(other);
  await new Promise((r) => setTimeout(r, 0));
  assert.strictEqual(uploads.length, 1);
  assert.strictEqual(other.defaultPrevented, false);
});
