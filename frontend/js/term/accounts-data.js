// 새 세션 계정 선택(핫스왑 H1)의 순수 로직 — 서버 선택기 목록을 화면 줄로 바꾼다.
// DOM·fetch를 쓰지 않아 node:test에서 값만 검증한다(term/accounts.js가 화면과 요청을 맡는다).
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

export function authLabel(auth) {
  if (auth === 'ok') return t('정상');
  if (auth === 'broken') return t('깨짐');
  return t('로그인 안 됨');
}

/** 여유 = 100 − 가장 많이 쓴 창의 사용률. 값이 없으면 null(정렬에서 맨 뒤). */
export function headroom(row) {
  const w = row.usage?.windows;
  if (!w || !w.length) return null;
  return 100 - Math.max(...w.map((x) => x.pct));
}

/** 'headroom'이면 여유 많은 순(모르는 것은 뒤, 같으면 이름순). 그 밖에는 서버가 준 순서 그대로. */
export function sortRows(rows, mode) {
  const list = rows.slice();
  if (mode !== 'headroom') return list;
  return list.sort((a, b) => {
    const ha = headroom(a), hb = headroom(b);
    if (ha === null && hb === null) return a.name.localeCompare(b.name);
    if (ha === null) return 1;
    if (hb === null) return -1;
    return hb - ha || a.name.localeCompare(b.name);
  });
}

export function durationText(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return t('{n}초', { n: s });
  const min = Math.round(s / 60);
  if (min < 60) return t('{n}분', { n: min });
  const hr = Math.floor(min / 60);
  if (hr < 24) return min % 60 ? t('{h}시간 {m}분', { h: hr, m: min % 60 }) : t('{n}시간', { n: hr });
  return t('{n}일', { n: Math.round(hr / 24) });
}

const WINDOW_LABEL = { '5h': '5시간', weekly: '주간' };
function windowName(w) {
  const k = WINDOW_LABEL[w.label];
  return k ? t(k) : (w.label || w.id);
}

/** 「5시간 42% · 2시간 뒤 리셋 · 주간 18%」. 값이 없으면 「알 수 없음」. */
export function usageText(row) {
  const w = row.usage?.windows;
  if (!w || !w.length) return t('사용량 알 수 없음');
  return w.map((x) => {
    const base = `${windowName(x)} ${Math.round(x.pct)}%`;
    return x.resetsInSec === null ? base : `${base} · ${t('{when} 뒤 리셋', { when: durationText(x.resetsInSec) })}`;
  }).join(' · ');
}

/** 「3분 전 갱신」. 시각이 없으면 빈 문자열. 오래된 값(stale)이면 그렇다고 적는다. */
export function updatedText(row, nowMs = Date.now()) {
  const at = row.usage?.checkedAt;
  if (at == null) return '';
  const base = t('{when} 전 갱신', { when: durationText(nowMs / 1000 - at) });
  return row.usage.stale ? `${base} · ${t('오래된 값')}` : base;
}

export function sourceText(row) {
  const s = row.usage?.source;
  if (s === 'oauth-usage') return t('출처: 공식 사용량 조회(OAuth)');
  if (s === 'codex-usage') return t('출처: Codex 사용량 조회');
  return '';
}

/** 선택기 줄의 보조 글: 도구 · 인증 상태 · 사용률/리셋 · 갱신 시각. */
export function rowSub(row, nowMs = Date.now()) {
  const parts = [TOOL_LABEL[row.tool], authLabel(row.auth)];
  if (!row.selectable && row.reason) parts.push(row.reason);
  else {
    parts.push(usageText(row));
    const u = updatedText(row, nowMs);
    if (u) parts.push(u);
  }
  return parts.join(' · ');
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

/**
 * 선택기가 그릴 줄. mode 'once' = 이번 세션만(없이 만들기 포함), 'default' = 기본 계정 바꾸기(해제 포함).
 * 쓸 수 없는 계정(깨짐·로그인 안 됨·미지원)은 disabled와 사유로 남긴다 — 숨기지 않는다.
 */
export function chooserEntries(acc, { mode, sort }) {
  const entries = sortRows(acc.profiles, sort).map((p) => ({
    kind: 'profile', id: p.id, tool: p.tool, name: p.name, sub: rowSub(p), tip: sourceText(p),
    disabled: !p.selectable, isDefault: p.isDefault,
  }));
  if (mode === 'once') {
    entries.push({ kind: 'none', id: 'none', name: t('계정 지정 없이'), sub: t('이번 세션은 평소 환경 그대로 만듭니다'), disabled: false });
  } else {
    for (const tool of Object.keys(acc.defaults)) {
      entries.push({ kind: 'clear', tool, name: t('{tool} 기본 계정 해제', { tool: TOOL_LABEL[tool] }), sub: t('새 세션에 계정을 지정하지 않습니다'), disabled: false });
    }
  }
  return entries;
}
