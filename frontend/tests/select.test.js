// ui/select.js — 기본 <select>를 FarShell 드롭다운으로 바꾸는 층. 네이티브 select는 값·이벤트의 정본으로 남는다.
const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach(w => w.close()));
const flush = () => new Promise(r => setTimeout(r, 5));

async function build(html = '<select id="s" class="vt-input" aria-label="기간"><option value="a">하루</option><option value="b" selected>일주일</option><option value="c" disabled>한달</option></select>') {
  const env = createDomEnv(`<html><body>${html}</body></html>`);
  const w = env.window; wins.push(w);
  const mod = await importFresh(path.join(__dirname, '../js/ui/select.js'), env.context, new Map());
  const sel = w.document.getElementById('s');
  const changes = [];
  sel?.addEventListener('change', () => changes.push(sel.value));
  return { w, mod, sel, changes, doc: w.document, key: (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })) };
}

test('select는 숨겨지고 버튼이 선택된 글자·클래스·접근 이름을 물려받는다', async () => {
  const { mod, sel, doc } = await build();
  mod.enhanceSelect(sel);
  const btn = doc.querySelector('.vt-dd');
  assert.ok(sel.classList.contains('vt-dd-native'));
  assert.equal(btn.querySelector('.vt-dd-text').textContent, '일주일');
  assert.ok(btn.classList.contains('vt-input'));
  assert.equal(btn.getAttribute('aria-label'), '기간');
  assert.equal(btn.getAttribute('role'), 'combobox');
});

test('코드가 value를 바꾸면 change 없이도 보이는 글자가 따라가고, 옵션·disabled 변경도 반영된다', async () => {
  const { mod, sel, doc } = await build();
  mod.enhanceSelect(sel);
  const btn = doc.querySelector('.vt-dd');
  sel.value = 'a';
  assert.equal(btn.querySelector('.vt-dd-text').textContent, '하루');
  sel.innerHTML = '<option value="x">새 값</option>';
  await flush();
  assert.equal(btn.querySelector('.vt-dd-text').textContent, '새 값');
  sel.disabled = true; await flush();
  assert.equal(btn.disabled, true);
});

test('클릭으로 열고 옵션을 고르면 change가 한 번만 나고 목록이 닫힌다(비활성 옵션은 못 고른다)', async () => {
  const { mod, sel, doc, changes } = await build();
  mod.enhanceSelect(sel);
  const btn = doc.querySelector('.vt-dd');
  btn.click();
  const list = doc.querySelector('.vt-dd-list');
  assert.ok(list); assert.equal(btn.getAttribute('aria-expanded'), 'true');
  const items = list.querySelectorAll('[role=option]');
  assert.equal(items.length, 3);
  assert.equal(items[1].getAttribute('aria-selected'), 'true');
  items[2].click();                       // disabled
  assert.deepEqual(changes, []); assert.ok(doc.querySelector('.vt-dd-list'));
  items[0].click();
  assert.deepEqual(changes, ['a']);
  assert.equal(sel.value, 'a');
  assert.equal(doc.querySelector('.vt-dd-list'), null);
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
});

test('키보드: 방향키로 열고 이동·Enter로 선택, Esc는 바깥 리스너에 전파되지 않고 값을 안 바꾼다', async () => {
  const { w, mod, sel, doc, changes, key } = await build();
  mod.enhanceSelect(sel);
  const btn = doc.querySelector('.vt-dd');
  let leaked = 0; doc.addEventListener('keydown', (e) => { if (e.key === 'Escape') leaked++; });
  key(btn, 'ArrowDown');
  assert.ok(doc.querySelector('.vt-dd-list'));
  key(btn, 'Home'); key(btn, 'Enter');
  assert.deepEqual(changes, ['a']);
  key(btn, 'Enter');                      // 다시 열기
  key(btn, 'End');                        // 마지막 활성 옵션(한달은 disabled라 건너뜀)
  assert.equal(doc.querySelector('.vt-dd-item.active').textContent.includes('일주일'), true);
  key(btn, 'Escape');
  assert.equal(doc.querySelector('.vt-dd-list'), null);
  assert.equal(leaked, 0);
  assert.deepEqual(changes, ['a']);
  assert.ok(w);
});

test('글자 입력으로 점프하고 optgroup 제목이 그려진다', async () => {
  const { mod, sel, doc, changes, key } = await build('<select id="s"><optgroup label="묶음"><option value="1">apple</option><option value="2">banana</option></optgroup></select>');
  mod.enhanceSelect(sel);
  const btn = doc.querySelector('.vt-dd');
  btn.click();
  assert.equal(doc.querySelector('.vt-dd-group').textContent, '묶음');
  key(btn, 'b'); key(btn, 'Enter');
  assert.deepEqual(changes, ['2']);
});

test('data-native·multiple은 건드리지 않고, 사라진 select의 버튼은 정리된다', async () => {
  const { w, mod, doc } = await build('<select id="n" data-native><option>a</option></select><select multiple><option>a</option></select><div id="host"><select id="s"><option value="1">하나</option></select></div>');
  mod.autoEnhanceSelects(doc);
  assert.equal(doc.querySelectorAll('.vt-dd').length, 1);       // #s만
  doc.getElementById('host').remove();
  await flush();
  assert.equal(doc.querySelectorAll('.vt-dd').length, 0);
  const late = doc.createElement('select'); late.innerHTML = '<option>z</option>';
  doc.body.append(late); await flush();
  assert.equal(doc.querySelectorAll('.vt-dd').length, 1);        // 나중에 생긴 select도 자동
  assert.ok(w);
});
