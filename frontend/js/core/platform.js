// OS의 키보드 규칙과 설정 저장 프로필은 별개의 값이다.
const platform = typeof navigator === 'undefined' ? '' : navigator.platform || navigator.userAgent || '';
const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent || '';
export const isMacKeyboard = /Mac|iPhone|iPad|iPod/i.test(platform);
export const osFamily = isMacKeyboard ? 'mac' : /Win/i.test(platform) ? 'windows' : 'linux';
export const settingsProfile = /Android|iPhone|iPad|iPod/i.test(ua)
  || (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) ? 'mobile' : osFamily;
export function isStandalone() {
  try { return window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true; }
  catch (_) { return false; }
}
