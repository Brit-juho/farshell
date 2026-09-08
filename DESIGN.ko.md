# FarShell 디자인 시스템

[![English](https://img.shields.io/badge/lang-English-lightgrey.svg)](./DESIGN.md)

> **v2.0.0 기준.** 2026년 9월 프런트엔드 재구조화로 레이아웃·토큰·진입 경로가 전부
> 바뀌었다 — 모듈 지도는 [ARCHITECTURE.ko.md](./ARCHITECTURE.ko.md) §2 참고.

---

## 0. 디자인 원칙

이 프로젝트가 실제로 지키는 규칙이다. 새 화면을 그리기 전에 여기부터 읽는다.

### 절제가 곧 취향이다

그라디언트·애니메이션·유리질감·장식 아이콘은 **넣을 수 있다는 이유만으로 넣지
않는다.** 모든 시각 요소는 자기 자리를 정당화해야 하고, 기본값은 언제나 단순함이다.

### 색 — 70/20/10

중립 배경 4단(`--bg-0..3`)이 70%, 텍스트 3단이 20%, 액센트는 10%다.

- **한 화면의 solid 액센트는 주 액션 1개**를 원칙으로 한다. rail 활성 항목·배지·
  기본 버튼이 동시에 액센트를 solid로 채우면 셋 다 눈에 안 띈다.
  → rail 활성 항목은 배경 단차(elevation)로 말하고 액센트는 아이콘 색으로만 남긴다.
- 요소마다 자기 배경색을 주지 않는다. 여백과 레이아웃이 먼저 분리한다.
- 배경 단차로 이미 나뉘는 곳에 테두리를 덧대지 않는다.
- **검사법:** 화면을 실눈으로 봤을 때 한 덩어리 색으로 뭉치면 실패다.

### 아이콘

- 아이콘을 **색 채운 라운드사각형/원 안에 가두지 않는다.** 아바타와 앱 아이콘만 예외.
- 그 아이콘이 사용자에게 필요한 정보를 전하지 않으면 지운다.
- 아이콘은 액션(버튼·네비)에서 일하고, 정보 카드의 장식으로 쓰지 않는다.
- 라이브러리를 섞지 않는다 — `frontend/js/ui/icons.js` 인라인 SVG 레지스트리 하나만 쓴다.
- **UI 자산으로 이모지를 쓰지 않는다.** 사용자 생성 콘텐츠(터미널 출력 등)는 예외.

### 타이포그래피

- 기본은 산세리프(`--font-ui`). 세리프를 히어로 제목의 "우아한 기본값"으로 쓰지 않는다.
- 위계는 크기·굵기·색으로 만든다. 컨테이너를 겹쳐 만들지 않는다.

### 유리질감 · 그라디언트 · 그림자

- `backdrop-filter: blur()`는 **스킨이 명시적으로 OS를 모사할 때만** 쓴다 —
  `macos`(반투명 상단바)와 `windows`(Windows Terminal 아크릴) 둘뿐이고, 그것도
  상단바 한 곳이다. 기본 스킨 `farshell`의 카드·패널은 전부 불투명 배경이다.
- 텍스트와 버튼에 그라디언트를 쓰지 않는다.
- 버튼 뒤에 같은 색을 흐린 **글로우 그림자를 두지 않는다.** 그림자는 회색·저투명도로,
  높이(elevation)를 말할 때만 쓴다(`--shadow-e1..e3`).
- 단, 0-blur 액센트 링은 그림자가 아니라 **포커스 링**이다 — 접근성 어포던스라 유지한다.

### 위계 — 중첩을 줄인다

- 카드 안의 카드를 만들지 않는다. 하위 카드가 그냥 부모의 내용일 수 있으면 없앤다.
- 보조 정보는 상자에 가두는 대신 작고 흐리게(`--sub`/`--muted`) 만든다.
- 모든 경계선은 "이 선이 사용자에게 무엇을 이해시키는가"에 답할 수 있어야 한다.

### 모션

- 콘텐츠에 등장 애니메이션(fade-in·slide-up)을 기본으로 걸지 않는다.
- hover에서 여러 transform을 동시에 걸지 않는다.
- 애니메이션은 120~300ms, 은은하게, 목적(피드백·상태 변화·공간 감각)이 있을 때만.
- 움직이는 것은 **한 화면에 하나**다. 상태 dot 중 `waiting`만 호흡한다 —
  세션이 여러 개 늘어선 화면에서 전부 움직이면 아무것도 눈에 안 띈다.
- `prefers-reduced-motion`에서는 전역으로 애니메이션·트랜지션을 끄고, `waiting`은
  깜빡임 대신 outline으로 같은 정보를 전한다.

### 착수 전 체크리스트

- [ ] 아이콘을 색 상자에 가두지 않았는가
- [ ] 이모지를 디자인 요소로 쓰지 않았는가
- [ ] 히어로에 기본값 세리프를 쓰지 않았는가
- [ ] 요청받지 않은 유리질감이 없는가
- [ ] 텍스트·버튼에 그라디언트가 없는가
- [ ] 버튼 뒤 컬러 글로우가 없는가
- [ ] 액센트가 70/20/10을 지키는가 (solid 액센트 = 주 액션 1개)
- [ ] 목적 없는 카드 중첩이 없는가
- [ ] 콘텐츠에 느린 등장 애니메이션이 없는가
- [ ] `border-left` + `border-radius`를 같이 쓰지 않았는가
      (모서리에서 막대가 잘려 보인다 — 왼쪽 강조는 `box-shadow: inset` 을 쓴다)
- [ ] 1순위로 볼 것이 무엇인지 0.5초 안에 답할 수 있는가

---

## 1. 스킨 — 6종

`<html data-skin="...">`로 전환한다. **모든 색은 토큰으로만** 참조한다 — 하드코딩
hex 금지. JS가 동적 생성하는 오버레이도 `.vt-*` 클래스로 토큰을 상속한다.

| 스킨 | 정체성 | 기본값 |
|------|--------|--------|
| `farshell` | 근흑(#0a0a0b) + **앰버 단일 액센트**(#f0a860). 어느 OS도 흉내내지 않는 우리 얼굴 | ✅ 기본 |
| `macos` | iTerm2/Terminal.app — 신호등, SF 폰트, 시스템 블루, 둥근 창 | |
| `catppuccin` | 파스텔 — 크롬 없음, 라벤더 강조 | |
| `windows` | Windows Terminal — 캡션 버튼, Cascadia, Fluent 블루, 각진 창 | |
| `vscode` | VS Code 통합 터미널 — 어두운 그레이, Fluent 블루 강조, 각진 UI | |
| `notepad` | 메모장/종이 느낌 — 유일한 **라이트 테마**, 따뜻한 오프화이트 배경 | |

전환은 rail ⚙ 설정 또는 팔레트(`Mod+K`)의 테마 칩. 부팅 시 `<head>` 인라인
스크립트가 페인트 전에 `data-skin`을 확정한다(FOUC 방지).

## 2. 토큰

| 파일 | 담는 것 |
|------|---------|
| `styles/theme/tokens.css` | 구조 토큰(간격·폰트·크기·반경·그림자·이징·브레이크포인트) + 기본 스킨 `farshell`의 색 토큰. Tailwind v4 `@theme static`이라 실제 `:root` 커스텀 프로퍼티로 컴파일된다 |
| `styles/theme/skins.css` | 나머지 5스킨의 같은 색 토큰 재매핑 |
| `styles/layers/components.css` | `.status-dot` — 신규 컴포넌트가 들어가는 자리 |
| `styles/layers/legacy.css` | 전 컴포넌트 + `.vt-*` 오버레이. 레이어 밖(unlayered)이라 항상 우선한다 |

Vite가 이 넷을 `frontend/dist/app.css`로 빌드한다.

### 색 토큰 (farshell 기준)

계약은 **색 17개**(배경 4 + 구분선 2 + 텍스트 3 + 액센트 3 + 상태 5)이고,
여기에 xterm에 넘기는 터미널 색 2개와 표면 상태 색 1개가 따로 붙는다.

| 토큰 | 값 | 용도 |
|------|-----|------|
| `--color-bg-0..3` | `#0a0a0b` `#131316` `#1a1a1e` `#232328` | 배경 4단 — 테두리 대신 이 단차로 요소를 나눈다 |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .08)` / `.16` | 구분선 2단 |
| `--color-txt` / `--color-sub` / `--color-muted` | `#eeeef0` `#9a9aa2` `#7c7c84` | 텍스트 3단 |
| `--color-acc` / `--color-acc-dim` / `--color-acc-ink` | `#f0a860` `#b3803f` `#17140f` | 액센트 · 죽인 액센트 · 액센트 위 텍스트 |
| `--color-st-{idle,working,waiting,done,error}` | `#7c7c84` `#4fd1a5` `#f0a860` `#5b8def` `#f06868` | 에이전트 4상태 + 에러 |
| `--color-term-bg` / `--color-term-fg` | `#0a0a0b` / `#eeeef0` | xterm 배경/전경 |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | "지금 선택됨" 표면. hover 오버레이의 2배 — 같은 값을 쓰면 포인터 장치에서 hover와 구분이 안 된다. 접근성 요구는 이게 아니라 액센트 막대가 담당한다 |

**대비 실측(WCAG 상대휘도):** `txt` 13.5~17.1:1 · `sub` 5.6~7.1:1 — 배경 4단
전부에서 AA(4.5:1) 통과. `muted`는 UI 컴포넌트 기준 3:1을 맞추려 스펙 초안
(`#6a6a72`, bg-3에서 2.92:1로 미달)보다 밝힌 `#7c7c84`(bg-3에서 3.78:1)로 조정했다.

### 구조 토큰

- **간격:** `--spacing: 4px` 단일 스케일. 모든 여백은 `calc(var(--spacing) * N)`.
- **글자 크기:** `--text-2xs`(10.5) ~ `--text-2xl`(28) 7단.
- **반경:** `--radius-sm`(6) / `md`(10) / `lg`(14) / `full`. 4단 위계 밖으로 나가지 않는다.
- **그림자:** `--shadow-e1..e3` — 전부 중립(검정 저투명도). 컬러 그림자는 없다.
- **모션:** `--dur-fast`(120ms) / `--dur-base`(200ms), `--ease-out-quint`.
- **브레이크포인트:** `--breakpoint-regular`(720px) / `--breakpoint-wide`(1024px).
  JS 쪽 상수는 `frontend/js/layout/breakpoints.js`에 **한 벌만** 둔다.

### 코드 파생 값 (기계 검증)

아래 값들은 **코드가 단일 진실**이고 이 문서는 그걸 옮겨 적을 뿐이다. 옮겨 적은
값은 언젠가 반드시 어긋나므로, `scripts/check_docs.py`의 `check_design_md()`가
CI에서 이 표를 코드와 직접 대조한다(I3). 산문이 아니라 표를 검사 대상으로 삼는
이유: `7`이나 `720` 같은 맨 숫자는 문서 어딘가엔 늘 있어서, 산문을 훑는 방식으로는
값을 틀리게 바꿔도 검사가 통과한다(실측으로 확인했다).

| 키 | 값 | 소스 |
|---|---|---|
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1024` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `2/4/6` | `frontend/js/layout/dnd.js` — `tierCap()` |
| `rail-items` | `7` | `frontend/index.html` — `.vt-rail-btn` 개수 |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error`는 예약) |
| `color-bg-0` | `#0a0a0b` | `styles/theme/tokens.css` |
| `color-acc` | `#f0a860` | `styles/theme/tokens.css` |
| `color-txt` | `#eeeef0` | `styles/theme/tokens.css` |

### 레거시 별칭

`legacy.css`의 `--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err`/
`--crust`/`--trad` 등은 위 `--color-*`를 가리키는 별칭이다. 소비처 수백 곳을
`var(--color-*)`로 일괄 교체하는 건 값의 출처를 옮기는 것과 완전히 다른 작업이라
아직 하지 않았다 — **새로 쓰는 코드는 `--color-*`를 직접 쓴다.**

### 타이포그래피 — OS 네이티브 (의도적)

`farshell`은 저장소에 **번들된 글꼴** IBM Plex Sans(UI) / IBM Plex Mono(터미널)를
쓴다(`frontend/vendor/fonts/`). `--mono`는 로컬에 설치돼 있으면 JetBrains Mono를
먼저 고르고, 없으면 번들된 Plex Mono로 떨어진다 — 어느 쪽이든 네트워크 요청이
없다.

OS 모사 스킨은 각 OS의 시스템 폰트를 쓴다. 거기서 `system-ui`는 "타이포 포기
신호"가 아니라 **iTerm2/Windows Terminal을 흉내내는 authentic한 선택**이다.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (터미널)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (터미널)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (터미널)

## 3. 레이아웃 — 3단

`⋯` 더보기 메뉴는 2.0에서 **완전히 사라졌다.** 그 자리를 두 경로가 나눠 갖는다
(ADR-8): **좌측 rail = 포인터 경로**, **커맨드 팔레트(`Mod+K`) = 키보드 경로**.
둘은 **같은 것을 노출**하므로 한쪽만 익혀도 된다.

```
┌──┬────────────────────────────────────────┬─────┐
│  │  [tab][tab][+][⌄]              🔍      │     │ ← #topbar (fine 38px / coarse 44px)
│r ├────────────────────────────────────────┤ 우측│
│a │ ┌──────────────┬───────────────────┐   │ 레일│
│i │ │  pane        │  pane             │   │(wide│
│l │ │  (xterm)     │  (xterm)          │   │ 전용│
│  │ ├──────────────┴───────────────────┤   │ 사용│
│  │ │  pane                            │   │ 량) │
│  │ └──────────────────────────────────┘   │     │
└──┴────────────────────────────────────────┴─────┘
   ↑ #vt-rail (48px, ≥720px에서만)
```

- **`#topbar`**: 신호등(macos만) · 탭(`#tabs`) · `+`(새 세션) · `⌄`(세션 프로파일)
  · 「세션」 버튼(좁은 화면 전용) · 빠른 열기(`#palette-toggle`) · 캡션 버튼(windows만).
  **마이크는 L7에서 상단바 밖으로 이관했다** — accent 버튼 하나를 회수해
  상단바에는 탭·`+`·팔레트만 남겼다. 마이크의 데스크톱 홈은 `#vt-rail-mic-slot`,
  터치 기기에서는 keybar로 옮겨진다.
- **`#vt-rail`** (48px, ≥720px): 세션 · 파일 · 큐 · 스니펫 · 포트 · 사용량 · ⚙설정 7개.
  compact에서는 통째로 숨고 팔레트와 바텀시트가 대신한다. 배지는
  `.vt-rail-badge`(액센트, 15px).
  **활성 표시는 색과 모양 두 채널**이다 — 3px 왼쪽 앰버 막대(`::before`)가 접근성
  요구(WCAG 1.4.11, 상태 식별 3:1)를 담당하고(실측 rail 배경 대비 3.05~9.25:1),
  전용 배경 토큰 `--color-surface-active`가 hover와의 구분을 담당한다(실측 1.31~1.39:1).
  아이콘은 `--txt`다 — 액센트를 아이콘에 쓰면 macos·vscode에서 대비가 1.82~2.14:1로
  떨어진다. **단, `.active`가 붙는 건 `session`과 `settings` 둘뿐이다**
  (`layout/rail.js:23` `PANEL_ITEMS`) — 나머지 5개는 패널이 아니라 모달을 연다.
- **분할 pane**: 터미널 영역은 pane의 **이진 트리**다. pane 헤더의 분할 버튼이나
  탭을 pane 가장자리에 드롭(드롭존 5개)해서 나누고, 구분선은 드래그로 옮긴다.
  `root`가 leaf 하나면 pane 헤더가 숨어 **1.7.0과 시각적으로 동일한 전체화면
  터미널**이 된다.
- **우측 레일**: wide(≥1024px)에서만. 사용량 게이지 자리 — 소스가 없으면
  통째로 사라진다.

### 반응형 3구간

| 구간 | 폭 | pane 상한 | 렌더 |
|------|-----|-----------|------|
| compact | <720px | 2 | 터치라면 한 번에 **한 pane만** 전체화면 + 헤더에 `· 1/2` 위치 표시, 좌우 스와이프로 이동 |
| regular | 720~1023px | 4 | 분할 트리 그대로. 패널은 오버레이(뒤 pane을 안 밀어냄) |
| wide | ≥1024px | 6 | 분할 트리 + 우측 레일. 패널은 밀어냄 |

상한을 넘으면 분할 버튼이 **이유를 tooltip에 담은 채 비활성**된다 — 조용히
아무 일도 안 일어나게 두지 않는다. 경계값은 `layout/breakpoints.js` 한 곳에만 있다.

## 4. 에이전트 상태 — 4상태

서버가 세션마다 상태를 하나 정하고, **프런트는 그리기만 한다.**

**계약은 5개 값이다** — `server/agent_status.py:46` `STATUSES = (IDLE, WORKING,
WAITING, DONE, ERROR)`. 이 중 실제로 진입 경로가 있는 건 앞의 4개고, `error`는
같은 파일 44행에 *"예약 — 2.0에서는 진입 경로 없음"*으로 표시돼 있다. 색 토큰
(`--color-st-error`)과 정렬 우선순위(`_URGENCY`)는 미리 자리를 잡아뒀다. 그래서
"4상태"는 **지금 관측 가능한 상태 수**이고, 소비하는 쪽은 5개를 다 처리해야 한다. 탭 · pane 헤더 · rail 목록(손이 필요한 것이 위로 정렬) · 파비콘 ·
앱 아이콘 배지(`waiting` 개수)가 같은 값을 본다.

- 컴포넌트 계약은 `.status-dot[data-state="..."]` 하나다
  (`styles/layers/components.css`). 색은 `--color-st-*`.
- **`waiting`만 호흡한다** — 레퍼런스 만장일치 결론: 가장 가치 있는 상태는
  실행 중도 완료도 아니라 **당신의 입력을 기다리는 중**이다.
- 카드/탭의 `working` 표시도 카드 전체가 아니라 **8px 점 하나**만 애니메이션한다
  (`opacity`/`transform`은 컴포지터 전용이라 리페인트가 없다 — 카드 전체에
  `box-shadow`를 애니메이션하면 그리드가 열려 있는 내내 리페인트가 돈다).
- Claude Code 훅(`fsh hooks install`)이 없으면 이 값은 안 온다. `fsh doctor`와
  설정 → 정보가 누락을 알려준다.

## 5. xterm.js 터미널 테마

"iTerm2 느낌 vs 윈도우 느낌"의 핵심은 창 크롬이 아니라 **터미널 자체의 배경 +
ANSI 16색**이다. `js/theme.js`의 `VT_XTERM_THEMES`에 스킨별 완전한 팔레트를
정의한다: `background/foreground/cursor/selection + black..white +
brightBlack..brightWhite`.

- farshell: 근흑(#0a0a0b) + 앰버 커서
- macos: 딥 블랙(#101012) + macOS 시스템 컬러(빨강 #ff453a, 초록 #32d74b, 파랑 #0a84ff …)
- catppuccin: #1e1e2e + Catppuccin Mocha 팔레트
- windows: **공식 Campbell 팔레트** (#0c0c0c, 빨강 #c50f1f, 파랑 #0037da …)
- vscode: #1e1e1e + VS Code 통합 터미널 기본 팔레트
- notepad: 유일한 라이트 배경(#fffefb) + 파랑 커서(#0060df)

`addSession()`(`js/term/xterm-setup.js`)이 생성 시 `getVtXtermTheme()`을 적용하고,
스킨 전환 시 `setVtSkin()`(`js/theme.js`)이 **열려 있는 모든 터미널**의
`term.options.theme`를 즉시 갱신한다.

## 6. 컴포넌트

### 커맨드 팔레트 (`Mod+K`)
세션·파일·명령을 아우르는 단일 확인 진입점(ADR-8). **각 명령 옆에 현재 키
바인딩을 표시**한다 — 값은 키맵 레지스트리(`js/core/keymap.js`)에서 읽으므로
사용자가 바꾼 바인딩이 그대로 보인다. `/` 접두사는 터미널 내 검색.

### 설정 (`Mod+,` / rail ⚙)
설정은 `localStorage`가 아니라 **`/api/workspace.settings`(서버)**에 산다 —
폰에서 바꾼 값이 맥북 브라우저에 그대로 뜬다. 키 바인딩은 재지정 가능하고,
**`passthrough`는 키를 터미널에 되돌려준다**(`Mod+F`를 셸의 `forward-char`로
되찾는 방법). 마우스 절에서 "앱으로 마우스 이벤트 전달"을 끄면 vim/tmux 마우스
모드 아래서도 드래그 선택이 항상 동작한다.

### 세션 카드 (`.vt-card`)
rail 세션 패널과 라이브 프리뷰가 공유한다. 위계 3단: 제목(가장 밝고 큼) >
프리뷰(중간 톤 `--txt2`, 실제 내용) > 부가 정보(`--sub`).

- 이미 탭으로 열려 있음: `box-shadow: inset 3px 0 0 var(--acc-dim)`.
  `border-left`를 쓰면 카드 radius에서 막대가 잘리고 레이아웃도 3px 밀린다.
- `working`: 테두리색 고정 + 8px 점 하나만 펄스. `done`: 테두리색 + `✓` 배지.
  둘 다 **컬러 링 그림자를 얹지 않는다** — 테두리색이 이미 같은 말을 한다.

### 토스트 (`.vt-toast`)
`.ok` / `.err` / `.info` 3종. 알림·업로드·에이전트 이벤트가 전부 이 하나를 쓴다.

### JS 동적 오버레이 (`.vt-*`, 토큰 상속)
- `.vt-onboarding` — 세션 0개 empty state. 히어로 아이콘은 `--muted`,
  액센트는 주 액션 버튼(`.vt-btn-primary`) 하나가 갖는다.
- `.vt-overlay` — 서버 연결 끊김 전체 화면 + `#conn-status` pill.
- `.vt-viewer-backdrop` / `.vt-viewer-card` — 코드 뷰어·큐·포트·가이드가 공유하는
  모달 뼈대(backdrop/닫기/Esc/배경클릭). compact에서는 같은 규칙이 바텀시트로 바뀐다.
- `.vt-banner` — 안전 모드 배너.

## 7. 접근성

- **터치 타깃:** coarse 포인터에서 `--topbar-h`가 44px가 되고 `.tab`과 닫기 버튼도
  실제 44px 높이를 쓴다. 아이콘 버튼은 시각 크기 30px을 유지하며 `::before`로
  44px 탭 영역을 확보한다. fine 포인터는 38px/28px 밀도를 유지한다.
- **좁은 화면:** `#tabs`가 0px로 눌려도 「세션」 버튼 → 바텀시트로 전환·이름 변경·
  개별 닫기가 남는다.
- **키보드:** `Mod+K` 팔레트, `Mod+,` 설정, `Ctrl/Cmd+F` 검색, Esc 닫기,
  `#add-btn` Enter/Space, `aria-haspopup`/`aria-expanded`,
  `:focus-visible` 아웃라인(`--acc`).
- **스크린리더:** 아이콘 버튼 `aria-label`, `#mic-status`
  `role="status" aria-live="polite"`.
- **`prefers-reduced-motion`:** 전역으로 애니메이션·트랜지션 비활성,
  `waiting` dot은 outline으로 대체.
- **safe-area-inset:** 상하좌 패딩 적용(노치/제스처 바).

## 8. 파일 맵

전체 모듈 지도는 [ARCHITECTURE.ko.md](./ARCHITECTURE.ko.md) §2에 있다. 이 표는
*시각적* 디자인과 직결된 파일만 추린 것이라 일부러 전수 목록이 아니다.

| 파일 | 책임 |
|------|------|
| `styles/theme/tokens.css` | 구조 토큰 + farshell 색 토큰 (`@theme static`) |
| `styles/theme/skins.css` | 나머지 5스킨의 색 재매핑 |
| `styles/layers/components.css` | `.status-dot` — 신규 컴포넌트 자리 |
| `styles/layers/legacy.css` | 전 컴포넌트 + `.vt-*` 오버레이 (레이어 밖) |
| `frontend/index.html` | 레이아웃 마크업, 부팅 테마 스크립트(FOUC 방지), 로그인 게이트 |
| `frontend/js/theme.js` | 스킨 전환, xterm 테마 정의/동기화 |
| `frontend/js/design/state-classes.js` | 상태 → 클래스/라벨 매핑 |
| `frontend/js/layout/breakpoints.js` | 반응형 3구간 경계값 (단일 소스) |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | pane 트리 렌더·compact 모드·드롭존 |
| `frontend/js/layout/rail.js` | 좌측 rail + 임베디드 패널 |
| `frontend/js/core/settings.js` · `keymap.js` | 서버 설정 스토어 · 키맵 레지스트리 |
| `frontend/js/ui/icons.js` | 인라인 SVG 아이콘 레지스트리 (유일한 아이콘 소스) |
| `frontend/js/ui/toast.js` | 통합 토스트 |
| `frontend/js/ui/favicon.js` | 상태 반영 파비콘 |
| `frontend/js/term/xterm-setup.js` | xterm 인스턴스 생성, `getVtXtermTheme()` 적용 |
| `frontend/js/term/conn-overlay.js` | 서버 연결 끊김 전체화면 오버레이 |
| `frontend/js/picker.js` | 모바일 세션 관리 시트, 파일 업로드 |
| `frontend/js/voice/` | 녹음/STT/TTS, 미디어키, 음성 전용 모드 — **별도** lib entry(`frontend/dist/voice.js`)로 독립 빌드돼 음성 capability가 켜졌을 때만 지연 로드된다 |
