// 음성 입력 대상 고정(target-pin.js)과 확인 후 전송(confirm.js) — recording.js·status-panel.js와 함께 돈다.
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { createDomEnv } = require('./helpers/dom-env');
const { importFresh } = require('./helpers/vm-esm');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const doms = [];
after(() => doms.forEach((d) => d.window.close()));
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

const reply = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });

async function build(o = {}) {
  const env = createDomEnv(html); doms.push(env.dom);
  const w = env.window;
  w.API_BASE = w.location.origin; w.registerAction = () => {};
  w.sessions = o.sessions || { 'web-A': { tmuxName: 'A' }, 'web-B': { tmuxName: 'B' } };
  w.activeId = 'web-A';
  w.URL.createObjectURL = () => 'blob:test'; w.URL.revokeObjectURL = () => {};
  const requests = [];
  w.fetch = async (url, opts = {}) => {
    const u = String(url);
    requests.push({ url: u, opts, body: opts.body && typeof opts.body === 'string' ? JSON.parse(opts.body) : undefined });
    if (u.includes('/voice/input') && o.input) return o.input(requests);
    if (u.includes('/voice/inject')) return o.inject ? o.inject(requests) : reply({ injected: true, text: 'x' });
    if (u.includes('/voice/target')) {
      const sid = JSON.parse(opts.body).session_id;
      return reply({ token: 'tok-' + sid, name: w.sessions[sid]?.tmuxName || '' });
    }
    return reply(u.includes('/stt/status') ? { available: true, selected_valid: true, selected_ready: true, loaded: true, model: 'm', loaded_model: 'm' }
      : u.includes('/stt/catalog') ? { models: [{ id: 'm', label: '모델', model_present: true, requirements: [] }] }
        : { text: '명령', injected: true });
  };
  let gum = 0;
  Object.defineProperty(w.navigator, 'mediaDevices', { value: { getUserMedia: async () => { gum++; return { getTracks: () => [{ stop() {} }] }; } } });
  w.gumCount = () => gum;
  w.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor() { this.mimeType = 'audio/webm'; }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.ondataavailable({ data: new w.Blob(['audio']) }); this.onstop(); }
  };
  const cache = new Map();
  const statusPanel = await importFresh(path.join(__dirname, '../js/voice/status-panel.js'), env.context, cache);
  statusPanel.mountVoiceStatus();
  const recording = await importFresh(path.join(__dirname, '../js/voice/recording.js'), env.context, cache);
  const pin = await importFresh(path.join(__dirname, '../js/voice/target-pin.js'), env.context, cache);
  return { w, recording, pin, requests };
}

const $ = (w, id) => w.document.getElementById(id);
async function record(w, recording) { await recording.startRecording(); recording.stopRecording(); await flush(); }
const sentInput = (requests) => requests.find((r) => r.url.includes('/voice/input'));

// ── 입력 대상 고정 ──────────────────────────────────────────────────

test('고정이 없으면 활성 세션을 따라가고, 고정하면 포커스가 바뀌어도 그 세션으로 보낸다', async () => {
  const { w, recording, pin, requests } = await build();
  await record(w, recording);
  assert.ok(sentInput(requests).url.includes('session_id=web-A'));
  pin.setPin('B');
  w.activeId = 'web-A';
  requests.length = 0;
  await record(w, recording);
  assert.strictEqual(requests.find((r) => r.url.includes('/voice/target')).body.session_id, 'web-B');
  assert.ok(sentInput(requests).url.includes('session_id=web-B'));
  assert.strictEqual(sentInput(requests).opts.headers['X-Voice-Target'], 'tok-web-B');
  assert.match($(w, 'voice-record-meta').textContent, /입력 대상: B \(고정\)/);
  pin.setPin('');  // 해제하면 다시 활성 세션
  requests.length = 0;
  await record(w, recording);
  assert.ok(sentInput(requests).url.includes('session_id=web-A'));
});

test('고정한 세션이 사라지면 활성 세션으로 대신하지 않고 마이크·서버 요청 없이 막는다', async () => {
  const { w, recording, pin, requests } = await build();
  pin.setPin('B');
  delete w.sessions['web-B'];
  await recording.startRecording();
  assert.strictEqual(recording.isRecording, false);
  assert.strictEqual(w.gumCount(), 0);
  assert.ok(!requests.some((r) => r.url.includes('/voice/target') || r.url.includes('/voice/input') || r.url.includes('/preload')));
  assert.strictEqual($(w, 'mic-status').textContent, '고정한 입력 대상이 없습니다');
  assert.strictEqual($(w, 'mic-btn-wrap').dataset.voiceState, 'error');
  assert.match($(w, 'voice-pin-note').textContent, /고정한 입력 대상이 없습니다/);
});

test('같은 tmux 세션이 여러 화면이면 활성 화면을, 원격 세션과 tmux가 아닌 세션은 후보에서 뺀다', async () => {
  const { w, pin } = await build({ sessions: {
    'web-1': { tmuxName: 'X' }, 'web-2': { tmuxName: 'X' }, 'web-3': { tmux_name: 'Y', remote: true }, 'web-4': { displayName: 'plain' } } });
  assert.deepStrictEqual(Array.from(pin.pinCandidates(), (c) => c.name), ['X']);
  pin.setPin('X'); w.activeId = 'web-2';
  assert.strictEqual(pin.resolveInputTarget().sid, 'web-2');
  w.activeId = 'web-4';
  assert.strictEqual(pin.resolveInputTarget().sid, 'web-1');
  pin.setPin('Y');
  assert.strictEqual(pin.resolveInputTarget().missing, true);  // 원격 세션은 이 서버에 입력할 수 없다
});

test('저장소 접근이 막혀도 고정이 이 화면에서는 동작한다', async () => {
  const { w, pin } = await build();
  Object.defineProperty(w, 'localStorage', { get() { throw new Error('denied'); }, configurable: true });
  pin.setPin('B');
  assert.strictEqual(pin.getPin(), 'B');
  assert.strictEqual(pin.resolveInputTarget().sid, 'web-B');
  pin.setPin('');
  assert.strictEqual(pin.getPin(), '');
});

test('패널의 입력 대상 목록: 열린 세션을 보이고, 고르면 저장하며 마이크 버튼에 고정 표시가 붙는다', async () => {
  const { w, pin } = await build();
  const sel = $(w, 'voice-pin-select');
  assert.deepStrictEqual(Array.from(sel.options).map((x) => x.value), ['', 'A', 'B']);
  assert.strictEqual(sel.value, '');
  sel.value = 'B'; sel.dispatchEvent(new w.Event('change'));
  assert.strictEqual(pin.getPin(), 'B');
  assert.strictEqual(w.localStorage.getItem('vt_voice_pin'), 'B');
  assert.strictEqual($(w, 'mic-btn-wrap').dataset.voicePin, 'B');
  assert.match($(w, 'mic-btn-wrap').getAttribute('aria-label') || '', /B \(고정\)/);
  delete w.sessions['web-B'];
  pin.setPin('B');  // 변경 알림으로 목록을 다시 그린다
  assert.deepStrictEqual(Array.from(sel.options).map((x) => x.value), ['', 'A', 'B']);
  assert.match(sel.options[2].textContent, /B \(없음\)/);
  sel.value = ''; sel.dispatchEvent(new w.Event('change'));
  assert.strictEqual($(w, 'mic-btn-wrap').dataset.voicePin, undefined);
  assert.strictEqual(w.localStorage.getItem('vt_voice_pin'), null);
});

test('녹음 중에는 대상 목록을 잠근다', async () => {
  const { w, recording } = await build();
  await recording.startRecording();
  assert.strictEqual($(w, 'voice-pin-select').disabled, true);
  recording.stopRecording(); await flush();
  assert.strictEqual($(w, 'voice-pin-select').disabled, false);
});

// ── 확인 후 전송 ────────────────────────────────────────────────────

const pending = (extra = {}) => () => reply({
  text: 'ls -la', injected: false, pending_confirm: true, confirm_token: 'ctok', confirm_reason: 'shell', confirm_ttl_sec: 300,
  target_name: 'A', ...extra });

test('녹음 요청은 확인 UI를 그릴 수 있다고 알리고, pending_confirm이면 입력 대신 확인 영역을 보인다', async () => {
  const { w, recording, requests } = await build({ input: pending() });
  await record(w, recording);
  assert.strictEqual(sentInput(requests).opts.headers['X-Voice-Confirm'], '1');
  assert.strictEqual($(w, 'voice-confirm').hidden, false);
  assert.strictEqual($(w, 'voice-confirm-text').value, 'ls -la');
  assert.match($(w, 'voice-confirm-reason').textContent, /A · 일반 셸에 입력하기 전에 확인/);
  assert.match($(w, 'mic-status').textContent, /확인 후 입력합니다 — A/);
  assert.ok(!/입력 완료/.test($(w, 'mic-status').textContent));
  assert.ok(!requests.some((r) => r.url.includes('/voice/inject')));
  assert.strictEqual($(w, 'voice-confirm-copy').hidden, true);
});

test('고친 텍스트로 「입력」하면 /voice/inject에 토큰·텍스트·녹음 ID를 보내고 영역을 닫는다', async () => {
  const { w, recording, requests } = await build({ input: pending(), inject: () => reply({ injected: true, text: 'ls -l', name: 'A' }) });
  await record(w, recording);
  const voiceId = sentInput(requests).opts.headers['X-Voice-Id'];
  $(w, 'voice-confirm-text').value = '  ls -l  ';
  $(w, 'voice-confirm-send').click(); await flush();
  const inj = requests.find((r) => r.url.includes('/voice/inject'));
  assert.deepStrictEqual(inj.body, { token: 'ctok', text: 'ls -l', voice_id: voiceId });
  assert.strictEqual(inj.opts.method, 'POST');
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
  assert.match($(w, 'mic-status').textContent, /입력 완료 — A: "ls -l"/);
});

test('빈 텍스트는 보내지 않고, 전송 중 연타는 한 번만 보낸다', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const { w, recording, requests } = await build({ input: pending(), inject: async () => { await gate; return reply({ injected: true, text: 'x' }); } });
  await record(w, recording);
  $(w, 'voice-confirm-text').value = '   ';
  $(w, 'voice-confirm-send').click(); await flush();
  assert.ok(!requests.some((r) => r.url.includes('/voice/inject')));
  assert.match($(w, 'voice-confirm-msg').textContent, /입력할 텍스트가 비었습니다/);
  $(w, 'voice-confirm-text').value = 'ok';
  $(w, 'voice-confirm-send').click(); $(w, 'voice-confirm-send').click(); await flush();
  assert.strictEqual(requests.filter((r) => r.url.includes('/voice/inject')).length, 1);
  assert.strictEqual($(w, 'voice-confirm-send').disabled, true);
  release(); await flush();
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
});

test('대상이 사라졌거나 만료되면 다른 곳으로 보내지 않고 입력을 잠가 복사만 남긴다', async () => {
  for (const code of ['target_gone', 'expired']) {
    const { w, recording, requests } = await build({ input: pending(), inject: () => reply({ error: '사라짐', code, injected: false }, 409) });
    await record(w, recording);
    $(w, 'voice-confirm-send').click(); await flush();
    assert.strictEqual($(w, 'voice-confirm').hidden, false, code);
    assert.strictEqual($(w, 'voice-confirm-send').disabled, true, code);
    assert.strictEqual($(w, 'voice-confirm-copy').hidden, false, code);
    assert.strictEqual($(w, 'voice-confirm-msg').textContent, '사라짐');
    assert.strictEqual($(w, 'voice-confirm-text').value, 'ls -la');
    $(w, 'voice-confirm-send').click(); await flush();
    assert.strictEqual(requests.filter((r) => r.url.includes('/voice/inject')).length, 1);
  }
});

test('일시 실패는 텍스트를 지키고 다시 누를 수 있다', async () => {
  let n = 0;
  const { w, recording, requests } = await build({ input: pending(), inject: () => (++n === 1 ? reply({ error: '실패', code: 'send_failed' }, 502) : reply({ injected: true, text: 'ls -la' })) });
  await record(w, recording);
  $(w, 'voice-confirm-text').value = '고친 것';
  $(w, 'voice-confirm-send').click(); await flush();
  assert.strictEqual($(w, 'voice-confirm-send').disabled, false);
  assert.strictEqual($(w, 'voice-confirm-text').value, '고친 것');
  assert.strictEqual($(w, 'voice-confirm-msg').textContent, '실패');
  $(w, 'voice-confirm-send').click(); await flush();
  assert.strictEqual(requests.filter((r) => r.url.includes('/voice/inject')).length, 2);
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
});

test('취소하면 영역을 닫고 아무것도 보내지 않는다. 새 녹음을 시작해도 이전 확인 대기는 버린다', async () => {
  const { w, recording, requests } = await build({ input: pending() });
  await record(w, recording);
  $(w, 'voice-confirm-cancel').click(); await flush();
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
  assert.strictEqual($(w, 'voice-confirm-text').value, '');
  assert.ok(!requests.some((r) => r.url.includes('/voice/inject')));
  assert.match($(w, 'mic-status').textContent, /취소/);
  await record(w, recording);                    // 다시 확인 대기
  assert.strictEqual($(w, 'voice-confirm').hidden, false);
  await recording.startRecording();               // 새 녹음 시작
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
  recording.stopRecording(); await flush();
});

test('확인 영역이 없는 화면에서는 입력되지 않음 + 복사로 안전하게 물러난다', async () => {
  const { w, recording } = await build({ input: pending() });
  $(w, 'voice-confirm').remove();
  await record(w, recording);
  assert.match($(w, 'mic-status').textContent, /입력되지 않음 — ls -la/);
});

test('토큰 없는 응답(구 서버)이나 pending_confirm 없는 응답은 기존 흐름 그대로다', async () => {
  const { w, recording } = await build({ input: () => reply({ text: '명령', injected: true }) });
  await record(w, recording);
  assert.strictEqual($(w, 'voice-confirm').hidden, true);
  assert.match($(w, 'mic-status').textContent, /입력 완료 — A: "명령"/);
});
