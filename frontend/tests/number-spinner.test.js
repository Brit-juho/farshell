const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const wins = [];
after(() => wins.forEach(w => w.close()));
const flush = () => new Promise(r => setImmediate(r));
async function build(options = {}, lang = 'ko') {
  const env = createDomEnv('<html><body></body></html>');
  const w = env.window; wins.push(w);
  if (lang === 'en') {
    w.document.documentElement.lang = 'en';
    w.eval(fs.readFileSync(path.join(__dirname, '../js/locales/en.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(__dirname, '../js/i18n-boot.js'), 'utf8'));
  }
  const { numberSpinner } = await importFresh(path.join(__dirname, '../js/ui/number-spinner.js'), env.context, new Map());
  const saved = [];
  const control = numberSpinner({ value: 14, min: 8, max: 28, onCommit: v => { saved.push(v); }, ...options });
  control.element.vtSetLabel(lang === 'en' ? 'Font size' : '글자 크기');
  w.document.body.append(control.element);
  const up = control.element.querySelector('.up'), down = control.element.querySelector('.down');
  const key = k => control.input.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  const edit = v => { control.input.value = v; control.input.dispatchEvent(new w.Event('change', { bubbles: true })); };
  return { ...control, up, down, key, edit, w, saved, numberSpinner };
}
test('bounds disable step buttons; direct input clamps and empty/invalid edits do not save', async () => {
  const c = await build();
  c.setValue(28); assert.equal(c.up.disabled, true); c.up.click(); assert.equal(c.saved.length, 0);
  c.setValue(8); assert.equal(c.down.disabled, true);
  c.edit('999'); await flush(); assert.equal(c.input.value, '28');
  c.edit(''); await flush(); assert.equal(c.input.value, '28');
  c.edit('bad'); await flush(); assert.equal(c.input.value, '28');
  assert.deepEqual(c.saved, [28]);
});
test('buttons and keyboard use step, Enter commits and unchanged blur does not duplicate', async () => {
  const c = await build();
  c.up.focus(); c.up.click(); await flush(); assert.equal(c.input.value, '15');
  assert.equal(c.w.document.activeElement, c.input);
  c.key('ArrowDown'); await flush(); assert.equal(c.input.value, '14');
  c.input.value = '20'; c.key('Enter'); await flush(); c.edit('20'); await flush();
  assert.deepEqual(c.saved, [15, 14, 20]);
  c.input.value = '25'; c.key('Escape'); assert.equal(c.input.value, '20');
});
test('decimal steps snap directionally without floating point drift', async () => {
  const c = await build({ value: 0.2, min: 0, max: 1, step: 0.1 });
  c.up.click(); await flush(); assert.equal(c.input.value, '0.3');
  c.input.value = '0.35'; c.up.click(); await flush(); assert.equal(c.input.value, '0.4');
  c.input.value = '0.35'; c.down.click(); await flush(); assert.equal(c.input.value, '0.3');
  c.edit('0.37'); await flush(); assert.equal(c.input.value, '0.4');
});
test('pending saves block repeat clicks and rejection restores value and boundaries', async () => {
  let finish; let calls = 0;
  const c = await build({ onCommit: () => { calls++; return new Promise(r => { finish = r; }); } });
  c.up.click(); c.up.click(); assert.equal(calls, 1); assert.equal(c.input.readOnly, true);
  finish(false); await flush(); assert.equal(c.input.value, '14'); assert.equal(c.input.disabled, false);
  c.setDisabled(true); assert.ok(c.up.disabled && c.down.disabled && c.input.disabled);
  c.setDisabled(false); assert.equal(c.up.disabled, false);
});
test('external updates win over stale save responses; thrown errors are reported', async () => {
  let finish;
  const c = await build({ onCommit: () => new Promise(r => { finish = r; }) });
  c.up.click(); c.setValue(22); finish(15); await flush(); assert.equal(c.input.value, '22');
  let error;
  const d = await build({ onCommit: () => { throw Error('offline'); }, onError: e => { error = e; } });
  d.down.click(); await flush(); assert.equal(d.input.value, '14'); assert.equal(error.message, 'offline');
});
test('accessible English button names and input number semantics', async () => {
  const c = await build({}, 'en');
  assert.equal(c.up.getAttribute('aria-label'), 'Increase Font size');
  assert.equal(c.down.getAttribute('aria-label'), 'Decrease Font size');
  assert.equal(c.input.getAttribute('aria-label'), 'Font size, enter value');
  assert.equal(c.input.type, 'number'); assert.equal(c.input.min, '8'); assert.equal(c.input.max, '28');
  for (const over of [{ step: 0 }, { min: 50 }, { value: NaN }, { step: Infinity }, { step: 1e-13 }]) {
    assert.throws(() => c.numberSpinner({ value: 14, min: 8, max: 28, onCommit() {}, ...over }), /spinner/);
  }
});
