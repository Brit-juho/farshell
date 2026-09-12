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
