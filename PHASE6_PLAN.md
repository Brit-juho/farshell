# Phase 6 — 크로스 플랫폼 터미널 통합 강화

> Phase 1~5 완료 후 도출된 보강 항목.
> 목적: tcgetattr 경고·p10k 충돌·셸별 차이를 해결하고, **모든 클라이언트가 진짜로 같은 tmux 서버를 공유**하도록 통일.

---

## 0. 배경

현재 `vt`는 `-L vt` 격리 소켓을 사용하지만:
- `voice_daemon.py`가 기본 소켓을 사용 → Voice Daemon 입력이 모바일·웹과 분리
- `.zshrc` 통합 가이드가 instant prompt와 충돌 → `tcgetattr failed: Operation not supported by device` 경고
- 터미널 앱마다 진입점이 달라 사용자가 직접 `.zshrc` 편집해야 함
- WSL2/PowerShell 환경에서 tmux 자동 attach 패턴 부재

오픈소스 조사 결과(tmuxinator·smug·sesh·oh-my-zsh tmux·zsh-tmux·tmux-continuum)에서 도출한 모범 사례를 통합 적용한다.

---

## 1. 전체 목표

다음 한 문장으로 요약:

> **사용자가 어떤 OS·터미널 앱·셸을 쓰든, 한 번의 설치 명령으로 모든 클라이언트(데스크톱·모바일·Voice Daemon)가 같은 tmux 세션을 공유하며, tcgetattr 경고나 instant prompt 충돌이 발생하지 않게 한다.**

### 성공 기준
1. macOS·Linux·WSL2에서 `vt install-profiles` 후 새 터미널 창을 열면 자동으로 vt 소켓의 dev 세션 진입
2. p10k instant prompt 활성 환경에서 zsh 시작 시 콘솔 출력 0건
3. Voice Daemon → 모바일 → 데스크톱 iTerm이 모두 같은 세션 화면 공유
4. 셸 init 의존을 제거할 수 있는 환경(로컬 GUI 터미널)에선 .zshrc 미수정으로도 동작
5. SSH 원격에서도 안전하게 자동 attach (셸 init fallback 경로)

---

## 2. 설계 규칙 (Design Principles)

각 작업 항목이 따라야 할 공통 원칙.

### R1. TTY 가드는 5중 결합
모든 자동 attach 코드는 다음을 모두 만족할 때만 실행:
```
[[ -o interactive ]]                       # 대화형 셸
&& [[ -t 0 && -t 1 ]]                      # stdin/stdout 둘 다 TTY
&& [[ -z "$TMUX" ]]                        # tmux 안이 아님
&& [[ -z "$INSIDE_EMACS$VSCODE_INJECTION$ZED_TERM$INTELLIJ_ENVIRONMENT_READER" ]]  # IDE 임베디드 셸 아님
&& command -v tmux >/dev/null
```

### R2. `2>/dev/null`은 단독 사용 금지
tcgetattr 경고를 stderr 리다이렉트로만 가리는 건 **증상 가림**. 죽은 채 살아있는 셸을 만든다. 가드를 통과한 후에만 호출하고, 실패 시 명확히 에러 표시.

### R3. `exec tmux new -A -s`로 부모 교체
셸 init에서 tmux를 띄울 때는 `exec`로 부모 zsh/bash를 교체. 이중 init 방지 + p10k 충돌 회피. `-A`로 idempotent.

### R4. 터미널 profile > 셸 init
가능하면 셸 init이 아닌 **터미널 앱의 profile/launch command**에 위임. SSH 폴백용으로만 셸 init을 둔다.

### R5. 단일 tmux 서버 원칙
모든 vt 클라이언트(CLI / server / daemon / hook)는 **하나의 tmux 소켓(`-L vt`)만** 사용. 호출 누락이 단일 진실의 원천을 깨뜨린다.

### R6. detached → attach 분리
세션 생성과 attach를 항상 분리. 생성은 `tmux -L vt new -d -s <name>` (TTY 불필요), attach는 호출자가 결정.

### R7. 멱등성 (Idempotent)
모든 install/setup 명령은 여러 번 실행해도 안전해야 한다. 기존 profile이 있으면 갱신, 없으면 생성.

### R8. 옵트인 (Opt-in)
사용자 환경(`.zshrc`, 터미널 settings.json 등)을 수정하기 전에 **항상 명시적 동의** 또는 dry-run 옵션 제공.

---

## 3. 작업 항목

### #6-1. Voice Daemon 격리 소켓 통일 (Phase 1 누락분 보강)

**목표:** Voice Daemon의 모든 tmux 호출이 `-L vt` 소켓을 사용하도록 통일. 모바일·웹·Voice Daemon이 같은 세션 공유.

**수정 파일**
- `server/voice_daemon.py` — `subprocess.run(["tmux", ...])` 3곳 모두 `-L vt` 적용

**핵심 구현**

```python
# server/voice_daemon.py 상단
import os
TMUX_SOCKET = os.environ.get("VT_TMUX_SOCKET", "vt")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]

# 기존 호출 변경
def get_active_tmux_pane() -> str | None:
    result = subprocess.run(
        TMUX_BASE + ["display-message", "-p", "#{pane_id}"],
        ...
    )

def get_any_tmux_pane() -> str | None:
    result = subprocess.run(
        TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
        ...
    )

def send_to_tmux(pane_id: str, text: str) -> bool:
    subprocess.run(
        TMUX_BASE + ["send-keys", "-t", pane_id, "--", text, "Enter"],
        ...
    )
```

**점검 절차**

1. `grep -n '"tmux"' server/voice_daemon.py` — 결과 0건이어야 함 (모두 `TMUX_BASE` 사용)
2. Voice Daemon 기동 후 Ctrl+Shift+V로 입력 → 모바일 화면에 즉시 반영되는지 확인
3. 사용자 기본 tmux 세션이 따로 있을 때 Voice Daemon 입력이 vt 소켓에만 들어가는지 검증
4. `tmux ls`(기본)와 `tmux -L vt ls`(격리)가 분리됨을 확인

---

### #6-2. `vt install-profiles` — 터미널 앱 profile 자동 등록

**목표:** 새 터미널 창을 열면 자동으로 `tmux -L vt new -A -s dev` 실행되도록 각 터미널 앱 설정 파일을 패치. 셸 init 의존 제거.

**수정/생성 파일**
- 신규 `bin/vt-profiles.sh` — 터미널별 등록 스크립트 모음 (또는 `bin/vt`에 인라인)
- `bin/vt` — `cmd_install_profiles` 추가
- 신규 `templates/profiles/` — 각 터미널별 템플릿
  - `iterm-dynamic-profile.json`
  - `ghostty.config`
  - `wezterm.lua.snippet`
  - `kitty.conf.snippet`
  - `windows-terminal.json.snippet`

**대상 터미널 매트릭스**

| 터미널 | 설정 위치 | 적용 방법 |
|--------|-----------|-----------|
| iTerm2 | `~/Library/Application Support/iTerm2/DynamicProfiles/vt.json` | JSON 파일 생성 (Dynamic Profile) |
| macOS Terminal.app | (gui 한정) | 가이드 출력만, 자동 X |
| Ghostty | `~/.config/ghostty/config` | `command = ...` 라인 append |
| WezTerm | `~/.config/wezterm/wezterm.lua` | `default_prog` snippet 안내 |
| Kitty | `~/.config/kitty/kitty.conf` | `shell = ...` 라인 append |
| Alacritty | `~/.config/alacritty/alacritty.toml` | `shell.program` snippet |
| Windows Terminal (WSL2) | `%LOCALAPPDATA%/Packages/Microsoft.WindowsTerminal_*/LocalState/settings.json` | profile에 `commandline` 패치 (PowerShell 헬퍼) |

**핵심 구현**

```bash
cmd_install_profiles() {
  echo ""
  echo "  📦 vt install-profiles — 터미널 앱 profile 자동 등록"
  echo ""

  local installed=0 skipped=0

  # iTerm2 Dynamic Profile
  if [ -d "/Applications/iTerm.app" ]; then
    _install_iterm_profile && installed=$((installed+1)) || skipped=$((skipped+1))
  fi

  # Ghostty
  if [ -d "/Applications/Ghostty.app" ] || command -v ghostty >/dev/null; then
    _install_ghostty_profile && installed=$((installed+1)) || skipped=$((skipped+1))
  fi

  # WezTerm / Kitty / Alacritty — config snippet 안내 (자동 편집은 위험)
  for app in wezterm kitty alacritty; do
    if command -v "$app" >/dev/null 2>&1; then
      _print_${app}_snippet
    fi
  done

  echo ""
  echo "  결과: ${installed}개 자동 등록, ${skipped}개 건너뜀"
  echo ""
}

_install_iterm_profile() {
  local DIR="$HOME/Library/Application Support/iTerm2/DynamicProfiles"
  mkdir -p "$DIR"
  local cmd="tmux -L $VT_TMUX_SOCKET new -A -s dev"
  cat > "$DIR/vt.json" <<EOF
{
  "Profiles": [{
    "Name": "vt (Voice Terminal)",
    "Guid": "vt-voice-terminal",
    "Custom Command": "Custom Shell",
    "Command": "/bin/zsh -c '$cmd'",
    "Working Directory Type": "Recycle"
  }]
}
EOF
  echo "  ✓ iTerm2 Dynamic Profile → $DIR/vt.json"
}

_install_ghostty_profile() {
  local CFG="$HOME/.config/ghostty/config"
  mkdir -p "$(dirname "$CFG")"
  if grep -q "^command = .*tmux -L vt" "$CFG" 2>/dev/null; then
    echo "  ⓘ Ghostty 이미 등록됨"
    return 1
  fi
  cat >> "$CFG" <<EOF

# vt (Voice Terminal) — auto-attach
# command = tmux -L $VT_TMUX_SOCKET new -A -s dev
EOF
  echo "  ✓ Ghostty config 주석 추가 (활성화 원하면 # 제거)"
}
```

**점검 절차**

1. `vt install-profiles` 실행 후 각 설정 파일 존재 확인
2. iTerm2 재시작 → Profiles 메뉴에 "vt (Voice Terminal)" 표시
3. 해당 profile로 새 창 열면 자동 tmux 진입, 콘솔 경고 없음
4. 두 번 실행해도 중복 추가 없음 (멱등성)
5. dry-run 모드(`--dry-run` 플래그)로 변경 미리보기 가능

---

### #6-3. `vt shell-init` — 셸별 안전 통합 스니펫 출력

**목표:** SSH 원격이나 profile 등록 불가 환경을 위한 셸 init 코드 생성. 사용자는 한 줄(`eval "$(vt shell-init zsh)"`)만 추가.

**수정/생성 파일**
- `bin/vt` — `cmd_shell_init` 추가

**지원 셸**: zsh, bash, fish, pwsh

**핵심 구현**

```bash
cmd_shell_init() {
  local shell="${1:-zsh}"
  case "$shell" in
    zsh)  _print_zsh_init  ;;
    bash) _print_bash_init ;;
    fish) _print_fish_init ;;
    pwsh|powershell) _print_pwsh_init ;;
    *)    echo "사용법: vt shell-init [zsh|bash|fish|pwsh]" >&2; return 1 ;;
  esac
}

_print_zsh_init() {
  cat <<'EOF'
# vt shell-init zsh — instant prompt 위에 배치
if [[ -o interactive ]] \
   && [[ -t 0 && -t 1 ]] \
   && [[ -z "$TMUX" ]] \
   && [[ -z "$INSIDE_EMACS$VSCODE_INJECTION$ZED_TERM$INTELLIJ_ENVIRONMENT_READER" ]] \
   && command -v tmux >/dev/null; then
  exec tmux -L vt new -A -s dev
fi
EOF
}

_print_bash_init() {
  cat <<'EOF'
# vt shell-init bash
case $- in *i*) ;; *) return ;; esac
if [ -t 0 ] && [ -t 1 ] && [ -z "$TMUX" ] \
   && [ -z "$INSIDE_EMACS$VSCODE_INJECTION$ZED_TERM" ] \
   && command -v tmux >/dev/null; then
  exec tmux -L vt new -A -s dev
fi
EOF
}

_print_fish_init() {
  cat <<'EOF'
# vt shell-init fish
if status is-interactive
  and not set -q TMUX
  and isatty stdin
  and isatty stdout
  and not set -q INSIDE_EMACS
  and not set -q VSCODE_INJECTION
  and not set -q ZED_TERM
  and command -v tmux >/dev/null
  exec tmux -L vt new -A -s dev
end
EOF
}

_print_pwsh_init() {
  cat <<'EOF'
# vt shell-init pwsh — WSL2 PowerShell용
# Windows Terminal profile에 직접 등록을 더 권장
if (-not $env:TMUX -and -not $env:INSIDE_EMACS) {
  if (Get-Command tmux -ErrorAction SilentlyContinue) {
    & tmux -L vt new -A -s dev
    exit
  }
}
EOF
}
```

**README 사용법**
```bash
# zsh
echo 'eval "$(vt shell-init zsh)"' >> ~/.zshrc

# bash
echo 'eval "$(vt shell-init bash)"' >> ~/.bashrc

# fish
vt shell-init fish >> ~/.config/fish/config.fish

# pwsh
vt shell-init pwsh >> $PROFILE
```

**점검 절차**

1. `vt shell-init zsh | sh -n` — syntax 통과
2. `bash -c "$(vt shell-init bash)"` — 무한 루프 없이 종료
3. p10k instant prompt 활성 zsh에서 통합 후 콘솔 출력 0건
4. SSH 원격에서 `eval "$(vt shell-init zsh)"` 시 정상 attach
5. VS Code 임베디드 터미널에서 가드가 작동해 tmux 미실행

---

### #6-4. `_ensure_tmux` 3단계 분리 (detached → optional populate → attach)

**목표:** 세션 생성과 attach를 분리하여 비-TTY 컨텍스트(CI·백그라운드)에서도 tcgetattr 경고 없이 동작.

**수정 파일**
- `bin/vt` — `_ensure_tmux` 재작성
- `bin/vt` — `_open_tmux_terminal_with()` 추가 (현 `_open_tmux_terminal` 일반화)

**핵심 구현**

```bash
# 1단계: detached 세션 보장 (TTY 불필요)
_ensure_tmux_session() {
  local name="${1:-dev}"
  if "${TMUX_BASE[@]}" has-session -t "$name" 2>/dev/null; then
    return 0
  fi
  "${TMUX_BASE[@]}" new-session -d -s "$name" -x 200 -y 50 2>/dev/null
}

# 2단계: 옵션 명령 주입 (TTY 불필요)
_tmux_populate() {
  local name="$1"; local cmd="$2"
  [ -z "$cmd" ] && return 0
  "${TMUX_BASE[@]}" send-keys -t "$name" "$cmd" C-m 2>/dev/null
}

# 3단계: attach (호출자가 TTY 컨텍스트 보장)
_tmux_attach_or_switch() {
  local name="${1:-dev}"
  if [ -n "${TMUX:-}" ]; then
    "${TMUX_BASE[@]}" switch-client -t "$name"
  else
    exec "${TMUX_BASE[@]}" attach-session -t "$name"
  fi
}

# 기존 _ensure_tmux는 wrapper로 유지
_ensure_tmux() {
  _ensure_tmux_session dev
  echo "✓ tmux 세션 'dev' 준비됨 (소켓: $VT_TMUX_SOCKET)"
}
```

**점검 절차**

1. `vt voice` / `vt mobile` — 정상 동작 (회귀 없음)
2. CI 환경(`CI=1`, TTY 없음)에서 `bin/vt status` 실행 시 경고 없음
3. tmux 안에서 `vt agent claude` 실행 → switch-client로 동작
4. tmux 밖에서 같은 명령 → exec attach
5. 세션 이미 존재하는 상태에서 재호출 → no-op

---

## 4. 구현 순서

의존성 + 위험도 기반.

```
Step 1: #6-1  voice_daemon -L vt 적용 (5분, 회귀 위험 낮음)
Step 2: #6-4  _ensure_tmux 3단계 분리 (15분)
Step 3: #6-3  vt shell-init 명령 (20분)
Step 4: #6-2  vt install-profiles 명령 (40분, 가장 큼)
Step 5: 문서화 (README, CLAUDE.md, ARCHITECTURE.md)
Step 6: 회귀 테스트 + 서브 에이전트 검증
```

---

## 5. 회귀 테스트 체크리스트

각 Step 후 실행.

```bash
# 기본 명령 회귀
vt help                       # 모든 명령 표시
vt doctor                     # 13개 항목 + 신규 항목 통과
vt status                     # 격리 소켓에 vt 세션 표시

# 신규 명령
vt install-profiles --dry-run  # 변경 미리보기
vt install-profiles            # 실제 등록
vt shell-init zsh              # 스니펫 출력
vt shell-init bash             # 스니펫 출력
vt shell-init fish             # 스니펫 출력

# 격리 소켓 통일성
ps aux | grep tmux | grep -v grep             # 서버 1개만
tmux ls                                        # 기본 소켓 (사용자 기존 세션만)
tmux -L vt ls                                  # vt 소켓 (dev 세션)
ps -p $(pgrep -f voice_daemon) -o command      # voice_daemon이 -L vt 호출 가능한지

# Voice Daemon 통합
# Ctrl+Shift+V → "테스트" → tmux -L vt 세션에 입력 + 모바일 즉시 반영
```

### 환경별 검증
- [ ] macOS + iTerm2: profile 등록 후 새 창 → 자동 tmux 진입
- [ ] macOS + Ghostty: config 활성화 후 새 창 검증
- [ ] macOS + Terminal.app: 가이드 출력 정확
- [ ] WSL2 + Windows Terminal: profile 패치 후 동작
- [ ] Linux + Alacritty: shell.program snippet 검증
- [ ] zsh + p10k: instant prompt 활성 상태에서 콘솔 출력 0건
- [ ] bash: `eval "$(vt shell-init bash)"` 통합 OK
- [ ] fish: config.fish 통합 OK
- [ ] SSH 원격: shell-init 폴백 동작

---

## 6. 문서화 (Step 5)

### README.md 추가 섹션

**"## 설치 후 통합"**
```markdown
설치 후 다음 중 하나를 선택:

### 방식 A — 터미널 profile 자동 등록 (권장)

`vt install-profiles`

이걸로 끝. 새 터미널 창을 열면 자동으로 vt 세션 진입.

### 방식 B — 셸 init (SSH 원격, profile 불가 환경)

```bash
echo 'eval "$(vt shell-init zsh)"' >> ~/.zshrc   # zsh
echo 'eval "$(vt shell-init bash)"' >> ~/.bashrc # bash
vt shell-init fish >> ~/.config/fish/config.fish # fish
vt shell-init pwsh >> $PROFILE                    # PowerShell
```
```

### CLAUDE.md 갱신
- 기존 `.zshrc` 스니펫 가이드 → `vt shell-init` 권장으로 교체

### ARCHITECTURE.md 갱신
- "단일 tmux 서버 원칙" 명시
- 클라이언트 매트릭스 (4개 모두 `-L vt` 사용)

---

## 7. 진행 상황 추적

| Step | 항목 | 상태 |
|------|------|------|
| 1 | #6-1 voice_daemon `-L vt` 통일 | ☑ |
| 2 | #6-4 `_ensure_tmux` 3단계 분리 | ☑ |
| 3 | #6-3 `vt shell-init` 명령 | ☑ |
| 4 | #6-2 `vt install-profiles` 명령 | ☑ |
| 5 | 문서화 (README/CLAUDE/ARCHITECTURE) | ☑ |
| 6 | 회귀 테스트 + 서브 에이전트 검증 | ☑ (100%) |

---

## 8. 위험 요소 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| 사용자 기존 `~/.zshrc`·terminal config 손상 | 높음 | R7(멱등성) + R8(옵트인) + dry-run 옵션 + backup |
| iTerm Dynamic Profile guid 충돌 | 낮음 | 고유한 guid `vt-voice-terminal` 사용 |
| Voice Daemon 변경으로 기존 사용자 음성 입력 끊김 | 중간 | Step 1 후 즉시 수동 검증, 문제 시 즉시 rollback |
| `exec tmux`가 셸 종료 시 의도치 않은 동작 | 중간 | tmux 종료 후 zsh 재진입 가능하도록 `tmux ; exec zsh` 옵션 안내 |
| 셸 init 무한 루프 ($TMUX 가드 누락 시) | 높음 | R1 가드 5중 결합 필수, 테스트 시나리오에 명시 |

---

## 9. 완료 정의 (Definition of Done)

- [ ] 4개 Step 모두 구현 + 커밋 + 푸시
- [ ] 회귀 테스트 체크리스트 모두 ✅
- [ ] 환경별 검증 9개 항목 모두 ✅
- [ ] README · CLAUDE.md · ARCHITECTURE.md 갱신
- [ ] 서브 에이전트 최종 검증 점수 ≥ 95%
- [ ] PHASE6_PLAN.md 진행 상황 표 ☐ → ☑
