import { hasBlockingScope } from '../core/keyboard-scopes.js';
// 클립보드: 복사(선택 자동복사/우클릭/단축키) · 붙여넣기 · 이미지 붙여넣기 업로드.
// F4에서 terminal.js(구 :172-263)에서 분리.
import { getSession } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { uploadFiles } from './upload.js';

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

export async function pasteFromClipboard(id, shouldSend = () => true) {
  // 이미지 우선 — 우클릭 및 네이티브 조합이 아닌 사용자 키맵은 paste 이벤트를
  // 안 거치므로 여기서 async Clipboard API로
  // 이미지를 직접 읽어 업로드한다. read()는 HTTPS/localhost(보안 컨텍스트)에서만
  // 되므로 실패하면 조용히 텍스트 붙여넣기로 폴백한다.
  try {
    if (navigator.clipboard && navigator.clipboard.read) {
      const items = await navigator.clipboard.read();
      for (const it of items) {
        const imgType = it.types.find((t) => t.indexOf('image/') === 0);
        if (imgType) {
          const blob = await it.getType(imgType);
          if (shouldSend()) pasteImageUpload(id, new File([blob], 'pasted', { type: imgType }));
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
  if (!shouldSend()) return;
  sendPaste(id, text);
}

// 일부 브라우저/xterm 조합은 Ctrl+Shift+V keydown 뒤에 paste 이벤트를 내지
// 않는다. 짧게 기다렸다가 이벤트가 없을 때만 Clipboard API로 읽는다.
// paste 이벤트가 늦게 와도 아직 진행 중인 비동기 읽기는 취소할 수 있다.
// native 이벤트에는 keydown id가 없다. 한 번의 전달권을 coordinator가 관리한다.
let nativeTransaction = null;
let pasteSequence = 0;
const eventTransactions = new WeakMap();
export function focusedTerminalSessionId() {
  const wrap = document.activeElement?.closest?.('[id^="term-"]');
  return wrap ? wrap.id.slice('term-'.length) : null;
}
export function cancelPasteTransactions(id) {
  const tx = nativeTransaction;
  if (!tx || (id && tx.sessionId !== id)) return;
  tx.state = 'cancelled'; clearTimeout(tx.timer); nativeTransaction = null;
}
export function queueNativePasteFallback(id, event) {
  if (event && eventTransactions.has(event)) return;
  cancelPasteTransactions();
  const tx = { id: ++pasteSequence, sessionId: id, handle: getSession(id)?.wsHandle,
    state: 'waiting-native', deliveredAt: 0, timer: null };
  nativeTransaction = tx;
  if (event) eventTransactions.set(event, tx);
  tx.timer = setTimeout(async () => {
    if (nativeTransaction !== tx || tx.state !== 'waiting-native') return;
    tx.state = 'reading-fallback';
    const claim = () => {
      if (nativeTransaction !== tx || tx.state !== 'reading-fallback') return false;
      if (hasBlockingScope() || !getSession(id) || getSession(id)?.wsHandle !== tx.handle || focusedTerminalSessionId() !== id) {
        cancelPasteTransactions(id); return false;
      }
      tx.state = 'delivered'; tx.deliveredAt = Date.now(); return true;
    };
    try { await pasteFromClipboard(id, claim); }
    catch (_) { tx.state = 'failed'; }
  }, 250);
}
// false면 이미 fallback이 전달한 직후의 모호한 native 이벤트. 중복 전송하지 않는다.
// 다음 keydown은 새 tx를 만들므로 빠른 연속 키 붙여넣기를 내용 비교로 제거하지 않는다.
export function claimNativePaste(id) {
  const tx = nativeTransaction;
  if (tx?.state === 'delivered' && Date.now() - tx.deliveredAt < 1000) {
    cancelPasteTransactions();
    window.showToast?.('붙여넣기가 이미 전달되었습니다. 추가로 붙이려면 다시 실행하세요.');
    return false;
  }
  cancelPasteTransactions();
  return true;
}
// 기존 외부 호출 호환. native 내용 처리에는 claimNativePaste를 사용한다.
export function cancelNativePasteFallback(id) { cancelPasteTransactions(); }

// 이미지 붙여넣기 → 서버 업로드 → 저장 경로를 터미널에 삽입 (Claude에 그대로 넘길 수 있게)
//
// 2026-09-23 — 예전엔 여기서 `apiFetch`로 직접 올렸다. `term/upload.js` 상단
// 계약("업로드 구현은 여기 하나뿐")을 이 함수만 어기고 있었고, 그래서
// 첨부 탭의 진행 표시·취소가 이미지 붙여넣기에는 닿지 않았다. `uploadFiles`로
// 합쳐서 나머지 세 진입점과 같은 진행/취소/완료 경로를 타게 한다.
export async function pasteImageUpload(id, file) {
  const ext = ((file.type.split('/')[1] || 'png')).replace('jpeg', 'jpg').replace('svg+xml', 'svg');
  await uploadFiles([file], { sessionId: id, insertPath: true, name: `pasted-${Date.now()}.${ext}` });
}

window.copyToClipboard = copyToClipboard;
window.sendToPty = sendToPty;
window.sendPaste = sendPaste;
