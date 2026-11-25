// 저장소 표식(shell/repo-marks.ts) — 그 맥의 전역 설정 rail.repoMarks의 읽기·쓰기·정화.
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
const plain = (o) => JSON.parse(JSON.stringify(o));

test('parseRepoMarks — 깨진 값·모르는 이름·나쁜 id는 버리고 기본은 남기지 않는다', async () => {
  const { parseRepoMarks } = await mod();
  assert.deepStrictEqual(plain(parseRepoMarks('')), {});
  assert.deepStrictEqual(plain(parseRepoMarks('{')), {});
  assert.deepStrictEqual(plain(parseRepoMarks('[]')), {});
  assert.deepStrictEqual(plain(parseRepoMarks('{"marks":[1]}')), {});
  const v = JSON.stringify({ marks: {
    a1: { icon: 'code', color: 'blue' },
    a2: { icon: '<img onerror=x>', color: 'red' },     // 모르는 아이콘 → 폴더, 색은 남는다
    a3: { icon: 'server', color: 'url(javascript:1)' }, // 모르는 색 → 기본 색
    a4: { icon: 'folder', color: '' },                   // 기본과 같으면 버린다
    'bad id!': { icon: 'code', color: 'blue' },
    a5: 'code',
  } }).replace('{"a1"', '{"__proto__":{"icon":"code","color":"blue"},"a1"');
  assert.deepStrictEqual(plain(parseRepoMarks(v)), {
    a1: { icon: 'code', color: 'blue' },
    a2: { icon: 'folder', color: 'red' },
    a3: { icon: 'server', color: '' },
  });
});

test('withMark — 일부만 바꾸면 나머지는 유지, 기본이 되면 지우고, 바꾼 것은 끝으로', async () => {
  const { withMark } = await mod();
  let m = withMark({}, 'p1', { icon: 'code' });
  assert.deepStrictEqual(plain(m), { p1: { icon: 'code', color: '' } });
  m = withMark(m, 'p2', { color: 'green' });
  m = withMark(m, 'p1', { color: 'red' });
  assert.deepStrictEqual(Object.keys(m), ['p2', 'p1']);
  assert.deepStrictEqual(plain(m.p1), { icon: 'code', color: 'red' });
  m = withMark(m, 'p2', { color: '' });
  assert.deepStrictEqual(Object.keys(m), ['p1']);
  m = withMark(m, 'p1', null);
  assert.deepStrictEqual(plain(m), {});
  assert.deepStrictEqual(plain(withMark({}, 'x y', { icon: 'code' })), {});
});

test('encodeRepoMarks — 왕복이 같고, 상한을 넘으면 오래된 것부터 뺀다', async () => {
  const { encodeRepoMarks, parseRepoMarks, withMark, MARKS_MAX } = await mod();
  let m = {};
  for (let i = 0; i < MARKS_MAX + 5; i++) m = withMark(m, 'p' + i, { icon: 'star' });
  const back = parseRepoMarks(encodeRepoMarks(m));
  const ids = Object.keys(back);
  assert.strictEqual(ids.length, MARKS_MAX);
  assert.strictEqual(ids[0], 'p5');
  assert.strictEqual(ids.at(-1), 'p' + (MARKS_MAX + 4));
});

test('markOf·markIconHtml·markStyle — 고정 데이터만 그린다', async () => {
  const { markOf, markIconHtml, markStyle, MARK_ICONS, MARK_COLOR_NAMES } = await mod();
  assert.strictEqual(markOf({}, 'constructor'), null);
  assert.strictEqual(markOf({ a: { icon: 'code', color: '' } }, null), null);
  for (const name of MARK_ICONS) assert.match(markIconHtml(name, 14), /^<svg [^>]+>.+<\/svg>$/s, name);
  assert.strictEqual(markIconHtml('nope', 14), markIconHtml('folder', 14));
  assert.strictEqual(markStyle(null), undefined);
  assert.strictEqual(markStyle({ icon: 'code', color: '' }), undefined);
  assert.deepStrictEqual(plain(markStyle({ icon: 'code', color: 'blue' }, false)), { color: 'var(--color-term-blue)' });
  assert.match(markStyle({ icon: 'code', color: 'blue' }).background, /color-mix/);
  assert.ok(MARK_COLOR_NAMES.includes(''));
});

test('readRailPrefs가 표식을 읽고, 설정 스키마에 키가 있다', async () => {
  const { readRailPrefs, SETTINGS_SCHEMA, MARKS_KEY } = await mod();
  assert.ok(SETTINGS_SCHEMA[MARKS_KEY], 'core/settings.js SCHEMA에 rail.repoMarks');
  assert.notStrictEqual(SETTINGS_SCHEMA[MARKS_KEY].scope, 'device');
  const p = readRailPrefs((k) => (k === MARKS_KEY ? '{"marks":{"p1":{"icon":"globe","color":"cyan"}}}' : undefined));
  assert.deepStrictEqual(plain(p.marks), { p1: { icon: 'globe', color: 'cyan' } });
  assert.deepStrictEqual(plain(readRailPrefs(() => undefined).marks), {});
});
