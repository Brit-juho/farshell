// 위험 포트 공개 확인(시안 P06-d, panels/ports/danger-confirm.js) — 포트 번호를 그대로 입력해야 「공개」가 켜지고,
// 그 확인을 받은 뒤에만 force_danger:true 를 보낸다. 위험 판정은 서버(409 dangerous_port)만 한다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');

const MOD = path.join(__dirname, '../js/panels/ports/danger-confirm.js');
const wins = [];
after(() => wins.forEach((w) => { try { w.close(); } catch (_) {} }));

async function load() {
  const env = createDomEnv('<!doctype html><html><body></body></html>');
  wins.push(env.window);
  const mod = await importFresh(MOD, env.context, new Map());
  return { mod, window: env.window, document: env.document };
}

function parts(t) {
  const dlg = t.document.querySelector('.vt-dialog');
  return {
    dlg,
    input: dlg.querySelector('.vt-pt-danger-input'),
    ok: dlg.querySelector('.vt-dialog-ok'),
    cancel: dlg.querySelector('.vt-dialog-cancel'),
  };
}
function type(t, input, v) {
  input.value = v;
  input.dispatchEvent(new t.window.Event('input', { bubbles: true }));
}

const REASON = 'Chrome 원격 디버깅 — 로그인된 브라우저 전체를 원격으로 조작할 수 있습니다';

test('portTypedMatches — 앞뒤 공백만 무시하고 글자 그대로 같아야 한다', async () => {
  const { mod } = await load();
  assert.equal(mod.portTypedMatches('9222', 9222), true);
  assert.equal(mod.portTypedMatches('  9222\n', 9222), true, '붙여넣기의 앞뒤 공백·개행');
  for (const bad of ['', '922', '92222', '09222', '9229', '9 222', '9222a', null, undefined]) {
    assert.equal(mod.portTypedMatches(bad, 9222), false, `${JSON.stringify(bad)}`);
  }
});

test('공개 버튼은 처음엔 꺼져 있고, 틀린 번호로는 계속 꺼져 있으며, 정확한 번호에서만 켜진다', async () => {
  const t = await load();
  const done = t.mod.confirmDangerousPort(9222, REASON);
  const p = parts(t);
  assert.ok(p.ok.disabled, '처음엔 꺼짐');
  assert.equal(t.document.activeElement, p.cancel, '기본 초점은 취소');
  assert.ok(p.dlg.textContent.includes(REASON), '서버가 준 이유를 보인다');
  assert.ok(p.dlg.textContent.includes('포트 번호를 그대로 입력'), '무엇을 해야 하는지 설명한다');
  assert.equal(p.dlg.querySelector('.vt-btn.solid'), null, '위험 확인은 채운 버튼이 없다');
  for (const wrong of ['9', '922', '9229', '92220', '09222', '2375']) {
    type(t, p.input, wrong);
    assert.ok(p.ok.disabled, `${wrong}: 꺼진 채`);
  }
  // 꺼진 버튼을 억지로 눌러도(클릭 이벤트 직접 발사) 열리지 않는다.
  p.ok.disabled = false; p.ok.click();
  assert.ok(t.document.querySelector('.vt-dialog'), '틀린 번호로는 닫히지 않는다');
  assert.ok(p.ok.disabled, '누르는 순간 다시 판정해 끈다');
  type(t, p.input, '9222');
  assert.equal(p.ok.disabled, false, '정확한 번호에서 켜짐');
  type(t, p.input, '92221');
  assert.ok(p.ok.disabled, '다시 틀리면 다시 꺼짐');
  type(t, p.input, ' 9222 ');
  p.ok.click();
  assert.equal(await done, true);
  assert.equal(t.document.querySelector('.vt-dialog'), null, '닫힘');
});

test('Enter 는 번호가 맞을 때만 확정한다', async () => {
  const t = await load();
  const done = t.mod.confirmDangerousPort(4040, 'ngrok');
  const p = parts(t);
  const enter = () => p.input.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  type(t, p.input, '404');
  enter();
  assert.ok(t.document.querySelector('.vt-dialog'), '틀린 번호 + Enter 는 아무 일도 없다');
  type(t, p.input, '4040');
  enter();
  assert.equal(await done, true);
});

test('취소·Esc 는 false — 번호를 맞게 입력해 둔 뒤라도', async () => {
  const t = await load();
  const d1 = t.mod.confirmDangerousPort(9222, REASON);
  const p1 = parts(t);
  type(t, p1.input, '9222');
  p1.cancel.click();
  assert.equal(await d1, false);

  const d2 = t.mod.confirmDangerousPort(9222, REASON);
  type(t, parts(t).input, '9222');
  t.document.dispatchEvent(new t.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(await d2, false);
});

function dangerErr(reason = REASON) {
  const e = new Error('dangerous_port');
  e.status = 409;
  e.data = { ok: false, error: 'dangerous_port', reason };
  return e;
}

test('exposeWithDangerConfirm — 위험 포트가 아니면 한 번만 보내고 force_danger 를 붙이지 않는다', async () => {
  const t = await load();
  const sent = [];
  const out = await t.mod.exposeWithDangerConfirm(3000, async (extra) => { sent.push(extra); return { ok: true, url: 'u' }; });
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [{}]);
  assert.equal(out.result.url, 'u');
  assert.equal(t.document.querySelector('.vt-dialog'), null, '확인 창을 띄우지 않는다');
});

test('exposeWithDangerConfirm — 409 dangerous_port 면 번호 확인 후에만 force_danger:true 로 다시 보낸다', async () => {
  const t = await load();
  const sent = [];
  const send = async (extra) => {
    sent.push(JSON.parse(JSON.stringify(extra)));
    if (extra.force_danger !== true) throw dangerErr();
    return { ok: true, url: 'https://x' };
  };
  const p = t.mod.exposeWithDangerConfirm(9222, send);
  await new Promise((r) => setTimeout(r, 0));
  const ui = parts(t);
  assert.deepEqual(sent, [{}], '확인 전에는 force 없이 한 번만');
  type(t, ui.input, '9229');
  assert.ok(ui.ok.disabled);
  assert.deepEqual(sent, [{}], '틀린 번호로는 아무것도 더 보내지 않는다');
  type(t, ui.input, '9222');
  ui.ok.click();
  const out = await p;
  assert.deepEqual(sent, [{}, { force_danger: true }]);
  assert.equal(out.result.url, 'https://x');
});

test('exposeWithDangerConfirm — 확인을 취소하면 force 요청을 보내지 않는다. 다른 실패는 그대로 던진다', async () => {
  const t = await load();
  const sent = [];
  const p = t.mod.exposeWithDangerConfirm(9222, async (extra) => { sent.push(extra); throw dangerErr(); });
  await new Promise((r) => setTimeout(r, 0));
  parts(t).cancel.click();
  assert.equal((await p).canceled, true);
  assert.equal(sent.length, 1);

  const other = Object.assign(new Error('network_mode'), { status: 409, data: { error: 'network_mode' } });
  await assert.rejects(t.mod.exposeWithDangerConfirm(3000, async () => { throw other; }), /network_mode/);
});
