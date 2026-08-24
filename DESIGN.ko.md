# FarShell 디자인 시스템

## 개요 — 5-테마 · 상단 통합 레이아웃

FarShell 프론트엔드는 **선택 가능한 5개 테마**를 가진 모바일 우선 웹 터미널이다.
테마는 `<html data-skin="...">` 속성으로 전환되며, **모든 색은 CSS 변수(토큰)로만**
참조한다. 하드코딩 hex 금지 — JS가 동적 생성하는 오버레이도 `.vt-*` 클래스를 통해
토큰을 상속한다.

| 스킨 | 정체성 | 기본값 |
|------|--------|--------|
| `macos` | iTerm2/Terminal.app — 신호등, SF 폰트, 시스템 블루, 둥근 창 | ✅ 기본 |
| `catppuccin` | 기존 파스텔 — 크롬 없음, 라벤더 강조 | |
| `windows` | Windows Terminal — 캡션 버튼, Cascadia, Fluent 블루, 각진 창 | |
| `vscode` | VS Code 통합 터미널 — 어두운 그레이, Fluent 블루 강조, 각진 UI | |
| `notepad` | 메모장/종이 느낌 — 유일한 **라이트 테마**, 따뜻한 오프화이트 배경 | |

전환: 상단 `⋯` 메뉴 → 테마 칩(`#theme-row`, 5개). `localStorage['vt-skin']`에 저장,
재방문 시 복원. 부팅 시 `<head>` 인라인 스크립트가 페인트 전에 `data-skin`을
확정(FOUC 방지).

## 레이아웃 (2026 리뉴얼)

과거의 3-존(상단 탭 바 · 터미널 · 하단 보이스 바)에서 **상단 통합 바**로 전환. 하단 바
제거로 터미널 세로 공간을 회복했다. 음성 입력은 더 이상 별도 플로팅 FAB가 아니라
**상단 바에 인라인 통합된 pill 버튼**이다 — `#voice-bar` 컨테이너는 HTML에서
제거됐고, `#mic-btn-wrap`은 `#topbar` 안의 다른 아이콘 버튼들과 같은 줄에 산다.

```
┌───────────────────────────────────────────────┐
│ ◉◉◉  [tab][tab][+]   ( 🎤 음성입력 ) 🔍 ⊞ ⋯ (⚊▢✕) │ ← #topbar (fine 38px / coarse 44px)
├───────────────────────────────────────────────┤
│                                                 │
│                 #terminal-container             │
│                 (xterm, 테마별 ANSI)             │
│                                     ┌──────┐    │
│                                     │ 상태 │    │ ← #mic-status (top-right, 상단 바 아래 뜨는 pill)
│                                     └──────┘    │
└───────────────────────────────────────────────┘
```

- **`#topbar`**: 신호등(macOS만) · 탭(`#tabs`, `#add-btn` 앞에 삽입) · 세션 점프
  드롭다운(`#voice-session-picker`, 좁은 화면에서만) · 음성 입력(`#mic-btn-wrap`, 인라인
  pill) · `🔍 검색` · `⊞ Grid`(`#grid-toggle`) · 코드 뷰어(`#viewer-toggle`)
  · `⋯ 더보기`(`#more-btn`) · 캡션 버튼(windows만).
- **`#more-menu`**: tmux 세션 · 맥에서도 열기(체크박스) · 음성 전용 · 이어폰 미디어키
  · 파일 업로드 · 코드 뷰어 · 프롬프트 큐 · 포트 · 가이드 · 푸시 알림 · 드래그 자동복사
  · 테마 칩 5종.
- **`#mic-status`**: 상단 바 아래 우측에 고정된 상태 pill(내용 없으면 자동 숨김).
  음성 전용 모드에서는 `#mic-btn-wrap`이 확대(svg 50px)되어 전체 화면 마이크로 전환.
  grid.js가 `/api/capabilities`로 음성 미설치 감지 시 `.needs-voice` 요소 전체를 숨김.

## 토큰 (CSS 변수)

`css/app.css`의 `html[data-skin="..."]` 블록에 정의. 스킨마다 재정의된다.

| 토큰 | 용도 |
|------|------|
| `--win` | 앱 배경 |
| `--bar` | 상단 바 배경 (모바일 theme-color 메타도 이 값) |
| `--tab` / `--tab-active` / `--tab-active-txt` | 탭 |
| `--term` | 터미널 배경 (xterm background와 일치) |
| `--txt` / `--sub` | 본문 / 보조 텍스트 |
| `--acc` / `--acc-ink` | 강조색 / 강조 위 텍스트 (FAB, active, 링크) |
| `--menu` / `--menu-hover` / `--line` | 메뉴·구분선 |
| `--ok` / `--warn` / `--err` / `--info` | 시맨틱 (연결끊김, 에러, 성공) |
| `--crust` | Grid 카드 프리뷰 배경 |
| `--wrad` / `--trad` | 창 / 탭 반경 |
| `--ui` / `--mono` | UI / 모노스페이스 폰트 스택 |

### 타이포그래피 — OS 네이티브 (의도적)

각 스킨은 해당 OS의 시스템 폰트를 쓴다. `system-ui`가 여기선 "타이포 포기 신호"가
아니라 **iTerm2/Windows Terminal을 흉내내는 authentic한 선택**이다.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (터미널)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (터미널)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (터미널)

## xterm.js 터미널 테마

"iTerm2 느낌 vs 윈도우 느낌"의 핵심은 창 크롬이 아니라 **터미널 자체의 배경 +
ANSI 16색**이다. `js/theme.js`의 `VT_XTERM_THEMES`에 스킨별 완전한 팔레트를 정의:
`background/foreground/cursor/selection + black..white + brightBlack..brightWhite`.

- macos: 딥 블랙(#101012) + macOS 시스템 컬러(빨강 #ff453a, 초록 #32d74b, 파랑 #0a84ff …)
- catppuccin: #1e1e2e + Catppuccin Mocha 팔레트
- windows: **공식 Campbell 팔레트** (#0c0c0c, 빨강 #c50f1f, 파랑 #0037da …)
- vscode: #1e1e1e + VS Code 통합 터미널 기본 팔레트
- notepad: 유일한 라이트 배경(#fffefb) + 파랑 커서(#0060df) — 밝은 배경에 맞춘 별도 팔레트

`addSession()`이 생성 시 `getVtXtermTheme()`을 적용하고, 테마 전환 시
`setVtSkin()`이 열려 있는 모든 터미널의 `term.options.theme`를 즉시 갱신한다.

## 컴포넌트

### 음성 버튼 (`#mic-btn-wrap`)
- 상단 바 인라인 pill(`.tbtn.mic`), `--acc` 배경 + 라벨 텍스트. 440px 이하에서는
  라벨을 숨기고 32px 정사각 아이콘 버튼으로 축소. 음성 전용 모드에서는 화면 중앙
  대형 버튼(svg 50px)으로 확대 — 더 이상 우하단 고정 FAB가 아니다.
- voice.js 계약: `.label` 자식 텍스트 + `.recording` 클래스(녹음 시 `--err` + pulse).
- 상태는 `#mic-status`(상단 바 아래 우측 고정 pill)가 텍스트로 표시: "녹음 중 — 탭하여
  중지" · "처리 중..." · "마이크 권한 필요" · `"<인식된 텍스트>"` · "인식 실패" · "전송 실패".

### ⋯ 메뉴 (`#more-menu`) / 팝업
- `--menu` 배경, `.mi` 항목(hover `--menu-hover`), `.msep` 구분선, `.mlabel` 섹션 헤더.
- 액션 클릭 시 자동 닫힘. 토글(체크박스/음성 전용/이어폰)은 열린 채 유지.

### JS 동적 오버레이 (`.vt-*` 클래스, 토큰 상속)
- `.vt-onboarding` — 세션 0개 empty state(주 액션: tmux 세션 / 일반 터미널).
- `.vt-overlay` — 서버 연결 끊김 전체 화면 + `#conn-status` pill.
- `.vt-menu` / `.vt-menu-item` — tmux 세션 드롭다운.
- `.vt-toast` (`.ok`/`.err`/`.info`) — 알림·업로드·에이전트 토스트.
- `.vt-card` / `.card-title` / `.card-cmd` / `.card-preview` / `.vt-grid-empty` — Grid 뷰.
- `.vt-banner` — 안전 모드 배너.

## 반응형 & 접근성

- 모바일 우선. 좁은 화면(<720px)에서는 현재 세션 이름을 표시하는 세션 관리 버튼이
  바텀시트를 열어 전환·이름 변경·개별 닫기를 제공한다. 탭 영역이 0px로 눌려도 이 기능은 남는다.
- 터치 타깃: coarse 포인터에서는 `--topbar-h`가 44px가 되고 `.tab`과 닫기 버튼도
  실제 44px 높이를 사용한다. 아이콘 버튼은 시각 크기 30px을 유지하며 `::before`로
  44px 탭 가능 영역을 확보한다.
- 키보드: `#add-btn` Enter/Space, `⋯` `aria-haspopup`/`aria-expanded`, 검색 Ctrl/Cmd+F,
  Grid/검색 Esc 닫기, `:focus-visible` 아웃라인(`--acc`).
- 스크린리더: 아이콘 버튼 `aria-label`, `#mic-status` `role="status" aria-live="polite"`.
- `prefers-reduced-motion`: 모든 애니메이션/트랜지션 비활성.
- safe-area-inset: 상하 패딩 적용(노치/제스처 바).

## 파일 맵

| 파일 | 책임 |
|------|------|
| `frontend/index.html` | 레이아웃 마크업, 부팅 테마 스크립트, `⋯` 메뉴 토글 |
| `frontend/css/app.css` | 토큰 3종 + 전 컴포넌트 + `.vt-*` 오버레이 |
| `frontend/js/theme.js` | 스킨 전환, localStorage, xterm 테마 정의/동기화 |
| `frontend/js/terminal.js` | 세션/탭/PTY, `getVtXtermTheme()` 적용, 오버레이 생성 |
| `frontend/js/picker.js` | 모바일 세션 관리 시트, 토스트, 파일 업로드 |
| `frontend/js/grid.js` | 라이브 프리뷰 Grid, capability 게이팅, 안전 모드 배너 |
| `frontend/voice.js` | 녹음/STT/TTS, 미디어키, 음성 전용 모드 (capability ON일 때 동적 로드) |
