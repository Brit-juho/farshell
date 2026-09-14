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

async function readClipboardText() {
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      return await navigator.clipboard.readText();
    }
  } catch (_) { /* 권한/비보안 컨텍스트 */ }
  return null;
}

// 텍스트를 활성 세션 PTY로 **키 입력**으로 주입한다(붙여넣기가 아니라). 실제
// 키 시퀀스(방향키·tmux prefix 등 — term/keybar.js) 전용. 외부에서도 bare
// identifier로 호출하므로 window 브리지 필요.
export function sendToPty(id, text) {
  if (!text) return;
  const handle = getSession(id)?.wsHandle;
  if (handle && handle.readyState === WebSocket.OPEN) {
    handle.send(new TextEncoder().encode(text));
  }
}

// N24(2.1.5 2/n) — 붙여넣기 전용 경로. 마커를 붙일지·개행을 어떻게 바꿀지는
// 더 이상 브라우저가 정하지 않는다(예전엔 `term.paste()`가 xterm 자신의
// bracketedPasteMode 추정을 따랐다) — 서버가 `input_mode.py`의 실측으로
// 판단한다(`server/paste_prepare.py`). 클립보드 붙여넣기·네이티브 paste
// 이벤트·스니펫 붙여넣기 모드·파일 경로 삽입이 전부 이 하나로 모인다 —
// 두 경로가 공존하면 같은 버그가 한쪽에만 남는다는 게 N24의 원래 동기다.
//
// WS가 아직 안 열렸거나 끊긴 사이(재연결 중)에는 HTTP 대체 경로로 떨어진다
// — sendToPty(위)는 그런 경우 조용히 유실됐지만, 붙여넣기는 사용자가 직접
// 한 행동이라 유실보다는 조금 늦게라도 도착하는 편이 낫다.
export async function sendPaste(id, text) {
  if (!text) return;
  const s = getSession(id);
  const ws = s && s.ws;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'paste', text }));
    return;
  }
  try {
    await apiFetch(`${API_BASE}/api/sessions/${encodeURIComponent(id)}/paste`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  } catch (_) { /* 조용히 무시 — 세션이 아예 없으면 다음 키 입력 때 사용자가 알아챈다 */ }
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
  sendPaste(id, text);
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
      // N24 — 경로 삽입도 붙여넣기 경로로: 사용자가 이어서 명령을 완성하고
      // 직접 Enter를 눌러야 하므로(자동 실행 아님) 의미상 paste지 keys가 아니다.
      sendPaste(id, data.path + ' ');
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
window.sendPaste = sendPaste;
