// 새 세션 계정 선택(핫스왑 H1) — 선택기 목록을 가져오고 메뉴로 보여 준다.
//
// 이 모듈은 세션을 만들지 않는다(term/session.js가 만든다). 상시 로드(core)에는 (1) 서버 목록 캐시와
// (2) 생성 요청에 실을 값만 둔다. 선택기 메뉴·기본 계정 변경 요청은 accounts-ui.js(shell 청크, 지연 로드),
// 줄 글은 accounts-text.js. 다른 맥(relay)을 보고 있으면 계정 기능 전체를 숨긴다 — 계정은 이 맥의 것이고 원격에서 고르지 못한다.
import { apiFetch } from '../core/api.js';
// API_BASE는 부를 때마다 window에서 읽는다(core/api.js와 같은 규칙). core/env.js를 import하면 그 순간 window.API_BASE를
// 다시 써서, 이 모듈을 불러오는 것만으로 다른 화면(설정 › 업데이트 시험)의 요청 주소가 바뀌었다.
const apiBase = () => window.API_BASE || '';
import { isRelayed } from '../core/host-route.js';
import { t } from '../core/i18n.js';
import { EMPTY_ACCOUNTS, normalizeAccounts, hasMarkedDefault } from './accounts-data.js';

const TTL_MS = 15000;
let _cache = { at: 0, value: EMPTY_ACCOUNTS };
let _inflight = null;

/** 마지막으로 받은 목록(동기). 아직 없으면 빈 목록. */
export function accountsSnapshot() {
  return isRelayed() ? EMPTY_ACCOUNTS : _cache.value;
}

/** 서버 선택기 목록. 15초 캐시, 실패(사람 세션 아님·옛 서버 등)는 「계정 없음」으로 둔다. */
export async function loadAccounts({ force = false } = {}) {
  if (isRelayed()) return EMPTY_ACCOUNTS;
  if (!force && Date.now() - _cache.at < TTL_MS) return _cache.value;
  if (_inflight) return _inflight;
  _inflight = (async () => {
    try {
      const res = await apiFetch(`${apiBase()}/api/agent-profiles/read/picker`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      _cache = { at: Date.now(), value: res.ok ? normalizeAccounts(await res.json()) : EMPTY_ACCOUNTS };
    } catch (_) {
      _cache = { at: Date.now(), value: EMPTY_ACCOUNTS };
    }
    return _cache.value;
  })().finally(() => { _inflight = null; });
  return _inflight;
}

export function forgetAccounts() { _cache = { at: 0, value: _cache.value }; }

/** 이번 생성 요청에 실을 `profile` 값. 고른 것이 없고 기본 계정도 없으면 undefined(= 지금 동작 그대로). */
export async function profileForCreate(explicit) {
  if (typeof explicit === 'string' && explicit) return explicit;
  if (isRelayed()) return undefined;
  const acc = await loadAccounts();
  return acc.ok && hasMarkedDefault(acc) ? 'default' : undefined;
}

/** 서버가 계정 때문에 세션 생성을 거절했을 때의 토스트 문구. 기본 계정으로 대체하지 않았다는 것을 밝힌다. */
export function refusalText(data) {
  const reason = data?.reason || data?.error || '';
  return t('계정 문제로 세션을 만들지 않았습니다: {reason}', { reason });
}
