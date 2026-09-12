// C1(80-multihost-agents.md §1 화면 4b) — 호스트 스위처의 순수 로직.
// rail-data.ts와 같은 원칙: DOM도 fetch도 모른다. `GET /api/hosts` 응답을
// 받아 드롭다운에 그릴 항목과, 원격 호스트를 골랐을 때 레일이 그릴 행
// 목록으로 바꾼다.
//
// 왜 "필터만"인가(인계 문서의 결정 완료 사항): pane 트리는 호스트가 섞여도
// 된다 — 맥의 세션과 gpu-box의 세션을 한 화면에 나란히 두는 게 원래 목적이다.
// 스위처가 바꾸는 건 "레일에서 뭘 고를지"뿐이고, 이미 열린 pane은 건드리지
// 않는다. 그래서 이 파일에는 pane/레이아웃 개념이 아예 없다.
import type { OtherRailRowInput } from './rail-data.js';
import type { AgentState } from '../core/types.js';

/** 로컬 호스트의 예약 id — 서버(`agent_status.LOCAL_HOST`)와 같은 값. */
export const LOCAL_HOST = 'local';

export interface HostSession {
  name: string;
  windows?: number;
  attached?: number;
  command?: string;
  cwd?: string;
  status?: string;
}

export interface HostEntry {
  id: string;
  label: string;
  url?: string;
  version?: string;
  online: boolean;
  /** online:false일 때만 온다 — "왜 연결이 안 되는가"(timeout/refused/…). */
  reason?: string;
  latencyMs?: number;
  lastSeen?: number;
  sessions?: HostSession[];
}

export interface HostMenuItem {
  id: string;
  label: string;
  /** 두 번째 줄 — 온라인이면 "세션 N · 12ms", 오프라인이면 이유. */
  detail: string;
  online: boolean;
  active: boolean;
}

const REASON_LABEL: Record<string, string> = {
  timeout: '응답 없음',
  refused: '연결 거부됨',
  unauthorized: '인증 거부됨(페어링 해제?)',
  network: '네트워크 오류',
};

export function reasonLabel(reason: string | undefined | null): string {
  if (!reason) return '연결 안 됨';
  return REASON_LABEL[reason] || `연결 안 됨 · ${reason}`;
}

/** 상대시간은 rail-data의 것과 포맷이 같아야 해서 그대로 재사용한다. */
export function hostDetail(host: HostEntry): string {
  if (!host.online) return reasonLabel(host.reason);
  const n = (host.sessions || []).length;
  const rtt = host.id === LOCAL_HOST ? '' : ` · ${host.latencyMs ?? 0}ms`;
  return `세션 ${n}${rtt}`;
}

/**
 * 드롭다운 항목. 서버가 로컬을 항상 첫 항목으로 내려주므로 순서는 그대로
 * 둔다 — 여기서 정렬을 다시 하면 "로컬이 맨 위"라는 서버 계약이 두 곳에
 * 적히게 된다.
 */
export function buildHostMenu(hosts: HostEntry[], activeId: string): HostMenuItem[] {
  const resolved = resolveActiveHost(hosts, activeId);
  return hosts.map((h) => ({
    id: h.id,
    label: h.label || h.id,
    detail: hostDetail(h),
    online: !!h.online,
    active: h.id === resolved,
  }));
}

/**
 * 저장된 activeHostId가 아직 유효한가. 호스트를 `fsh host rm`으로 지운 뒤에도
 * 기기 설정에는 그 id가 남아 있다 — 그 경우 레일이 영원히 빈 목록을 그리지
 * 않도록 로컬로 되돌린다. **오프라인은 되돌리지 않는다**: 잠깐 꺼진 호스트를
 * 고른 상태는 사용자의 의도이고, 그 사실("연결 안 됨")을 보여주는 게 맞다.
 */
export function resolveActiveHost(hosts: HostEntry[], activeId: string): string {
  if (!activeId || activeId === LOCAL_HOST) return LOCAL_HOST;
  return hosts.some((h) => h.id === activeId) ? activeId : LOCAL_HOST;
}

const STATUSES: AgentState[] = ['idle', 'working', 'waiting', 'done', 'error'];
function coerceStatus(s: string | undefined): AgentState {
  return STATUSES.includes(s as AgentState) ? (s as AgentState) : 'idle';
}

/**
 * 원격 호스트를 고른 상태의 레일 행. 원격에는 워크트리 API가 없으므로(2.2 범위)
 * 전부 "행 = 세션"이다. sessionId는 웹 세션 id가 아니라 `remote:<host>:<name>`
 * 합성 키다 — 로컬 세션 id와 절대 겹치지 않게 하고, 클릭 경로가 로컬 세션
 * 전환으로 새는 것을 막는다(원격 attach는 멀티호스트 3단계).
 */
export function remoteSessionRows(host: HostEntry): OtherRailRowInput[] {
  return (host.sessions || []).map((s) => ({
    kind: 'session',
    sessionId: `remote:${host.id}:${s.name}`,
    tmuxName: s.name,
    name: s.name,
    status: coerceStatus(s.status),
    since: null,
    tool: null,
    diffFiles: null,
    remote: true,
  }));
}
