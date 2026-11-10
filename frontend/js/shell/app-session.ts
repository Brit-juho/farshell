// FarShell 메뉴의 「로그아웃」과 「캐시 지우기」 본체(shell 지연 청크). 진입은 core/app-session-lazy.js의 액션이다.
//
// 로그아웃 — 서버의 `POST /api/auth/logout`을 그대로 쓴다. **웹 로그인 세션 쿠키(vt_session)만** 지우고 기기 등록
// (vt_device)은 서버가 남겨 둔다(다시 들어올 때 OTP를 또 치지 않게). 기기 자체를 끊는 건 `fsh device revoke`다.
//
// 캐시 지우기 — 서비스 워커 등록 해제 + Cache Storage 전체 삭제 + 앱 정적 자산 재요청(HTTP 캐시 우회) 뒤 새로고침.
// **localStorage는 건드리지 않는다**: 사용자 설정(`ui.*`)은 그대로다(설정 초기화는 별개 기능).
// 서비스 워커를 풀면 브라우저가 푸시 구독도 함께 버리므로, 구독돼 있던 기기는 다시 등록하고 다시 구독한다.
import { t } from '../core/i18n.js';

export interface AppSessionEnv {
  vtFetch: (path: string, init?: { method?: string }) => Promise<any>;
  confirm: (message: string, opts?: { title?: string; okLabel?: string }) => Promise<boolean>;
  toast: (msg: string, type?: string) => void;
  /** 아래는 테스트용 주입. 기본은 실제 브라우저 객체. */
  reload?: () => void;
  caches?: Pick<CacheStorage, 'keys' | 'delete'> | null;
  serviceWorker?: { getRegistrations: () => Promise<{ unregister: () => Promise<boolean> }[]>; register: (url: string, o?: object) => Promise<unknown>; ready?: Promise<unknown> } | null;
  fetchAsset?: (url: string) => Promise<unknown>;
  assetUrls?: () => string[];
  /** window.VTPush(push/swreg.js) — 없으면 푸시 복원은 건너뛴다. */
  push?: { isSubscribed: () => Promise<boolean>; subscribe: () => Promise<unknown> } | null;
}

const doReload = (env: AppSessionEnv) => (env.reload ?? (() => location.reload()))();

/** 로그아웃 결과. `skipped`는 비밀번호 로그인이 없어 끊을 세션이 없는 경우. */
export async function logout(env: AppSessionEnv): Promise<{ ok: boolean; skipped?: boolean }> {
  let status: { password_set?: boolean } | null = null;
  try { status = await env.vtFetch('/api/auth/status'); } catch (_) { status = null; }
  // 상태를 못 읽었으면(null) 그대로 진행한다 — 쿠키를 지우는 쪽이 안전한 쪽이다.
  if (status && !status.password_set) {
    env.toast(t('비밀번호 로그인이 설정돼 있지 않아 끝낼 세션이 없습니다'), 'info');
    return { ok: true, skipped: true };
  }
  try {
    await env.vtFetch('/api/auth/logout', { method: 'POST' });
  } catch (_) {
    // 실패해도 되돌리지 않는다 — 쿠키가 이미 지워졌을 수 있다. 다시 여는 쪽이 진실(로그인 화면)을 보여준다.
  }
  doReload(env);
  return { ok: true };
}

/** 지금 페이지가 쓰는 같은 출처 정적 자산 주소(스크립트·스타일시트·modulepreload) + 문서 자신. */
export function pageAssetUrls(): string[] {
  const out = new Set<string>(['/']);
  const sel = 'script[src], link[rel="stylesheet"][href], link[rel="modulepreload"][href]';
  document.querySelectorAll<HTMLElement>(sel).forEach((el) => {
    const raw = el.getAttribute('src') || el.getAttribute('href') || '';
    try {
      const u = new URL(raw, location.href);
      if (u.origin === location.origin) out.add(u.pathname + u.search);
    } catch (_) { /* 잘못된 주소는 건너뛴다 */ }
  });
  return [...out];
}

export async function clearAppCache(env: AppSessionEnv): Promise<{ ok: boolean; cancelled?: boolean }> {
  const msg = t('저장된 앱 파일(캐시)을 지우고 새로고침할까요? 설정과 로그인은 그대로입니다.');
  if (!await env.confirm(msg, { title: t('캐시 지우기'), okLabel: t('지우기') })) return { ok: false, cancelled: true };
  const sw = env.serviceWorker !== undefined ? env.serviceWorker : ('serviceWorker' in navigator ? navigator.serviceWorker : null);
  const cs = env.caches !== undefined ? env.caches : (typeof caches !== 'undefined' ? caches : null);
  const push = env.push !== undefined ? env.push : ((window as unknown as { VTPush?: AppSessionEnv['push'] }).VTPush ?? null);
  try {
    let hadPush = false;
    if (push) { try { hadPush = !!(await push.isSubscribed()); } catch (_) { hadPush = false; } }
    if (sw) await Promise.all((await sw.getRegistrations()).map((r) => r.unregister()));
    if (cs) await Promise.all((await cs.keys()).map((k) => cs.delete(k)));
    // 앱 정적 자산을 HTTP 캐시까지 우회해 다시 받아 둔다 — 새로고침이 옛 파일을 집지 않게. 개별 실패는 무시한다.
    const fetchAsset = env.fetchAsset ?? ((u: string) => fetch(u, { cache: 'reload', credentials: 'same-origin' }));
    await Promise.all((env.assetUrls ?? pageAssetUrls)().map((u) => Promise.resolve().then(() => fetchAsset(u)).catch(() => null)));
    if (hadPush && sw && push) {
      try { await sw.register('/sw.js', { scope: '/' }); await sw.ready; await push.subscribe(); } catch (_) { /* 알림은 설정에서 다시 켤 수 있다 */ }
    }
  } catch (_) {
    env.toast(t('캐시를 모두 지우지 못했습니다'), 'error');
    return { ok: false };
  }
  doReload(env);
  return { ok: true };
}
