// 새 세션 계정 선택(핫스왑 H1)의 순수 로직 — 서버 선택기 목록을 정규화하고 기본 계정을 요약한다.
// 상시 로드(core)에 실리는 부분만 여기 둔다. 줄 글·정렬·선택기 줄(authLabel·rowSub·chooserEntries…)은
// accounts-text.js(shell 청크). DOM·fetch를 쓰지 않아 node:test에서 값만 검증한다.
//
// 원칙: 서버가 준 값만 보인다. 값이 없으면 「알 수 없음」이고, 사용률로 계정을 자동 고르지 않는다 —
// 정렬 옵션(「여유 많은 순」)은 목록 순서만 바꾼다.
import { t } from '../core/i18n.js';

export const TOOL_LABEL = { claude: 'Claude', codex: 'Codex' };
export const EMPTY_ACCOUNTS = Object.freeze({ ok: false, profiles: [], defaults: {} });

/** 서버 응답을 믿지 않고 모양을 맞춘다. 비밀·경로는 서버가 이미 안 준다. */
export function normalizeAccounts(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.profiles)) return { ...EMPTY_ACCOUNTS };
  const profiles = [];
  for (const p of raw.profiles) {
    if (!p || typeof p.id !== 'string' || typeof p.name !== 'string' || (p.tool !== 'claude' && p.tool !== 'codex')) continue;
    profiles.push({
      id: p.id, name: p.name, tool: p.tool,
      auth: p.auth === 'ok' || p.auth === 'broken' ? p.auth : 'missing',
      selectable: p.selectable === true,
      reason: typeof p.reason === 'string' ? p.reason : null,
      isDefault: p.default === true,
      usage: normalizeUsage(p.usage),
    });
  }
  const defaults = {};
  for (const [tool, d] of Object.entries(raw.defaults && typeof raw.defaults === 'object' ? raw.defaults : {})) {
    if ((tool === 'claude' || tool === 'codex') && d && typeof d.id === 'string') defaults[tool] = { id: d.id, valid: d.valid === true };
  }
  return { ok: true, profiles, defaults };
}

function normalizeUsage(u) {
  if (!u || typeof u !== 'object' || !Array.isArray(u.windows) || !u.windows.length) return null;
  const windows = u.windows
    .filter((w) => w && typeof w.pct === 'number' && Number.isFinite(w.pct))
    .map((w) => ({
      id: String(w.id ?? ''), label: String(w.label ?? ''), pct: w.pct,
      resetsInSec: typeof w.resets_in_sec === 'number' ? w.resets_in_sec : null,
    }));
  if (!windows.length) return null;
  return { windows, checkedAt: typeof u.checked_at === 'number' ? u.checked_at : null, stale: u.stale === true, source: typeof u.source === 'string' ? u.source : '' };
}

/** 기본 계정으로 표시된 줄들. [{tool, name, valid}] */
export function defaultSummary(acc) {
  const out = [];
  for (const tool of ['claude', 'codex']) {
    const d = acc.defaults[tool];
    if (!d) continue;
    const p = acc.profiles.find((x) => x.id === d.id);
    out.push({ tool, name: p ? p.name : null, valid: d.valid && !!p });
  }
  return out;
}

export function hasMarkedDefault(acc) { return Object.keys(acc.defaults).length > 0; }

/** 메뉴 한 줄 요약: 「기본: alice(Claude) · bob(Codex)」 / 「기본 계정 없음」. */
export function defaultLine(acc) {
  const items = defaultSummary(acc);
  if (!items.length) return t('기본 계정 없음');
  return t('기본: {names}', { names: items.map((i) => `${i.name ?? t('알 수 없음')}(${TOOL_LABEL[i.tool]})`).join(' · ') });
}

