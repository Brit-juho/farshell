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
