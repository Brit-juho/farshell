// 한 브라우저 탭의 tmux attach 식별자. 새로고침에는 유지되고 다른 기기/탭에는
// 공유되지 않는다. 서버는 (tmux 세션, 이 값)마다 별도 PTY를 만든다.
const KEY = 'vt.screenId';
let memoryId = '';

export function screenId() {
  if (memoryId) return memoryId;
  try {
    const saved = sessionStorage.getItem(KEY);
    if (/^[0-9a-f]{32}$/.test(saved || '')) return (memoryId = saved);
  } catch (_) { /* 저장소를 막은 브라우저도 이 문서 안에서는 같은 ID를 쓴다 */ }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  memoryId = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  try { sessionStorage.setItem(KEY, memoryId); } catch (_) { /* 메모리 ID로 동작 */ }
  return memoryId;
}
