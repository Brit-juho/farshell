// 「로그아웃」「캐시 지우기」(shell/app-session.ts) 계약 — 순수 단위 시험(브라우저 객체는 전부 주입).
'use strict';
const { test, after } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadSolidBuild } = require('./helpers/solid-env');

const _doms = [];
after(() => { for (const d of _doms) { try { d.dom.window.close(); } catch (_) {} } });
let _mod = null;
async function mod() {
  if (!_mod) { _mod = await loadSolidBuild(); _doms.push(_mod); }
  return _mod;
}

function mkEnv(over = {}) {
  const log = [];
  return {
    log,
    env: {
      vtFetch: async (p, init) => {
        log.push(`${init?.method || 'GET'} ${p}`);
        if (p === '/api/auth/status') return { password_set: true };
        return { ok: true };
      },
      confirm: async (m) => { log.push('confirm'); return true; },
      toast: (m, type) => log.push(`toast:${type}`),
      reload: () => log.push('reload'),
      ...over,
    },
  };
}

test('logout — 상태 확인 뒤 POST /api/auth/logout, 그리고 새로고침(기기 등록 API는 부르지 않는다)', async () => {
  const { logout } = await mod();
  const { env, log } = mkEnv();
  const r = await logout(env);
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(Array.from(log), ['GET /api/auth/status', 'POST /api/auth/logout', 'reload']);
});

test('logout — 비밀번호 로그인이 없으면 아무것도 끊지 않고 안내만', async () => {
  const { logout } = await mod();
  const { env, log } = mkEnv({ vtFetch: async (p, i) => { log2.push(`${i?.method || 'GET'} ${p}`); return { password_set: false }; } });
  const log2 = [];
  const r = await logout(env);
  assert.strictEqual(r.skipped, true);
  assert.deepStrictEqual(log2, ['GET /api/auth/status']);
  assert.deepStrictEqual(Array.from(log), ['toast:info']);
});

test('logout — 서버 호출이 실패해도 새로고침한다(다시 여는 쪽이 진실을 보여준다)', async () => {
  const { logout } = await mod();
  const { env, log } = mkEnv({ vtFetch: async () => { throw new Error('net'); } });
  await logout(env);
  assert.deepStrictEqual(Array.from(log), ['reload']);
});

function cacheEnv(over = {}) {
  const ev = [];
  const regs = [{ unregister: async () => { ev.push('unreg1'); return true; } }, { unregister: async () => { ev.push('unreg2'); return true; } }];
  const store = new Set(['vt-static-v11', 'other']);
  const { env, log } = mkEnv({
    serviceWorker: { getRegistrations: async () => regs, register: async (u) => { ev.push(`register:${u}`); }, ready: Promise.resolve() },
    caches: { keys: async () => [...store], delete: async (k) => { ev.push(`del:${k}`); return store.delete(k); } },
    fetchAsset: async (u) => { ev.push(`fetch:${u}`); if (u === '/bad') throw new Error('x'); },
    assetUrls: () => ['/', '/static/dist/app.js', '/bad'],
    push: null,
    ...over,
  });
  return { env, log, ev, store };
}

test('clearAppCache — 확인 뒤 SW 해제 · 캐시 전부 삭제 · 자산 재요청(일부 실패 무시) · 새로고침', async () => {
  const { clearAppCache } = await mod();
  const { env, log, ev, store } = cacheEnv();
  const r = await clearAppCache(env);
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(Array.from(ev).filter((e) => e.startsWith('unreg')), ['unreg1', 'unreg2']);
  assert.strictEqual(store.size, 0, 'Cache Storage 전체 삭제');
  assert.ok(ev.includes('fetch:/static/dist/app.js') && ev.includes('fetch:/'));
  assert.ok(ev.indexOf('unreg2') < ev.indexOf('fetch:/'), '해제·삭제 다음에 재요청');
  assert.deepStrictEqual(Array.from(log), ['confirm', 'reload']);
});

test('clearAppCache — 취소하면 아무것도 건드리지 않는다', async () => {
  const { clearAppCache } = await mod();
  const { env, log, ev } = cacheEnv({ confirm: async () => false });
  const r = await clearAppCache(env);
  assert.strictEqual(r.cancelled, true);
  assert.deepStrictEqual(Array.from(ev), []);
  assert.ok(!log.includes('reload'));
});

test('clearAppCache — 해제·삭제가 실패하면 새로고침 없이 오류 토스트', async () => {
  const { clearAppCache } = await mod();
  const { env, log } = cacheEnv({ caches: { keys: async () => { throw new Error('denied'); }, delete: async () => true } });
  const r = await clearAppCache(env);
  assert.strictEqual(r.ok, false);
  assert.ok(Array.from(log).includes('toast:error'));
  assert.ok(!log.includes('reload'));
});

test('clearAppCache — 푸시 구독이 있었으면 SW를 다시 등록하고 다시 구독한다(없었으면 안 한다)', async () => {
  const { clearAppCache } = await mod();
  const calls = [];
  const on = cacheEnv({ push: { isSubscribed: async () => true, subscribe: async () => { calls.push('subscribe'); } } });
  await clearAppCache(on.env);
  assert.ok(on.ev.includes('register:/sw.js'));
  assert.deepStrictEqual(calls, ['subscribe']);
  const off = cacheEnv({ push: { isSubscribed: async () => false, subscribe: async () => { calls.push('x'); } } });
  await clearAppCache(off.env);
  assert.ok(!off.ev.includes('register:/sw.js'));
  assert.deepStrictEqual(calls, ['subscribe']);
});

test('clearAppCache — SW·Cache Storage가 없는 환경(http 등)에서도 끝까지 간다', async () => {
  const { clearAppCache } = await mod();
  const { env, log } = cacheEnv({ serviceWorker: null, caches: null });
  const r = await clearAppCache(env);
  assert.strictEqual(r.ok, true);
  assert.ok(log.includes('reload'));
});

test('pageAssetUrls — 같은 출처 스크립트·스타일만, 문서(/) 포함', async () => {
  const { pageAssetUrls, document } = await mod();
  document.head.innerHTML = '<script src="/static/dist/app.js"></script><link rel="stylesheet" href="/static/dist/app.css">'
    + '<script src="https://cdn.example.com/x.js"></script>';
  const urls = Array.from(pageAssetUrls());
  assert.ok(urls.includes('/') && urls.includes('/static/dist/app.js') && urls.includes('/static/dist/app.css'));
  assert.ok(!urls.some((u) => u.includes('example.com')));
});

test('계약 — 캐시 지우기 본체는 localStorage(사용자 설정)를 건드리지 않는다', () => {
  const src = fs.readFileSync(path.join(__dirname, '../js/shell/app-session.ts'), 'utf8')
    .split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
  assert.ok(!/localStorage|sessionStorage/.test(src));
});

test('계약 — 두 액션은 상시 로드 스텁이 등록하고 팔레트 명령과 이름이 같다', async () => {
  const stub = fs.readFileSync(path.join(__dirname, '../js/core/app-session-lazy.js'), 'utf8');
  assert.match(stub, /registerAction\('session\.logout'/);
  assert.match(stub, /registerAction\('app\.clear-cache'/);
  const main = fs.readFileSync(path.join(__dirname, '../js/main.js'), 'utf8');
  assert.match(main, /import '\.\/core\/app-session-lazy\.js'/);
  const { DEFAULT_COMMANDS } = await mod();
  const by = Object.fromEntries(Array.from(DEFAULT_COMMANDS, (c) => [c.action, c.label]));
  assert.strictEqual(by['session.logout'], '로그아웃');
  assert.strictEqual(by['app.clear-cache'], '캐시 지우기');
});
