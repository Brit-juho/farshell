// 마우스/선택 배선 — 드래그 자동복사·우클릭·이미지 붙여넣기·단축키·OSC52.
// F4에서 terminal.js(구 :482-565)에서 분리. addSession에서 term.open 직후 호출.
import { wireKeyboardInput } from './keyboard-input.js';
import { get as setting } from '../core/settings.js';
import { copyToClipboard, pasteFromClipboard, pasteImageUpload, sendPaste, claimNativePaste } from './clipboard.js';
import { getSession } from '../core/store.js';
import { _isCoarsePointer } from '../core/env.js';
import { t } from '../core/i18n.js';


export function wireClipboard(id, term, wrapper) {
  // 1) copy-on-select — 드래그(브라우저 선택) 끝나면 자동 복사.
  //    ⚠ tmux mouse on이면 일반 드래그는 tmux가 가로채므로, 브라우저 선택은
  //    Shift(또는 Option/Alt)+드래그에서 발생한다.
  //    "⋯ → 설정 → 드래그 시 자동 복사"로 끌 수 있다 — 꺼도 선택 자체는 그대로
  //    되고(브라우저 네이티브), 실제 복사만 우클릭으로 넘어간다.
  //
  //    2026-09-23(사용자 요청) — 터치 기기에서는 이 항목 자체를 무시한다.
  //    모바일은 이제 꾹 눌러 만든 선택(term/touch-select.js, xterm 자신의
  //    select() 모델)만 쓰고 그 복사는 keybar의 「복사」 버튼(명시적 탭)이
  //    맡는다. 손가락으로 아무거나 훑었을 뿐인데 자동으로 복사되는 동작은
  //    "필요 없으니 제거"가 요청 그대로였다 — 설정값과 무관하게 항상 끈다
  //    (데스크톱 마우스 드래그는 이 변경과 무관, 설정을 그대로 따른다).
  wrapper.addEventListener('mouseup', () => {
    if (_isCoarsePointer()) return;
    // S2: 같은 키를 세 파일이 각자 localStorage에서 읽고 있었다(여기 2곳 +
    // moreMenu.js + voice/notify.js). 이제 설정 스토어 하나만 본다.
    if (!setting('mouse.autocopyOnSelect')) return;
    const sel = term.getSelection && term.getSelection();
    if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast(t('복사됨')); });
  });

  // 2) 우클릭 — 선택 있으면 복사, 없으면 붙여넣기 (PuTTY 스타일)
  //
  //    2026-09-23(사용자 요청) — 터치 기기는 이 핸들러를 아예 안 탄다. Android
  //    Chrome은 롱프레스를 contextmenu로도 보내는데, 예전엔 "선택 모드가 아닐
  //    때"만 이 PuTTY 동작(선택 없으면 곧장 붙여넣기)을 태워서 — 막 시작한
  //    롱프레스가 선택 모드에 들어가기도 전에 클립보드 읽기 권한 팝업을 띄우고
  //    (그 요청 자체가) 브라우저의 제스처를 가로챘다. 이제 롱프레스는 전부
  //    term/touch-select.js가 맡으므로 여기서는 그 네이티브 컨텍스트 메뉴/
  //    콜아웃 자체를 막기만 하고 아무 일도 하지 않는다.
  wrapper.addEventListener('contextmenu', (e) => {
    if (_isCoarsePointer()) { e.preventDefault(); return; }
    e.preventDefault();
    const sel = term.getSelection && term.getSelection();
    if (sel && sel.trim()) copyToClipboard(sel).then((ok) => { if (ok) showToast(t('복사됨')); });
    else pasteFromClipboard(id);
  });

  // 3) 붙여넣기 — 이미지는 업로드+경로삽입, 텍스트는 N24 경로(sendPaste)로
  //    통일한다. capture로 textarea보다 먼저 검사. **xterm 기본 paste
  //    핸들링에는 더 이상 위임하지 않는다** — 위임하면 마커 판단이 다시
  //    브라우저(xterm.js의 bracketedPasteMode 추정)로 흩어진다.
  //    ⚠ 우리가 처리한 건 **stopPropagation까지** 해야 한다. xterm은 paste
  //    리스너를 textarea와 element 두 곳에 걸어두고, 그 핸들러
  //    (`handlePasteEvent`)는 `defaultPrevented`를 보지 않는다 — 즉
  //    preventDefault만으로는 xterm이 같은 텍스트를 onData로 한 번 더
  //    보내는 걸 못 막는다(2026-09-18: 붙여넣기가 2번 들어가던 원인).
  //    capture 단계에서 전파를 끊으면 target/bubble의 xterm 리스너가 아예
  //    돌지 않는다.
  wrapper.addEventListener('paste', (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    for (const it of items) {
      if (it.type && it.type.indexOf('image/') === 0) {
        e.preventDefault();
        e.stopPropagation();
        if (!claimNativePaste(id)) return;
        const file = it.getAsFile();
        if (file) pasteImageUpload(id, file);
        return;
      }
    }
    const text = e.clipboardData && e.clipboardData.getData('text/plain');
    if (text != null) {
      e.preventDefault();
      e.stopPropagation();
      if (claimNativePaste(id)) sendPaste(id, text);
    }
  }, true);

  // 4) 붙여넣기 단축키 — 기본 Ctrl+Shift+V는 브라우저 paste 이벤트를 우선한다.
  //    · Cmd+V(Mac)는 위 3)의 브라우저 네이티브 paste 이벤트로 처리된다(이미지 포함)
  //      — 여기서 안 건드린다.
  //    · 순수 Ctrl+V는 그대로 pty로 흘려보낸다 — bash readline(quoted-insert),
  //      vim(visual-block) 등 터미널 프로그램이 실제로 쓰는 키라 가로채면 깨진다.
  //    복사 단축키(예전 Cmd+C / Ctrl+Insert)와 붙여넣기 Shift+Insert는 제거했다:
  //    드래그하면 이미 자동 복사(copy-on-select)되고, 수동 복사/붙여넣기는 우클릭으로
  //    되므로 중복이라 오히려 혼란만 준다.
  //    Ctrl+Shift+V는 브라우저의 "서식 없이 붙여넣기"가 만든 paste 이벤트를
  //    우선한다. 이벤트가 없을 때만 지연 Clipboard API 폴백이 실행된다.
  // 키 액션과 전달 대상은 공통 router가 한 번 결정한다. xterm callback은
  // 저장된 결정만 사용하며 앱+터미널 조합도 협상된 encoder로 한 번 보낸다.
  // N12 — kitty 키보드 프로토콜. 앱이 모드를 켰을 때만 동작한다(협상 2층).
  wireKeyboardInput(id, term, wrapper);

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
        copyToClipboard(text).then((ok) => { if (ok) showToast(t('클립보드 동기화됨 (OSC52)')); });
      } catch (_) { /* 잘못된 base64 무시 */ }
      return true;
    });
  }
}
