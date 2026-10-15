// 승인함·권한·감사 화면이 쓰는 순수 로직(DOM·fetch 없음). 문구 규칙은 docs/ref/auth.md 「Inbox and grants」.
import { t } from '../core/i18n.js';

/** 프리셋 이름 — 서버가 주는 id 를 화면 언어로. 알 수 없는 id 는 그대로 보인다. */
export const PRESET_LABEL = {
  view: t('보기'), collab: t('협업'), drive: t('운전'), control: t('제어'),
};
export const KIND_LABEL = {
  share: t('세션 공유'), msg: t('메시지'), peer: t('다른 맥'), generic: t('일반 요청'), deleg: t('위임'),
};
export const kindLabel = (k) => KIND_LABEL[k] || String(k || '');

export function presetName(preset) {
  if (!preset) return '';
  const base = PRESET_LABEL[preset.id] || preset.label || preset.id;
  return preset.exact === false ? t('{name} 일부', { name: base }) : base;
}

/** 서버가 준 조건 `{scope:{cond:[v…]}}` 를 `scope[cond=a,b]` 줄로 — 가공 없이 그대로 보이는 상세 줄용. */
export function condsText(conds) {
  const out = [];
  for (const [scope, entry] of Object.entries(conds || {})) {
    const parts = Object.entries(entry || {}).map(([k, v]) => `${k}=${(Array.isArray(v) ? v : [v]).join(',')}`);
    if (parts.length) out.push(`${scope}[${parts.join('; ')}]`);
  }
  return out.join(' · ');
}

/** 상세 줄: 정확한 scope 와 조건. */
export function detailLine(item) {
  const sc = (item.scopes || []).join(', ');
  const cd = condsText(item.conds);
  return cd ? `${sc} · ${cd}` : sc;
}

export function requester(item) {
  const s = item.subject || {};
  return s.name || s.id || '?';
}

export function resourceText(item) {
  const r = item.resource;
  if (r && typeof r === 'object') return r.label ? `${r.label}` : (r.id || '?');
  return r || '?';
}

/** 요청이 뜻하는 것을 프리셋 이름으로 말한다. 프리셋이 없으면 scope 개수로. */
export function askSentence(item) {
  const who = requester(item);
  const res = resourceText(item);
  const name = presetName(item.preset);
  if (item.kind === 'msg') return t('「{who}」이(가) 「{res}」 세션의 에이전트에게 메시지를 보내려 합니다', { who, res });
  if (item.kind === 'peer') return t('「{who}」이(가) 호스트 「{res}」에 대한 권한 변경을 요청합니다 — 「{name}」', { who, res, name: name || t('{n}개 scope', { n: (item.scopes || []).length }) });
  return name
    ? t('「{who}」이(가) 「{res}」에 「{name}」 권한을 요청합니다', { who, res, name })
    : t('「{who}」이(가) 「{res}」에 {n}개 scope 를 요청합니다', { who, res, n: (item.scopes || []).length });
}

export function lineageText(item) {
  const l = item.lineage;
  if (!l || !l.delegation) return '';
  return t('위임된 하위 세션 · 위임 {id}', { id: l.delegation });
}

function span(sec) {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return null;
  const min = Math.round(s / 60);
  if (min < 60) return t('{n}분', { n: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('{n}시간', { n: hr });
  return t('{n}일', { n: Math.round(hr / 24) });
}
export function ageText(sec) {
  const v = span(sec);
  return v ? t('{span} 전 요청', { span: v }) : t('방금 요청');
}
export function expiryText(sec) {
  const v = span(sec);
  return v ? t('{span} 뒤 만료', { span: v }) : t('곧 만료');
}

/** 이 종류가 받는 선택지. 서버가 알려 주는 것은 오류뿐이라 종류별 규칙(API.md)을 그대로 옮긴다. */
export function choicesFor(item) {
  const onlyAllowDeny = item.kind === 'msg' || item.kind === 'peer';
  return [
    { choice: 'once', label: t('한 번만'), available: !onlyAllowDeny,
      why: onlyAllowDeny ? t('이 종류의 요청은 허용·거부만 받습니다 — 한 번만은 고를 수 없습니다') : '' },
    { choice: 'session', label: t('이 세션 동안'), available: true, why: '' },
    { choice: 'deny', label: t('거부'), available: true, why: '' },
  ];
}

export function errorText(e) {
  const code = e && (e.code || (e.data && e.data.error));
  if (code === 'human_session_required') return t('비밀번호로 로그인한 브라우저에서만 할 수 있습니다 (토큰 링크 접속에서는 불가)');
  if (code === 'elevation_required') return t('비밀번호를 다시 확인해야 합니다');
  if (code === 'already_decided') return t('이미 다른 결정이 내려진 요청입니다');
  if (code === 'approver_lacks_scope') return t('이 요청이 구하는 권한을 승인하는 사람이 갖고 있지 않아 승인할 수 없습니다');
  if (code === 'audit_unavailable') return t('감사 기록을 쓰지 못해 변경을 중단했습니다');
  const reason = e && e.data && e.data.reason;
  return reason || (e && e.message) || t('요청에 실패했습니다');
}

// ── 권한 ─────────────────────────────────────────────────────────────────

export function grantSummary(g) {
  return g.preset ? presetName(g.preset) : t('{n}개 scope', { n: (g.scopes || []).length });
}

const VIA_LABEL = { manual: t('직접 승인'), window: t('자동 승인 구간'), system: t('시스템'), migrated: t('이전된 권한') };
export function approvedText(g) {
  const by = g.approvedBy || '';
  const via = g.approvedVia ? (VIA_LABEL[g.approvedVia] || g.approvedVia) : '';
  return [by, via].filter(Boolean).join(' · ');
}

export function grantExpiry(g, nowSec = Date.now() / 1000) {
  const parts = [];
  if (g.expires != null) {
    const left = g.expires - nowSec;
    parts.push(left <= 0 ? t('만료됨') : (span(left) ? t('{span} 뒤 만료', { span: span(left) }) : t('곧 만료')));
  } else {
    parts.push(t('만료 없음'));
  }
  if (g.maxUses != null) parts.push(t('남은 횟수 {n}', { n: Math.max(0, g.maxUses - (g.uses || 0)) }));
  return parts.join(' · ');
}

/** 읽기 권한만 남기고 입력만 거두는 「쓰기만 회수」가 가능한가(공유 grant 에 입력 scope 가 있을 때). */
export function canRevokeWriteOnly(g) {
  return g.kind === 'share' && (g.scopes || []).includes('session.input');
}

/** 기본 규칙 — 코드가 정해 둔 것이라 회수 대상이 아니다. 서버 목록에는 오지 않아 말로 적어 둔다. */
export function builtinDefaults() {
  return [
    { id: 'human', who: t('로그인한 사람'), what: t('이 맥의 거의 모든 권한 (승인 결정은 비밀번호를 다시 확인한 뒤)') },
    { id: 'agent', who: t('에이전트'), what: t('자기 세션 보기·읽기·한 줄 입력, 승인 요청 보내기, 한도 안의 하위 세션 위임') },
    { id: 'peer', who: t('다른 맥'), what: t('연결 확인만') },
  ];
}

// ── 감사 ─────────────────────────────────────────────────────────────────

/** 화면에 내보내는 감사 항목 필드의 허용 목록 — 이 밖의 필드는 있어도 그리지 않는다(내용 누출 방지). */
export function auditView(e) {
  return {
    ts: e.ts, source: e.source === 'denials' ? 'denials' : 'chain',
    principal: e.principal || '', action: e.action || '', resource: e.resource || '',
    scopes: Array.isArray(e.scopes) ? e.scopes.map(String) : [],
    outcome: e.result || e.decision || '', reason: e.reason || '',
  };
}

export const RANGES = [
  { id: 'all', label: t('전체 기간'), sec: 0 },
  { id: '1h', label: t('최근 1시간'), sec: 3600 },
  { id: '24h', label: t('최근 24시간'), sec: 86400 },
  { id: '7d', label: t('최근 7일'), sec: 7 * 86400 },
];
export function sinceFor(rangeId, nowSec = Date.now() / 1000) {
  const r = RANGES.find((x) => x.id === rangeId);
  return r && r.sec ? Math.floor(nowSec - r.sec) : 0;
}

export function stamp(ts) {
  if (ts == null) return '';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 해시 체인 배지 모델. broken 만 경고다. */
export function chainBadge(res) {
  const chain = res && res.chain;
  if (chain === 'ok') return { state: 'ok', text: t('기록 무결성 확인됨') };
  if (chain === 'broken') {
    return { state: 'broken', text: res.brokenSeq != null ? t('기록 체인이 끊겼습니다 (#{seq} 부근) — 기록이 변조됐을 수 있습니다', { seq: res.brokenSeq }) : t('기록 체인이 끊겼습니다 — 기록이 변조됐을 수 있습니다') };
  }
  return { state: 'unknown', text: t('기록 무결성을 확인하지 못했습니다') };
}
