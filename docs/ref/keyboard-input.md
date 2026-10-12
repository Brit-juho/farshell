# 키보드 입력

2026-10-12 구현. 상세 분석과 설계 이력은 [통합 설계](./keyboard-input-plan.md)를 참고한다.

## 처리 순서

`core/platform.js`가 OS의 primary modifier와 설정 저장 프로필을 제공한다. Mac은 Command, 그 외는 Control을 `Mod`로 해석한다. 명시적인 Meta는 비Mac에서도 Control과 구분한다.

`core/keyboard-router.js`는 document capture에서 이벤트를 한 번 결정하고 WeakMap에 결과를 저장한다. 녹화 → 최상위 scope → 편집창 → 앱 → 터미널 순으로 처리한다. IME/Dead/AltGraph는 앱 단축키로 처리하지 않는다. 분할·닫기 등 앱 액션의 key repeat는 소비하되 재실행하지 않는다.

`core/keymap.js`는 액션 정의·등록·바인딩 저장·표시를 담당한다. 알파벳/숫자 앱 조합은 `code`로 위치를 판단한다. 실제 입력 문자는 `key`로 유지하며 기호 조합은 논리 문자로 판단한다. 따라서 Option으로 `d`가 `∂`가 되거나 입력 배열이 바뀌어도 Cmd+Option+D 앱 조합은 유지된다. code가 없는 이벤트는 key로 호환 처리한다.

`term/keyboard-input.js`는 동일 이벤트의 저장된 결정을 사용한다. 앱 바인딩을 다시 매칭하지 않는다. 레거시·IME는 xterm에 위임하고 협상된 확장 키만 CSI u로 인코딩한다. kitty는 구현한 disambiguation 플래그 1만 광고한다. 기본키를 알 수 없는 비ASCII Option 조합은 추측하지 않고 xterm에 위임한다.

## 전달 대상과 설정

| 모드 | 동작 |
|---|---|
| 앱 동작 | 앱 액션 한 번, PTY 전달 차단 |
| 앱 + 터미널 | 앱 액션 한 번, 원래 터미널 타깃에 전달용 이벤트 한 번 |
| 터미널만 | 앱 액션 없이 원래 터미널에 전달 |

기존 passthrough=true는 앱+터미널로 유지된다. 전달용 이벤트는 별도 terminal 결정을 가지므로 앱 액션을 재실행하지 않는다. 원래 세션/연결이 변경되거나 타깃이 닫히면 다른 활성 세션에 보내지 않는다.

기존 global `keymap.overrides`는 읽기 호환용이다. 최초 편집 시 기존 값을 `keymap.bindingsV2`의 `{version:2, bindings:{...}}`로 복사해 device 스코프에 저장한다. 이후 reset도 V2에서 처리하므로 옛 global 값이 다시 살아나지 않는다. 원본 global은 다른 프로필과 롤백을 위해 남긴다. 저장 실패는 설정 스토어의 기존 정책에 따라 로컬 적용과 실패 안내로 구분한다.

기기 스코프는 개별 하드웨어가 아니라 mac/windows/linux/mobile 종류별이다. 기존 클라이언트가 수정한 V1 값은 V2 편집을 시작한 프로필에 자동 합쳐지지 않는다. 여러 탭에서 같은 바인딩 JSON을 동시에 수정할 때의 서버 revision 기반 병합은 구현하지 않았다. 한 설정 키에 대한 기존 settings 저장 계약을 따른다.

설정 → 키맵의 Mac Option 키는 `문자 입력`과 `터미널 Meta 키`를 선택한다. 기본은 기존 문자 입력이며, 선택은 생성된 터미널과 이미 열린 터미널의 `macOptionIsMeta`에 반영된다. PWA도 OS/브라우저 예약 키를 가로챌 수 있다고 보장하지 않는다.

## Scope와 가상 입력

`core/keyboard-scopes.js`는 push/dispose와 최상위 소유권, Tab trap, focus 복귀를 제공한다. 공용 panel/dialog, 세션 시트, Palette, 워크트리/저장소 dialog, 연결된 화면, 검색창, 키맵 녹화가 이를 사용한다. 일반 버튼의 Enter/Space와 위젯 내부 이벤트는 유지한다. 모달 뒤 터미널로 향하는 키는 차단한다.

키바 버튼은 동일 keyboard-input adapter로 전달하고, 공유 keyseq modifier 규칙을 사용한다. `Ctrl+C`, tmux prefix, Shift+Tab 고정 버튼에는 sticky를 추가하지 않는다. sticky는 인코딩 전 단일 ASCII 소프트 input에만 적용한다. 물리 keydown, IME 확정, 다문자, 붙여넣기, 프로토콜 응답에는 적용하거나 once 상태를 소비하지 않는다. 입력 출처를 판단할 수 없는 브라우저 이벤트에서는 원문을 보존한다.

`term/ws.js`는 onData 바이트를 변환하지 않는다. 터치 키바 DOM과 touch 제스처는 고정 파일명 `touch.js` 청크에서 로드한다. 상태/인코더는 core에 한 번만 존재한다. 터치 전용 코드가 데스크톱 초기 번들에 포함되지 않으며 두 Vite 빌드와 app.js/app.css/voice.js 고정 출력은 유지한다.

## 붙여넣기

텍스트는 기존 `sendPaste`로 보내고 서버가 bracketed paste를 결정한다. wrapper capture는 paste를 소비하여 xterm의 별도 paste 리스너가 중복 전송하지 못하게 한다. 빈 paste도 진행 중 예약을 취소한다.

native 조합은 먼저 브라우저 이벤트를 기다린다. Mac Cmd+V/Shift+Cmd+V와 Ctrl+Shift+V, 비Mac Ctrl+Shift+V는 커스텀 액션의 등록 여부와 독립적으로 동작한다. 비Mac Ctrl+V는 quoted-insert를 유지한다. 이벤트가 없으면 250ms 뒤 Clipboard API를 사용한다. native와 fallback 중 전달권은 하나만 갖는다. 읽기 중 native가 오면 fallback을 취소한다. 포커스/연결 변경, 모달·녹화, 세션 제거는 예약 전달을 막는다.

fallback 완료 후 1초 이내에 native 이벤트가 늦게 오면 재전송하지 않고 재실행 안내를 표시한다. 다음 붙여넣기 keydown은 새로운 transaction을 시작한다. Web paste 이벤트에는 공유 transaction id가 없으므로 이 구간의 별도 메뉴 붙여넣기도 보수적으로 차단될 수 있다. 1초 이후 매우 늦게 도착한 native 이벤트의 중복까지 보장하지 않는다. 내용 일치로 정상 반복 붙여넣기를 제거하지 않는다.

## 검증과 남은 범위

자동 검사는 물리 code 판별, 단일 실행, 녹화/최상위 scope, IME/AltGraph/repeat, V2 이관/reset, 전달 대상, CSI u, native/fallback/빈 paste와 키바를 검증한다. Chrome 격리 페이지에서는 실제 vendor xterm으로 Ctrl+C 바이트, 앱+터미널 CSI u 한 번, 네이티브 폴백과 지연 이벤트, 물리/소프트/응답의 sticky 분리를 확인했다.

합성 Option 이벤트와 Playwright 키는 macOS가 실제 발행하는 Option 문자나 OS 예약 단축키의 증명이 아니다. 실제 맥북 물리 키, Safari 및 tmux 안의 Codex/Claude 조합은 추가 실측 대상이다. Android 실기기 검증은 아래를 참고한다. tmux extended-keys 옵션은 변경하지 않았다. 프로토콜을 제품 이름으로 추정하지 않으며 현재 지원하지 않는 키는 xterm의 레거시 전달 한계를 따른다.

### Android ADB 실측 (2026-10-12)

Android device 계열(SM-XXXX), Android 14, Chrome, Samsung 키보드에서 확인했다. 앱 설정 화면은 실제 실행 인스턴스로 확인했고, 문자/수식키 검증은 별도 로컬 페이지와 격리된 raw PTY로 수행해 작업 중인 tmux 세션에 테스트 키를 보내지 않았다.

- 키맵 16개와 전달 대상 선택 15개가 렌더링됐고, 가로 넘침이 없었다. native paste 항목은 전달 대상 선택을 숨기는 기존 계약을 유지한다.
- 기존 키맵의 새 select가 공통 테마를 사용하지 않는 문제를 발견해 공통 `vt-select`/`vt-select-wrap`으로 수정했다. 가로 화면(832×331 CSS px)에서 24px로 줄어드는 터치 표적도 coarse pointer에서는 최소 44px로 보완했다.
- Samsung 키보드의 `key=Unidentified`, `keyCode=229`, `insertText`에서 sticky Ctrl+C가 `03`과 원문 `63`을 둘 다 보내는 회귀를 재현했다. xterm의 지연 textarea 검사와 커스텀 input이 각각 전송한 것이다.
- 변환할 입력의 beforeinput 값을 기록하고, 소비한 native textarea 변경을 원복해 지연 검사에서 같은 문자를 다시 읽지 않게 수정했다. 신뢰할 beforeinput 값이 없으면 원문을 보존하고 sticky를 소비하지 않는다.
- 수정 후 실제 ADB 터치로 Ctrl+C=`03` 한 번, Alt+C=`1b 63` 한 번, 일반 c=`63` 한 번을 확인했다. once 수식키는 해당 입력 후 off가 된다.
- 한글 `가 ` 조합은 원문 한 번으로 전달되고 armed Ctrl을 소비하지 않았다.
- 실제 ADB Escape로 재지정 녹화만 취소되고 설정 화면이 유지되는 것을 확인했다.
- Android KEYCODE_PASTE로 paste 경로 한 번, 일반 키 바이트 0회를 확인했다. 검증용 clipboard를 사용한 뒤 기존 ClipboardItem을 복원했다.

ADB는 Android 동작을 검증하며 macOS Command/Option 물리 키의 검증을 대체하지 않는다.
