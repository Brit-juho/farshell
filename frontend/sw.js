// Service Worker — Phase 9 #4: 정적 자원 캐시.
// v2: voice.js / index / manifest는 network-first로 변경 (v1 stale 캐시 이슈 수정).
//     vendor/* immutable 자산만 stale-while-revalidate.
const CACHE = 'vt-static-v2';

const PRECACHE = [
  '/static/icon-192.png',
  '/static/icon-512.png',
  '/static/vendor/xterm.min.js',
  '/static/vendor/xterm.min.css',
  '/static/vendor/addon-fit.min.js',
  '/static/vendor/addon-search.min.js',
  '/static/vendor/lucide.min.css',
  '/static/vendor/lucide.woff2',
  '/static/vendor/nacl.min.js',
  '/static/vendor/nacl-util.min.js',
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

// API/WS/voice는 항상 네트워크. 정적 자원만 캐시 처리.
const NETWORK_ONLY = /^\/(api\/|ws|voice\/)/;

// 자주 바뀌는 우리 코드 — network-first, 네트워크 실패 시만 캐시 fallback.
// vendor/*는 immutable이므로 SWR 유지 (속도 이득).
const NETWORK_FIRST = /^\/$|^\/static\/voice\.js$|^\/manifest\.json$|^\/static\/sw\.js$/;

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
