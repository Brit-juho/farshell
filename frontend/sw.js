// Service Worker — Phase 9 #4: 정적 자원 캐시 + Web Push 수신.
// v7: D5 — 아이콘을 Lucide 웹폰트에서 인라인 SVG(js/ui/icons.js)로 전환,
//     vendor/lucide.{min.css,woff2} 제거(-285KB) — 프리캐시 목록에서도 뺀다.
// v6: Web Push(P5) — push 리스너 추가. 등록 주체도 voice.js → js/swreg.js 로 이동
//     (voice.js는 음성 미설치 시 로드되지 않아 SW가 아예 안 떴다).
// v5: 코드 뷰어(P2) — highlight.min.js vendor 추가. vendor는 SWR 캐시라 키 bump 필수.
// v4: WebGL/Canvas GPU 렌더러 addon 추가 (DOM 렌더러 CPU/메모리 비용 문제) — 캐시 키 bump.
// v3: index.html을 css/app.css + js/{terminal,search,picker,grid}.js로 분리.
//     기존 inline <script> 캐시가 stale 상태가 되므로 캐시 키 bump 필수.
// v2: voice.js / index / manifest는 network-first로 변경 (v1 stale 캐시 이슈 수정).
//     vendor/* immutable 자산만 stale-while-revalidate.
// v8(2.1.6): UI 글꼴이 IBM Plex Sans → Wanted Sans로 바뀌면서 index.html이
//     새 <link>를 갖게 됐다. index는 network-first라 원칙적으로는 bump 없이도
//     따라오지만, 실브라우저 검증에서 **예전 index.html이 그대로 나오는 걸
//     실제로 재현**했다(캐시를 지우니 바로 새 글꼴이 붙었다). 글꼴이 안 바뀌면
//     화면 전체가 예전 그대로라 "고쳤는데 안 바뀐다"로 보이므로 키를 올린다.
// v10(2026-09-18): icon-192/512.png를 voice-terminal 시절 마이크에서 FarShell
//     마크로 교체했다. 이 둘은 PRECACHE에 있고 vendor와 같은
//     stale-while-revalidate 경로를 타므로(아래 fetch 핸들러), 키를 안 올리면
//     **이미 설치된 사용자는 계속 마이크를 본다** — 캐시가 먼저 응답하고
//     갱신은 다음 방문에야 반영된다. activate가 CACHE와 다른 키를 전부 지우므로
//     이 한 줄이 옛 아이콘을 확실히 몰아낸다.
const CACHE = 'vt-static-v10';

const PRECACHE = [
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/vendor/xterm.min.js',
  '/static/vendor/xterm.min.css',
  '/static/vendor/addon-fit.min.js',
  '/static/vendor/addon-search.min.js',
  '/static/vendor/addon-webgl.min.js',
  '/static/vendor/addon-canvas.min.js',
  '/static/vendor/nacl.min.js',
  '/static/vendor/nacl-util.min.js',
  '/static/vendor/highlight.min.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(PRECACHE).catch(() => null))  // 일부 실패해도 install 진행
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// (P5) Web Push 수신 — 앱이 닫혀 있어도 여기로 온다. 이게 기존 Notification API와
// 다른 지점이다(그쪽은 페이지가 살아 있어야 한다).
// userVisibleOnly:true 로 구독했으므로 반드시 알림을 하나 띄워야 한다 —
// 안 띄우면 브라우저가 "조용한 푸시"로 보고 구독을 폐기한다.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {}; }
  const title = d.title || 'FarShell';
  e.waitUntil(
    self.registration.showNotification(title, {
      body: d.body || '',
      icon: '/static/icon-192.png',
      badge: '/static/icon-192.png',
      // 같은 tag는 덮어쓴다 — 알림이 쌓여 잠금화면을 도배하지 않도록.
      tag: d.tag || 'vt-task',
      renotify: true,
      data: { url: d.url || '/' },
    })
  );
});

// 알림 클릭 → 이미 열린 앱 탭이 있으면 포커스, 없으면 새로 연다.
// (모바일 Chrome은 new Notification() 대신 이 SW 알림 경로만 허용하므로 클릭 처리도 여기서.)
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cls) => {
      for (const c of cls) { if ('focus' in c) return c.focus(); }
      const url = (e.notification.data && e.notification.data.url) || '/';
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});

// API/WS/voice는 항상 네트워크. 정적 자원만 캐시 처리.
const NETWORK_ONLY = /^\/(api\/|ws|voice\/)/;

// 자주 바뀌는 우리 코드 — network-first, 네트워크 실패 시만 캐시 fallback.
// vendor/*는 immutable이므로 SWR 유지 (속도 이득).
// F1(2026-09): frontend/css/app.css + js/bootstrap.js가 폐기되고 Vite 산출물
// frontend/dist/app.{js,css}가 그 자리를 대신한다 — 'dist'를 빠뜨리면 새 코드가
// 아래 vendor immutable(stale-while-revalidate) 경로로 떨어져서, 정확히 이 파일이
// 막으려던 사고(브라우저가 옛 app.js를 계속 캐시)가 재현된다.
// F4: voice.js(최상위 파일)는 frontend/js/voice/ 아래 ES 모듈로 옮겨가 이미
// `static/js/`로 매치된다 — 최상위 특례(`^\/static\/voice\.js$`)는 삭제.
// 2026-09-18: **아이콘을 여기로 옮긴다.** icon-192/512.png와 /favicon.ico는
// 그전까지 아래 "vendor immutable" SWR 경로로 떨어져 있었다 — 그런데 이 파일들은
// immutable이 아니다. 브랜드 마크라 실제로 바뀐다(이번에 마이크 → FarShell 마크로
// 바꿨다). SWR은 캐시가 **먼저** 답하고 갱신은 다음 방문으로 미루므로, 파일을
// 바꿔도 이미 방문한 사용자에게는 옛 아이콘이 계속 나온다(실제로 그렇게
// 보고됐다: 서버는 새 바이트를 주는데 탭은 마이크). CACHE 키를 올리면 그 한 번은
// 풀리지만, 다음에 아이콘을 또 바꿀 때 같은 사고가 반복된다 — 바로 위 dist/
// 주석이 app.js에 대해 말하는 그 사고와 같은 종류다. 경로로 고쳐 둔다.
const NETWORK_FIRST = /^\/$|^\/manifest\.json$|^\/favicon\.ico$|^\/static\/sw\.js$|^\/static\/icon-\d+\.png$|^\/static\/(css|js|dist)\//;

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (NETWORK_ONLY.test(url.pathname)) return;

  // 우리 코드: network-first
  if (NETWORK_FIRST.test(url.pathname)) {
    e.respondWith((async () => {
      const cache = await caches.open(CACHE);
      try {
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      } catch (_) {
        const cached = await cache.match(req);
        return cached || Response.error();
      }
    })());
    return;
  }

  // vendor immutable 자산: stale-while-revalidate
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req);
    const networkP = fetch(req).then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    }).catch(() => cached);  // 네트워크 실패 시 캐시 fallback
    return cached || networkP;
  })());
});
