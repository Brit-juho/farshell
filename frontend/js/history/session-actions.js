import { promptDialog, confirmDialog } from '../core/dialog.js';
import { t } from '../core/i18n.js';
import { historyApi, operationId } from './api.js';

const notify = () => window.dispatchEvent(new Event('vt:catalog-changed'));

/** 종료된 세션을 같은 세션 id로 이어서 시작하고, 열려 있는 터미널 화면으로 가져온다. */
export async function resumeSession(id, { signal } = {}) {
  const result = await historyApi(`/api/session-catalog/sessions/${id}/resume`, { body: { operation_id: operationId() }, signal });
  notify();
  // 터미널 전환 실패는 시작 성공을 뒤집지 않는다. 행이 곧 실행 중 행으로 바뀐다.
  try {
    await window.vtOpenSessionFromRail?.({ sessionId: null, tmuxName: result.name, catalogSessionId: result.session_id, runtimeId: result.runtime_id });
  } catch (_) { /* ignore */ }
  return result;
}

export async function renameSession(session, { signal } = {}) {
  const title = await promptDialog(t('작업 이름'), { defaultValue: session.title });
  if (title === null || title.trim() === session.title) return false;
  await historyApi(`/api/session-catalog/sessions/${session.id}`, { body: { title, revision: session.revision }, signal });
  notify();
  return true;
}

export async function setSessionArchived(session, archived, { signal } = {}) {
  await historyApi(`/api/session-catalog/sessions/${session.id}`, { body: { archived, revision: session.revision }, signal });
  notify();
  return true;
}

/** 보관함의 세션 기록 한 줄을 지운다. 대화 보관함의 대화와 원본 사본은 남는다. 확인창에서 취소하면 false. */
export async function deleteSession(session, { signal } = {}) {
  if (!await confirmDialog(t('「{title}」 세션 기록을 삭제합니다. 대화 보관함의 대화와 원본은 남습니다. 계속할까요?', { title: session.title }))) return false;
  await historyApi(`/api/session-catalog/sessions/${session.id}`, { method: 'DELETE', body: { revision: session.revision }, signal });
  notify();
  return true;
}

/** 확인 없이 차례로 삭제한다(호출한 쪽이 이미 한 번 확인했다). 막힌 것(위임 관계·다른 화면에서 바뀜)은 건너뛰고 센다. */
export async function deleteMany(sessions) {
  let done = 0, failed = 0;
  for (const session of sessions) {
    try { await historyApi(`/api/session-catalog/sessions/${session.id}`, { method: 'DELETE', body: { revision: session.revision } }); done++; }
    catch (_) { failed++; }
  }
  notify();
  return { done, failed };
}

/** "3분 전", "2시간 전" 같은 상대 시각. 입력은 초 단위 epoch. */
export function agoLabel(seconds) {
  if (!seconds) return '';
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 60) return t('방금 전');
  if (diff < 3600) return `${Math.floor(diff / 60)}${t('분 전')}`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}${t('시간 전')}`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}${t('일 전')}`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/**
 * 카탈로그가 기록하는 실행 상태를 사용자 문구와 다음 동작으로 바꾼다.
 * 서버가 실제로 쓰는 값: never_started(런타임 없음) · running · unknown(tmux 조회 불완전) · stopped.
 * attached=false인 running은 서버는 실행 중으로 아는데 현재 tmux 목록에 아직 안 보이는 경우다.
 */
export function executionStatus(session, attached = false) {
  if (session.archived) return { label: t('보관됨'), tone: 'idle' };
  if (session.ended != null || session.execution_state === 'stopped') return { label: t('실행 종료됨'), tone: 'idle' };
  switch (session.execution_state) {
    case 'never_started': return { label: t('아직 실행 전'), hint: t('이 세션은 아직 한 번도 실행되지 않았습니다'), tone: 'idle' };
    case 'running': return attached ? { label: t('실행 중'), tone: 'ok' }
      : { label: t('연결 확인 중'), hint: t('실행 중으로 기록돼 있지만 터미널 목록에는 아직 없습니다'), tone: 'warn', recheck: true };
    case 'starting': return { label: t('시작하는 중'), tone: 'warn' };
    case 'stopping': return { label: t('종료하는 중'), tone: 'warn' };
    case 'conflicted': return { label: t('식별자 충돌'), hint: t('같은 이름의 다른 실행과 겹칩니다. 터미널에서 직접 확인하세요'), tone: 'bad' };
    default: return { label: t('상태 확인 필요'), hint: t('터미널(tmux) 상태를 읽지 못해 종료 여부를 확정하지 못했습니다'), tone: 'warn', recheck: true };
  }
}

export async function recheckCatalog({ signal } = {}) {
  const result = await historyApi('/api/session-catalog/reconcile', { body: {}, signal });
  notify();
  return result;
}

/** 종료됐지만 연결된 대화가 없는 세션(대화 연결 기록이 생기기 전에 끝난 과거 기록). */
export function isLegacy(session) {
  return session.ended != null && !session.archived && !session.resume_conversation_id;
}

/** 순서대로 보관한다. 다른 화면에서 바뀐 세션(revision 충돌)은 건너뛰고 개수만 센다. */
export async function archiveMany(sessions) {
  let done = 0, failed = 0;
  for (const session of sessions) {
    try { await historyApi(`/api/session-catalog/sessions/${session.id}`, { body: { archived: true, revision: session.revision } }); done++; }
    catch (_) { failed++; }
  }
  notify();
  return { done, failed };
}
