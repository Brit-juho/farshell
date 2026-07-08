# VT 디자인 시스템

## 개요 — 3-테마 · 상단 통합 레이아웃

VT 프론트엔드는 **선택 가능한 3개 테마**를 가진 모바일 우선 웹 터미널이다.
테마는 `<html data-skin="...">` 속성으로 전환되며, **모든 색은 CSS 변수(토큰)로만**
참조한다. 하드코딩 hex 금지 — JS가 동적 생성하는 오버레이도 `.vt-*` 클래스를 통해
토큰을 상속한다.

| 스킨 | 정체성 | 기본값 |
|------|--------|--------|
| `macos` | iTerm2/Terminal.app — 신호등, SF 폰트, 시스템 블루, 둥근 창 | ✅ 기본 |
| `catppuccin` | 기존 파스텔 — 크롬 없음, 라벤더 강조 | |
| `windows` | Windows Terminal — 캡션 버튼, Cascadia, Fluent 블루, 각진 창 | |

전환: 상단 `⋯` 메뉴 → 테마 칩. `localStorage['vt-skin']`에 저장, 재방문 시 복원.
부팅 시 `<head>` 인라인 스크립트가 페인트 전에 `data-skin`을 확정(FOUC 방지).

## 레이아웃 (2026 리뉴얼)

과거의 3-존(상단 탭 바 · 터미널 · 하단 보이스 바)에서 **상단 통합 바 + 플로팅
음성 FAB**로 전환. 하단 바 제거로 터미널 세로 공간을 회복했다.

```
┌───────────────────────────────────────────────┐
│ ◉◉◉  [tab][tab][+]        🔍  ⊞  ⋯   (⚊▢✕)     │ ← #topbar (48px)
├───────────────────────────────────────────────┤
│                                                 │
│                 #terminal-container             │
│                 (xterm, 테마별 ANSI)             │
│                                    ┌──────┐     │
│                                    │ 상태 │     │ ← #mic-status
│                                    └──────┘     │
│                                      ( 🎤 )     │ ← #mic-btn-wrap (FAB)
└───────────────────────────────────────────────┘
```

- **`#topbar`**: 신호등(macOS만) · 탭(`#tabs`, `#add-btn` 앞에 삽입) · 세션 점프
  드롭다운(`#voice-session-picker`, 좁은 화면에서만) · `🔍 검색` · `⊞ Grid`(`#grid-toggle`)
  · `⋯ 더보기`(`#more-btn`) · 캡션 버튼(windows만).
- **`#more-menu`**: tmux 세션 · 맥에서도 열기(체크박스) · 음성 전용 · 이어폰 미디어키
  · 파일 업로드 · 테마 칩 3종.
- **`#voice-bar`**: 우하단 플로팅 클러스터. `#mic-status`(상태 pill) + `#mic-btn-wrap`(FAB).
  grid.js가 `/api/capabilities`로 음성 미설치 감지 시 `#voice-bar`와 `.needs-voice` 숨김.

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

`addSession()`이 생성 시 `getVtXtermTheme()`을 적용하고, 테마 전환 시
`setVtSkin()`이 열려 있는 모든 터미널의 `term.options.theme`를 즉시 갱신한다.

## 컴포넌트

### 음성 FAB (`#mic-btn-wrap`)
- 우하단 62px 원형(엄지 도달), `--acc` 배경. 음성 전용 모드에서 132px.
- voice.js 계약: `.label` 자식 텍스트 + `.recording` 클래스(녹음 시 `--err` + pulse).
- 상태는 `#mic-status`가 텍스트로 표시: "녹음 중 — 탭하여 중지" · "처리 중..."
  · "마이크 권한 필요" · `"<인식된 텍스트>"` · "인식 실패" · "전송 실패".

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

- 모바일 우선. 좁은 화면(<720px)에서 세션 점프 드롭다운 노출, 넓은 화면은 탭으로 충분.
- 터치 타깃: FAB 62px, 상단 아이콘 36px(coarse 포인터에서 min 44px), 탭 34px.
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
| `frontend/js/picker.js` | 세션 드롭다운 동기화, 토스트, 파일 업로드 |
| `frontend/js/grid.js` | 라이브 프리뷰 Grid, capability 게이팅, 안전 모드 배너 |
| `frontend/voice.js` | 녹음/STT/TTS, 미디어키, 음성 전용 모드 (capability ON일 때 동적 로드) |
