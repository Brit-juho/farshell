// OSC 52(터미널 출력이 클립보드에 쓰기를 요청) 판정 — DOM·xterm 없이 도는 순수 로직.
// 배선(registerOscHandler)은 term/selection.js가 한다.
//
// 규칙(위에서 아래로):
//  1. 재생분(scrollback 다시 그리기) · 「드래그 시 자동 복사」 꺼짐 · 빈 payload/`?`(질의) · 잘못된 base64 → 무시
//  2. 디코드 결과가 1MB(1,048,576바이트)를 넘으면 → 무시
//  3. tmux 세션이 아니고 `terminal.osc52Untrusted`가 꺼져 있으면 → 차단(세션마다 첫 1회만 경고)
//  4. 그 외 → 복사 + 미리보기 토스트
// tmux 세션(원격 칸 포함)은 tmux가 `set-clipboard external`로 pane 안 프로그램의 요청을
// 이미 거르고 tmux 자체 복사(copy-mode 등)만 내보낸다. tmux 없는 일반 터미널은 출력 속
// 시퀀스가 그대로 오므로 신뢰할 수 없는 출력이 클립보드를 바꿀 수 있다(pastejacking).

export const OSC52_MAX_BYTES = 1048576;
// 1MB를 base64로 쓴 길이(패딩 포함). 이보다 길면 디코드 전에 버린다.
const MAX_B64_CHARS = Math.ceil(OSC52_MAX_BYTES / 3) * 4;
const PREVIEW_CHARS = 40;

export function osc52Preview(text) {
  const chars = Array.from(text);
  const head = chars.slice(0, PREVIEW_CHARS).join('')
    .replace(/\r\n|\r|\n/g, '⏎')
    .replace(/[\u0000-\u001f\u007f]/g, ' ');
  return { preview: head + (chars.length > PREVIEW_CHARS ? '…' : ''), length: chars.length };
}

export function osc52Decision({ payload, isTmux, allowUntrusted, autocopy, replaying }) {
  if (replaying) return { action: 'ignore', reason: 'replay' };
  if (!autocopy) return { action: 'ignore', reason: 'autocopy-off' };
  if (!payload || payload === '?') return { action: 'ignore', reason: 'empty' };
  if (payload.length > MAX_B64_CHARS) return { action: 'ignore', reason: 'too-large' };
  let bytes;
  try {
    const bin = atob(payload);
    bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  } catch (_) {
    return { action: 'ignore', reason: 'invalid-base64' };
  }
  if (bytes.length > OSC52_MAX_BYTES) return { action: 'ignore', reason: 'too-large' };
  if (!isTmux && !allowUntrusted) return { action: 'block', reason: 'untrusted' };
  const text = new TextDecoder('utf-8').decode(bytes);
  return { action: 'copy', text, reason: isTmux ? 'tmux' : 'allowed' };
}

// 핸들러 본체. deps: { session, setting, copy, toast, t }. 세션 객체에 `_osc52Warned`를 얹어
// 차단 경고를 세션마다 한 번만 띄운다. 토스트는 textContent 경로(showToast)로만 그린다.
export function handleOsc52(data, { session, setting, copy, toast, t }) {
  const semi = data.indexOf(';');
  const payload = semi >= 0 ? data.slice(semi + 1) : data;
  const d = osc52Decision({
    payload,
    isTmux: !!(session && (session.tmuxName || session.tmux_name)),
    allowUntrusted: !!setting('terminal.osc52Untrusted'),
    autocopy: !!setting('mouse.autocopyOnSelect'),
    replaying: !!(session && session._replayingScrollback),
  });
  if (d.action === 'block') {
    if (session && !session._osc52Warned) {
      session._osc52Warned = true;
      toast(t('이 터미널의 프로그램이 클립보드에 쓰려 해 막았습니다 — 설정 › 터미널에서 허용할 수 있습니다'), 'warn');
    }
  } else if (d.action === 'copy') {
    const { preview, length } = osc52Preview(d.text);
    Promise.resolve(copy(d.text)).then((ok) => {
      if (ok) toast(t('클립보드에 복사됨 (OSC52): "{preview}" · {n}자', { preview, n: length }));
    });
  }
  return d;
}
