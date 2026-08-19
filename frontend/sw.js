// Service Worker — Phase 9 #4: 정적 자원 캐시 + Web Push 수신.
// v6: Web Push(P5) — push 리스너 추가. 등록 주체도 voice.js → js/swreg.js 로 이동
//     (voice.js는 음성 미설치 시 로드되지 않아 SW가 아예 안 떴다).
// v5: 코드 뷰어(P2) — highlight.min.js vendor 추가. vendor는 SWR 캐시라 키 bump 필수.
// v4: WebGL/Canvas GPU 렌더러 addon 추가 (DOM 렌더러 CPU/메모리 비용 문제) — 캐시 키 bump.
// v3: index.html을 css/app.css + js/{terminal,search,picker,grid}.js로 분리.
//     기존 inline <script> 캐시가 stale 상태가 되므로 캐시 키 bump 필수.
// v2: voice.js / index / manifest는 network-first로 변경 (v1 stale 캐시 이슈 수정).
//     vendor/* immutable 자산만 stale-while-revalidate.
const CACHE = 'vt-static-v6';

const PRECACHE = [
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/vendor/xterm.min.js',
  '/static/vendor/xterm.min.css',
  '/static/vendor/addon-fit.min.js',
  '/static/vendor/addon-search.min.js',
  '/static/vendor/addon-webgl.min.js',
  '/static/vendor/addon-canvas.min.js',
  '/static/vendor/lucide.min.css',
  '/static/vendor/lucide.woff2',
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
  const title = d.title || 'farshell';
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
const NETWORK_FIRST = /^\/$|^\/static\/voice\.js$|^\/manifest\.json$|^\/static\/sw\.js$|^\/static\/(css|js)\//;

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
