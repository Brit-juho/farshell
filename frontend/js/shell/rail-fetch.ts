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

import type { WorktreeRailRowInput, OtherRailRowInput } from './rail-data.js';

// §5 원문: "60초 캐시" — 「기타」 세션 행에만 쓴다(워크트리 행은 changed
// 요약을 서버가 준다).
const GIT_CACHE_MS = 60000;

export interface RailDeps {
  vtFetch: (path: string, opts?: RequestInit) => Promise<unknown>;
  getAction: (name: string) => unknown;
}

export function safeFetch<T>(deps: RailDeps, path: string): Promise<T | null> {
  return deps.vtFetch(path).then((v) => v as T).catch(() => null);
}

export type DesktopRailRow =
  | (WorktreeRailRowInput & { statusSentence: string })
  | (OtherRailRowInput & { statusSentence: string });

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

// ---- 데이터 수집 ----

export interface AgentDetail {
  since: number | null;
  tool: string | null;
  question: string | null;
  options: { key: string; label: string }[] | null;
  /** 떠 있는 CLI 이름(claude·codex·…). ui/icons.js의 agentMarkFor가 마크로 바꾼다. */
  agent: string | null;
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
      // 2.1.6 — 어떤 CLI가 떠 있는지. 지금까지 이 값을 쓰는 곳은 헤더의
      // 워크트리 탭(layout/tabbar.js)뿐이었고, 레일 행과 폰의 플릿 행은
      // 같은 세션을 그리면서도 에이전트 마크를 안 달았다. 같은 세션을 보는
      // 세 화면이 서로 다른 것을 말하고 있었다.
      agent: (entry as any).agent ?? null,
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

// 30-worktree.md §2: 세션이 하나도 없는 워크트리를 여는 API. 흐리게 표시된
// 행을 클릭했을 때만 탄다(대부분은 이미 tmux 세션이 있어 attachTmux로 충분).
export async function openWorktree(deps: RailDeps, wtId: string): Promise<string | null> {
  try {
    const data = await deps.vtFetch(`/api/worktrees/${encodeURIComponent(wtId)}/open`, { method: 'POST' }) as { tmux_session?: string };
    return data?.tmux_session || null;
  } catch (_) {
    return null;
  }
}

// 행이 가리키는 "열 수 있는" 대상. 워크트리 행 중 세션이 전혀 없는(흐리게
// 표시된) 행은 null — 컨텍스트 메뉴(세션 대상 액션)를 못 연다, 클릭은 openRow가
// 별도로 open API로 처리한다.
export function actionSessionId(row: DesktopRailRow): string | null {
  // C1: 원격 세션의 sessionId는 `remote:<host>:<name>` 합성 키라 로컬 세션 맵에
  // 없다 — 여기서 null로 잘라야 활성 표시·컨텍스트 메뉴가 로컬 id와 엉키지 않는다.
  if (row.kind === 'session') return row.remote ? null : row.sessionId;
  return row.primarySessionId;
}
