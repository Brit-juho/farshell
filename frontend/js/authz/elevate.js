// 재인증(승격) — 기존 비밀번호 확인 대화상자를 그대로 쓴다(panels/mcp.js 와 같은 방식).
import { promptDialog } from '../core/dialog.js';
import { vtFetch } from '../core/api.js';
import { t } from '../core/i18n.js';
import { needsElevation } from './api.js';

const toast = (m, type) => { if (typeof window.showToast === 'function') window.showToast(m, type); };

/** 비밀번호를 한 번 묻고 승격한다. 취소·실패면 false. */
export async function elevate(message) {
  const pw = await promptDialog(message || t('이 작업은 비밀번호를 다시 확인해야 합니다.'), { title: t('비밀번호 확인'), password: true });
  if (pw === null) return false;
  try {
    await vtFetch('/api/auth/elevate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
    return true;
  } catch (_) {
    toast(t('비밀번호 확인 실패'), 'error');
    return false;
  }
}

/** `call()` 을 부르고, 서버가 재인증을 요구하면 한 번 묻고 다시 부른다. 취소하면 null. */
export async function withElevation(call, message) {
  try {
    return await call();
  } catch (e) {
    if (!needsElevation(e)) throw e;
    if (!(await elevate(message))) return null;
    return call();
  }
}
