// Service Worker 등록 + Web Push 구독 (P5).
//
// 등록이 원래 voice.js 안에 있었는데, 그 파일은 음성 미설치 환경에서 로드되지 않는다
// (grid.js가 /api/capabilities 를 보고 결정). 그래서 PWA 오프라인 캐시도, 알림도
// 음성 설치 여부에 인질로 잡혀 있었다. 항상 로드되는 이 파일로 옮겼다.
//
// 구독이 만들어지지 않는 정상적인 경우들 — 실패가 아니므로 조용히 넘어간다:
//   - http 접속 (secure context 아님) → SW 자체가 등록 안 됨
//   - iOS 사파리 탭 (홈 화면에 PWA로 설치해야 Web Push가 열린다, iOS 16.4+)
//   - 사용자가 알림 권한을 거부

(function () {
  'use strict';

  if (!('serviceWorker' in navigator)) return;

  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});

  // --- Web Push ---

  function b64ToU8(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
  }

  function tokenQuery() {
    try { return (typeof _tokenQuery === 'string' ? _tokenQuery : ''); }
    catch (_) { return ''; }
  }

  function api(path, opts) {
    const sep = path.includes('?') ? '&' : '?';
    return fetch(`${path}${sep}${tokenQuery().replace(/^[?&]/, '')}`, opts);
  }

  // 왜 구독이 불가능한지 사람이 읽을 수 있는 이유. UI가 그대로 보여준다.
  function blockReason() {
    if (!window.isSecureContext) {
      return 'https 접속이 아닙니다 (평문 http에서는 브라우저가 알림을 막습니다)';
    }
    if (!('PushManager' in window)) {
      const iOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
      const standalone = window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true;
      if (iOS && !standalone) {
        return 'iOS에서는 홈 화면에 앱으로 추가해야 알림을 받을 수 있습니다 (공유 → 홈 화면에 추가)';
      }
      return '이 브라우저는 Web Push를 지원하지 않습니다';
    }
    if (Notification.permission === 'denied') {
      return '알림 권한이 거부돼 있습니다 (브라우저 설정에서 허용해 주세요)';
    }
    return '';
  }

  async function subscribe() {
    const reason = blockReason();
    if (reason) return { ok: false, reason };

    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      return { ok: false, reason: '알림 권한이 허용되지 않았습니다' };
    }

    const res = await api('/api/push/key');
    if (!res.ok) return { ok: false, reason: '서버에 VAPID 키가 없습니다' };
    const { key } = await res.json();

    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      // 서버 키가 바뀌었으면(vapid.json 재생성) 기존 구독은 못 쓴다 — 갈아탄다.
      const cur = sub.options && sub.options.applicationServerKey;
      const same = cur && btoa(String.fromCharCode(...new Uint8Array(cur)))
        === btoa(String.fromCharCode(...b64ToU8(key)));
      if (!same) { try { await sub.unsubscribe(); } catch (_) {} sub = null; }
    }
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64ToU8(key),
      });
    }

    const r = await api('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subscription: sub.toJSON(),
        origin: location.origin,
        label: navigator.platform || '',
      }),
    });
    if (!r.ok) return { ok: false, reason: '서버 등록 실패' };
    return { ok: true };
  }

  async function unsubscribe() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return { ok: true };
    await api('/api/push/subscribe', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint }),
    });
    try { await sub.unsubscribe(); } catch (_) {}
    return { ok: true };
  }

  async function isSubscribed() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return false;
    try {
      const reg = await navigator.serviceWorker.ready;
      return !!(await reg.pushManager.getSubscription());
    } catch (_) { return false; }
  }

  window.VTPush = { subscribe, unsubscribe, isSubscribed, blockReason };
})();
