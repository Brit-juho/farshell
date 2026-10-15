// 에이전트 세션 공유 — 화면이 쓰는 순수 로직(DOM·fetch 없음). 규칙의 출처는
// docs/ref/terminal.md 「Agent-shared sessions」와 API.md 「Agent session sharing」.

import { t } from '../core/i18n.js';
export const NICK_RE = /^[A-Za-z0-9_-]{1,32}$/;
/** 서버 라우트가 받는 세션 이름(`routes/agent_share.py _NAME_RE`). 한글 이름 세션은 공유할 수 없다. */
export const SESSION_NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;

export const MODE_LABEL = { read: t('읽기 전용'), readwrite: t('읽기·쓰기') };
export const MODE_HELP = {
  read: t('에이전트가 이 세션의 화면을 읽기만 합니다.'),
  readwrite: t('에이전트가 이 터미널에 명령을 입력할 수도 있습니다.'),
};

export const WARNING_LINES = [
  t('공유하면 에이전트가 이 세션의 화면에 보이는 모든 내용을 읽을 수 있습니다.'),
  t('읽기·쓰기는 에이전트가 이 터미널에서 명령을 실행할 수 있다는 뜻입니다.'),
  t('화면에 비밀번호·토큰이 있으면 공유하지 마세요.'),
  t('처음 쓰려는 에이전트마다 승인을 묻습니다.'),
];
export const UNSHARE_NOTE = t('「공유 해제」는 이 세션의 모든 권한과 대기 중인 요청을 즉시 지웁니다.');
export const APPROVAL_NOTE = t('허용하면 이 에이전트는 /clear 후에도 계속 쓸 수 있습니다. 「에이전트 권한」에서 언제든 철회할 수 있습니다.');

/** 닉네임 검사. 통과하면 null, 아니면 사람이 읽는 이유. */
export function validateNick(nick) {
  if (!nick) return t('닉네임을 입력하세요');
  if (nick.length > 32) return t('32자까지입니다');
  if (!NICK_RE.test(nick)) return t('영문·숫자·_·- 만 쓸 수 있습니다');
  return null;
}

/** 세션 이름에서 닉네임 기본값을 만든다. 허용 밖의 글자는 `-` 로, 앞뒤 `-` 는 뗀다. */
export function suggestNick(sessionName) {
  const s = String(sessionName || '').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32).replace(/-+$/g, '');
  return s || 'session';
}

export function normalizeMode(v) { return v === 'readwrite' ? 'readwrite' : 'read'; }

/** 세션 목록 항목의 `share` 를 검증해 `{nick, mode}` 또는 null. 손으로 심은 값은 서버가 걸러 주지만 한 번 더 본다. */
export function normalizeShare(v) {
  if (!v || typeof v !== 'object' || typeof v.nick !== 'string' || !NICK_RE.test(v.nick)) return null;
  return { nick: v.nick, mode: normalizeMode(v.mode) };
}

/** 행 메뉴 요약줄 조각: 「공유 · 닉네임 · 읽기 전용」. */
export function shareSentence(share) {
  return share ? t('공유 · {nick} · {mode}', { nick: share.nick, mode: MODE_LABEL[share.mode] || MODE_LABEL.read }) : '';
}

/** 서버 오류 → 한 줄. */
export function errorText(e) {
  const code = e && (e.code || (e.data && e.data.error));
  if (code === 'nick_taken') return t('이미 쓰는 닉네임입니다');
  if (code === 'human_session_required') return t('비밀번호로 로그인한 브라우저에서만 할 수 있습니다 (토큰 링크 접속에서는 불가)');
  if (code === 'read_only_share') return t('이 공유는 읽기 전용입니다 — 공유 설정에서 읽기·쓰기로 올린 뒤 허용하세요');
  const reason = e && e.data && e.data.reason;
  return reason || (e && e.message) || t('요청에 실패했습니다');
}

export function requestSentence(req) {
  const nick = (req.target && req.target.nick) || '?';
  const agent = (req.agent && req.agent.name) || '?';
  if (req.kind === 'msg') return t('「{agent}」이(가) 「{nick}」 세션의 에이전트에게 메시지를 보내려 합니다', { agent, nick });
  if (req.kind === 'peer') {
    return req.change === 'allow-agent'
      ? t('「{agent}」이(가) 호스트 「{nick}」의 에이전트 메시지를 이 맥이 받도록 허용하려 합니다', { agent, nick })
      : t('「{agent}」이(가) 호스트 「{nick}」에 제어 등급을 주려 합니다', { agent, nick });
  }
  return req.kind === 'write'
    ? t('「{agent}」이(가) 「{nick}」 세션의 쓰기 권한을 요청합니다', { agent, nick })
    : t('「{agent}」이(가) 「{nick}」 세션의 읽기 권한을 요청합니다', { agent, nick });
}

export function kindHelp(kind) {
  if (kind === 'msg') return t('메시지: 받는 세션의 에이전트에게 다른 에이전트가 보낸 글로 표시되어 전달됩니다. 승인해도 그 에이전트의 권한은 늘지 않습니다.');
  if (kind === 'peer') return t('호스트 권한: 제어 등급은 그 맥이 이 맥의 터미널에 명령을 칠 수 있다는 뜻입니다(원격 셸과 같습니다). 에이전트 메시지는 그 맥의 에이전트가 이 맥 에이전트에게 일을 시킬 수 있다는 뜻입니다.');
  return kind === 'write'
    ? t('쓰기: 에이전트가 이 터미널에서 명령을 실행할 수 있습니다.')
    : t('읽기: 에이전트가 이 세션의 화면에 보이는 내용을 읽습니다.');
}

export function relTime(tsSec, nowMs = Date.now()) {
  if (tsSec == null) return '';
  const sec = Math.max(0, Math.round(nowMs / 1000 - tsSec));
  if (sec < 60) return t('방금');
  const min = Math.round(sec / 60);
  if (min < 60) return t('{n}분 전', { n: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('{n}시간 전', { n: hr });
  return t('{n}일 전', { n: Math.round(hr / 24) });
}

/** 권한 수준 한 줄. effective(공유 모드와의 교집합) 기준이고, 쓰기를 받았지만 지금 막혀 있으면 그렇게 말한다. */
export function grantLevel(g) {
  const eff = g.effective || { read: g.read, write: g.write };
  if (eff.write) return t('읽기·쓰기');
  if (g.write && !eff.write) return eff.read ? t('읽기 (쓰기는 공유가 읽기 전용이라 멈춤)') : t('없음');
  return eff.read ? t('읽기') : t('없음');
}

/** 감사 기록에서 (에이전트 id, 닉네임)의 마지막 성공 사용 시각. 서버가 grant 에 lastUsed 를 주지 않아 여기서 뽑는다. */
export function lastUsedOf(grant, entries) {
  let last = null;
  for (const e of entries || []) {
    if (e.by !== 'agent' || (e.action !== 'read' && e.action !== 'send') || e.result !== 'ok') continue;
    if (!e.agent || e.agent.id !== grant.agent.id || e.nick !== grant.target.nick) continue;
    if (last == null || e.ts > last) last = e.ts;
  }
  return last;
}

const ACTION_LABEL = {
  read: t('화면 읽음'), send: t('입력 보냄'), list: t('목록 조회'), request: t('승인 요청'),
  decide: t('결정'), revoke: t('철회'), share: t('공유'), unshare: t('공유 해제'),
};
const RESULT_LABEL = {
  ok: t('성공'), allowed: t('허용'), denied: t('거부'), pending: t('대기'), pending_dup: t('대기'),
  read_only_share: t('읽기 전용이라 거절'), rate_limited: t('호출 제한'), request_denied: t('거부됨'),
};
export function auditLine(e, nowMs = Date.now()) {
  const who = e.by === 'user' ? t('사용자') : (e.agent && e.agent.name) || t('에이전트');
  const act = ACTION_LABEL[e.action] || e.action;
  const res = RESULT_LABEL[e.result] || e.result;
  return `${relTime(e.ts, nowMs)} · ${who} · ${act}${e.nick ? ` · ${e.nick}` : ''} · ${res}`;
}
