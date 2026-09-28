// C1(80-multihost-agents.md §1 화면 4b) — 호스트 스위처의 순수 로직.
// rail-data.ts와 같은 원칙: DOM도 fetch도 모른다. `GET /api/hosts` 응답을
// 받아 드롭다운에 그릴 항목과, 원격 호스트를 골랐을 때 레일이 그릴 행
// 목록으로 바꾼다.
//
// 2026-09-28부터 스위처는 필터가 아니라 **화면 전체의 전환**이다(core/host-route.js,
// docs/ref/multihost.md 「Host switch」). 이 파일은 여전히 메뉴 모양만 만든다.

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
