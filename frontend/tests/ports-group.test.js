// U4/D4(60-settings-palette.md §6) — 포트 대시보드의 pid 그룹핑. 서버(portscan.py:
// scan)가 (protected, port) 오름차순으로 내려주므로, 같은 pid가 처음 등장하는
// 자리가 곧 그 pid의 가장 낮은 포트다 — _groupByPid는 그 순서를 그대로 대표
// 선정 기준으로 쓴다(별도 정렬 없음). 이 테스트는 그 불변조건을 고정한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const PORTS_JS = path.join(__dirname, '../js/panels/ports/ports.js');

const _doms = [];
after(() => { for (const d of _doms) { try { d.window.close(); } catch (_) {} } });

async function load() {
  const env = createDomEnv('<!doctype html><html><body><div id="app"></div></body></html>');
  _doms.push(env.dom);
  const cache = new Map();
  const ports = await importFresh(PORTS_JS, env.context, cache);
  return ports;
}

test('_groupByPid — 같은 pid는 처음 등장한(=가장 낮은 포트) 항목을 대표로 묶는다', async () => {
  const { _groupByPid } = await load();
  const input = [
    { port: 5173, pid: 48213, cmd: 'node · vite', protected: false },
    { port: 5174, pid: 48213, cmd: 'node · vite', protected: false },   // hmr 소켓, 같은 pid
    { port: 7777, pid: 1204, cmd: 'python · farshell', protected: false },
    { port: 22, pid: 1, cmd: 'sshd', protected: true },
  ];
  const groups = _groupByPid(input);

  assert.strictEqual(groups.length, 3, '고유 pid 개수만큼 그룹이 생긴다');

  assert.strictEqual(groups[0].rep.port, 5173, '같은 pid 중 먼저 나온(=가장 낮은) 포트가 대표');
  assert.strictEqual(groups[0].subs.length, 1);
  assert.strictEqual(groups[0].subs[0].port, 5174, '나머지는 subs로 들어간다');

  assert.strictEqual(groups[1].rep.port, 7777);
  assert.strictEqual(groups[1].subs.length, 0, 'pid가 하나뿐이면 subs가 비어 있다');

  assert.strictEqual(groups[2].rep.port, 22);
  assert.strictEqual(groups[2].subs.length, 0);
});

test('_groupByPid — pid가 전부 다르면 행 개수가 그대로 유지된다(그룹 1개씩)', async () => {
  const { _groupByPid } = await load();
  const input = [
    { port: 3000, pid: 100, cmd: 'a', protected: false },
    { port: 4000, pid: 200, cmd: 'b', protected: false },
    { port: 6379, pid: 300, cmd: 'redis-server', protected: false },
  ];
  const groups = _groupByPid(input);
  assert.strictEqual(groups.length, 3);
  for (const g of groups) assert.strictEqual(g.subs.length, 0);
});

test('_groupByPid — 세 개 이상의 포트가 같은 pid를 쓰면 전부 한 그룹의 subs로 모인다', async () => {
  const { _groupByPid } = await load();
  const input = [
    { port: 8000, pid: 555, cmd: 'x', protected: false },
    { port: 8001, pid: 555, cmd: 'x', protected: false },
    { port: 8002, pid: 555, cmd: 'x', protected: false },
  ];
  const groups = _groupByPid(input);
  assert.strictEqual(groups.length, 1);
  assert.strictEqual(groups[0].rep.port, 8000);
  // vm 모듈 경계를 넘는 배열은 realm이 달라 deepStrictEqual이 reference로
  // 어긋난다 — rail-data.test.js와 같은 방식으로 Array.from을 거쳐 비교한다.
  assert.deepStrictEqual(Array.from(groups[0].subs, p => p.port), [8001, 8002]);
});

test('_groupByPid — 빈 목록은 빈 그룹을 반환한다', async () => {
  const { _groupByPid } = await load();
  assert.deepStrictEqual(Array.from(_groupByPid([])), []);
});
