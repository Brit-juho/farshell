import { t } from '../core/i18n.js';
import { operationId } from '../core/operation-id.js';
import { promptDialog } from '../core/dialog.js';

export function historyError(code) {
  return ({
    identity_migration_required: t('기존 실행 권한을 검토하고 다시 연결하세요'),
    identity_migration_changed: t('실행이나 권한이 변경됐습니다. 다시 검토하세요'),
    identity_unverified: t('현재 실행 확인 불가'),
    runtime_must_stop: t('실행을 종료한 뒤 보관할 수 있습니다'),
    revision_conflict: t('다른 화면에서 변경됐습니다. 새로고침 후 다시 시도하세요'),
    source_unavailable: t('등록한 원본 경로를 확인할 수 없습니다'),
    native_source_missing: t('CLI 원본이 없습니다. 보관본에서 먼저 복구하세요'),
    native_identity_unverified: t('대화 식별자를 확인하지 못해 이어갈 수 없습니다'),
    native_child_resume_unsupported: t('에이전트 내부 자식은 기록 조회만 지원합니다'),
    session_archived: t('보관된 세션은 보관을 해제한 뒤 시작할 수 있습니다'),
    no_resumable_conversation: t('이어갈 수 있는 대화가 없습니다'),
    session_not_found: t('세션을 찾을 수 없습니다'),
    conversation_already_running: t('같은 대화가 이미 실행 중입니다'),
    workspace_missing: t('작업 폴더가 없습니다'),
    cli_unavailable: t('CLI 실행 파일을 찾을 수 없습니다'),
    restore_target_exists: t('원본 위치에 파일이 있어 덮어쓰지 않았습니다'),
    capture_incomplete: t('불완전한 보관본입니다. 원본 내보내기로 확인하세요'),
    archive_quota_reached: t('보관 용량이 부족해 수집을 멈췄습니다'),
    history_access_denied: t('허용된 기록 경로인지 확인하세요'),
    replication_not_allowed: t('이 호스트의 기록 복제가 승인되지 않았습니다'),
    peer_not_paired: t('먼저 호스트를 페어링하세요'),
    invalid_replication_key: t('복제 공개키 형식이 올바르지 않습니다'),
    plan_expired: t('변경 계획이 만료됐습니다. 다시 검토하세요'),
    force_confirmation_required: t('실행 종료 확인이 필요합니다'),
    launch_needs_review: t('실행 결과를 확인하지 못했습니다. 중복 실행하지 말고 터미널을 확인하세요'),
    store_unavailable: t('기록 저장소를 읽을 수 없습니다. 기존 파일을 보존했습니다'),
  })[code] || t('작업을 완료하지 못했습니다');
}

export async function historyApi(path, { body, signal, elevated = true } = {}) {
  const run = async () => {
    const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal });
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(historyError(value.error));
      error.code = value.error; error.status = response.status;
      throw error;
    }
    return value;
  };
  try { return await run(); }
  catch (error) {
    if (!elevated || error.status !== 401 || error.code !== 'elevation_required') throw error;
    const password = await promptDialog(t('설정을 변경하려면 비밀번호를 확인하세요'), { password: true });
    if (password === null || signal?.aborted) throw new Error(t('변경을 취소했습니다'));
    await historyApi('/api/auth/elevate', { body: { password }, elevated: false, signal });
    return run();
  }
}

export { operationId };
