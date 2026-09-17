> **FarShell v2.1.5** (2026-09-16) — 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고

> **다른 에이전트:** 이 문서는 Claude 전용이 아니라 모든 AI 코딩 에이전트를 위한 프로젝트
> 개요다. CLI 공통 규칙과 깨면 안 되는 계약은 [`AGENTS.md`](./AGENTS.md)에 있다
> ([agents.md](https://agents.md) 규약 — Codex·opencode·Amp·Cursor 등이 읽는다).
> `GEMINI.md`와 `.aider.conf.yml`도 그쪽을 가리킨다.

## fsh CLI (어디서든 실행)

터미널 어디서든 `fsh` 명령으로 FarShell을 제어합니다:

```bash
fsh start [--voice]    # 전체 시작 (서버+터널, --voice로 음성 데몬도 함께)
fsh stop [--purge]     # 종료 (--purge: tmux 세션까지 완전 종료)
fsh status             # 현재 상태 확인
fsh mobile [--e2e]     # 모바일 접속 URL + QR (--e2e: 페이로드 암호화)
fsh manage             # TUI 관리 도구 (세션/타깃/핫키/상태) — Wave 4
fsh attach [name]      # 임의 tmux 세션을 새 창에 attach
fsh voice              # 음성 모드 (백그라운드, 노션 작업 중에도 사용)
fsh voice-target [name|--auto]  # 음성 daemon 타깃 lock/해제
fsh clip               # 클립보드 동기화 데몬 (맥 클립보드 변경 → 웹, OSC52 보완)
fsh queue [list|add "내용" [세션]|run|rm <id>|unblock <id>|clear]  # 프롬프트 큐 (P4)
fsh files [ls|add <path> [--share ttl] [--pin]|rm <id>|share <id> [--ttl] [--pin] [--once]|unshare <id>|insert <id>]  # 파일 저장소 + 공유 링크 (N19/N23)
fsh worktree [list|add <이름> [--base b] [--ports] [--copy-modules] [--agent claude]|rm <이름> [--force]|open <이름>]  # git 워크트리 (N8/N44)
fsh git-account [list|add --provider github|gitlab [--host H] --token-stdin|rm <id>|bind <repo> <id>]  # git 계정 저장소 (N30 — 구현만 됐고 미사용, ADR-27)
fsh hotkey [list|set|reset|disable]  # 핫키 조회/변경
fsh hooks [status|install|uninstall]  # Claude Code 훅 등록(상태 배지·큐·TTS의 전제)
fsh pane report [--state ...] [--agent ...]  # 이 pane의 상태 보고(훅 없는 에이전트용)
fsh clauth [status|which]  # 사용량 조회(읽기 전용, clauth 미설치면 숨김)
fsh usage [list|add --model <이름> --tokens <N> --seconds <N>]  # 누적형 사용량 기록/조회 (로컬 LLM 등, 한도 없음)
fsh password [clear]   # 웹 로그인 비밀번호 설정(해시 저장) / clear=해제
fsh otp [status|setup|disable]   # 새 기기 등록 시 OTP 요구 (setup 전까지 완전 비활성)
fsh device [list|rename <id> <별명>|revoke <id>]  # 등록된 기기 조회 / 이름 변경 / 폐기(폰 분실 시 세션까지 함께 무효)
fsh help <topic>       # concepts/voice/hotkeys/target/troubleshoot
fsh claude             # 새 터미널 창에 tmux dev + claude --resume (내부적으로 fsh agent claude)
fsh agent <name>       # claude/codex/aider/gemini 등 임의 에이전트로 시작 (fsh claude의 일반화)
fsh template [save|apply|list|rm] <name>  # CLAUDE.md 템플릿 저장/적용 관리
fsh popup <action>     # tmux 3.2+ popup으로 fsh 명령 빠른 호출
fsh run "..."          # headless `claude -p` 백그라운드 실행 + 완료 시 TTS 알림
fsh handoff mobile     # 현재 tmux 세션을 폰으로 넘김 (QR + #tmux=)
fsh handoff desktop    # 폰 세션을 맥 터미널로 가져옴
fsh tunnel expose 3000 "앱 이름"  # 다른 로컬 포트를 별도 Cloudflare 터널로 공개
fsh tunnel unexpose 3000          # 해당 포트 터널 종료
fsh tunnel list                   # 열려 있는 터널 전부 (메인 + 추가 포트)
fsh tunnel hook                   # URL 변경 훅 확인 + 즉시 실행 (fsh help tunnel-hook)
fsh tunnel restart                # 좀비 재연결(응답 없음) 상태여도 강제로 새 터널 기동 + 훅 재실행
fsh tunnel watchdog               # 좀비 재연결 자동 감지 데몬 상태 확인/시작 (평소엔 fsh start/voice/mobile가 자동 기동)
fsh ssh [session]      # Tailscale + SSH로 tmux 세션 직접 접속 명령 안내 (D9, 회사망 등)
fsh doctor             # 설치/환경 진단 (Linux 항목 포함)
fsh install-profiles   # 터미널 앱 profile 자동 등록 (iTerm2 Dynamic Profile + 기타 snippet)
fsh shell-init zsh     # 셸 init 스니펫 출력 (eval "$(fsh shell-init zsh)" >> ~/.zshrc)
```

> **지원 OS**: macOS / Linux (X11) / WSL2 (Linux로 동작). Windows 네이티브는 미지원.

**Phase 6 — 단일 tmux 서버 원칙:** fsh CLI · server · Voice Daemon · hook이 모두 `-L vt` 격리 소켓 사용(소켓 이름은 CLI 이름과 무관하게 `vt`로 유지). Voice Daemon은 `VT_TMUX_SOCKET` 환경변수로 오버라이드 가능. 사용자 기존 `tmux ls`와 분리됨.

**`voice` / `mobile` / `start` 실행 시 자동 동작:** 현재 쓰는 터미널 앱(iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app)에 새 창이 열리고 그 안에서 `tmux new -A -s dev 'claude --resume'`이 실행됩니다. 이미 tmux 안이면 새 창을 열지 않습니다.

**노션 작업 중 음성 코딩 워크플로:**
1. `fsh voice` → 백그라운드 시작 (+ 새 iTerm 창에 `tmux dev` + `claude --resume` 자동 오픈)
2. 새 창의 resume 목록에서 현재 대화 선택 → 이후 음성/모바일이 그 Claude로 연결됨
3. 원래 창은 그대로 두고 노션으로 돌아가서 작업
4. Ctrl+Shift+V → 말하기 ("git status") → tmux dev에 자동 입력
5. `fsh stop` → 종료

> 이미 tmux 안에서 `fsh` 명령을 부르면 새 창을 열지 않습니다 (`$TMUX` 체크).
> 자동 오픈은 macOS + iTerm 환경 한정. 그 외에는 수동 명령(`tmux new -A -s dev 'claude --resume'`) 안내가 출력됩니다.

### Claude 전역 스킬

| 커맨드 | 설명 |
|--------|------|
| `/fsh` | 전역 스킬(구 `/vt`). 어디서든 "음성 모드", "모바일 접속" 등으로 호출 |

### 프로젝트 스킬

| 커맨드 | 설명 |
|--------|------|
| `/fsh-start` | 서버 시작 + tmux 준비 + Cloudflare Tunnel 원격 접속 |
| `/fsh-mobile` | 모바일 테스트 (adb 포트포워딩, Chrome 열기, 스크린샷) |
| `/fsh-voice` | Voice Daemon 설치/실행 (핫키 → STT → tmux 주입) |

### 신규 사용자 설치

**기본 경로는 `./install.sh`** (원라인 설치, 2026-04-14 추가). 아래는 대화형 안내가 필요한 경우에만 사용.

```bash
# 원라인 설치 (추천)
./install.sh            # 터미널만 (~50MB)
./install.sh voice      # 터미널 + 음성 모드 (~1.5GB)
```

`install.sh`가 자동으로: Python venv 생성 → 프로필별 패키지 설치 → fsh CLI 심링크 → `~/.vt.env` 생성 → PATH 갱신.

---

### 레거시: 대화형 설치 (수동)

install.sh가 작동하지 않거나 conda/pyenv 등 다른 환경을 선호하는 경우에만 아래 절차를 따르세요.

> **Python 환경 관리:** 모든 실행 관련 경로/포트는 `~/.vt.env`(사용자 로컬, gitignored)와 `config/vt.defaults.env`(커밋된 기본값)로 관리됩니다. 사용자에게 환경을 묻는 단계에서 venv/conda/pyenv/시스템 Python 중 선택하게 한 뒤 결과를 `~/.vt.env`의 `VT_PYTHON`에 기록하세요.

#### Step 1: OS 감지

```bash
uname -s  # Darwin=macOS, Linux=Linux/WSL2
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL2" || echo "Native"
```

사용자에게 확인: "macOS / WSL2 / Linux 환경이 맞나요?"

#### Step 2: 설치 구성 선택

사용자에게 물어보세요:

> 어떤 기능을 설치할까요?
>
> 1. **터미널만** — 모바일에서 터미널 접속 (~500MB)
>    - FastAPI 서버 + xterm.js 웹 터미널 + Cloudflare Tunnel
>    - 음성 기능 없음
>
> 2. **터미널 + 음성 모드** — 음성으로 코딩 (~3GB)
>    - 위 기능 + Whisper STT + edge-tts TTS + Voice Daemon
>    - macOS 핫키(Ctrl+Shift+V), 모바일 음성 입력

#### Step 3: Python 환경 준비

사용자에게 어떤 환경을 사용할지 물어보세요 (venv / conda / pyenv / 시스템 Python). 결과를 Step 6의 `VT_PYTHON`에 기록합니다.

**기본 권장 — venv:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**conda 선호 시:**
```bash
conda create -n fsh python=3.11 -y && conda activate fsh
```

**pyenv 선호 시:**
```bash
pyenv install 3.11.7 && pyenv local 3.11.7
```

#### Step 4: 패키지 설치 (프로필별)

**터미널만 (옵션 1):**
```bash
pip install -r requirements-core.txt
```

**터미널 + 음성 (옵션 2):**
```bash
pip install -r requirements-core.txt -r requirements-voice.txt
```

macOS 음성 모드 추가:
```bash
pip install pyobjc-framework-Cocoa
```

#### Step 5: fsh CLI 등록

```bash
mkdir -p ~/.local/bin
chmod +x bin/fsh
ln -sf "$(pwd)/bin/fsh" ~/.local/bin/fsh
```

PATH 확인:
```bash
echo "$PATH" | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

#### Step 6: 설정 파일 생성 (`~/.vt.env`, gitignored)

Step 3에서 선택한 Python 경로를 기록합니다. 모든 키 목록은 `config/vt.defaults.env` 참고.

```bash
# Step 3에서 만든 환경의 python 절대 경로를 사용 (예시)
PY_PATH="$(pwd)/.venv/bin/python"   # venv의 경우
# PY_PATH="$(conda info --base)/envs/vt/bin/python"   # conda 사용 시
# PY_PATH="$(pyenv which python)"                       # pyenv 사용 시

cat > ~/.vt.env << EOF
VT_PORT=7777
VT_PYTHON=$PY_PATH
# VT_TOKEN=my-secret-token  # 원격 접속 시 인증 (선택)
EOF
```

#### Step 7: cloudflared 설치 (모바일 원격 접속용)

```bash
# macOS
brew install cloudflared

# Linux/WSL2
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared
```

#### Step 8: Claude Code 스킬 등록 (선택)

```bash
mkdir -p ~/.claude/skills/fsh
cp .claude/skills/fsh/SKILL.md ~/.claude/skills/fsh/SKILL.md 2>/dev/null || true
```

#### Step 9: 설치 확인

```bash
fsh status
```

사용자에게 안내:
- `fsh mobile` — 모바일 접속 (QR코드)
- `fsh voice` — 음성 모드 (옵션 2 선택 시)
- `fsh stop` — 종료

#### 플랫폼별 참고

**macOS:** 음성 모드 시 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
**WSL2:** 음성 핫키는 WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용. PowerShell: `.\bin\fsh.ps1 voice`

---

## FarShell 프로젝트 가이드

### 서버 실행

```bash
# 방법 1: 스크립트 (~/.vt.env의 VT_PYTHON 자동 사용)
./run_server.sh

# 방법 2: 직접 실행
cd server
"$VT_PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 7777
```

- Python 경로는 환경별로 다름 — `fsh doctor`로 현재 감지된 값 확인
- 패키지: `requirements-core.txt`(필수) + `requirements-voice.txt`(음성 모드)

### 접속

| 환경 | URL |
|------|-----|
| 데스크톱 | `http://localhost:7777` |
| 같은 네트워크 모바일 | `http://맥북-IP:7777` (IP는 `ipconfig getifaddr en0`) |
| adb 연결 모바일 | `adb reverse tcp:7777 tcp:7777` → `http://localhost:7777` |
| 원격 (어디서든) | `cloudflared tunnel --url http://localhost:7777` → 생성된 HTTPS URL 사용 |

### 모바일 테스트 (adb)

```bash
# 1. 포트 포워딩
adb reverse tcp:7777 tcp:7777

# 2. Chrome 열기
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome

# 3. 스크린샷 캡처
adb shell screencap -p /sdcard/test.png && adb pull /sdcard/test.png /tmp/test.png

# 4. 화면 켜기 (잠김 상태)
adb shell input keyevent KEYCODE_WAKEUP && adb shell input swipe 540 2000 540 1000 300
```

### API 엔드포인트

전체 REST/WebSocket 레퍼런스는 **[API.md](./API.md)** 참고 — 표를 여기 CLAUDE.md에도
따로 유지하면 한쪽만 갱신될 때마다 드리프트가 생겨서(2026-08-20에 실제로 발견·정정함),
카테고리 목록만 두고 상세는 API.md 하나로 일원화했다.

| 카테고리 | 대표 경로 |
|----------|-----------|
| 세션 / PTY | `/api/sessions`, `/ws/{id}` |
| tmux | `/api/tmux/*` (sessions·attach·create·kill·open-on-mac·preview) |
| 음성 | `/voice/input`, `/voice/output`, `/voice/cancel`, `/voice/local/*`, `/voice/stt/*` |
| 인증 | `/api/auth`, `/api/auth/status`, `/api/auth/logout` |
| 코드 뷰어 / diff / Git 액션 | `/api/fs/*`, `/api/git/status`·`diff`·`stage`·`unstage`·`commit` (D16) |
| 프롬프트 큐 | `/api/queue*` (P4) |
| 포트 대시보드 | `/api/ports*` (P3) |
| Web Push | `/api/push/*` (P5) |
| 에이전트 상태 / 알림 / 진단 | `/api/agent*`, `/api/notify/*`, `/api/safe-mode`, `/api/tailscale/status`, `/api/tunnel/status` |
| 워크스페이스 / 기타 | `/api/workspace`, `/api/capabilities`, `/api/upload`, `/api/download`, `/api/clipboard/push` |
| WebSocket | `/ws/{id}`, `/ws-notify`, `/ws-preview/{name}`, `/ws-agent`, `/ws-workspace` |

### E2E 테스트 방법

```bash
# 1. 세션 생성
SID=$(curl -s -X POST http://localhost:7777/api/sessions -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. WebSocket으로 명령 실행 (Python)
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect(f'ws://localhost:7777/ws/$SID') as ws:
        await ws.send(b'echo hello\n')
        for _ in range(5):
            try:
                d = await asyncio.wait_for(ws.recv(), timeout=1)
                if b'hello' in d: print('OK'); break
            except: break
asyncio.run(t())
"

# 3. TTS 테스트
curl -s -X POST http://localhost:7777/voice/output \
  -H 'Content-Type: application/json' \
  -d '{"text":"테스트"}' -o /tmp/tts.mp3 -w "bytes: %{size_download}"

# 4. 좀비 프로세스 확인
curl -s -X DELETE "http://localhost:7777/api/sessions/$SID"
ps aux | grep defunct | grep -v grep || echo "No zombies"

# 5. 파일 업로드 테스트
echo "hello" > /tmp/test_upload.txt
curl -s -X POST "http://localhost:7777/api/upload?session_id=$SID" \
  -F "file=@/tmp/test_upload.txt"

# 6. 세션 이름 변경 테스트
curl -s -X PATCH "http://localhost:7777/api/sessions/$SID" \
  -H 'Content-Type: application/json' -d '{"name":"my-session"}'

# 7. Scrollback 테스트 — 브라우저 새로고침 후 이전 출력이 보이는지 확인
```

### Claude Code 훅 (에이전트 상태 + TTS 자동 요약)

`server/agent_hook.sh`가 훅 3종의 단일 진입점이다: `{pre,post,stop}`을
`POST /api/agent/event`로 보내고(에이전트 상태 배지, 프롬프트 큐 자동 투입),
`stop`일 때는 stdin을 `tts_hook.sh`에 위임해 TTS 요약을 재생한다.

등록은 `fsh hooks install`(멱등, 다른 훅 보존, `settings.json` 백업 —
`./install.sh`가 설치 시 대신 실행한다). `fsh hooks status`와 `fsh doctor`가
3종 등록 여부를 알려준다 — 등록이 없으면 서버는 이벤트를 한 건도 못 받는데
아무 것도 눈에 띄게 실패하지 않는다.
**등록할 것은 `agent_hook.sh stop`이지 `tts_hook.sh`가 아니다** — 둘 다
등록하면 TTS 요약이 두 번 재생된다.

- 스크립트: `server/tts_hook.sh` (TTS 부분. `agent_hook.sh stop`이 호출한다)
- 설정: `~/.claude/settings.json`의 `hooks.PreToolUse` / `hooks.PostToolUse` / `hooks.Stop`
- 동작: transcript에서 마지막 assistant 응답(최대 200자) 추출 → 서버 TTS → `afplay` 재생
- fallback: 서버 미실행 시 macOS `say -v Yuna` 사용

```bash
# hook 테스트 (서버 실행 중)
echo '{"transcript_path":"/tmp/test_transcript.jsonl"}' | ./server/tts_hook.sh
```

### Voice Daemon (macOS 독립 음성 입력)

서버 없이 맥북에서 핫키로 음성 입력 → tmux에 직접 타이핑하는 데몬.

```bash
# 실행
"$VT_PYTHON" server/voice_daemon.py &

# 사용: Ctrl+Shift+V (토글) → 말하기 → STT → 활성 tmux pane에 입력
# macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
```

### Clipboard Daemon (macOS 클립보드 동기화)

원격/모바일에서 웹 터미널에 접속하면 브라우저는 "그 기기"의 클립보드에만 접근할 수
있어, 맥북(서버) 쪽에서 복사한 게 자동으로 넘어오지 않는다. 두 경로로 보완:

- **OSC52** (별도 실행 불필요) — `vim`, `tmux copy-mode` 등 터미널 프로그램 안에서
  일어난 복사는 PTY 출력 스트림에 이미 실려 오므로, `frontend/js/terminal.js`가
  `term.parser.registerOscHandler(52, ...)`로 가로채 웹이 열린 기기의 클립보드에 반영.
- **폴링 데몬** (`fsh clip`) — Safari/Finder 등 터미널 밖에서 일어난 복사는 OSC52로
  못 잡으므로, `server/clipboard_daemon.py`가 `NSPasteboard.changeCount`를 폴링해
  변경 시 `POST /api/clipboard/push` → `/ws-notify` 브로드캐스트로 웹에 전달.

```bash
# 실행 (또는 fsh clip)
"$VT_PYTHON" server/clipboard_daemon.py &
```

### tmux 중심 세션 관리

웹 UI는 tmux 세션을 기본으로 사용한다:
- 시작 시 tmux 세션 자동 감지 → 첫 번째 세션에 attach
- "+ New" → tmux 세션 생성 (`POST /api/tmux/create`)
- 탭 닫기 → detach만 (tmux 세션 유지). Kill은 `DELETE /api/tmux/kill/{name}`
- 중복 attach 방지: 이미 웹에 열린 tmux 세션은 기존 탭으로 전환
- iTerm2와 웹이 같은 tmux 세션에 동시 접속 가능

### 주요 기능

| 기능 | 설명 |
|------|------|
| Voice Daemon | macOS 핫키(Ctrl+Shift+V) → STT → tmux 직접 입력 |
| Clipboard 동기화 | OSC52(터미널 내부 복사) + `fsh clip` 폴링 데몬(터미널 밖 복사) → 웹 클립보드 push |
| 붙여넣기 (N24·N25·N26·N29, 2.1.5) | **브라우저가 아니라 서버가 정한다.** 브라우저는 pane 안쪽 앱이 괄호 붙여넣기를 켰는지 알 수 없어서, 추측하면 엉뚱한 곳에 `200~`가 그대로 찍히거나 반대로 자동 들여쓰기가 계단처럼 망가졌다. 이제 서버가 pane의 실제 입력 모드를 읽어(termios의 `ICANON`, 출력 스트림에서 본 DECSET 2004) 결정한다. 경로는 WS `paste` 메시지와 `POST /api/sessions/{id}/paste`. **tmux 세션은 tmux 자신에게 위임한다**(`paste-buffer -p`가 pane의 실제 모드를 본다. 3.7+에서는 `-S`로 tmux 쪽 정제도 끈다 — 우리가 이미 정제했다) — 우리가 한 번 더 추측하지 않는다. 제어문자는 iTerm2 규칙으로 화이트리스트(tab·LF·FF·CR·`^V`)하고, ESC는 괄호일 때만 `␛`로 무력화하며 아니면 그냥 제거한다. 정규 모드에서 `PC_MAX_CANON`(맥 1024B)을 넘는 줄은 잘리는 게 아니라 **통째로 증발한다**(실제 PTY로 확인) — 이제 보내기 전에 이유와 함께 거절한다. `fsh doctor paste`가 지금 이 터미널이 실제로 무엇을 보내는지 보여준다 |
| 핸즈프리 모드 | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| 음성 전용 모드 | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 조작용) |
| 웹 로그인 비밀번호 | `fsh password`로 설정 → scrypt 해시(`VT_AUTH_PASSWORD_HASH`)만 저장, 원문 미저장. 로그인 시 `VT_AUTH_SESSION_KEY`로 서명된 24h 세션 쿠키 발급(원문·토큰 아님). 사람용 인증. `server/auth/` |
| 기기 등록 + OTP 관문 | 로그인은 **항상 비밀번호**. OTP는 "처음 보는 기기를 등록할 때"만 요구하는 관문이다. 등록된 기기는 `vt_device` 장기 쿠키(90일)를 갖고 이후 비밀번호만으로 통과 — IP가 아니라 기기 단위라 폰이 LTE↔wifi를 오가도 안 끊긴다. **`fsh otp setup` 전까지 OTP는 완전 비활성**이고 기기 등록만 조용히 쌓이므로, 나중에 켜도 쓰던 기기는 잠기지 않는다. 저장은 `~/.vt/devices.json`(0600, sha256 해시만). `fsh device revoke <id>`로 폐기하면 그 기기의 세션 쿠키까지 즉시 무효 |
| 1회용 기기 등록 티켓 | `fsh mobile`/`fsh handoff`의 QR·URL에 상시 토큰 대신 5분짜리 1회용 티켓(`?ticket=`)을 싣는다. QR을 띄우는 시점에 맥 물리 접근이 이미 증명되므로 스캔=등록 승인. 상시 토큰을 URL에 박던 방식은 그 값이 로그·히스토리·QR 이미지에 영구히 남았다 |
| 크로스 사이트 차단 | `OriginGuardMiddleware`(`server/main.py`) — Origin이 자기 자신이 아니면 HTTP·WS 모두 403. 인증·OTP로는 막을 수 없는 유일한 경로(브라우저에 이미 쿠키가 있으면 인증은 통과한다). CORS 기본 `*`도 제거 — 필요 시 `VT_ALLOWED_ORIGINS`로 옵트인 |
| API 토큰 인증 | `VT_AUTH_TOKEN` 환경변수 = 기계용 토큰(데몬·훅·TUI). URL `?token=xxx` 또는 `Authorization: Bearer xxx`. **둘째 비밀번호가 아니다 — 로그인 폼은 이 값을 거부한다(2026-09-17).** 예전엔 받아줬고, 그래서 `fsh password`로 바꿀 수도 만료시킬 수도 없는 사람용 자격증명이 하나 더 있었다("비밀번호를 바꿨는데 예전 값으로 계속 들어가진다"의 정체). 그 결과 레거시 `?token=` 링크는 더 이상 세션 쿠키로 교환되지 않는다(쿼리 파라미터로는 그대로 동작). 기기 등록은 `fsh mobile`의 1회용 티켓을 쓴다. (구 이름 `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY`도 fallback 인식) |
| tmux 세션 관리 | 웹에서 tmux 생성/attach/detach/kill |
| Scrollback 버퍼 | WS 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 스크롤백 영속화 (N13) | 옵트인, 기본 OFF — 설정 → 「스크롤백」의 `scrollback.persist` 토글. 켜면 PTY **출력만**(입력은 절대 아님 — 호출 지점이 `write()`가 아니라 `pty_manager._flush_session`) `~/.vt/scrollback/<session>.log`(0600)에 이어붙인다. 20MB마다 회전(`.1` 세대 1개만 유지), 주기 정리로 7일 보관. 이 설정과 무관하게 재접속 시 클라이언트는 여전히 최근 256KB만 받는다 — 영속본은 `GET /api/sessions/{id}/scrollback?before=`("더 불러오기", base64, 최신→과거 페이지네이션)로 따로 읽는다. `server/scrollback_persist.py`. 2.1.2부터 팔레트 `~` 검색도 이 로그를 읽는다 — 세션당 소스는 하나(로그가 있으면 로그, 없으면 링버퍼, 둘 다는 아님)라 **끝난 세션·서버 재시작 이전의 출력**까지 찾힌다. 로그는 꼬리에서 4MB까지만 훑는다. |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js search addon |
| 세션 이름 편집 | 레일 세션 행의 ✎ 또는 모바일 세션 시트 → 이름 변경 (PATCH API, tmux 세션명도 `rename-session`으로 함께 바뀐다 — 영숫자·dash·underscore가 아니면 tmux는 안 건드리고 웹 라벨만 바뀐다). 2.1.4에서 세션 탭 줄이 사라져 「탭 더블클릭」 경로는 없다 — 이름의 단일 출처는 세션 레코드다(`core/store.js`의 `sessionDisplayName`) |
| 분할 pane (2.0) | 터미널 영역이 pane 이진 트리다. pane 헤더 버튼이나 **레일 세션 행**을 pane 가장자리에 드롭(5구역)해 분할하고(2.1.4 전에는 세션 탭이 드래그 소스였다), 구분선은 드래그로 조절한다. 폭 구간별 상한(compact 2 / regular 4 / wide 6)을 넘으면 분할 버튼이 **비활성 + 이유가 툴팁**에 뜬다(조용히 아무 일도 안 일어나지 않는다). <720px 터치 기기에서는 같은 트리를 한 번에 한 pane씩 그리고 좌우 스와이프로 넘긴다. 레이아웃은 `/api/workspace`에 저장돼 복원되고, 세션이 죽은 leaf는 유령이 아니라 빈 pane으로 강등된다 |
| 좌측 rail + 커맨드 팔레트 (2.0) | ⋯ 메뉴는 사라졌다. 좌측 rail(세션/파일/큐/포트/사용량/설정)이 포인터 경로, `Mod+K`가 키보드 경로이고 **둘의 내용이 같다** — 하나만 익혀도 된다. 팔레트는 각 명령의 현재 키 바인딩을 키맵 레지스트리에서 읽어 표시한다 |
| 에이전트 상태 (2.0) | 서버가 세션별로 `idle`/`working`/`waiting`/`done` 하나를 판정하고 나머지는 그걸 표시만 한다 — 워크트리 탭·pane 헤더·rail 목록(개입이 필요한 것이 맨 위로 정렬)·파비콘·앱 아이콘 배지(`waiting` 수). Claude Code 훅이 전제다: `fsh hooks install`(빠져 있으면 `fsh doctor`와 설정 → 정보가 알려준다) |
| 승인 대기 감지 (`waiting`) | PTY 출력에서 `server/detect/*.toml`의 패턴으로 감지한다. exit 패턴·그 pane에 대한 사용자 입력·다음 훅 이벤트·2분 TTL 중 아무거나로 풀린다. **프롬프트 큐는 `waiting`인 pane에 절대 투입하지 않는다** — 거기 `send-keys`를 하면 큐 텍스트가 승인 답변으로 소비된다 |
| 감지 커버리지 (2.1.1) | 설정 → 에이전트 → "감지 커버리지"(`GET /api/agents/coverage`). 패턴이 없는 CLI는 승인 대기 감지가 **조용히 아무것도 안 하는데** 그걸 확인할 방법이 없었다 — 이 표가 CLI별로 드러낸다: `path`(`hook`/`pty`/`none`) · toml 줄 수 · 표현 가능한 상태 · `trust`(`high`/`mid`/`low`). `hook`/`high`에 도달할 수 있는 건 `claude`뿐이고, 그것도 `fsh hooks install`로 세 이벤트가 전부 등록됐을 때만이다. codex/gemini/aider는 추측으로 채우지 않고 각 프로젝트의 **공식 저장소 소스 코드**에서 리터럴을 확인해 채웠다 — 출처와 upstream 커밋 해시는 각 `server/detect/*.toml` 맨 위 주석에 있다 — 그래서 `pty`/`mid`다: 소스를 읽은 것이지 실제 터미널 렌더(ANSI·줄 분할)를 검증한 게 아니다. `aider`는 상시 표시되는 상태줄이 없어 안전한 `exit` 리터럴을 못 찾아 **exit이 비어 있다** — 사용자 입력·다음 훅 이벤트·TTL이라는 나머지 해제 경로로 풀린다. `enter`/`exit`는 정규식이 아니라 2048바이트 윈도에 대한 **리터럴 바이트 문자열** 매칭이고, 정규식은 `options` 하나뿐이다. 배경과 패턴 고치는 법: `docs/help/agent-detect.md` |
| 사용량 게이지 (2.0 · Codex는 2.1.5) | 소스 둘을 한 목록으로 합친다. **clauth**는 `~/.clauth/status.json`을 읽는다(CLI가 아니라 파일). 피드 schema는 **1·2를 모두** 받고(하나만 지원하면 clauth를 올릴 때마다 조용히 꺼진다), 모르는 버전은 조용히 끄되 그 사실이 설정 →「정보」에 보인다(2.1.3). wide에서는 우측 레일에 상시, 그 외에는 패널로. **소스가 없으면 통째로 사라진다** — `VT_USAGE_PROVIDER`(`auto`/`clauth`/`none`)로 제어하고, 토큰류는 필드 화이트리스트로 제외된다 **Codex(2.1.5)** 는 두 번째 한도형 provider다 — Codex CLI 자신이 쓰는 OAuth 사용량 API를 그대로 부르고, 토큰은 매 호출마다 `~/.codex/auth.json`에서 읽어 그 자리에서만 쓰고 저장하지 않는다. 원격 API라 60초 TTL 캐시를 쓰며 429·네트워크 오류에는 이전 값을 `stale`로 표시해 계속 보여준다(끄는 것보다 낫다). 응답은 들어올 때 화이트리스트로 거른다(`credits`·`model_usage`는 정규화 함수를 나가는 순간 존재하지 않는다). `panels/usage.js`는 손대지 않았다 — 프로필 카드가 이미 provider를 안 가린다 |
| MCP 서버 (97, 2.1.5) | 설정 → **MCP**. Claude Code·Codex·agy에 정의된 MCP 서버를 **전역 + 지금 보고 있는 워크트리** 기준으로 한 화면에 모아 켜고 끈다. 세 CLI 모두 스코프와 on/off를 공식 지원하지만 각자 "지금 이 프로젝트"만 알아서, 프로젝트를 오가며 쓰면 전체를 보는 화면이 없었다. **아무것도 저장하지 않는다** — 섹션을 열 때마다 실제 설정 파일을 읽고 토글하면 그 파일에 바로 쓴다. 그래서 터미널에서 직접 고친 것도 바로 보이고 "우리 기록 vs 실제 파일"이 어긋나는 상태가 없다. **값은 서버 밖으로 안 나간다** — `env`·`headers`는 키 이름과 "리터럴이냐 참조냐"까지만 내려간다(`~/.claude.json` 하나를 읽으면 다른 모든 서버의 키가 같이 올라오는데 이 UI는 터널 너머에 노출된다). 이 화면은 거짓말을 안 한다: 세 CLI 다 설정을 세션 시작 시 한 번만 읽으므로 "껐다"가 "지금 안 불린다"가 아니다 — 반영 시점을 항상 같이 보여주고, **확인 못 한 건 확인 못 했다고 쓰며**(agy의 반영 시점이 그렇다), 결과를 확인하지 못한 쓰기는 성공으로 그리지 않는다. 진단 둘이 덤으로 붙는다: 저장소에 커밋되는 `.mcp.json`의 평문 키, 그리고 Codex에서 확장되지 않는 `${VAR}` 표기(리터럴이 그대로 전달돼 조용히 깨진다). 토글은 **승격 세션이 필요하다** — 에이전트가 부를 수 있는 도구를 바꾸는 일이다. `GET /api/mcp`, `POST /api/mcp/toggle` |
| 설정 + 키맵 (2.0) | `Mod+,`. 설정은 `/api/workspace.settings`에 저장돼 폰에서 바꾼 값이 맥에도 반영된다. 키 바인딩은 재지정 가능하고 **`passthrough`로 키를 터미널에 돌려줄 수 있다** — `Mod+F`를 셸의 `forward-char`로 되찾는 경로가 이것이다. 「마우스 · 선택」에서 "앱에 마우스 이벤트 전달"을 끄면 vim/tmux가 마우스를 잡아도 드래그 선택이 항상 된다 |
| 연결된 화면 (2.0) | rail → 세션 → 「연결된 화면」. 그 tmux 세션에 붙은 클라이언트를 "나" 배지와 함께 보여주고, "이 화면만 남기기"로 나머지를 끊는다(맥 iTerm2 창 포함). tty는 보내지 않는다 — 서버가 web session id로 역산하고, 자기 자신은 끊지 못하게 막는다 |
| 코드 뷰어 / diff (P2) | ⋯ 메뉴 → "코드 뷰어". CLI만으로 원격 개발할 때 코드를 눈으로 못 보는 문제를 푼다. 파일 트리 · 문법 하이라이팅(highlight.js, 36개 언어) · `git diff` 렌더링. **읽기 전용이며 쓰기 API가 없다.** 공개 터널 너머로 열리므로 방어가 3중이다: ① 루트 확정(`VT_BROWSE_ROOTS`, 기본 `~/GitHub` — `$HOME`을 열면 `~/.ssh`·`~/.aws`가 사정권에 든다) ② `Path.resolve()` + `is_relative_to`(startswith 금지 — 형제 디렉토리가 통과한다. `resolve()`가 심링크를 펼치므로 루트 밖을 가리키는 링크도 함께 걸린다) ③ 거부 목록(`.env*`·`*.pem`·`id_rsa`·`.ssh/`·`.aws/` 등, 경로의 모든 구성요소를 검사). 판정은 `server/fsguard.py` 한 곳에만 있다. **2.1.3: 이미지는 인라인으로 그린다**(T3) — 바이트는 `GET /api/fs/raw`가 주고, 같은 열람 경계 위에 타입 화이트리스트를 하나 더 얹는다(png·jpeg·gif·webp·bmp·ico, 그것도 확장자와 매직 바이트가 둘 다 일치할 때만). **SVG·HTML·PDF는 뺐다** — 같은 오리진으로 나가는 스크립트 가능 문서라 그 자체가 XSS다 |
| 워크트리 (N8/N44, 2.1.1) | 좌측 레일, 또는 `fsh worktree`(`server/worktree.py`를 직접 호출하므로 서버가 안 떠 있어도 된다). 발견은 fsguard 루트를 깊이 3까지 훑되 `.git`을 만나면 거기서 멈추고, 저장소마다 `git worktree list --porcelain`을 읽어 ahead/behind · 변경 파일 수 · pane cwd가 그 안에 있는 tmux 세션 · 포트 대역을 붙인다. 5초 캐시, 모든 경로는 fsguard로 재검증한다. 생성 위치는 `~/.worktrees/<저장소>/<이름>`, 브랜치는 `feat/<이름>`으로 고정이고 선택 단계가 넷이다: node_modules(**기본 심링크** — 재설치 없이 바로 쓸 수 있다) · `.env`(`inherit` — **이미 있는** `PORT`/`VITE_PORT`/`DEV_PORT`/`NEXT_PUBLIC_PORT` 줄만 배정된 대역으로 고쳐 쓰고 없는 키는 절대 만들지 않는다. `.env` 내용은 API 응답에 실리지 않는다) · 포트 대역(5200부터 워크트리마다 +100, `~/.vt/worktrees.json` 0600 + flock에 기록) · `wt-<저장소>-<브랜치>` 이름의 detached tmux 세션에서 시작하는 에이전트. **`git worktree add` 이후 단계는 하나라도 실패하면 전부 롤백된다**(node_modules 제거 → `git worktree remove --force` + `git branch -D`) — 반쯤 만들어진 워크트리가 남지 않는다. 삭제는 메인 워크트리를 아예 거부하고, 커밋 안 된 변경이 있으면 `force:true` 없이는 409 `dirty:true`로 막는다. tmux 세션은 `killSessions:true`일 때만 죽이고, 브랜치는 지우지 않는다. 심링크한 node_modules는 `base`가 아니라 메인 저장소의 **현재** 트리를 비추므로, `GET /api/worktrees/precheck`가 양쪽의 package.json + 락파일을 해시로 비교해 생성 다이얼로그 전에 `lockfile_mismatch` 경고를 띄운다. `~/.worktrees`는 존재하면 fsguard 루트에 자동 추가된다 — 안 그러면 여기서 만든 워크트리가 전부 열람 경계 밖이라 자기 목록에서 조용히 사라진다 |
| dock 소스컨트롤 (2.1.1) | dock → "소스컨트롤". 2.1.0에서는 읽기 전용이었고, 이제 stage/unstage/commit이 `POST /api/git/{stage,unstage,commit}`에 연결됐다(D16). 파일 행에는 그 파일을 pane 리프로 여는 「뷰어」 버튼이 붙는다. **push·PR/MR은 의도적으로 없다 — 비활성 버튼도 아니고 아예 렌더링하지 않는다(ADR-27).** FarShell은 사용자 자신의 맥 터미널을 그대로 돌리는 도구고, push는 이미 거기서 하고 있으며, 계정을 여럿 쓰는 사람은 저장소별 git config(SSH `Host` 별칭, `git config user.email`)로 이미 해결해뒀다 — FarShell이 "어느 계정으로 쓸지"를 대신 정해줄 이유가 없다. 따라서 커밋은 **ambient git 자격증명**으로 나간다. 서버 쪽 방어는 fsguard 저장소 해석 · 파일마다 저장소 상대경로 검사 · 커밋 메시지 길이 상한 · 스테이지된 게 없으면 400, 이 넷이다 |
| git 계정 저장소 (N30/N31 — 구현만 됐고 미사용) | `~/.vt/git-accounts.json` + `~/.vt/git-bindings.json`(0600 + flock)과 15분짜리 **승격 세션**(`POST /api/auth/elevate`가 비밀번호+OTP를 재검증하고 쿠키에 `elev` 클레임을 얹어 재발급한다. 기기 스코프가 아니라 세션 스코프라 탭·기기마다 각자 승격한다). 계정 라우트는 핸들러마다 검사를 붙이지 않고 `APIRouter(dependencies=[Depends(require_elevated)])` 하나로 묶여 있어, 가드 없이 쓰기 경로를 추가하는 게 불가능하다. PAT만 받는다 — 공개 터널에서 콜백을 받는 설계가 더 위험해서 OAuth는 만들지 않았다. 토큰은 응답에 절대 실리지 않고 `auth.masked`만 나가며, `fsh git-account add`는 `--token-stdin`만 받는다(argv 금지). **이 층 전체가 지금 어디에도 연결돼 있지 않다(ADR-27)** — push/PR을 위해 만들었는데 그 기능을 접었고, 그 자체로 위험한 코드는 아니라서 되돌리지 않고 남겼다. 2.2에서 여러 계정을 넘나드는 워크트리 자동화 같은 용도가 생기면 다시 쓴다. 접근 경로는 `fsh git-account`뿐이고 UI에서 닿는 곳은 없다 |
| Web Push (P5) | ⋯ 메뉴 → "푸시 알림". 기존 알림(`/ws-notify` → Notification API)은 **PWA 탭이 살아 있어야만** 동작해서, 폰 화면을 끄면 "승인 대기 중"을 놓쳤다. 그 격차를 메운다. WS 클라이언트가 하나라도 붙어 있으면 푸시를 보내지 않는다(같은 알림이 두 번 온다). **성립 조건**: ① https — 평문 http에서는 Service Worker 자체가 등록되지 않는다 ② iOS는 홈 화면에 PWA로 추가해야 한다(16.4+, 사파리 탭에서는 구독이 안 만들어진다. 우회 불가). **구독은 origin에 묶인다** — trycloudflare URL이 바뀌면 기존 구독이 전부 죽으므로 구독마다 origin을 저장해 어긋난 것은 발송에서 제외하고, 404/410 응답은 그 자리에서 정리한다. 알림 본문에는 명령·경로·코드를 넣지 않는다(잠금화면에 뜬다). VAPID 키는 `~/.vt/vapid.json`(0600) 자동 생성 — **지우면 기존 구독이 전부 무효화된다**. SW 등록은 `js/swreg.js`가 담당한다(예전엔 `voice.js` 안에 있어서 음성 미설치 시 SW가 아예 안 떴다) |
| 프롬프트 큐 (P4) | ⋯ 메뉴 → "프롬프트 큐", 또는 `fsh queue`. 에이전트가 작업 중일 때 지시를 쌓아뒀다 순차 투입한다. **음성 모드와 짝** — 지금은 작업 중에 말하면 씹히는데, 큐가 있으면 걸어가며 3개를 던져놓고 순서대로 실행시킬 수 있다. 자동 투입은 **Claude Code의 stop 훅에서만** 걸린다(`POST /api/agent/event`). codex/aider/gemini는 훅이 없어 `fsh queue run` / "지금 실행"으로 수동 투입해야 한다 — 출력 유휴로 추측해 투입하는 방식은 빌드 로그가 잠깐 끊긴 것과 작업 완료를 구분할 수 없어 채택하지 않았다. 투입 전 관문 4개: 유예 시간(`VT_QUEUE_GRACE_SEC`, 기본 3초 — 사용자가 직접 타이핑을 시작했을 수 있다) · safe_mode · 타깃 pane 생존 확인 · 한 번에 한 건. 막히거나 실패한 항목은 **버리지 않고** `blocked` 로 큐에 남는다. 타깃 결정은 음성과 같은 규칙(`server/tmux_target.py`)을 쓴다. 저장은 `~/.vt/queue.json`(0600), 동시 쓰기는 flock으로 직렬화 **A3(2.1.2): 큐 항목이 원격 호스트를 대상으로 할 수 있다** — `target: {session, host}`, `host`가 없으면 언제나 로컬이다. 원격 항목은 로컬 tmux를 아예 안 본다(`send-keys`가 로컬 소켓이고 `dev`는 두 기계에 다 있다) — `POST /api/peer/input`에 `enter: true`로 나가며 상대의 `control` 등급이 필요하다. 실패하면 이유와 함께 `blocked`로 남기고 버리지 않는다. |
| 프롬프트 스니펫 (L3) | 좌측 rail → 📋, 또는 `Mod+K` → "프롬프트 스니펫". iTerm2 Snippets와 같은 개념 — 자주 쓰는 지시문·명령 뭉치를 저장해뒀다 지금 보고 있는 pane에 바로 투입한다. **큐와 구분된다** — 큐는 "에이전트가 한가해지면 실행해줘"라는 대기열이고, 스니펫은 대기 개념 자체가 없이 즉시 들어간다. 그래서 `snippet_store.py`에는 status/target/drain 같은 상태 기계가 없고 순수 CRUD다. 여러 줄 스니펫은 줄마다 trailing `\n`이 붙어 순차 실행된다. 저장은 `~/.vt/snippets.json`(0600 + flock, 큐와 같은 규칙), 상한 100건 / 8000자. **웹 UI 전용 — `fsh snippet` 서브커맨드는 없다** |
| 포트 대시보드 (P3) | ⋯ 메뉴 → "포트". 맥 앞에 없을 때 "지금 뭐가 떠 있지 / 3000번 죽여줘"를 폰에서 처리한다. 포트·PID·가동시간·CPU·메모리 표시, 원클릭 종료, `fsh tunnel expose` 연동. **VT 서버 자신과 cloudflared/tailscaled/sshd는 종료가 막혀 있다** — 죽이면 이 화면이 끊긴다. 다른 사용자 프로세스도 막는다(sudo 안 씀). 종료 직전 `port→pid`를 재확인해 PID 재사용으로 엉뚱한 프로세스를 죽이는 것을 막고, 불일치면 409. `expose`는 로컬 서버를 **공개 인터넷**에 여는 것이라 `confirm:true` 없이는 428이고, `VT_NETWORK_MODE`가 `all`이 아니면 아예 거부한다(접근 범위를 좁혀놓고 다시 뚫으면 의미가 없다). 판정은 `server/portscan.py` |
| 파일 저장소 / 업로드 (N19) | `~/.vt/files/`(id 기반, 디렉토리 0700/파일 0600) — 예전 경로 기반 `/tmp/vt-uploads/`를 대체한다. 키바 📎 슬롯(모바일 — 키바를 접어도 안 사라진다) 또는 `Mod+K` → "파일 업로드", **터미널에 이미지를 붙여넣어도 업로드된다**. 용량 상한 `VT_MAX_UPLOAD_MB`(기본 200)를 스트리밍으로 검사해 초과 시 413. 전체 상한 `VT_FILES_MAX_GB`(기본 2)·`VT_FILES_TTL_DAYS`(기본 30)는 기동 시 + 6시간마다 정리 — 공유 중이거나 `pin`된 파일은 정리 대상에서 제외. 기존 `/tmp/vt-uploads`는 기동 시 자동 이전된다. `#file-input` 엘리먼트를 세 업로드 진입점이 공유한다 — 두 번째 input을 만들지 말 것. **API는 id만 받는다**(`server/file_store.py`) — `GET /api/files/{id}/download`(`attachment`+`nosniff`+`no-store`), `DELETE /api/files/{id}`, `POST /api/files/{id}/insert`(경로를 tmux pane에 타이핑, Enter 없음). 경로 기반 다운로드 API는 더 이상 없다 |
| dock 파일 탭 (N19~N21) | dock 탭 「파일」(좌측 레일 파일 아이콘·`Mod+K` → 「파일 · 공유 링크」도 같은 화면). 행 동작 5개는 `50-files-share.md` §4 그대로: 다운로드 / 경로 복사 / 터미널에 삽입 / 링크 발급 / 삭제. 칩은 `all|shared|expiring` 필터(만료 임박 = 3일 이내), 푸터 용량 게이지는 `GET /api/files`의 `quota`로 그린다. 링크 발급은 **승격(`require_elevated`)을 요구하는 첫 UI 경로**다 — 401이면 비밀번호를 한 번 더 묻고 재시도한다. **공유 토큰은 목록 API로 절대 내려주지 않는다**(목록은 승격 없이 읽히므로 토큰이 실리면 승격 관문을 우회하는 셈) — 그래서 [복사]는 이 브라우저 세션에서 방금 발급한 링크에만 열린다. QR은 없다: 프런트에 생성기가 없고 의존성을 들일 값이 아니다(`fsh mobile`이 서버 쪽에서 이미 만든다). 렌더러는 지연 청크 `panels`에 있다 — app.js에 넣었더니 300KiB 상한을 실제로 넘겼다 |
| 오프라인 호스트 레이아웃 (C3) | **원격** 호스트의 세션을 복원 때 못 찾은 pane은 더 이상 빈 pane으로 강등되지 않는다 — `unreachable` 상태로 자리를 지키고 어느 호스트의 어느 세션을 기다리는지 말한다([다시 시도]는 `GET /api/hosts?fresh=1` 재확인, [다른 세션 고르기]로 포기 가능). 잠깐 꺼진 호스트와 죽은 세션은 다르다: 둘을 같이 강등하면 호스트를 다시 켜도 배치가 영영 사라진다. 로컬 세션은 종전대로 강등한다 — 로컬 tmux에 없는 이름은 실제로 없는 것이다. leaf의 `host`를 이제 저장하며(`frontend/js/layout/persist.js`), 다시 저장해도 기다리는 상태가 지워지지 않는다 |
| 멀티호스트 3단계 — 원격 pane (N7/N39) | 원격 tmux 세션이 평범한 탭/pane으로 열린다. 경로는 **WS 프록시**: 브라우저 → 이 서버(평소 로그인 인증) → 상대 서버의 `/api/peer/ws/{name}`(peer 서명). 브라우저 WebSocket은 서명 헤더를 못 보내는데, 그게 애초에 이 구조가 서버-서버인 이유다. 입력은 `control` 등급이 필요하고(`fsh host allow-control <id>`) **판정은 소유 호스트가 한다** — 프록시는 따지지 않고 넘긴다. 프록시 연결마다 소유자 쪽에 전용 PTY가 생기고 연결이 끊기면 같이 죽는다(소유자의 PTY를 공유하면 두 화면이 크기를 두고 싸운다). 알려진 함정 3개를 닫았다: 프록시는 출력 감시·스크롤백 영속화를 하지 않고(**PTY를 소유한 호스트만 알리고 기록한다** — 이중 알림 방지), 「연결된 화면」은 **2.1.2까지 원격에서 숨겼다**(`/api/tmux/clients`는 *이 맥의* tmux를 보고 "나"를 로컬 PTY tty로 역산한다 — 「이 화면만 남기기」가 자기 자신을 끊을 수 있었다). **2.1.3에서 열렸다**: 목록·끊기를 PTY 소유 호스트에게 물어보고(`/api/peer/clients{,/detach,/solo}`), "나"는 브라우저가 만든 화면 토큰(`peer-<상대 id>-<screen>` 접두사 강제)으로 그쪽이 판정한다. **목록은 view, 끊기는 control**, 원격 pane에 파일 삽입은 이유와 함께 거부한다(파일 바이트가 이 맥에만 있다). 진입점은 `frontend/js/term/remote.js`(`window.attachRemoteSession`) — 레일 행과 C3 자리표시자의 「다시 시도」가 같은 경로를 쓴다 **A2(2.1.2): 원격 pane에 파일 삽입이 가능해졌다** — 바이트를 먼저 보내고(`POST /api/files/{id}/send` → 상대의 `POST /api/peer/file`, `control` 등급) 그 호스트가 **자기 쪽** 경로를 pane에 타이핑한다. peer 호출 중 **본문 해시까지 서명**하는 유일한 경로다(`X-Peer-Body`): 다른 본문은 작은 JSON이지만 파일은 터널을 지나는 큰 덩어리라 "서명은 맞는데 바이트가 다른" 경우를 구분할 수 있어야 한다. 재전송은 origin(보낸 호스트 + 그쪽 파일 id)으로 건너뛴다 — 내용 해시가 아니다: 내용이 같아도 보낸 사람이 다르면 다른 파일이고, 200MB를 매번 해싱할 이유도 없다. |
| 헤더 탭 = 워크트리 (10 §4) | 헤더에 **워크트리 탭 줄**(`#vt-wtabs`)이 생겼고 **탭마다 자기 pane 트리를 가진다** — 다른 탭에 갔다 와도 그 워크트리에서 쓰던 분할이 그대로다. 레이아웃 스냅샷이 `v2`(`{tabs:[{id,worktreeId,label,tree}], activeTab}`)로 올라갔다. `v1` 스냅샷은 "탭 1개"라는 뜻이고, v1 필드도 **롤백용으로 계속 적는다**(2.1.2를 되돌리면 옛 코드가 그 스냅샷을 읽는다). **2.1.4(3단계)에서 세션 탭 줄(`#tabs`)은 사라졌다** — 상단 바의 탭은 워크트리 탭 하나뿐이다. 그 줄이 하던 일은 전부 옮겼다: 이름·순서는 세션 레코드(`core/store.js` — 순서는 명시적 배열, 재정렬은 `Mod+Alt+Shift+←/→`), 드래그 소스·닫기·이름 변경은 레일 세션 행, 에이전트 마크·상태 dot·읽지 않음은 워크트리 탭 자신. 탭이 하나뿐이면 줄 자체를 안 그리고, 탭을 닫아도 세션은 안 죽으며, 마지막 탭은 닫히지 않는다(그릴 트리가 없어진다). 같은 세션이 두 탭에 동시에 들어가지 않는다 — 표면 레이어는 DOM 노드 하나를 옮겨 다닌다. |
| 공유 링크 (N21) | `POST /api/files/{id}/share`(승격 필요)가 세션 쿠키와 같은 키로 서명한 `v1.<exp>.<fileId>.<shareId>.<hmac>` 토큰을 발급(`auth.sign_payload`) — `GET /s/{token}`이 공개 진입점으로, 의도적으로 `TokenAuthMiddleware`의 평소 인증 게이트 밖에 있다(대신 `server/routes/share.py`가 자체 검증한다). **device 모드**: 등록된 `vt_device` + 유효한 `vt_session`이 필요 — 없으면 `/?next=/s/{token}`으로 302(로그인 후 `frontend/js/gate.js`가 자동으로 돌아간다). **pin 모드**: 앱 크롬 없는 정적 PIN 페이지 → 정답이면 60초짜리 1회용 다운로드 쿠키 발급, 5회 실패 시 공유 자동 취소. 취소(`DELETE .../share/{shareId}`)는 `files.json`에서 레코드를 지운다 — 서명이 암호학적으로 여전히 유효해도 그 URL은 즉시 404가 된다. `once` 공유는 첫 다운로드 성공 후 스스로 사라진다. 발급 다이얼로그 UI는 아직 없다(dock 파일 탭 §4 — N19/N22/N23/멀티호스트/스크롤백과 함께 2.1.2 잔여 항목으로 이월) |
| 멀티호스트 페어링 (N7/N39 1단계) | 맥 두 대에 각각 FarShell을 깔고 **서버끼리** 페어링한다 — SSH가 아니다. 원래 계획(SSH attach) 대신 2026-09-12에 이 방향으로 정한 이유는 SSH가 다음 셋 중 무엇도 못 하기 때문이다: 다른 기기 로그인을 건드리지 않고 연결 하나만 취소, 읽기 전용으로 시작, 감사 기록. `fsh host pair`(B에서)가 5분짜리 1회용 티켓을 발급하고, `fsh host add <url> --ticket <t>`(A에서)가 그걸 **그 연결 전용 secret**으로 교환한다. 요청에는 `id+ts+nonce+HMAC`만 실리고 **secret 자체는 절대 전송되지 않는다**(60초 창, 1회용 nonce, 서명이 method+path에 묶임) — SSH 개인키와 같은 절충이다(0600 디스크엔 평문, 선로엔 안 나감). 기본 등급은 `view`(읽기 전용)이고 입력 허용은 `fsh host allow-control <id>`로만 켠다. 의도적으로 **별도 `/api/peer/*` 네임스페이스**(`server/routes/peer.py`)라 peer secret이 새도 다른 API에는 닿지 못한다(회귀 테스트로 고정). 취소(`fsh host rm`)하면 서명이 아직 유효해도 그 즉시 404. `~/.vt/hosts.json`(0600)이 두 방향을 분리 보관하고(`peers` outbound / `grants` inbound), `local`/`self`/`me`는 예약 id다. 감사 로그 `~/.vt/peer_audit.log`(`fsh host log`), 비상용 `fsh host revoke-all`. **1단계는 레지스트리+페어링+ping까지만** — 원격 세션(2단계)·입력(3단계)은 아직 없다 |
| tmux detach 감지 | PTY EOF 시 `[process exited]` 표시 |
| 경계값은 낡은 환경변수를 이긴다 | 설정 우선순위는 "환경변수 > `~/.vt.env`"이고 포트·경로·인스턴스 격리에는 그게 맞다. 하지만 **보안 경계를 정하는 값**에 쓰면 낡은 사본이 조용히 경계를 넓힌다. 2026-09-17에 두 번 겪었다 — `VT_BROWSE_ROOTS`를 `~/GitHub`로 좁히고 재시작했는데 `fsh`를 실행한 셸이 옛 값을 export하고 있어 **공개 터널 너머로 홈 전체가 계속 열려 있었고**, `VT_AUTH_TOKEN`을 재발급하자 이미 떠 있던 에이전트 세션들의 훅이 전부 401이 됐다(490건, 아무 표시 없이). 그래서 `BOUNDARY_KEYS`(`server/vt_env.py`, `lib/vt_env.sh`에 같은 목록)는 **파일을 먼저 읽는다**. `os.environ.get("VT_…")` 호출부가 69곳이라 전부 고치는 대신 **부팅 때 한 번 환경을 정규화**하고(`apply_boundary_overrides()`), **⚠ 그 호출은 `import auth`보다 먼저여야 한다** — auth는 import 시점에 값을 읽는다. 파일에 없는 키는 건드리지 않아 일회성 실험은 그대로 된다. `VT_CONFIG`는 의도적으로 경계값이 아니다 — *어느 파일을 읽을지* 고르는 키이고 격리 테스트 서버가 그 성질에 의존한다(그래서 `server/tests/conftest.py`가 빈 설정 파일로 고정해 스위트를 사용자 설정과 분리한다). `fsh status`/`doctor`는 창의 환경변수를 바로잡았을 때 그 사실을 말하고, `doctor`는 **돌고 있는 서버**의 경계값도 파일과 대조한다 — N28은 옛 *코드*만 봤다 |
| 터널 제공자 (`VT_TUNNEL_PROVIDER`) | 밖에서 들어오는 길을 누가 담당하는가 — `cloudflare`(기본) · `ngrok` · `none`. 미설정이면 기존과 완전히 같다. `start`/`stop`/`status`/`mobile`/`handoff`/`tunnel expose`가 전부 이 값을 따르고, 현재 URL을 아는 곳은 `bin/fsh`의 `_main_tunnel_url` **하나뿐**이다 — 예전엔 status·QR·훅이 각자 cloudflared 로그를 grep해서, 제공자가 하나 늘면 드리프트할 곳이 셋이었다. **ngrok**은 `VT_NGROK_DOMAIN`으로 주소를 고정한다(없으면 실행마다 임시 주소가 잡혀서 고정 주소라는 목적 자체가 사라진다). 추가 포트도 되지만 임시 주소다 — 무료 계정의 예약 도메인은 1개이고 그건 메인 터널이 쓴다. **워치독은 cloudflare 전용으로 남긴다** — 좀비 재연결은 cloudflared 특유의 증상이고 감지 로직도 그 로그 형식 전용이다. **URL 변경 훅은 주소가 실제로 바뀌었을 때만** 발사한다(고정 도메인인데 start마다 쏘면 노션에 같은 URL을 계속 덮어쓴다). `server/tunnel.py`의 `get_tunnel_status()`도 제공자를 보는데 이유는 같다 — 이 함수가 `/api/tunnel/status` → HUD를 먹이므로, 안 고치면 ngrok이 멀쩡히 서비스하는 동안 화면이 "터널 끊김"이라고 거짓말한다. `portscan.py`의 보호 목록에도 `ngrok`을 넣었다 — 폰의 포트 대시보드에서 그걸 죽이면 그 명령을 내리는 화면 자체가 끊긴다 |
| 추가 포트 터널 | `fsh tunnel expose <port>` — Cloudflare quick tunnel은 호스트↔포트 1:1이라 경로(`/localhost:3000`)로 포트를 바꿀 수 없다. 포트마다 터널을 하나씩 띄우고 fsh가 PID/레지스트리로 추적 |
| 터널 URL 변경 훅 | `VT_TUNNEL_HOOK` — URL이 바뀔 때 임의 명령 실행(stdin: `라벨<TAB>URL`). 게시 대상은 사람마다 다르므로(Notion/Slack/ntfy/파일) fsh는 서비스를 알지 않는다. 예시·주의사항: `fsh help tunnel-hook` |
| 터널 좀비 재연결 자동 복구 | cloudflared는 프로세스가 살아있어도(`kill -0` 성공) 엣지와의 QUIC 컨트롤 스트림만 끊긴 채 재연결을 무한 반복하는 좀비 상태에 빠질 수 있다(정적 파일은 어쩌다 200, API는 503). `server/tunnel_watchdog.py`가 `fsh start`/`voice`/`mobile` 시 자동 기동돼 `/tmp/cloudflared.log`의 재연결 실패 패턴을 감시하다가(기본: 90초 안에 4회 이상) `fsh tunnel restart`를 자동 호출한다. 수동 확인/기동: `fsh tunnel watchdog`, 수동 강제 재시작: `fsh tunnel restart` |
| Tailscale 원격 접속 (D9) | `fsh ssh` — 화면 원격이 막힌 회사망 등에서 SSH로 tmux에 직접 접속. `fsh mobile --network tailscale`은 웹 UI도 tailnet으로만 제한 |
| 클라이언트 접속 알림 (D9) | `VT_NOTIFY_CLIENT_EVENTS=1` — tmux client-attached/detached 훅 → ntfy/Telegram push |
| kitty 키보드 프로토콜 (N12) | `Ctrl+Shift+문자` · `Shift+Enter` · `Ctrl+Enter` · `Ctrl+Tab`처럼 레거시 인코딩에 자리가 없어 **아예 전달되지 않던** 조합을 CSI u로 보낸다(`frontend/js/term/kitty-keys.js`). **앱이 `CSI > flags u`로 모드를 켰을 때만** 인코딩한다 — 안 그러면 모드를 안 켠 셸에 `\x1b[97;6u`가 그대로 찍힌다. 모드는 push/pop 스택이라 셸 안에서 vim이 떴다 지면 이전 값으로 돌아간다. 우리 UI 단축키를 먼저 판정하므로 모드가 켜져 있어도 `Mod+K`가 PTY로 새지 않는다. ⚠ **3층(tmux `extended-keys`)은 넣지 않았다** — 사용자 tmux 서버 옵션 변경이라 별도 승인 대상이다. tmux 3.3+에서 꺼져 있으면 tmux가 막아 안쪽 앱까지 못 간다(`set -s extended-keys on`) |
| 테마 가져오기 (N14) | 설정 →「모양」에 Ghostty config나 Warp 테마 YAML을 붙여넣으면 터미널 팔레트와 **UI 토큰까지 추론해** 7번째 스킨(`imported`)으로 추가한다. 형식은 내용으로 판별한다. 임의의 테마에는 "흰 배경 + 노란 전경" 같은 조합이 실제로 들어오므로 본문 4.5:1 · 상태색 3:1 · 채움 버튼 글자 4.5:1을 넘을 때까지 자동 보정하고, 그래도 모자라면 무엇이 모자라는지 화면에 적는다(막지는 않는다 — 사용자가 고른 테마다). 파서·추론(`theme-import.js`)은 지연 청크이고, 부팅 경로는 저장된 토큰 맵을 CSS 변수로 쓰기만 한다(`theme-custom.js` + index.html 인라인 — 색까지 페인트 전에 심어야 FOUC가 없다). 가져온 것이 없으면 스킨 목록에도 칩에도 없다 |
| 감지 커버리지 등급 (N9) | 설정 →「에이전트」의 표. `trust: high`는 **1차 신호**가 있을 때다 — Claude Code 훅(`hook`) 또는 `fsh pane report --agent <이름>` 자기보고(`report`). 2.1.3까지는 `hook`만 high였고 그 값은 claude일 때만 세워져, 다른 CLI는 패턴을 아무리 넣어도 영원히 mid였다. 자기보고 기록은 `~/.vt/agent-report-seen.json`에 남는다 — 훅이 "설치돼 있다"는 지속적 사실로 읽히는 것과 같은 층위다(상태 엔트리는 TTL로 만료돼 등급이 깜빡인다) |
| 서버 코드 최신성 경고 (N28) | `fsh doctor`·`fsh status`가 `server/*.py`의 최신 mtime과 서버 프로세스 시작 시각을 비교해, 코드가 더 새로우면 재시작을 안내한다. 파이썬은 import 시점에 모듈을 올리므로 재시작 없이는 수정이 **전혀** 반영되지 않는다 — 붙여넣기 잘림을 코드 버그로 몇 시간 쫓은 실제 사건의 재발 방지책 |

### 아키텍처

```
server/
  main.py           — FastAPI (WS + REST + Voice + 파일 업로드/다운로드)
  auth.py           — 웹 로그인 인증 (scrypt 비밀번호 해시 + HMAC 서명 세션 쿠키
                      + 기기 화이트리스트 + TOTP 관문 + 1회용 등록 티켓).
                      `python auth.py <cmd>` CLI로 bin/fsh가 서버 없이 직접 호출한다.
                      런타임 상태는 ~/.vt/{devices,totp,tickets}.json (0600) —
                      설정(~/.vt.env)과 분리해 서버 재시작 없이 즉시 반영된다.
  pty_manager.py    — PTY 세션 (broadcast, scrollback 버퍼, EOF 감지)
  voice_handler.py  — STT (faster-whisper) + TTS (edge-tts / macOS say)
  output_watcher.py — 출력 감시 → 작업 완료 TTS 알림
  local_mic.py      — MacBook 로컬 마이크 (sounddevice)
  session_store.py  — 세션 메타데이터 (이름 변경 지원)
  agent_hook.sh     — Claude Code 훅 진입점 (pre/post/stop → /api/agent/event, stop은 tts_hook.sh에 위임)
  claude_hooks.py   — ~/.claude/settings.json 멱등 등록기 (fsh hooks install/status/uninstall)
  tts_hook.sh       — Claude Code Stop hook (TTS 자동 요약)
  voice_daemon.py   — 독립 음성 입력 데몬 (핫키 → STT → tmux)
  clipboard_daemon.py — macOS 클립보드 폴링 데몬 (changeCount → /api/clipboard/push)
  tunnel_watchdog.py — cloudflared 좀비 재연결 감시 데몬 (로그 패턴 감지 → fsh tunnel restart 자동 호출)
  routes/clipboard.py — POST /api/clipboard/push → /ws-notify 브로드캐스트
  platform_utils.py — 크로스 플랫폼 유틸리티 (macOS/Linux/WSL2)
  tailscale.py      — Tailscale 상태 감지 (D9, tunnel.py와 동일 패턴)
  vt_env.py         — ~/.vt.env 파서 (bash source와 동일 해석). voice/config.py·clipboard_daemon 공용
  hooks/tmux_client_notify.sh — tmux client-attached/detached → /api/notify/client-event (D9)

lib/
  vt_env.sh         — ~/.vt.env 형식 정의 + 단일 reader/writer
                      (vt_env_load/get/set/unset/lint). 설정 파일은 source하지 않고 파싱한다
                      — 실행 구문 미지원, 'literal' vs "expanded" 구분, 권한 600 보장.
                      ⚠ 설정 파일을 echo/sed로 직접 건드리지 말 것.

frontend/
  index.html        — xterm.js UI 셸 (워크트리 탭·검색·파일 업로드. 세션 탭 줄은 2.1.4에서 제거)
  voice.js          — 마이크 녹음 + TTS + 알림 + Media Session
  manifest.json     — PWA manifest
  sw.js             — Service Worker
```
