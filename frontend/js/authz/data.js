// 승인함·권한·감사 화면이 쓰는 순수 로직(DOM·fetch 없음). 문구 규칙은 docs/ref/auth.md 「Inbox and grants」.
import { t } from '../core/i18n.js';

/** 프리셋 이름 — 서버가 주는 id 를 화면 언어로. 알 수 없는 id 는 그대로 보인다. */
export const PRESET_LABEL = {
  view: t('보기'), collab: t('협업'), drive: t('운전'), control: t('제어'),
};
export const KIND_LABEL = {
  share: t('세션 공유'), msg: t('메시지'), peer: t('다른 맥'), generic: t('일반 요청'), deleg: t('위임'), pair: t('페어'),
};
export const kindLabel = (k) => KIND_LABEL[k] || String(k || '');

export function presetName(preset) {
  if (!preset) return '';
  const base = PRESET_LABEL[preset.id] || preset.label || preset.id;
  // exact:false = 요청이 프리셋보다 좁다(프리셋 안에 든다). 「… 일부」는 영어로 어색해 「… 범위 안」으로 쓴다.
  return preset.exact === false ? t('{name} 범위 안', { name: base }) : base;
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
  if (item.kind === 'pair' && item.pair) return pairSentence(item);
  if (item.kind === 'msg') return t('「{who}」이(가) 「{res}」 세션의 에이전트에게 메시지를 보내려 합니다', { who, res });
  if (item.kind === 'peer') return t('「{who}」이(가) 호스트 「{res}」에 대한 권한 변경을 요청합니다 — 「{name}」', { who, res, name: name || t('권한 범위 {n}개', { n: (item.scopes || []).length }) });
  return name
    ? t('「{who}」이(가) 「{res}」에 「{name}」 권한을 요청합니다', { who, res, name })
    : t('「{who}」이(가) 「{res}」에 권한 범위(scope) {n}개를 요청합니다', { who, res, n: (item.scopes || []).length });
}

/** 승인함 행의 상세 줄 — 페어는 scope 목록 대신 묶음(세션·폴더·lease)을 말로, 나머지는 정확한 scope+조건. */
export function rowDetail(item) {
  return item.kind === 'pair' && item.pair && item.pair.bundle ? (item.pair.bundle.summary || '') : detailLine(item);
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
  if (item.kind === 'pair') {      // 선택지(한 번만·기간·범위)가 없다 — 허용 또는 거부뿐
    return [
      { choice: 'allow', label: t('허용'), available: true, why: '' },
      { choice: 'deny', label: t('거부'), available: true, why: '' },
    ];
  }
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
  if (code === 'pair_disabled') return t('페어 기능이 꺼져 있습니다');
  const reason = e && e.data && e.data.reason;
  return reason || (e && e.message) || t('요청에 실패했습니다');
}

// ── 권한 ─────────────────────────────────────────────────────────────────

export function grantSummary(g) {
  return g.preset ? presetName(g.preset) : t('권한 범위 {n}개', { n: (g.scopes || []).length });
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
    // 페어·lease 줄만 detail(개수·상태)을 요약 글로 넘긴다 — detail 객체 자체는 내보내지 않는다.
    summary: auditDetailText(e), reasonText: auditReasonText(e),
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

// ── 페어 ─────────────────────────────────────────────────────────────────

export const ROLE_LABEL = { driver: t('운전자'), navigator: t('길잡이') };
export const roleLabel = (r) => ROLE_LABEL[r] || String(r || '');

export const PAIR_STATE_LABEL = {
  proposed: t('코드 대기'), code_issued: t('코드 발급됨'), joined: t('승인 대기'), active: t('진행 중'),
  handoff_pending: t('역할 교체 대기'), ended: t('끝남'), expired: t('만료됨'), revoked: t('회수됨'),
};
export const pairStateLabel = (s) => PAIR_STATE_LABEL[s] || String(s || '');
export const PAIR_LIVE = ['proposed', 'code_issued', 'joined', 'active', 'handoff_pending'];
export const PAIR_ENDED = ['ended', 'expired', 'revoked'];

/** 구성원 표시 이름 — 에이전트는 닉네임, 이름 없는 사람은 「사람(나)」. */
export function memberName(m) {
  if (!m) return '?';
  if (m.name) return m.name;
  return m.kind === 'human' ? t('사람(나)') : t('상대 에이전트');
}

/** 승인 요청(`req.pair.roles`)의 구성원 둘 — 시작한 쪽이 먼저. */
export function pairParties(req) {
  const roles = (req.pair && req.pair.roles) || {};
  return ['initiator', 'joiner'].map((seat) => ({ seat, ...(roles[seat] || {}), name: memberName(roles[seat]) }));
}

/** 역할이 받는 프리셋 `{name, detail}` — 이름은 보기/협업/운전/제어(일부면 「… 범위 안」), 상세는 정확한 scope+조건. */
export function rolePreset(req, role) {
  const pr = ((req.pair && req.pair.presets) || {})[role] || {};
  const name = presetName(pr.preset) || t('권한 범위 {n}개', { n: (pr.scopes || []).length });
  return { name, detail: detailLine({ scopes: pr.scopes || [], conds: pr.conds || {} }) };
}

export function pairSentence(req) {
  const [a, b] = pairParties(req);
  return t('「{a}」와(과) 「{b}」이(가) 같은 작업 묶음을 함께 하려 합니다', { a: a.name, b: b.name });
}

/** 허용 버튼 글자 — 허용하면 무엇이 주어지는지를 그대로(역할별 프리셋 + 체크한 높은 권한). */
export function pairAllowLabel(req, ticked = []) {
  const parts = pairParties(req).map((p) => t('{who}: {role} · {preset}', { who: p.name, role: roleLabel(p.role), preset: rolePreset(req, p.role).name }));
  const items = ((req.pair && req.pair.elevatedItems) || []).filter((it) => ticked.includes(it.id));
  const tail = items.length ? t('높은 권한 {list}', { list: items.map((it) => String(it.label || it.id).split('(')[0].trim()).join(', ') }) : t('높은 권한 없이');
  return t('허용 — {parts} · {tail}', { parts: parts.join(' / '), tail });
}

/** 서버가 준 사유를 우선한다 — 확인 문구 불일치·elevation_required·already_decided 등은 서버 문장이 가장 정확하다. */
export function pairErrorText(e) {
  const reason = e && e.data && e.data.reason;
  return reason || errorText(e);
}

/** 서버가 요청을 닫은 응답 — 목록에서 뺀다. */
export const PAIR_CLOSED_CODES = ['phrase_mismatch_locked', 'member_gone'];

/** `pp-<raw>-…` 권한 id → pair id `pr-<raw>`. 형식이 아니면 ''. */
export function pairIdOfGrant(g) {
  const m = /^pp-([0-9a-f]{10})-/.exec(String((g && g.id) || ''));
  return m ? `pr-${m[1]}` : '';
}

export function pairTimeLeft(p, nowSec = Date.now() / 1000) {
  if (!p || p.expires == null || !PAIR_LIVE.includes(p.state)) return '';
  const left = p.expires - nowSec;
  return left <= 0 ? t('곧 끝남') : (span(left) ? t('{span} 남음', { span: span(left) }) : t('1분 미만 남음'));
}

/** m:ss — 코드 유효 시간 카운트다운. */
export function mmss(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function pairBundleText(p) {
  const b = (p && p.bundle) || {};
  const names = (b.sessions || []).map((s) => s.name).filter(Boolean);
  const dirs = b.dirs || [];
  const bits = [];
  if (names.length) bits.push(t('세션 {list}', { list: names.join(', ') }));
  if (dirs.length) bits.push(t('폴더 {list}', { list: dirs.join(', ') }));
  bits.push(b.leases ? t('폴더 점유 시작') : t('점유 안 함'));
  return bits.join(' · ');
}

// ── lease ────────────────────────────────────────────────────────────────

export const MODE_LABEL = { write: t('쓰기'), read: t('읽기') };

export function leaseExpiry(l) {
  const left = l.remaining != null ? l.remaining : (l.expires != null ? l.expires - Date.now() / 1000 : null);
  if (left == null) return t('만료 없음');
  if (left <= 0) return t('만료됨');
  return span(left) ? t('{span} 뒤 만료', { span: span(left) }) : t('곧 만료');
}

// ── 감사 문장(페어·lease) ───────────────────────────────────────────────────

const PAIR_ACTION = {
  'pair.create': t('페어 만듦'), 'pair.code': t('참여 코드 발급'), 'pair.join': t('참여 코드로 합류'), 'pair.approve': t('페어 승인'),
  'pair.handoff_offer': t('역할 교체 제안'), 'pair.handoff': t('역할 교체'), 'pair.end': t('페어 끝'),
};
const LEASE_ACTION = {
  'lease.acquire': t('폴더 점유 시작'), 'lease.renew': t('폴더 점유 연장'), 'lease.release': t('폴더 점유 해제'),
  'lease.revoke': t('폴더 점유 회수'), 'lease.expire': t('폴더 점유 만료'), 'lease.override': t('사람이 점유 중인 경로에 직접 씀'),
};
const END_REASON = {
  denied: t('승인 거부'), phrase_mismatch: t('확인 문구 불일치'), member_gone: t('참여 세션이 사라짐'), panic: t('비상 정지'),
  idle: t('오래 쓰지 않아 끝남'), ended: t('구성원이 끝냄'), revoked_by_human: t('사람이 회수'), superseded: t('같은 시작자의 새 페어로 교체'),
  proposal_expired: t('합류 전에 만료'), approval_expired: t('승인 전에 만료'), max_lifetime: t('최대 시간(8시간)에 도달'),
};

export const endReasonText = (r) => END_REASON[r] || String(r || '');

/** 감사 항목의 표시 이름 — 페어·lease 는 읽기 쉬운 말, 나머지는 action 그대로. */
export function auditActionText(action) {
  return PAIR_ACTION[action] || LEASE_ACTION[action] || action;
}

/**
 * 페어·lease 감사 줄의 한 줄 요약 — 서버 기록의 개수·상태만 쓴다(세션 이름·경로·코드·확인 문구는 기록에 없다).
 * 다른 action 이거나 detail 이 없으면 ''.
 */
export function auditDetailText(e) {
  const d = e && e.detail;
  if (!d || typeof d !== 'object') return '';
  const a = String(e.action || '');
  const bits = [];
  if (a.startsWith('pair.')) {
    if (Number.isFinite(d.sessions) || Number.isFinite(d.dirs)) {
      bits.push(t('세션 {s}개 · 폴더 {d}개', { s: Number(d.sessions) || 0, d: Number(d.dirs) || 0 }));
    }
    if (typeof d.leases === 'boolean') bits.push(d.leases ? t('폴더 점유 포함') : t('폴더 점유 없음'));
    if (Number.isFinite(d.elevated)) bits.push(d.elevated > 0 ? t('높은 권한 {n}개 요청', { n: d.elevated }) : t('높은 권한 없음'));
    if (d.state) bits.push(t('상태 {state}', { state: pairStateLabel(d.state) }));
    if (Number.isFinite(d.generation) && d.generation > 0) bits.push(t('교체 {n}회', { n: d.generation }));
    return bits.join(' · ');
  }
  if (a.startsWith('lease.')) {
    if (d.mode) bits.push(MODE_LABEL[d.mode] || String(d.mode));
    if (d.exclusive) bits.push(t('배타'));
    if (d.derived_from) bits.push(t('상위에서 상속'));
    if (d.deleg) bits.push(t('위임 하위'));
    return bits.join(' · ');
  }
  return '';
}

/** 감사 항목의 reason 칸 — pair 종료 사유처럼 알려진 코드는 말로, 아니면 그대로. */
export function auditReasonText(e) {
  const r = String((e && e.reason) || '');
  if (!r) return '';
  // 반환값이 곧 화면 글이다(빈 글이면 숨긴다) — 모르는 코드는 그대로 돌려준다.
  if ((e.action === 'pair.code' && r === 'issued') || (e.action === 'pair.join' && r === 'redeemed')) return '';   // 줄 이름이 이미 말한다
  if (String(e.action || '') === 'pair.end') return END_REASON[r] || r;
  if (String(e.action || '') === 'pair.approve') {
    const m = /^elevated=(\d+)$/.exec(r);
    if (m) return Number(m[1]) > 0 ? t('높은 권한 {n}개 허용', { n: Number(m[1]) }) : t('높은 권한 허용 없음');
  }
  if (/^role=(driver|navigator)$/.test(r) && e.action === 'pair.create') return t('시작자 역할 {role}', { role: roleLabel(r.slice(5)) });
  return r;
}
