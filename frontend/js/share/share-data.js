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
