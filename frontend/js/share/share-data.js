// 에이전트 세션 공유 — 화면이 쓰는 순수 로직(DOM·fetch 없음). 규칙의 출처는
// docs/ref/terminal.md 「Agent-shared sessions」와 API.md 「Agent session sharing」.

export const NICK_RE = /^[A-Za-z0-9_-]{1,32}$/;
/** 서버 라우트가 받는 세션 이름(`routes/agent_share.py _NAME_RE`). 한글 이름 세션은 공유할 수 없다. */
export const SESSION_NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;

export const MODE_LABEL = { read: '읽기 전용', readwrite: '읽기·쓰기' };
export const MODE_HELP = {
  read: '에이전트가 이 세션의 화면을 읽기만 합니다.',
  readwrite: '에이전트가 이 터미널에 명령을 입력할 수도 있습니다.',
};

export const WARNING_LINES = [
  '공유하면 에이전트가 이 세션의 화면에 보이는 모든 내용을 읽을 수 있습니다.',
  '읽기·쓰기는 에이전트가 이 터미널에서 명령을 실행할 수 있다는 뜻입니다.',
  '화면에 비밀번호·토큰이 있으면 공유하지 마세요.',
  '처음 쓰려는 에이전트마다 승인을 묻습니다.',
];
export const UNSHARE_NOTE = '「공유 해제」는 이 세션의 모든 권한과 대기 중인 요청을 즉시 지웁니다.';
export const APPROVAL_NOTE = '허용하면 이 에이전트는 /clear 후에도 계속 쓸 수 있습니다. 「에이전트 권한」에서 언제든 철회할 수 있습니다.';

/** 닉네임 검사. 통과하면 null, 아니면 사람이 읽는 이유. */
export function validateNick(nick) {
  if (!nick) return '닉네임을 입력하세요';
  if (nick.length > 32) return '32자까지입니다';
  if (!NICK_RE.test(nick)) return '영문·숫자·_·- 만 쓸 수 있습니다';
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
  return share ? `공유 · ${share.nick} · ${MODE_LABEL[share.mode] || MODE_LABEL.read}` : '';
}

/** 서버 오류 → 한 줄. */
export function errorText(e) {
  const code = e && (e.code || (e.data && e.data.error));
  if (code === 'nick_taken') return '이미 쓰는 닉네임입니다';
  if (code === 'human_session_required') return '비밀번호로 로그인한 브라우저에서만 할 수 있습니다 (토큰 링크 접속에서는 불가)';
  if (code === 'read_only_share') return '이 공유는 읽기 전용입니다 — 공유 설정에서 읽기·쓰기로 올린 뒤 허용하세요';
  const reason = e && e.data && e.data.reason;
  return reason || (e && e.message) || '요청에 실패했습니다';
}

export function requestSentence(req) {
  const nick = (req.target && req.target.nick) || '?';
  const agent = (req.agent && req.agent.name) || '?';
  return `「${agent}」이(가) 「${nick}」 세션의 ${req.kind === 'write' ? '쓰기' : '읽기'} 권한을 요청합니다`;
}

export function kindHelp(kind) {
  return kind === 'write'
    ? '쓰기: 에이전트가 이 터미널에서 명령을 실행할 수 있습니다.'
    : '읽기: 에이전트가 이 세션의 화면에 보이는 내용을 읽습니다.';
}

export function relTime(tsSec, nowMs = Date.now()) {
  if (tsSec == null) return '';
  const sec = Math.max(0, Math.round(nowMs / 1000 - tsSec));
  if (sec < 60) return '방금';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}분 전`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  return `${Math.round(hr / 24)}일 전`;
}

/** 권한 수준 한 줄. effective(공유 모드와의 교집합) 기준이고, 쓰기를 받았지만 지금 막혀 있으면 그렇게 말한다. */
export function grantLevel(g) {
  const eff = g.effective || { read: g.read, write: g.write };
  if (eff.write) return '읽기·쓰기';
  if (g.write && !eff.write) return eff.read ? '읽기 (쓰기는 공유가 읽기 전용이라 멈춤)' : '없음';
  return eff.read ? '읽기' : '없음';
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
  read: '화면 읽음', send: '입력 보냄', list: '목록 조회', request: '승인 요청',
  decide: '결정', revoke: '철회', share: '공유', unshare: '공유 해제',
};
const RESULT_LABEL = {
  ok: '성공', allowed: '허용', denied: '거부', pending: '대기', pending_dup: '대기',
  read_only_share: '읽기 전용이라 거절', rate_limited: '호출 제한', request_denied: '거부됨',
};
export function auditLine(e, nowMs = Date.now()) {
  const who = e.by === 'user' ? '사용자' : (e.agent && e.agent.name) || '에이전트';
  const act = ACTION_LABEL[e.action] || e.action;
  const res = RESULT_LABEL[e.result] || e.result;
  return `${relTime(e.ts, nowMs)} · ${who} · ${act}${e.nick ? ` · ${e.nick}` : ''} · ${res}`;
}
