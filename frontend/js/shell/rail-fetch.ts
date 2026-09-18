// 워크트리 레일의 **데이터 수집**과 window 브리지 어댑터. 그리기는 Rail.tsx.
//
// ⚠ Rail.tsx와 같은 제약을 그대로 진다: core/store.js·layout/store.js·
// agent/state.js를 **직접 import하지 않는다.** 이 파일도 `shell` 지연 청크에
// 들어가므로, 정적 import하면 Vite lib 모드가 그 상태를 복제한다(ADR-26/N35).
// 그래서 전부 window 브리지로만 읽고 vtFetch는 인자로 받는다.
//
// Rail.tsx에서 떼어낸 이유: "무엇을 그리는가"와 "무엇을 가져오는가"가 한
// 파일에 있으면 폴링 주기·캐시 같은 것을 고칠 때 JSX 400줄을 지나쳐야 한다.
import { createSignal, onCleanup } from 'solid-js';

import type { OtherRailRowInput } from './rail-data.js';

// §5 원문: "60초 캐시" — 워크트리에 안 속한(changed 요약을 서버가 안 주는)
// 세션에만 쓴다.
const GIT_CACHE_MS = 60000;

export interface RailDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  getAction: (name: string) => unknown;
}

export function safeFetch<T>(deps: RailDeps, path: string): Promise<T | null> {
  return deps.vtFetch(path).then((v) => v as T).catch(() => null);
}

export type DesktopRailRow = OtherRailRowInput & { statusSentence: string };

// ---- window 브리지 시그널 어댑터 (core/signals.ts의 지연-청크 안전 버전) ----

export function useSessionsVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).storeSubscribe?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

export function useAgentVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).onStatusChange?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

/** 2026-09-18 후속(그룹 재정의) — 그룹은 이제 화면(pane 트리)에서 도출된다.
 * `storeSubscribe`(세션 추가/삭제)만으로는 "이미 있는 세션을 다른 pane으로
 * 옮겼다" 같은 순수 레이아웃 변경을 못 잡으므로, layout/store.js가 따로
 * 노출한 `window.onLayoutChange`를 구독한다. */
export function useLayoutVersion() {
  const [v, setV] = createSignal(0);
  const unsub = (window as any).onLayoutChange?.(() => setV((n) => n + 1));
  onCleanup(() => unsub?.());
  return v;
}

// ---- 데이터 수집 ----

export interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
}

/** tmux 세션 이름 → 떠 있는 CLI. 헤더 탭(layout/tabbar.js)이 쓰던 것과 **같은
 * 출처**다. `/api/agent/status`에는 이 값이 없다 — 거기서 읽으려다 마크가 한
 * 개도 안 그려지는 걸 실브라우저에서 확인하고 고쳤다. 상태(무엇을 하는 중인가)와
 * 정체(어떤 CLI인가)는 서버에서 아예 다른 소스라, 둘을 한 응답으로 착각하면
 * 조용히 빈 값이 된다. */
export async function fetchAgentNames(deps: RailDeps): Promise<Record<string, string>> {
  const data = await safeFetch<Record<string, { agent?: string }>>(deps, '/api/agents');
  const out: Record<string, string> = {};
  for (const [name, v] of Object.entries(data || {})) {
    if (v && v.agent) out[name] = v.agent;
  }
  return out;
}

export async function fetchAgentDetails(deps: RailDeps): Promise<Record<string, AgentDetail>> {
  const data = await safeFetch<{ all?: Record<string, any> }>(deps, '/api/agent/status');
  const out: Record<string, AgentDetail> = {};
  for (const entry of Object.values(data?.all || {})) {
    const name = (entry as any)?.tmux_session;
    if (!name) continue;
    out[name] = {
      since: (entry as any).since ?? null,
      tool: (entry as any).tool || (entry as any).last_tool || null,
      question: (entry as any).question ?? null,
      options: (entry as any).options ?? null,
    };
  }
  return out;
}

const _gitCache = new Map<string, { at: number; files: number | null }>();

// Rail.tsx가 이 캐시를 **직접** 들여다보고 있었는데 `_gitCache`도 `GIT_CACHE_MS`도
// 이 모듈 밖으로 나가지 않는 값이라 런타임에 `ReferenceError: _gitCache is not
// defined`가 났다(실브라우저 콘솔에서 발견, 2026-09-17). 스토어 구독자 안에서
// 던지므로 그 뒤의 행 조립이 통째로 중단된다 — 레일에 세션 행이 안 그려지던 원인.
// 빌드가 못 잡은 이유는 vite가 타입 검사 없이 트랜스파일만 하기 때문이다.
// 캐시를 또 복제하지 않고(Fleet.tsx가 이미 사본을 갖고 있다) 접근자만 연다.

/** 캐시에 있는 값. `undefined`는 "아직 모름", `null`은 "git 저장소가 아님" — 둘을 구분한다. */
export function cachedDiffCount(cwd: string): number | null | undefined {
  return _gitCache.get(cwd)?.files;
}

/** 비었거나 낡았으면 true. 호출자가 fetchDiffCount로 채운다. */
export function diffCountStale(cwd: string): boolean {
  const hit = _gitCache.get(cwd);
  return !hit || Date.now() - hit.at >= GIT_CACHE_MS;
}
export async function fetchDiffCount(deps: RailDeps, cwd: string): Promise<number | null> {
  const hit = _gitCache.get(cwd);
  if (hit && Date.now() - hit.at < GIT_CACHE_MS) return hit.files;
  const data = await safeFetch<{ repo?: boolean; files?: unknown[] }>(deps, `/api/git/status?repo=${encodeURIComponent(cwd)}`);
  const files = data?.repo ? (data.files || []).length : null;
  _gitCache.set(cwd, { at: Date.now(), files });
  return files;
}

// 30-worktree.md §2: 세션이 하나도 없는 워크트리를 여는 API. ADR-29 B부터
// 레일에는 이걸 부를 자리(워크트리 행)가 없다 — C단계(저장소 시트)가
// 이어받는다. 그때까지 쓰이지 않고 대기한다.
export async function openWorktree(deps: RailDeps, wtId: string): Promise<string | null> {
  try {
    const data = await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}/open`, { method: 'POST' }) as { tmux_session?: string };
    return data?.tmux_session || null;
  } catch (_) {
    return null;
  }
}

// 2.1 D5 — 서버엔 있었지만 어느 화면도 안 부르던 삭제 API를 여기서 처음
// 연결했다(30-worktree.md §3). ADR-29 C(저장소 시트)가 호출자를 이어받는다
// — B단계부터 레일에는 워크트리 행 자체가 없어 이 함수를 부를 자리가
// 없다(잠시 미사용, C가 곧 이어받는다). 확인은 두 단계다: 먼저 평범한 확인,
// 서버가 409(더러움)로 거절하면 "그래도 지울지"를 한 번 더 물어 force로
// 재시도한다 — 뜻하지 않게 커밋 안 된 변경을 날리는 사고를 막기 위해서다.
// 메인 워크트리는 애초에 호출자가 메뉴에 안 띄운다(서버도 400으로 거절하지만,
// 거절당하는 것 자체가 이미 "왜 안 되지"라는 물음표다).
export async function deleteWorktreeRow(
  deps: RailDeps, wtId: string, label: string, hasSessions: boolean,
): Promise<{ ok: boolean; error?: string }> {
  const firstWarn = hasSessions
    ? `'${label}' 워크트리를 삭제합니다. 열려 있는 세션의 tmux는 그대로 남지만 다음에 자동으로 재연결되지는 않습니다. 되돌릴 수 없습니다.`
    : `'${label}' 워크트리를 삭제합니다. 되돌릴 수 없습니다.`;
  if (!window.confirm(firstWarn)) return { ok: false };
  try {
    await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
    return { ok: true };
  } catch (e: any) {
    if (e?.status === 409 && e?.data?.dirty) {
      if (!window.confirm(`'${label}'에 커밋하지 않은 변경사항이 있습니다. 그래도 삭제할까요? 그 변경은 사라집니다.`)) {
        return { ok: false };
      }
      try {
        await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}`, {
          method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ force: true }),
        });
        return { ok: true };
      } catch (e2: any) {
        return { ok: false, error: e2?.data?.error || e2?.message || '삭제 실패' };
      }
    }
    return { ok: false, error: e?.data?.error || e?.message || '삭제 실패' };
  }
}

// 행이 가리키는 "열 수 있는" 대상. 잠자는 세션(awake=false)은 sessionId가
// 빈 문자열이라 null로 떨어진다 — switchTo 등 웹 세션 id가 필요한 동작은
// 못 하고, 깨우기(attachTmux)만 가능하다(Rail.tsx의 openRow가 그 갈림을 본다).
export function actionSessionId(row: OtherRailRowInput): string | null {
  // C1: 원격 세션의 sessionId는 `remote:<host>:<name>` 합성 키라 로컬 세션 맵에
  // 없다 — 여기서 null로 잘라야 활성 표시·컨텍스트 메뉴가 로컬 id와 엉키지 않는다.
  if (row.remote) return null;
  return row.sessionId || null;
}

// 2026-09-18 후속(그룹 재정의) — `setSessionGroup`/`renameGroup`(둘 다
// `@fsh_grp`/`~/.vt/groups.json` 기반)을 여기서 지웠다. 그룹은 이제 서버
// 왕복 없이 화면(pane 트리)에서 실시간으로 도출된다(layout/store.js의
// tabGroups) — 이름 짓기는 그 탭 자체에 라벨을 붙이는 것뿐이라
// `window.vtRenameTab`(layout/tabbar.js) 하나로 끝난다.
