// 저장소 2차 — 「종료 N」(shell/ProjectEnded.tsx): 끝난 대화 칸과 접기. 저장소가 세션 없이도 남고, 기록은 대화를 켠 저장소에 남는다.
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const { loadSolidBuild } = require('./helpers/solid-env');

const _envs = [];
after(() => { for (const e of _envs) { try { e.dom.window.close(); } catch (_) {} } });
let _m = null;
async function mod() { if (!_m) { _m = await loadSolidBuild(); _envs.push(_m); } return _m; }
const conv = (over = {}) => ({ id: 'c1', native_id: 'n1', provider: 'claude', title: '결제 버그', activity: Date.now() / 1000 - 60,
  branch: 'fix/pay', last_user_text: '결제 고쳐줘', user_message_count: 4, archived: null, ...over });

test('대화 칸 — 제목·경과·「이어서」(늘 보임)·⋯, 둘째 줄 브랜치·지시 수, 셋째 줄 마지막 지시', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  m.mountConversationItem(root, { conversation: conv(), last: true });
  assert.strictEqual(root.querySelector('.vt-rg-name').textContent, '결제 버그');
  assert.match(root.querySelector('.vt-rg-rs').textContent, /이어서/);
  assert.ok(root.querySelector('.vt-rg-more'));
  assert.match(root.textContent, /fix\/pay/);
  assert.match(root.textContent, /지시 4/);
  assert.match(root.textContent, /「결제 고쳐줘」/);
  const untitled = m.document.createElement('div');
  m.document.body.appendChild(untitled);
  m.mountConversationItem(untitled, { conversation: conv({ title: '', archived: 5 }), last: false });
  assert.strictEqual(untitled.querySelector('.vt-rg-name').textContent, '제목 없음');
  assert.match(untitled.querySelector('.vt-rg-rs').textContent, /해제/, '보관한 대화는 「해제」');
});

test('「종료 N」 접기 — 끝난 대화 수와 마지막 활동, 접혀 있으면 목록을 받지 않는다', async () => {
  const m = await mod();
  const root = m.document.createElement('div');
  m.document.body.appendChild(root);
  let toggled = 0;
  m.mountProjectEnded(root, { projectId: 'p1', count: 12, latest: Date.now() / 1000 - 3600, open: false, onToggle: () => { toggled++; } });
  const fold = root.querySelector('.vt-rg-fold');
  assert.match(fold.textContent, /종료 12/);
  assert.match(fold.textContent, /마지막/);
  assert.strictEqual(fold.getAttribute('aria-expanded'), 'false');
  assert.strictEqual(root.querySelectorAll('.vt-rg-ended').length, 0);
  fold.click();
  assert.strictEqual(toggled, 1);
});
