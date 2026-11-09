// 리디자인 5단계(시안 P01) — 소스컨트롤 패널의 순수 규칙들.
//   1. 상태 글자: 두 칸 상태 코드를 한 글자로(미추적은 U, 병합 충돌은 !) — 색만이 아니라 글자로 말한다.
//   2. 커밋 시각: 「12분」·「2시간」·「3일」, 30일이 넘으면 날짜.
//   3. 바뀐 낱말: diff의 del → add 짝에서 앞뒤 공통 부분을 뺀 가운데만 표시한다 — 줄 전체를 칠하지 않는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const GIT_JS = path.join(__dirname, '../js/panels/viewer/git.js');
const DIFF_JS = path.join(__dirname, '../js/panels/viewer/diff.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load(file) {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  _doms.push(env.dom);
  return importFresh(file, env.context, new Map());
}

test('상태 글자 — 미추적은 U, 병합 충돌은 !, 나머지는 보는 쪽(스테이지/작업 트리)의 코드', async () => {
  const { _gitStatusLetter } = await load(GIT_JS);
  assert.strictEqual(_gitStatusLetter({ index_status: '?', worktree_status: '?' }, false), 'U');
  assert.strictEqual(_gitStatusLetter({ index_status: 'U', worktree_status: 'U' }, false), '!');
  assert.strictEqual(_gitStatusLetter({ index_status: 'M', worktree_status: 'D' }, true), 'M');
  assert.strictEqual(_gitStatusLetter({ index_status: 'M', worktree_status: 'D' }, false), 'D');
  assert.strictEqual(_gitStatusLetter({ index_status: 'A', worktree_status: '' }, false), 'A');
});

test('커밋 시각 — 가장 큰 단위 하나, 30일이 넘으면 날짜, 못 읽으면 빈 글자', async () => {
  const { relCommitTime } = await load(GIT_JS);
  const now = Date.parse('2026-11-09T12:00:00Z');
  assert.strictEqual(relCommitTime('2026-11-09T11:59:30Z', now), '방금');
  assert.strictEqual(relCommitTime('2026-11-09T11:48:00Z', now), '12분');
  assert.strictEqual(relCommitTime('2026-11-09T10:00:00Z', now), '2시간');
  assert.strictEqual(relCommitTime('2026-11-06T12:00:00Z', now), '3일');
  assert.strictEqual(relCommitTime('2026-09-01T12:00:00+09:00', now), '2026-09-01');
  assert.strictEqual(relCommitTime('', now), '');
});

test('바뀐 낱말 — 공통 앞뒤를 빼고 낱말 경계까지 넓힌다', async () => {
  const { changedSpans } = await load(DIFF_JS);
  const a = '레일 폭 252, 행은 세 줄.';
  const b = '레일 폭 300, 행은 세 줄.';
  const [da, db] = changedSpans(a, b);
  assert.strictEqual(a.slice(da[0], da[1]), '252');
  assert.strictEqual(b.slice(db[0], db[1]), '300');
  // 낱말 가운데에서 갈라지지 않는다 — "waiting"→"working"은 낱말 전체가 바뀐 구간이다.
  const [x, y] = changedSpans('state is waiting now', 'state is working now');
  assert.strictEqual('state is waiting now'.slice(x[0], x[1]), 'waiting');
  assert.strictEqual('state is working now'.slice(y[0], y[1]), 'working');
});

test('바뀐 낱말 — 거의 다 바뀐 줄과 같은 줄은 표시하지 않는다', async () => {
  const { changedSpans } = await load(DIFF_JS);
  assert.strictEqual(changedSpans('abc', 'xyz'), null);
  assert.strictEqual(changedSpans('same', 'same'), null);
  // 끝에 덧붙인 경우는 뒤쪽만 바뀐 구간이다.
  const [da, db] = changedSpans('foo(a)', 'foo(a, b)');
  assert.ok(da[1] - da[0] <= 1, JSON.stringify(da));
  assert.strictEqual('foo(a, b)'.slice(db[0], db[1]).includes(', b'), true);
});
