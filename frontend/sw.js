// Service Worker — PWA 설치 지원 (오프라인 캐시 불필요, 설치만 지원)
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
