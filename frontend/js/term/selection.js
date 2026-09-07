// 마우스/선택 배선 — 드래그 자동복사·우클릭·이미지 붙여넣기·단축키·OSC52.
// F4에서 terminal.js(구 :482-565)에서 분리. addSession에서 term.open 직후 호출.
import { match as matchKey } from '../core/keymap.js';
import { get as setting } from '../core/settings.js';
import { copyToClipboard, pasteFromClipboard, pasteImageUpload } from './clipboard.js';
import { getSession } from '../core/store.js';

export function wireClipboard(id, term, wrapper) {
  // 1) copy-on-select — 드래그(브라우저 선택) 끝나면 자동 복사.
  //    ⚠ tmux mouse on이면 일반 드래그는 tmux가 가로채므로, 브라우저 선택은
  //    Shift(또는 Option/Alt)+드래그에서 발생한다.
  //    "⋯ → 설정 → 드래그 시 자동 복사"로 끌 수 있다 — 꺼도 선택 자체는 그대로
  //    되고(브라우저 네이티브), 실제 복사만 우클릭으로 넘어간다.
  wrapper.addEventListener('mouseup', () => {
    // S2: 같은 키를 세 파일이 각자 localStorage에서 읽고 있었다(여기 2곳 +
    // moreMenu.js + voice/notify.js). 이제 설정 스토어 하나만 본다.
    if (!setting('mouse.autocopyOnSelect')) return;
    const sel = term.getSelection && term.getSelection();
    if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast('복사됨'); });
  });

  // 2) 우클릭 — 선택 있으면 복사, 없으면 붙여넣기 (PuTTY 스타일)
  wrapper.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const sel = term.getSelection && term.getSelection();
    if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast('복사됨'); });
    else pasteFromClipboard(id);
  });

  // 3) 이미지 붙여넣기 — clipboard에 이미지가 있으면 업로드+경로삽입, 아니면 텍스트는
  //    xterm 기본 붙여넣기에 위임(preventDefault 안 함). capture로 textarea보다 먼저 검사.
  wrapper.addEventListener('paste', (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.type && it.type.indexOf('image/') === 0) {
        e.preventDefault();
        const file = it.getAsFile();
        if (file) pasteImageUpload(id, file);
        return;
      }
    }
  }, true);

  // 4) 붙여넣기 단축키 — Ctrl+Shift+V 하나로 통일 (크로스플랫폼 안전 경로).
  //    · Cmd+V(Mac)는 위 3)의 브라우저 네이티브 paste 이벤트로 처리된다(이미지 포함)
  //      — 여기서 안 건드린다.
  //    · 순수 Ctrl+V는 그대로 pty로 흘려보낸다 — bash readline(quoted-insert),
  //      vim(visual-block) 등 터미널 프로그램이 실제로 쓰는 키라 가로채면 깨진다.
  //    복사 단축키(예전 Cmd+C / Ctrl+Insert)와 붙여넣기 Shift+Insert는 제거했다:
  //    드래그하면 이미 자동 복사(copy-on-select)되고, 수동 복사/붙여넣기는 우클릭으로
  //    되므로 중복이라 오히려 혼란만 준다.
  //    ⚠ Ctrl+Shift+V는 Chrome/Firefox 등에서 "서식 없이 붙여넣기" 네이티브 단축키와
  //    겹친다 — preventDefault()로 먼저 막지 않으면 이중 붙여넣기가 된다.
  // S3: 하드코딩된 두 조합을 키맵 레지스트리로 옮겼다. 여기는 xterm의
  // attachCustomKeyEventHandler라 성격이 다르다 — xterm이 키를 PTY로 보내기
  // **전에** 판정해야 하므로, 레지스트리의 match()로 "이 키가 어떤 액션인가"만
  // 물어보고 실행은 이 자리에서 한다(document 리스너로 옮기면 xterm이 먼저
  // 먹어서 PTY에 이스케이프가 흘러간다).
  // passthrough가 켜져 있으면 true를 반환해 터미널로도 흘린다.
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    const hit = matchKey(e);
    if (!hit) return true;
    if (hit.id === 'paste') {
      if (!hit.passthrough) e.preventDefault();
      pasteFromClipboard(id);
      return hit.passthrough;
    }
    // 코드 뷰어 토글. Ctrl+B는 tmux prefix라 절대 쓰지 않는다(viewer.js 참고).
    if (hit.id === 'viewer') {
      if (!hit.passthrough) e.preventDefault();
      showViewer();
      return hit.passthrough;
    }
    return true;
  });

  // 5) OSC 52 — 서버 쪽 프로그램(vim, tmux copy-mode 등)이 클립보드에 쓰려는
  //    요청은 PTY 출력 스트림에 이스케이프 시퀀스로 실려 이미 여기까지 온다.
  //    별도 연결 없이 가로채서 이 브라우저(=이 기기)의 시스템 클립보드에 반영.
  //    "?"(쿼리) 응답은 미지원 — set 요청만 처리.
  if (term.parser && typeof term.parser.registerOscHandler === 'function') {
    term.parser.registerOscHandler(52, (data) => {
      const semi = data.indexOf(';');
      const payload = semi >= 0 ? data.slice(semi + 1) : data;
      if (!payload || payload === '?') return true;
      // ws.js가 scrollback 재생 구간 동안 켜두는 플래그 — 재접속마다 세션 도중
      // 쌓인 과거 OSC52들이 scrollback과 함께 통째로 재생되며 매번 다시 복사+토스트를
      // 띄우던 버그(새로고침 시 "클립보드 동기화됨" 우르르 뜸)를 막는다. 재생분은
      // 이미 과거에 한 번 처리된 요청이므로 조용히 무시한다.
      if (getSession(id)?._replayingScrollback) return true;
      // tmux가 `set-clipboard external`이면 일반 드래그도 자체 copy-mode를 거쳐
      // 여기로 OSC52를 쏜다(키보드로 하는 vim/tmux copy-mode 복사도 동일 경로라
      // 드래그만 따로 구분할 수 없음) — "드래그 시 자동 복사" 토글을 꺼도 이 경로가
      // 살아있으면 사용자 입장에선 "꺼도 계속 복사된다"로 보이므로 같은 설정을 공유한다.
      if (!setting('mouse.autocopyOnSelect')) return true;
      try {
        const bin = atob(payload);
        const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
        const text = new TextDecoder('utf-8').decode(bytes);
        copyToClipboard(text).then((ok) => { if (ok) showToast('클립보드 동기화됨 (OSC52)'); });
      } catch (_) { /* 잘못된 base64 무시 */ }
      return true;
    });
  }
}
