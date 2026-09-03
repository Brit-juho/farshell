// F0: js/ui/toast.js — showToast 통합본.
//
// 이 파일이 지키는 실버그(회귀 방지):
//   예전엔 showToast 가 picker.js:71(타입 지원)과 grid.js:377(#agent-toast 싱글톤,
//   타입 미지원)에 두 벌 있었고 bootstrap manifest 순서상 grid.js 가 이겼다. 그래서
//   picker/terminal/snippets 11곳이 넘기던 'error'/'success' 가 조용히 버려졌고
//   (에러 토스트가 중립색으로 떴다), 싱글톤이라 연속 토스트가 서로를 덮어썼다.
//   → 아래 테스트는 "타입이 클래스로 반영되는가"와 "쌓이는가"를 둘 다 고정한다.
//   key 옵션(잦은 에이전트 도구 이벤트용 제자리 교체)도 함께 고정한다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const TOAST_JS = fs.readFileSync(path.join(__dirname, '../js/ui/toast.js'), 'utf8');

const _windows = [];
after(() => { for (const w of _windows) { try { w.close(); } catch (_) {} } });

function buildWindow() {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://localhost/', runScripts: 'dangerously',
  });
  const { window } = dom;
  _windows.push(window);
  const script = window.document.createElement('script');
  script.textContent = TOAST_JS;
  window.document.body.appendChild(script);
  return window;
}

const toasts = (w) => [...w.document.querySelectorAll('#vt-toasts .vt-toast')];

test('타입이 CSS 클래스로 반영된다 (덮어써지던 실버그)', () => {
  const w = buildWindow();
  w.showToast('중립');
  w.showToast('실패', 'error');
  w.showToast('성공', 'success');

  const els = toasts(w);
  assert.strictEqual(els.length, 3);
  assert.ok(els[0].classList.contains('info'), 'info 클래스');
  assert.ok(els[1].classList.contains('err'), 'error → err 클래스');
  assert.ok(els[2].classList.contains('ok'), 'success → ok 클래스');
});

test('알 수 없는 타입은 info 로 떨어진다', () => {
  const w = buildWindow();
  w.showToast('??', 'nonsense');
  assert.ok(toasts(w)[0].classList.contains('info'));
});

test('연속 토스트가 서로를 덮어쓰지 않고 쌓인다', () => {
  const w = buildWindow();
  w.showToast('첫째');
  w.showToast('둘째');
  w.showToast('셋째');

  const els = toasts(w);
  assert.strictEqual(els.length, 3, '싱글톤이면 1개가 된다');
  assert.deepStrictEqual(els.map((e) => e.textContent), ['첫째', '둘째', '셋째']);
});

test('같은 key 는 제자리에서 교체된다 (잦은 에이전트 이벤트용)', () => {
  const w = buildWindow();
  const a = w.showToast('🔧 Bash 실행 중...', 'info', { key: 'agent' });
  const b = w.showToast('🔧 Edit 실행 중...', 'info', { key: 'agent' });

  assert.strictEqual(a, b, '같은 엘리먼트를 재사용해야 한다');
  const els = toasts(w);
  assert.strictEqual(els.length, 1);
  assert.strictEqual(els[0].textContent, '🔧 Edit 실행 중...');
});

test('key 있는 것과 없는 것이 공존한다', () => {
  const w = buildWindow();
  w.showToast('에이전트', 'info', { key: 'agent' });
  w.showToast('일반1');
  w.showToast('에이전트2', 'info', { key: 'agent' });
  w.showToast('일반2');

  const els = toasts(w);
  assert.strictEqual(els.length, 3, 'key 하나 + 일반 둘');
  assert.deepStrictEqual(els.map((e) => e.textContent), ['에이전트2', '일반1', '일반2']);
});

test('duration 이 지나면 사라지고, 0 이면 남는다', async () => {
  const w = buildWindow();
  w.showToast('잠깐', 'info', { duration: 20 });
  w.showToast('영구', 'info', { duration: 0 });
  assert.strictEqual(toasts(w).length, 2);

  await new Promise((r) => setTimeout(r, 60));
  const els = toasts(w);
  assert.strictEqual(els.length, 1, 'duration 이 지난 것만 사라져야 한다');
  assert.strictEqual(els[0].textContent, '영구');
});

test('교체되면 기존 타이머가 갱신된다 (옛 타이머가 새 내용을 지우면 안 됨)', async () => {
  const w = buildWindow();
  w.showToast('옛것', 'info', { key: 'k', duration: 30 });
  await new Promise((r) => setTimeout(r, 20));
  w.showToast('새것', 'info', { key: 'k', duration: 200 });

  await new Promise((r) => setTimeout(r, 40));   // 옛 타이머(30ms)는 이미 지났을 시점
  const els = toasts(w);
  assert.strictEqual(els.length, 1, '옛 타이머가 새 토스트를 지우면 안 된다');
  assert.strictEqual(els[0].textContent, '새것');
});

test('에러만 role=alert, 나머지는 role=status (aria-live 보완)', () => {
  const w = buildWindow();
  w.showToast('보통');
  w.showToast('실패', 'error');
  const els = toasts(w);
  assert.strictEqual(els[0].getAttribute('role'), 'status');
  assert.strictEqual(els[1].getAttribute('role'), 'alert');
  assert.strictEqual(w.document.getElementById('vt-toasts').getAttribute('aria-live'), 'polite');
});

test('null/undefined 메시지에 죽지 않는다', () => {
  const w = buildWindow();
  w.showToast(null);
  w.showToast(undefined, 'error');
  assert.deepStrictEqual(toasts(w).map((e) => e.textContent), ['', '']);
});

test('dismissToast 로 즉시 제거할 수 있고, 같은 key 를 다시 쓸 수 있다', () => {
  const w = buildWindow();
  const a = w.showToast('A', 'info', { key: 'k', duration: 0 });
  w.dismissToast(a);
  assert.strictEqual(toasts(w).length, 0);

  const b = w.showToast('B', 'info', { key: 'k', duration: 0 });
  assert.notStrictEqual(a, b, '제거된 엘리먼트를 재사용하면 안 된다');
  assert.deepStrictEqual(toasts(w).map((e) => e.textContent), ['B']);
});
