# FarShell 디자인 시스템

[![English](https://img.shields.io/badge/lang-English-lightgrey.svg)](./DESIGN.md)

> **v2.1.0 기준.** 2026년 9월 「새 셸」 개편(N15~N46)으로 표면 레이어·Solid/TS
> 셸·레일·dock·HUD·팔레트가 전부 바뀌었다 — 모듈 지도는
> [ARCHITECTURE.ko.md](./ARCHITECTURE.ko.md) §2 참고. 2.1.0은 **세션 단위**로
> 동작한다 — 워크트리 모델(N8)과 소스컨트롤 쓰기(push/PR, N30~32)는 2.1.1,
> 멀티호스트(N39)·색점 램프(N33 §5)의 저장소 해싱은 2.1.1~2.1.2로 미뤄졌다
> (ADR-10, ADR-20). 아래 문서는 **지금 코드에 있는 것만** 적는다.

---

## 0. 디자인 원칙

이 프로젝트가 실제로 지키는 규칙이다. 새 화면을 그리기 전에 여기부터 읽는다.

### 절제가 곧 취향이다

그라디언트·애니메이션·유리질감·장식 아이콘은 **넣을 수 있다는 이유만으로 넣지
않는다.** 모든 시각 요소는 자기 자리를 정당화해야 하고, 기본값은 언제나 단순함이다.

### 색 — 70/20/10

중립 배경 4단(`--color-bg-0..3`)이 70%, 텍스트 3단이 20%, 액센트는 10%다.

- **한 화면의 solid 액센트는 주 액션 1개**를 원칙으로 한다. rail 활성 항목·배지·
  기본 버튼이 동시에 액센트를 solid로 채우면 셋 다 눈에 안 띈다.
  → rail·dock 활성 항목은 배경 단차(elevation)로 말하고 액센트는 상태 막대·아이콘
  색으로만 남긴다.
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
  상단바 한 곳이다. 기본 스킨 `farshell`의 카드·패널·dock·레일은 전부 불투명 배경이다.
- 텍스트와 버튼에 그라디언트를 쓰지 않는다.
- 버튼 뒤에 같은 색을 흐린 **글로우 그림자를 두지 않는다.** 그림자는 회색·저투명도로,
  오버레이(팔레트·시트·다이얼로그)의 높이(elevation)를 말할 때만 쓴다(`--shadow-e2/e3`).
  그 외의 경계는 1px 선(`--color-line`)으로 표현한다 — `--shadow-e1`은 2.1.0에서
  삭제됐다.
- 단, 0-blur 액센트 링은 그림자가 아니라 **포커스 링**이다 — 접근성 어포던스라 유지한다.

### 위계 — 중첩을 줄인다

- 카드 안의 카드를 만들지 않는다. 하위 카드가 그냥 부모의 내용일 수 있으면 없앤다.
- 보조 정보는 상자에 가두는 대신 작고 흐리게(`--color-sub`/`--color-muted`) 만든다.
- 모든 경계선은 "이 선이 사용자에게 무엇을 이해시키는가"에 답할 수 있어야 한다.

### 모션

- 콘텐츠에 등장 애니메이션(fade-in·slide-up)을 기본으로 걸지 않는다.
- hover에서 여러 transform을 동시에 걸지 않는다.
- 애니메이션은 120~200ms, 은은하게, 목적(피드백·상태 변화·공간 감각)이 있을 때만
  (`--dur-fast`/`--dur-base`, `--ease-out-quint`).
- 움직이는 것은 **한 화면에 하나**다. 상태 dot 중 `waiting`만 호흡한다 —
  세션이 여러 개 늘어선 화면(레일·플릿 홈)에서 전부 움직이면 아무것도 눈에 안 띈다.
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
- [ ] 행 높이가 24/28/36 세 가지를 벗어나지 않는가
- [ ] `border-left` + `border-radius`를 같이 쓰지 않았는가
      (모서리에서 막대가 잘려 보인다 — rail 상태 막대는 별도 `::before`/전용
      요소로 두거나 `box-shadow: inset`을 쓴다)
- [ ] 1순위로 볼 것이 무엇인지 0.5초 안에 답할 수 있는가

---

## 1. 스킨 — 6종

`<html data-skin="...">`로 전환한다. **모든 색은 토큰으로만** 참조한다 — 하드코딩
hex 금지(`frontend/js`·`styles/layers` 전역, `grep -rnE "#[0-9a-f]{3,8}\b"`가
tokens/skins 파일을 뺀 곳에서 0건이어야 한다). JS가 동적 생성하는 오버레이(팔레트·
dock)도 `.vt-*` 클래스로 토큰을 상속한다.

| 스킨 | 정체성 | 기본값 |
|------|--------|--------|
| `farshell` | 근흑(#08090a) + **앰버 단일 액센트**(#f0a860). 어느 OS도 흉내내지 않는 우리 얼굴 | ✅ 기본 |
| `macos` | iTerm2/Terminal.app — 신호등, SF 폰트, 시스템 블루, 반투명 상단바 | |
| `catppuccin` | 파스텔 — 크롬 없음, 라벤더 강조 | |
| `windows` | Windows Terminal — 캡션 버튼, Cascadia, Fluent 블루, 각진 UI(radius-sm 2px로 재정의) | |
| `vscode` | VS Code 통합 터미널 — 어두운 그레이, Fluent 블루 강조, 각진 UI(radius-sm 2px) | |
| `notepad` | 메모장/종이 느낌 — 유일한 **라이트 테마**, 따뜻한 오프화이트 배경 | |

전환은 헤더 우측 ⚙ 설정 또는 팔레트(`Mod+K`, 접두사 없이 검색하면 테마 칩도
섞여 나온다 / `>` 설정 모드)의 테마 칩. 부팅 시 `<head>` 인라인 스크립트가
페인트 전에 `data-skin`을 확정한다(FOUC 방지).

## 2. 토큰

| 파일 | 담는 것 |
|------|---------|
| `styles/theme/tokens.css` | 구조 토큰(간격·폰트·크기·반경·그림자·이징·브레이크포인트·셸 치수) + 기본 스킨 `farshell`의 색 토큰. Tailwind v4 `@theme static`이라 실제 `:root` 커스텀 프로퍼티로 컴파일된다 |
| `styles/theme/skins.css` | 나머지 5스킨의 같은 색 토큰 재매핑 |
| `styles/layers/components.css` | `.status-dot` — 신규 컴포넌트가 들어가는 자리 |
| `styles/layers/legacy.css` | 전 컴포넌트 + `.vt-*` 오버레이 + 레일/dock/HUD 프레임의 CSS. 레이어 밖(unlayered)이라 항상 우선한다 |

Vite가 이 넷을 `frontend/dist/app.css`로 빌드한다.

### 색 토큰 (farshell 기준)

계약은 **색 20개**(배경 4 + 구분선 2 + 텍스트 3 + 액센트 6 + 상태 5)이고,
여기에 xterm에 넘기는 터미널 색 2개와 표면 상태 색 1개가 따로 붙는다. 2.1.0에서
모서리가 한 단계(`--radius-md` 삭제)로 줄고, 4단 배경의 순서가 바뀌었다.

| 토큰 | 값 | 용도 |
|------|-----|------|
| `--color-bg-0` | `#08090a` | 최심 배경 |
| `--color-bg-1` | `#0e1011` | 크롬(헤더·레일·dock·HUD) |
| `--color-bg-2` | `#0b0d0e` | 행 헤더·서브바 — **`bg-1`보다 어둡다.** 서브바가 크롬 자체보다 한 단 더 가라앉아야 "행 위에 얹힌 부제목"으로 읽힌다는 실측 결론(2.0의 단조 증가 순서에서 바뀜) |
| `--color-bg-3` | `#141719` | 호버·활성 행 |
| `--color-line` / `--color-line-strong` | `rgb(255 255 255 / .07)` / `.14` | 구분선 2단 |
| `--color-txt` / `--color-sub` / `--color-muted` | `#e8e9ea` `#8b8f93` `#666a6e` | 텍스트 3단(`muted`는 스펙 초안보다 밝혀 `bg-3`에서 3:1을 맞췄다) |
| `--color-acc` | `var(--acc-farshell)` = `#f0a860` | 액센트. 상태 전용 5색과 함께 **이 두 용도 밖으로 쓰지 않는다** |
| `--color-acc-dim` | `#b3803f` | 배지 테두리·아웃라인 |
| `--color-acc-surface` | `var(--color-acc)`(스킨별 재정의 가능) | 액센트 위에 `acc-ink` 텍스트가 얹히는 면(주 액션 버튼·활성 탭 밑줄 배경). 기본은 액센트 그대로고 **macos만 `acc-dim`으로 재정의** — 애플 시스템 블루 위 흰 글자가 3.65:1이었다 |
| `--color-acc-ink` | `#17140f` | 액센트 위 텍스트 |
| `--acc-farshell/macos/catppuccin/windows/vscode/notepad` | 스킨 6개 각자의 액센트 hex | **스코프 밖 상수.** 테마 칩이 "지금 스킨이 아닌 다른 스킨"의 액센트 점을 보여줘야 해서, `data-skin`에 스코프된 `--color-acc`와 별도로 존재한다 — 이 상수가 칩 점과 각 스킨 `--color-acc`의 단일 소스 |
| `--color-st-{idle,working,waiting,done,error}` | `#7c7c84` `#4fd1a5` `#f0a860` `#5b8def` `#f06868` | 에이전트 5상태(§4) |
| `--color-term-bg` / `--color-term-fg` | `#0a0a0b` / `#eeeef0` | xterm 배경/전경 |
| `--color-surface-active` | `rgb(255 255 255 / .18)` | "지금 선택됨" 표면(레일·플릿 홈 행). hover 오버레이의 2배 — 같은 값을 쓰면 포인터 장치에서 hover와 구분이 안 된다. 접근성 요구(WCAG 1.4.11)는 이게 아니라 행 왼쪽 3px 상태 막대가 담당한다 |
| `--color-on-solid` | `#ffffff` | 상태 배너 등 고정 흰 텍스트 |

### 대비 규칙

역할마다 기준이 다르다. `muted`까지 4.5:1을 강요하면 `sub`와 구분이 안 돼 3단
램프가 무너지므로, WCAG가 실제로 요구하는 대로 나눈다.

| 토큰 | 기준 | 근거 |
|---|---|---|
| `txt` | **4.5:1** | 본문 텍스트 (WCAG 1.4.3 AA) |
| `sub` | **4.5:1** | 보조지만 여전히 읽는 텍스트 |
| `muted` · `st-*` | **3:1** | UI 컴포넌트·그래픽 객체 (WCAG 1.4.11) |

기준은 **배경 4단 전부**에서 성립해야 한다 — 같은 색이 `bg-0`에선 통과하고
`bg-3`에선 떨어지는 일이 실제로 있었다.

**실측(6스킨 × 배경 4단 중 최악값, `node --test frontend/tests/tokens-contrast.test.js`
가 계산하는 값과 같은 방식):**

| 스킨 | `txt` | `sub` | `muted` |
|---|---|---|---|
| farshell | 14.81 | 5.53 | 3.30 |
| macos | 9.12 | 4.52 | 3.02 |
| catppuccin | 6.31 | 4.52 | 3.00 |
| windows | 9.18 | 5.24 | 3.25 |
| vscode | 8.68 | 4.59 | 3.07 |
| notepad | 11.53 | 4.78 | 4.28 |

이 표는 `frontend/tests/tokens-contrast.test.js`가 CI에서 매번 재계산한다 —
토큰 값을 고칠 때마다 이 문서를 손으로 맞출 필요는 없지만, 표가 크게 틀어지면
다시 갱신한다.

### 구조 토큰

- **간격:** `--spacing: 4px` 단일 스케일. 모든 여백은 `calc(var(--spacing) * N)`.
- **글자 크기:** `--text-2xs`(10.5) ~ `--text-2xl`(28) 7단.
- **반경:** `--radius-sm`(**5px**, 모든 컨트롤) / `--radius-lg`(**10px**, 바깥 프레임·
  시트) / `--radius-full`. **`--radius-md`는 2.1.0에서 삭제** — 사용처는 전부
  `sm`으로 치환됐다. windows·vscode 스킨은 각진 UI를 위해 `--radius-sm`을 2px로
  스킨 안에서 덮어쓴다.
- **그림자:** `--shadow-e2`/`e3`만 남았다(오버레이 전용). **`--shadow-e1`은 삭제.**
- **모션:** `--dur-fast`(120ms) / `--dur-base`(200ms), `--ease-out-quint`.
- **브레이크포인트:** `--breakpoint-regular`(720px) / `--breakpoint-wide`(1280px) /
  `--breakpoint-xwide`(1600px). JS 쪽 상수는 `frontend/js/layout/breakpoints.js`에
  **한 벌만** 둔다(`COMPACT_MAX`/`REGULAR_MAX`/`WIDE_MAX`).
- **셸 치수:** 헤더 36px · 워크트리 레일 기본 252px(240~480 리사이즈) · dock 기본
  392px(320~560, 접히면 36px) · HUD 24px. 레일·dock의 폭·접힘은 **기기 스코프
  설정**(`ui.rail.*`/`ui.dock.*`, §6 「설정」)이라 폰에서 접은 게 맥에 반영되지
  않는다.

아래 값들은 **코드가 단일 진실**이고 이 문서는 그걸 옮겨 적을 뿐이다. 옮겨 적은
값은 언젠가 반드시 어긋나므로, `scripts/check_docs.py`의 `check_design_md()`가
CI에서 이 표를 코드와 직접 대조한다(I3). 산문이 아니라 표를 검사 대상으로 삼는
이유: `7`이나 `720` 같은 맨 숫자는 문서 어딘가엔 늘 있어서, 산문을 훑는 방식으로는
값을 틀리게 바꿔도 검사가 통과한다(실측으로 확인했다).

### 코드 파생 값

| 키 | 값 | 소스 |
|---|---|---|
| `skins` | `farshell, macos, catppuccin, windows, vscode, notepad` | `frontend/js/theme.js` — `VT_SKINS` |
| `breakpoints` | `720/1280` | `frontend/js/layout/breakpoints.js` — `COMPACT_MAX`/`REGULAR_MAX` |
| `pane-cap` | `2/4/6` | `frontend/js/layout/dnd.js` — `tierCap()`(compact/regular/wide. xwide는 `Infinity`라 표에 안 잡힌다) |
| `rail-items` | `6` | `frontend/index.html` — `.vt-rail-btn` 개수(**2.1.0에서 CSS로 숨긴 레거시 48px 아이콘 레일** `#vt-rail`. §3 참고 — 새 셸의 워크트리 레일 `#vt-wgrail`은 이 카운트 대상이 아니다) |
| `agent-states` | `5` | `server/agent_status.py` — `STATUSES` (`error`는 예약, §5) |
| `color-bg-0` | `#08090a` | `styles/theme/tokens.css` |
| `color-acc` | `#f0a860` | `styles/theme/tokens.css` |
| `color-txt` | `#e8e9ea` | `styles/theme/tokens.css` |

### 레거시 별칭

`legacy.css`의 `--win`/`--bar`/`--acc`/`--txt`/`--sub`/`--line`/`--ok`/`--err`/
`--crust`/`--trad` 등은 위 `--color-*`를 가리키는 별칭이다. 소비처 수백 곳을
`var(--color-*)`로 일괄 교체하는 건 값의 출처를 옮기는 것과 완전히 다른 작업이라
아직 하지 않았다 — **새로 쓰는 코드는 `--color-*`를 직접 쓴다.**

### 타이포그래피

`--font-ui`: IBM Plex Sans 400/500/600(번들 있음, UI 크롬 전용). `--font-mono`:
JetBrains Mono가 로컬에 있으면 우선(터미널, 2.0 ADR-9 유지), 없으면 번들된
IBM Plex Mono 400/600으로 떨어진다 — 어느 경로든 네트워크 요청이 없다.

OS 모사 스킨은 각 OS의 시스템 폰트를 쓴다. 거기서 `system-ui`는 "타이포 포기
신호"가 아니라 **iTerm2/Windows Terminal을 흉내내는 authentic한 선택**이다.

- macOS: `-apple-system, "SF Pro Text"` (UI), `ui-monospace, "SF Mono", Menlo` (터미널)
- windows: `"Segoe UI"` (UI), `"Cascadia Code", "Cascadia Mono", Consolas` (터미널)
- catppuccin: `system-ui` (UI), `ui-monospace, "SF Mono", Menlo, Consolas` (터미널)

## 3. 셸 레이아웃

2.0의 `⋯` 더보기 메뉴는 완전히 사라졌다(ADR-8, 2.0). 2.1.0은 그 골격을 표면
레이어 위에 다시 짰다(N16~N18, ADR-26) — **`#vt-chrome`(Solid가 소유하는 크롬:
헤더·레일·dock·HUD·페인 크롬)과 `#vt-surface`(위치·크기만 받는 xterm wrapper
계층)를 분리**해서, 페인 크기·활성 전환이 xterm 재생성이나 불필요한 `resize`
전송 없이 일어나게 한다. `#vt-surface`의 각 `.vt-term`은 한 번 붙으면 다시
DOM을 옮기지 않고 `layout/surface.js`가 `transform`만 바꾼다 — refit(및 서버로의
`resize` 전송)은 rect의 폭/높이가 실제로 바뀔 때만 일어난다.

```
┌ header 36 ────────────────────────────────────────────────────────────┐
│ [워크스페이스 칩] [탭…] [+]                       [⌘K] [화면 N] [⚙] │
├──────────┬───────────────────────────────────────────┬───────────────┤
│ rail 252 │ pane tree (표면 레이어)                    │ dock 392      │
│(워크트리)│ ┌ pane header 24 ┐                         │ ┌ tabs 28 ┐   │
│          │ │ cwd · agent     │                        │ │소스|큐|…│   │
│          │ └─────────────────┘                        │ └─────────┘  │
├──────────┴───────────────────────────────────────────┴───────────────┤
│ HUD 24                                                                │
└─────────────────────────────────────────────────────────────────────┘
```

### 반응형 4구간

| 구간 | 폭 | 레일 | dock | 페인 상한 | 렌더 |
|---|---|---|---|---|---|
| compact | < 720px | 없음(플릿 홈, §7) | 하단 내비의 탭 | 2 | 1페인 전체화면, 좌우 스와이프 |
| regular | 720~1279px | 48px 아이콘(레거시, `#vt-rail`) | 접힘 36px 기본, 열면 오버레이 | 4 | 분할 트리 |
| wide | 1280~1599px | 252px(`#vt-wgrail`) | 접힘 36px 기본, 열면 밀어냄 | 6 | 분할 트리 + 레일 |
| xwide | ≥ 1600px | 252px | 392px 열림 기본 | **무제한**(N4) | wide와 동일 + 상한 해제 |

레일·dock의 열림/폭은 **기기 스코프 설정**(`ui.rail.collapsed`, `ui.rail.width`,
`ui.dock.collapsed`, `ui.dock.width`) — 폰에서 접은 게 맥에 반영되면 안 된다.
경계값은 `layout/breakpoints.js` 한 곳에만 있다. 상한을 넘으면 분할 버튼이
**이유를 tooltip에 담은 채 비활성**된다.

### 헤더 36px

좌: 워크스페이스 칩(저장소명 + 호스트, 클릭 → 팔레트 `@` 모드). 중앙: 탭 —
**2.1.0에서는 세션**(2.1.1부터 워크트리, ADR-20)이 탭 하나다. 탭 구성은
`[에이전트 마크 14px][이름][상태 dot][읽지 않음 배지]`. 우: `⌘K`(팔레트),
연결된 화면 아이콘+수, 설정 ⚙. 탭 드래그 → 페인 드롭은 기존 `layout/dnd.js`
그대로 쓴다(`frontend/js/shell/HeaderExtras.tsx`가 워크스페이스 칩·연결된
화면만 새로 그리고, 탭 자체는 손대지 않는다).

### 워크트리 레일 252px (`#vt-wgrail`, `shell/Rail.tsx`)

2.1.0은 워크트리 모델이 아직 없어(2.1.1 몫) **세션을 워크트리 행 자리에
그대로 그린다** — 세션 하나 = 행 하나. 행 구성: 왼쪽 3px **상태 막대**
(`--color-st-*` 톤 — idle/working/waiting/done/error, §4와 같은 색) · 에이전트
마크 · 이름 · 우측 diff 배지(세션 cwd 기준 `GET /api/git/status`, 60초 캐시) ·
2번째 줄 상태 문장. 그룹 순서는 고정: **개입 필요**(waiting·error) →
**작업 중**(working) → **유휴**(idle·done), 그룹 안은 최근 활동순. 클릭 → 그
세션을 활성 페인에. 레일 폭 리사이저 240~480px. 접히면(48px) 마크와 상태 막대만
남는다.

> 아직 없는 것: §5의 계획 문서(20-design-system.md)가 제안했던 "저장소 이름
> 해시 → 색점"(색점 램프)은 2.1.0 코드에 없다 — 왼쪽 막대는 지금은 **상태색**만
> 쓴다. 저장소별 구분은 워크트리 모델(2.1.1)과 함께 다시 판단한다.

### dock 392px (`shell/Dock.tsx`)

우측 고정, `›`로 36px 스트립까지 접힘(하단 dock은 만들지 않는다 — ADR-21, 터미널
세로를 아끼기 위해). 탭 4개: **소스컨트롤 · 큐 · 포트 · 사용량**. 프레임이 책임지는
건 탭 전환·접기·폭 리사이저(320~560)·기기 스코프 저장뿐이고, 각 탭 내용은 기존
패널 렌더러(`queue.js`·`ports.js`·`panels/usage.js`·`panels/viewer/scm.js`)를
그대로 이식해 그린다 — 2.0의 팝업 모달 4종은 제거됐고, `queue.show`·`ports.show`
·`usage.open`·`scm.show` 같은 기존 액션 id는 유지한 채 dock 탭을 여는 것으로
재배선됐다(팔레트·키맵이 그 id를 그대로 참조한다). 포트·사용량 탭은
`/api/capabilities`에 해당 소스가 없으면 탭 자체가 사라진다.

**소스컨트롤 탭은 2.1.1까지 읽기 전용이다**(ADR-22) — stage/unstage/commit/push/
PR 생성은 승격 세션(`POST /api/auth/elevate`)과 git 계정 저장소가 있어야 하는데
둘 다 2.1.1 몫. 2.1.0은 상태·diff 조회만.

**코드뷰어는 dock에 없다.** 팔레트 `/` 모드로 파일을 고르면 **페인으로 열린다**
(N4 리프 일반화 — `layout/tree.js`의 leaf에 `kind: 'terminal' | 'viewer'` 필드가
생겼다. `viewer` 리프는 `session`이 항상 `null`이고 파일 경로만 가진다). 2.0의
모달 코드뷰어 백드롭은 사라졌다.

### HUD 24px (`shell/Hud.tsx`)

`서버 :7777 · 터널 named · E2E ON · 세이프모드 ON · 연결된 화면 3 · Claude 62% ·
4h12m 후 초기화 · v2.1.0` 형태의 칩 나열. 30초 폴링 + `/ws-notify` 이벤트로 즉시
갱신. 값이 없는 항목은 **숨김**(사용량 provider 없으면 칩 없음 — 2.0 규칙 유지).
compact에서는 HUD 자체가 없다(하단 내비가 그 자리를 대신한다).

### 리사이즈 오버레이 (N43)

분할선 드래그 중 활성 페인 중앙에 `142 × 38`(px) + `cols × rows` 캡션이 뜬다
(`layout/resize-overlay.js`). xterm의 `fit()`을 매 프레임 부르지 않는다 — 표면
레이어의 refit 게이트가 드래그 중 fit을 미루는 최적화를 오버레이가 우회하지
않도록, 드래그 시작 시점의 셀 크기 하나로 매 프레임 칸 수를 추정한다. 드래그
종료 200ms 후 사라지고, `prefers-reduced-motion`에서는 페이드 없이 즉시 사라진다.

### 레거시로 남은 것 (아직 지우지 않음)

`#vt-rail`(48px 아이콘 레일, 세션·큐·스니펫·포트·사용량·설정 6개 버튼)과
`#vt-right-rail`(우측 사용량 전용 레일)은 **DOM에는 남아 있지만 CSS로 숨겨져
있다**(`legacy.css`의 `#vt-rail { display:none !important; }`). 워크트리 레일
(`#vt-wgrail`)과 dock 사용량 탭이 각각 그 역할을 대신한다. `#vt-rail-settings-tpl`
(데스크톱 마이크·테마 칩 플라이아웃)만은 손대지 않았다 — Rail.tsx의 "⋯ 더보기"가
그 안의 버튼을 그대로 다시 쓴다.

## 4. 코드뷰어 · 파일 페인

`layout/tree.js`의 리프 `kind: 'viewer'`. 팔레트 `/` 모드에서 파일을 고르면 그
파일이 새 페인으로 열린다 — 파일 트리 없이 한 파일씩. 세션 페인과 같은 분할·
드래그·리사이즈 규칙을 그대로 쓴다(N4 리프 일반화). **읽기 전용**이며 쓰기
API는 없다(`server/fsguard.py`의 3중 방어는 2.0과 동일 — 고정 루트
`VT_BROWSE_ROOTS` · `Path.resolve()` + `is_relative_to` · 파일명 denylist).

## 5. 에이전트 상태 — 5값(관측 가능은 4)

서버가 세션마다 상태를 하나 정하고, **프런트는 그리기만 한다.**

계약은 5개 값이다 — `server/agent_status.py` `STATUSES = (IDLE, WORKING, WAITING,
DONE, ERROR)`. 이 중 실제로 진입 경로가 있는 건 앞의 4개고, `error`는 같은
파일에 *"예약 — 진입 경로 없음"*으로 표시돼 있다. 색 토큰(`--color-st-error`)과
정렬 우선순위는 미리 자리를 잡아뒀다. 그래서 "4상태"는 **지금 관측 가능한 상태
수**이고, 소비하는 쪽(탭·pane 헤더·워크트리 레일·플릿 홈·파비콘·앱 아이콘
배지)은 5개를 다 처리해야 한다.

- 컴포넌트 계약은 `.status-dot[data-state="..."]` 하나다(`styles/layers/components.css`).
  색은 `--color-st-*`.
- **`waiting`만 호흡한다** — 가장 가치 있는 상태는 실행 중도 완료도 아니라
  **당신의 입력을 기다리는 중**이다.
- 카드/탭/레일 행의 `working` 표시도 전체가 아니라 **작은 점 하나**만
  애니메이션한다(`opacity`/`transform`은 컴포지터 전용이라 리페인트가 없다).
- Claude Code 훅(`fsh hooks install`)이 없으면 이 값은 안 온다. `fsh doctor`와
  설정 → 정보가 누락을 알려준다.

### 에이전트 마크 5종 (N33)

`frontend/js/ui/icons.js`에 14px 정사각, stroke 1.75, `currentColor`로 등록.

| 키 | 형태 | 대상 |
|---|---|---|
| `agent-claude` | 스파크(4갈래 별) | claude |
| `agent-codex` | 브래킷 `[ ]` | codex |
| `agent-local` | 칩(사각 + 핀 4개) | 로컬 LLM(ollama·lmstudio·qwen 등) |
| `agent-ssh` | 지구(원 + 경선) | 원격 SSH 세션(2.1.2, N39) |
| `agent-shell` | 프롬프트 `>_` | 셸(에이전트 없음), 미지의 이름, gemini·aider(2.1.0 한정 — 2.1.1에서 전용 마크 추가 여부 재검토) |

## 6. 설정 · 팔레트

### 설정 (`Mod+,` / 헤더 ⚙)

설정은 두 스코프로 나뉜다 — `frontend/js/core/settings.js`의 `SCHEMA`가 항목마다
`scope: 'global' | 'device'`를 가진다(기본은 `global`).

- **global**: `/api/workspace.settings`(서버) — 폰에서 바꾼 값이 맥북 브라우저에
  그대로 뜬다. 키맵·스킨·자동복사·스크롤백 청크 수 등.
- **device**: `GET/PUT /api/device-settings` — `vt_device` 쿠키로 기기를 식별해
  `~/.vt/device-settings/<device_id>.json`(0600)에 저장. 레일·dock의 폭/접힘,
  터미널 폰트 크기, 마우스 이벤트 전달, 모바일 홈 탭(`ui.mobile.home`)처럼
  "이 기기에서만" 의미 있는 값. 폰의 핀치줌이 맥 터미널 폰트를 키우면 안 되므로
  분리됐다(N3).

키 바인딩은 재지정 가능하고, **`passthrough`는 키를 터미널에 되돌려준다**
(`Mod+F`를 셸의 `forward-char`로 되찾는 방법). 마우스 절에서 "앱으로 마우스
이벤트 전달"을 끄면 vim/tmux 마우스 모드 아래서도 드래그 선택이 항상 동작한다.

### 커맨드 팔레트 (`Mod+K`, `shell/Palette.tsx`)

2.0의 `quickopen.js`(접두사 3개: `/` 파일 · `:` 명령 · `>` 없음)와 인페인
`search.js`를 하나로 합쳤다(N5/N40/N46). 접두사 8개:

| 접두 | 모드 | 소스 |
|---|---|---|
| (없음) | `default` | 세션·파일·명령을 섞어 상위 결과 |
| `/` | `file` | 파일 fuzzy 검색(`GET /api/fs/search?q=`, 디바운스) |
| `@` | `session` | 세션(2.1.1부터 워크트리) |
| `:` | `keymap` | 명령 — 각 명령 옆에 **현재 키 바인딩**을 표시(`core/keymap.js` 레지스트리 값이라 사용자가 바꾼 바인딩이 그대로 보인다) |
| `#` | `queue` | 프롬프트 큐 항목 |
| `!` | `port` | 열린 포트 |
| `~` | `scrollback` | 스크롤백 서버 검색(`GET /api/search/scrollback?q=`, N40 — 세션 링버퍼 전체를 grep) |
| `>` | `settings` | 설정(테마·푸시 등 — 2.0의 `⋯` 「설정」 그룹을 대체하던 기존 기능이라 계획 문서의 7모드에 8번째로 얹었다. 문서에 없던 결정이라 여기 남긴다) |

2단 레이아웃 — 좌: 결과 목록, 우: **미리보기**(파일이면 앞부분 하이라이트,
세션이면 마지막 줄 + 상태, 스크롤백 결과면 앞뒤 컨텍스트). 폭이 좁으면 미리보기가
숨는다. `⌘F`는 팔레트 `~` 모드로 재배선됐고, 기존 인페인 검색바는 `⌘⇧F`로
강등(rebind 가능).

## 7. 모바일 (compact, < 720px)

```
┌ 상단 24 ── [호스트 칩]                            [대기 N] ┐
│                                                             │
│   탭 내용 (플릿 / 터미널 / 변경 / 큐 / 설정)                │
│                                                             │
├ keybar (터미널 탭에서만) ───────────────────────────────────┤
├ 하단 내비 52 ── 플릿 | 터미널 | 변경 | 큐 | 설정 ────────────┤
└──────────────────────────────────────────────────────────────┘
```

하단 내비 **5탭**(`frontend/index.html`의 `#vt-mnav-bottom`). dock의 탭 4개
(소스컨트롤·큐·포트·사용량)는 여기서 「변경」(=소스컨트롤)·「큐」로 승격되고,
포트·사용량·파일은 「설정」 아래로 들어간다. HUD는 없고, 상단 24px은 호스트 칩과
대기 수만 보여준다.

**플릿 홈**(`shell/Fleet.tsx`, ADR-27)이 첫 화면이다(`ui.mobile.home` 기본값
`fleet`) — compact에서 하는 일의 대부분은 "지금 뭐가 나를 기다리나"이지 타이핑이
아니라는 판단. 워크트리 레일(`rail-data.ts`)과 **정확히 같은** 데이터 소스·그룹핑·
정렬을 재사용해 "데스크톱 레일 맨 위가 폰에서도 맨 위"가 항상 성립한다.
`waiting` 상태 행에서 detect 패턴이 번호 선택지를 잡으면 **인라인 승인 버튼**
(`1 · Yes` / `3 · No`)이 붙는다 — 못 잡으면 「터미널로」 버튼만.

## 8. 사용량 (dock 「사용량」 탭)

`server/usage/base.py`에 두 인터페이스(N10/N41):

```python
class LimitProvider:    # 한도형 — 남은 비율
    def read(self) -> list[LimitGauge]     # {label, pct, resetsAt, window}
class CounterProvider:  # 누적형 — 한도 없이 누적
    def read(self, since) -> list[Counter] # {label, tokens, seconds, tokPerSec, samples}
```

- **한도형**: `usage/clauth.py` — clauth의 `~/.clauth/status.json`을 읽는다(2.0과
  동일 소스). HUD에는 한도형 게이지만 노출.
- **누적형**: `usage/counter_jsonl.py` — `~/.vt/usage-counter.jsonl`을 읽는다.
  기록은 `fsh usage add --model ... --tokens ... --seconds ...`(CLI) 또는
  `POST /api/usage/counter`로 채워진다 — 어느 스크립트가 그 호출을 하는지는
  사용자 몫이고, FarShell은 저장·표시만 한다. `usage/ollama.py`(선택)는
  `localhost:11434/api/ps`가 응답하면 실행 중인 로컬 모델을 counter 라벨로 얹는다.
- 두 provider 모두 없으면 dock 탭 자체가 사라진다(2.0 규칙 유지) — `VT_USAGE_PROVIDER`
  (`auto`/`clauth`/`none`)가 이 선택을 제어한다.

## 9. xterm.js 터미널 테마

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

## 10. 컴포넌트 규칙

- **행(row)**: 높이 24/28/36 세 가지만. 텍스트는 `--font-ui` 12px, 수치·경로·
  시간은 `--font-mono` 10.5~11px.
- **배지**: 1px `--color-line` 테두리, 배경 없음, `--radius-sm`. 상태 배지만
  상태색 테두리.
- **탭(헤더·dock)**: 활성 = 하단 2px `--color-acc` 밑줄 + `--color-acc-surface`
  배경. 비활성 = 텍스트 `--color-sub`.
- **버튼**: 기본 = 테두리만. 주 액션(커밋·링크 발급·만들고 전환) = `--color-acc`
  채움 + `--color-on-solid` 글자. **화면당 채움 버튼 1개.**
- **토글**: ON/OFF가 **글자로** 먼저 읽히는 세그먼트(`[ON][OFF]`), 스위치가 아니다.
  활성 세그먼트는 acc 테두리.
- **입력**: 배경 `--color-bg-0`, 테두리 `--color-line`, 포커스 시 `--color-line-strong`.
  글로우 없음.
- **분할선**: 1px line, 호버 시 line-strong, 드래그 중 acc.

## 11. 접근성

- **터치 타깃:** coarse 포인터에서 하단 내비·keybar 버튼이 44px 탭 영역을
  확보한다. fine 포인터는 더 조밀한 밀도를 유지한다.
- **키보드:** `Mod+K` 팔레트, `Mod+,` 설정, `Ctrl/Cmd+F`(팔레트 `~` 모드),
  `Ctrl/Cmd+Shift+F`(인페인 검색), Esc 닫기, `:focus-visible` 아웃라인(`--color-acc`).
- **스크린리더:** 아이콘 버튼 `aria-label`, 마이크 상태 `role="status"
  aria-live="polite"`.
- **`prefers-reduced-motion`:** 전역으로 애니메이션·트랜지션 비활성, `waiting`
  dot은 outline으로 대체.
- **safe-area-inset:** 상하좌 패딩 적용(노치/제스처 바).

## 12. 파일 맵

전체 모듈 지도는 [ARCHITECTURE.ko.md](./ARCHITECTURE.ko.md) §2에 있다. 이 표는
*시각적* 디자인과 직결된 파일만 추린 것이라 일부러 전수 목록이 아니다.

| 파일 | 책임 |
|------|------|
| `styles/theme/tokens.css` | 구조 토큰 + farshell 색 토큰 + 셸 치수(`@theme static`) |
| `styles/theme/skins.css` | 나머지 5스킨의 색 재매핑 |
| `styles/layers/components.css` | `.status-dot` — 신규 컴포넌트 자리 |
| `styles/layers/legacy.css` | 전 컴포넌트 + `.vt-*` 오버레이 + 레일/dock/HUD 프레임 CSS (레이어 밖) |
| `frontend/index.html` | 부팅 테마 스크립트(FOUC 방지)·로그인 게이트·`#vt-chrome`/`#vt-surface` 마운트 지점·모바일 하단 내비·keybar |
| `frontend/js/layout/surface.js` | 표면 레이어 배치 · refit 게이트(N16) |
| `frontend/js/core/signals.ts` | `core/store.js`/`layout/store.js` → Solid signal 브리지 |
| `frontend/js/core/types.ts` | 공용 TS 타입 |
| `frontend/js/shell/Rail.tsx` | 워크트리 레일 252px(N36) |
| `frontend/js/shell/Dock.tsx` | dock 392px 프레임(N35) |
| `frontend/js/shell/Hud.tsx` · `hud-data.ts` | HUD 24px(N34) |
| `frontend/js/shell/HeaderExtras.tsx` | 워크스페이스 칩 · 연결된 화면(N37) |
| `frontend/js/shell/Fleet.tsx` | 모바일 플릿 홈(N38) |
| `frontend/js/shell/Palette.tsx` · `palette-data.ts` | 커맨드 팔레트 7+1모드(N5/N40/N46) |
| `frontend/js/layout/resize-overlay.js` | 분할선 드래그 중 px/cols×rows 캡션(N43) |
| `frontend/js/layout/tree.js` | 페인 트리 — leaf `kind: 'terminal'\|'viewer'`(N4) |
| `frontend/js/layout/breakpoints.js` | 반응형 4구간 경계값 (단일 소스) |
| `frontend/js/layout/panes.js` · `compact.js` · `dnd.js` | 페인 트리 rect 산출 · compact 모드 · 드롭존 |
| `frontend/js/core/settings.js` | 서버 설정 스토어 — global/device 스코프(N3) |
| `frontend/js/core/keymap.js` | 키맵 레지스트리 |
| `frontend/js/panels/viewer/scm.js` | dock 소스컨트롤 탭 렌더러(읽기 전용) |
| `frontend/js/ui/icons.js` | 인라인 SVG 아이콘 레지스트리(유일한 아이콘 소스) — 에이전트 마크 5종 포함 |
| `frontend/js/term/xterm-setup.js` | xterm 인스턴스 생성, `getVtXtermTheme()` 적용 |
| `server/usage/base.py` · `clauth.py` · `counter_jsonl.py` · `ollama.py` | LimitProvider/CounterProvider(N10/N41) |
| `server/device_settings.py` | 기기 스코프 설정 저장(N3) |
| `frontend/js/voice/` | 녹음/STT/TTS, 미디어키, 음성 전용 모드 — **별도** lib entry(`frontend/dist/voice.js`)로 독립 빌드돼 음성 capability가 켜졌을 때만 지연 로드된다 |
