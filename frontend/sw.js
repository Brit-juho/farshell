// Service Worker — Phase 9 #4: stale-while-revalidate 정적 자원 캐시.
const CACHE = 'vt-static-v1';

const PRECACHE = [
  '/',
  '/manifest.json',
  '/static/voice.js',
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

// API/WS/voice는 항상 네트워크. 정적 자원만 SWR.
const NETWORK_ONLY = /^\/(api\/|ws|voice\/)/;

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (NETWORK_ONLY.test(url.pathname)) return;

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
