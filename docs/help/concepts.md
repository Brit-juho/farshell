# fsh help concepts — 핵심 개념

FarShell을 잘 쓰려면 터미널 생태계의 3-레이어 구조 이해가 도움됩니다.

## 1. 3-레이어 구조

    ┌──────────────────────────────────────────┐
    │ 레이어 1: 터미널 에뮬레이터 (GUI 앱)         │
    │   iTerm2, Terminal.app, Ghostty, gnome-   │
    │   terminal, konsole, alacritty, kitty 등  │
    │   → "검은 창"을 그려주는 GUI 프로그램         │
    └──────────────────────────────────────────┘
                         ↓ PTY 만들어서
    ┌──────────────────────────────────────────┐
    │ 레이어 2: 그 창 안에서 도는 프로세스           │
    │   - 보통은 셸 (zsh, bash, fish)           │
    │   - 또는 vim, less, python, claude, ...  │
    │   - 그리고 ★ tmux ★도 들어갈 수 있음        │
    └──────────────────────────────────────────┘
                         ↓ tmux는 또 자기 PTY를 만들어서
    ┌──────────────────────────────────────────┐
    │ 레이어 3: tmux 안의 셸                      │
    │   zsh, bash 등 — tmux가 만든 PTY 안에서 돔 │
    └──────────────────────────────────────────┘

핵심 정리:
- **iTerm2** = 검은 창 그리는 GUI
- **zsh / bash** = 명령 해석기 (`ls` 치면 실행)
- **tmux** = 셸 여러 개를 담는 컨테이너 + 영속성 제공 (셸의 한 종류 ❌)

## 2. tmux가 하는 일

### 영속성

iTerm 창을 닫아도 tmux 서버 안의 세션은 살아있음. vim/claude 등 그 안의 작업도 그대로.
다시 attach하면 끊긴 시점 그대로 복원.

### 다중 클라이언트 attach

같은 tmux 세션에 데스크톱 iTerm + 모바일 폰 + 웹 브라우저가 동시에 attach 가능.
모두 같은 화면을 보고, 모두 입력 가능.

→ **이게 FarShell의 핵심 인프라.** tmux 없으면 모바일/음성 기능이 안 돔.

### 멀티플렉싱

한 tmux 세션 안에 window + pane 여러 개.

    tmux 세션 "dev"
    ├─ window 0
    │  ├─ pane 0  ← claude 돌고 있음
    │  └─ pane 1  ← npm run dev
    └─ window 1
       └─ pane 0  ← vim

## 3. 격리 소켓 `-L vt`

FarShell은 별도 tmux 서버(`/tmp/tmux-{uid}/vt` 소켓)를 씀.

    tmux ls          # 사용자 평소 세션 (default 소켓)
    tmux -L vt ls    # FarShell 전용 세션 (vt 소켓)

→ 두 세계가 완전히 분리됨. 사용자 기존 워크플로 안 건드림.

## 4. 자동 attach 메커니즘

새 터미널 창이 자동으로 FarShell tmux로 들어가려면 둘 중 하나 등록:

### 메커니즘 A — 셸 init 스니펫

    eval "$(fsh shell-init zsh)" >> ~/.zshrc

새 zsh 시작 시마다 5중 가드 통과 후 자동 attach. iTerm Cmd+N, split, 새 탭 모두 적용.

### 메커니즘 B — 터미널 profile

    fsh install-profiles

iTerm Dynamic Profile / Ghostty / WezTerm 등에 등록. 새 창 default command가 tmux로.

## 5. `fsh stop`이 안 끄는 것

`fsh stop`은 server/tunnel/voice daemon만 종료.
**tmux 세션은 그대로 살아있음** — 이게 영속성의 핵심.

완전 종료: `fsh stop --purge`

## 6. 자주 묻는 질문

- **Q. 기존에 열린 iTerm 창들이 fsh 실행 후 자동 통합되나?**
  - A. ❌ 안 됨. zsh 가드는 새 창 시작 시에만 동작. 기존 창은 수동 `tmux -L vt attach -t dev`

- **Q. 모든 iTerm 창을 처음부터 tmux로 시작하면 되나?**
  - A. ✅ 그게 권장 방식. `fsh install-profiles` 또는 `fsh shell-init`이 그것.

- **Q. tmux 서버를 항상 켜두는 부담?**
  - A. ~5–10MB 메모리, idle 시 거의 0% CPU. 시스템 sleep 시 다른 사용자 프로세스와 함께 멈춤. 부담 없음.
        완전 종료가 필요하면 `fsh stop --purge`.
