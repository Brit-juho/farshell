// 클립보드: 복사(선택 자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 업로드.
// F4에서 terminal.js(구 :172-263)에서 분리.
import { getSession } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';

// 시스템 클립보드에 쓰기. HTTPS/localhost가 아니면 clipboard API가 막히므로
// execCommand('copy') 폴백을 둔다.
export async function copyToClipboard(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* 폴백으로 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (_) { return false; }
}

export async function readClipboardText() {
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      return await navigator.clipboard.readText();
    }
  } catch (_) { /* 권한/비보안 컨텍스트 */ }
  return null;
}

// 텍스트를 활성 세션 PTY로 주입 (붙여넣기 공통 경로). 외부(snippets.js/
// viewer.js/picker.js)에서도 bare identifier로 호출하므로 window 브리지 필요.
export function sendToPty(id, text) {
  if (!text) return;
  const handle = getSession(id)?.wsHandle;
  if (handle && handle.readyState === WebSocket.OPEN) {
    handle.send(new TextEncoder().encode(text));
  }
}

export async function pasteFromClipboard(id) {
  // 이미지 우선 — Ctrl+Shift+V / 우클릭 붙여넣기는 네이티브 paste 이벤트를
  // 안 거치므로(그쪽은 Cmd+V/Ctrl+V 전용), 여기서 async Clipboard API로
  // 이미지를 직접 읽어 업로드한다. read()는 HTTPS/localhost(보안 컨텍스트)에서만
  // 되므로 실패하면 조용히 텍스트 붙여넣기로 폴백한다.
  try {
    if (navigator.clipboard && navigator.clipboard.read) {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const imgType = it.types.find((t) => t.indexOf('image/') === 0);
        if (imgType) {
          const blob = await it.getType(imgType);
          pasteImageUpload(id, new File([blob], 'pasted', { type: imgType }));
          return;
        }
      }
    }
  } catch (_) { /* 권한/비보안 컨텍스트 — 텍스트 폴백 */ }
  const text = await readClipboardText();
  if (text == null) {
    showToast('클립보드 읽기 불가 — HTTPS/localhost에서만 가능. Cmd/Ctrl+V를 쓰세요.');
    return;
  }
  // term.paste()는 앱이 bracketed paste 모드면 마커로 감싼다 — 멀티라인 붙여넣기가
  // 셸에서 줄마다 즉시 실행되는 것을 막는다. (raw sendToPty는 그 보호가 없음)
  const term = getSession(id)?.term;
  if (term && typeof term.paste === 'function') term.paste(text);
  else sendToPty(id, text);
}

// 이미지 붙여넣기 → 서버 업로드 → 저장 경로를 터미널에 삽입 (Claude에 그대로 넘길 수 있게)
export async function pasteImageUpload(id, file) {
  try {
    showToast('이미지 업로드 중...');
    const ext = ((file.type.split('/')[1] || 'png')).replace('jpeg', 'jpg').replace('svg+xml', 'svg');
    const fd = new FormData();
    fd.append('file', file, `pasted-${Date.now()}.${ext}`);
    const res = await apiFetch(`${API_BASE}/api/upload?session_id=${encodeURIComponent(id)}`, {
      method: 'POST', body: fd,
    });
    if (!res.ok) { showToast(`이미지 업로드 실패 (${res.status})`); return; }
    const data = await res.json();
    if (data && data.path) {
      sendToPty(id, data.path + ' ');
      showToast('이미지 경로 삽입됨');
    } else {
      showToast('업로드 응답에 경로 없음');
    }
  } catch (_) {
    showToast('이미지 업로드 오류');
  }
}

window.copyToClipboard = copyToClipboard;
window.sendToPty = sendToPty;
