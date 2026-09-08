> **FarShell v2.0.0** (2026-09-08) — 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고

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
fsh hotkey [list|set|reset|disable]  # 핫키 조회/변경
fsh hooks [status|install|uninstall]  # Claude Code 훅 등록(상태 배지·큐·TTS의 전제)
fsh pane report [--state ...] [--agent ...]  # 이 pane의 상태 보고(훅 없는 에이전트용)
fsh clauth [status|which]  # 사용량 조회(읽기 전용, clauth 미설치면 숨김)
fsh password [clear]   # 웹 로그인 비밀번호 설정(해시 저장) / clear=해제
fsh otp [status|setup|disable]   # 새 기기 등록 시 OTP 요구 (setup 전까지 완전 비활성)
fsh device [list|revoke <id>]    # 등록된 기기 조회 / 폐기(폰 분실 시 세션까지 함께 무효)
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
ln -sf "$(pwd)/bin/vt" ~/.local/bin/vt   # 하위 호환 — bin/vt는 bin/fsh를 가리키는 심링크
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
| 핸즈프리 모드 | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| 음성 전용 모드 | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 조작용) |
| 웹 로그인 비밀번호 | `fsh password`로 설정 → scrypt 해시(`VT_AUTH_PASSWORD_HASH`)만 저장, 원문 미저장. 로그인 시 `VT_AUTH_SESSION_KEY`로 서명된 24h 세션 쿠키 발급(원문·토큰 아님). 사람용 인증. `server/auth.py` |
| 기기 등록 + OTP 관문 | 로그인은 **항상 비밀번호**. OTP는 "처음 보는 기기를 등록할 때"만 요구하는 관문이다. 등록된 기기는 `vt_device` 장기 쿠키(90일)를 갖고 이후 비밀번호만으로 통과 — IP가 아니라 기기 단위라 폰이 LTE↔wifi를 오가도 안 끊긴다. **`fsh otp setup` 전까지 OTP는 완전 비활성**이고 기기 등록만 조용히 쌓이므로, 나중에 켜도 쓰던 기기는 잠기지 않는다. 저장은 `~/.vt/devices.json`(0600, sha256 해시만). `fsh device revoke <id>`로 폐기하면 그 기기의 세션 쿠키까지 즉시 무효 |
| 1회용 기기 등록 티켓 | `fsh mobile`/`fsh handoff`의 QR·URL에 상시 토큰 대신 5분짜리 1회용 티켓(`?ticket=`)을 싣는다. QR을 띄우는 시점에 맥 물리 접근이 이미 증명되므로 스캔=등록 승인. 상시 토큰을 URL에 박던 방식은 그 값이 로그·히스토리·QR 이미지에 영구히 남았다 |
| 크로스 사이트 차단 | `OriginGuardMiddleware`(`server/main.py`) — Origin이 자기 자신이 아니면 HTTP·WS 모두 403. 인증·OTP로는 막을 수 없는 유일한 경로(브라우저에 이미 쿠키가 있으면 인증은 통과한다). CORS 기본 `*`도 제거 — 필요 시 `VT_ALLOWED_ORIGINS`로 옵트인 |
| API 토큰 인증 | `VT_AUTH_TOKEN` 환경변수 = 기계용 토큰(데몬/QR/URL). URL `?token=xxx` 또는 `Authorization: Bearer xxx`. 비밀번호 로그인과 병존. (구 이름 `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY`도 fallback 인식) |
| tmux 세션 관리 | 웹에서 tmux 생성/attach/detach/kill |
| Scrollback 버퍼 | WS 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js search addon |
| 세션 이름 편집 | 탭 더블클릭 → 이름 변경 (PATCH API, tmux 세션명도 `rename-session`으로 함께 바뀐다 — 영숫자·dash·underscore가 아니면 tmux는 안 건드리고 웹 라벨만 바뀐다) |
| 분할 pane (2.0) | 터미널 영역이 pane 이진 트리다. pane 헤더 버튼이나 탭을 pane 가장자리에 드롭(5구역)해 분할하고, 구분선은 드래그로 조절한다. 폭 구간별 상한(compact 2 / regular 4 / wide 6)을 넘으면 분할 버튼이 **비활성 + 이유가 툴팁**에 뜬다(조용히 아무 일도 안 일어나지 않는다). <720px 터치 기기에서는 같은 트리를 한 번에 한 pane씩 그리고 좌우 스와이프로 넘긴다. 레이아웃은 `/api/workspace`에 저장돼 복원되고, 세션이 죽은 leaf는 유령이 아니라 빈 pane으로 강등된다 |
| 좌측 rail + 커맨드 팔레트 (2.0) | ⋯ 메뉴는 사라졌다. 좌측 rail(세션/파일/큐/포트/사용량/설정)이 포인터 경로, `Mod+K`가 키보드 경로이고 **둘의 내용이 같다** — 하나만 익혀도 된다. 팔레트는 각 명령의 현재 키 바인딩을 키맵 레지스트리에서 읽어 표시한다 |
| 에이전트 상태 (2.0) | 서버가 세션별로 `idle`/`working`/`waiting`/`done` 하나를 판정하고 나머지는 그걸 표시만 한다 — 탭·pane 헤더·rail 목록(개입이 필요한 것이 맨 위로 정렬)·파비콘·앱 아이콘 배지(`waiting` 수). Claude Code 훅이 전제다: `fsh hooks install`(빠져 있으면 `fsh doctor`와 설정 → 정보가 알려준다) |
| 승인 대기 감지 (`waiting`) | PTY 출력에서 `server/detect/*.toml`의 패턴으로 감지한다. exit 패턴·그 pane에 대한 사용자 입력·다음 훅 이벤트·2분 TTL 중 아무거나로 풀린다. **프롬프트 큐는 `waiting`인 pane에 절대 투입하지 않는다** — 거기 `send-keys`를 하면 큐 텍스트가 승인 답변으로 소비된다 |
| 사용량 게이지 (2.0) | clauth의 `~/.clauth/status.json`을 읽는다(CLI가 아니라 파일). wide에서는 우측 레일에 상시, 그 외에는 패널로. **소스가 없으면 통째로 사라진다** — `VT_USAGE_PROVIDER`(`auto`/`clauth`/`none`)로 제어하고, 토큰류는 필드 화이트리스트로 제외된다 |
| 설정 + 키맵 (2.0) | `Mod+,`. 설정은 `/api/workspace.settings`에 저장돼 폰에서 바꾼 값이 맥에도 반영된다. 키 바인딩은 재지정 가능하고 **`passthrough`로 키를 터미널에 돌려줄 수 있다** — `Mod+F`를 셸의 `forward-char`로 되찾는 경로가 이것이다. 「마우스 · 선택」에서 "앱에 마우스 이벤트 전달"을 끄면 vim/tmux가 마우스를 잡아도 드래그 선택이 항상 된다 |
| 연결된 화면 (2.0) | rail → 세션 → 「연결된 화면」. 그 tmux 세션에 붙은 클라이언트를 "나" 배지와 함께 보여주고, "이 화면만 남기기"로 나머지를 끊는다(맥 iTerm2 창 포함). tty는 보내지 않는다 — 서버가 web session id로 역산하고, 자기 자신은 끊지 못하게 막는다 |
| 코드 뷰어 / diff (P2) | ⋯ 메뉴 → "코드 뷰어". CLI만으로 원격 개발할 때 코드를 눈으로 못 보는 문제를 푼다. 파일 트리 · 문법 하이라이팅(highlight.js, 36개 언어) · `git diff` 렌더링. **읽기 전용이며 쓰기 API가 없다.** 공개 터널 너머로 열리므로 방어가 3중이다: ① 루트 확정(`VT_BROWSE_ROOTS`, 기본 `~/GitHub` — `$HOME`을 열면 `~/.ssh`·`~/.aws`가 사정권에 든다) ② `Path.resolve()` + `is_relative_to`(startswith 금지 — 형제 디렉토리가 통과한다. `resolve()`가 심링크를 펼치므로 루트 밖을 가리키는 링크도 함께 걸린다) ③ 거부 목록(`.env*`·`*.pem`·`id_rsa`·`.ssh/`·`.aws/` 등, 경로의 모든 구성요소를 검사). 판정은 `server/fsguard.py` 한 곳에만 있다 |
| Web Push (P5) | ⋯ 메뉴 → "푸시 알림". 기존 알림(`/ws-notify` → Notification API)은 **PWA 탭이 살아 있어야만** 동작해서, 폰 화면을 끄면 "승인 대기 중"을 놓쳤다. 그 격차를 메운다. WS 클라이언트가 하나라도 붙어 있으면 푸시를 보내지 않는다(같은 알림이 두 번 온다). **성립 조건**: ① https — 평문 http에서는 Service Worker 자체가 등록되지 않는다 ② iOS는 홈 화면에 PWA로 추가해야 한다(16.4+, 사파리 탭에서는 구독이 안 만들어진다. 우회 불가). **구독은 origin에 묶인다** — trycloudflare URL이 바뀌면 기존 구독이 전부 죽으므로 구독마다 origin을 저장해 어긋난 것은 발송에서 제외하고, 404/410 응답은 그 자리에서 정리한다. 알림 본문에는 명령·경로·코드를 넣지 않는다(잠금화면에 뜬다). VAPID 키는 `~/.vt/vapid.json`(0600) 자동 생성 — **지우면 기존 구독이 전부 무효화된다**. SW 등록은 `js/swreg.js`가 담당한다(예전엔 `voice.js` 안에 있어서 음성 미설치 시 SW가 아예 안 떴다) |
| 프롬프트 큐 (P4) | ⋯ 메뉴 → "프롬프트 큐", 또는 `fsh queue`. 에이전트가 작업 중일 때 지시를 쌓아뒀다 순차 투입한다. **음성 모드와 짝** — 지금은 작업 중에 말하면 씹히는데, 큐가 있으면 걸어가며 3개를 던져놓고 순서대로 실행시킬 수 있다. 자동 투입은 **Claude Code의 stop 훅에서만** 걸린다(`POST /api/agent/event`). codex/aider/gemini는 훅이 없어 `fsh queue run` / "지금 실행"으로 수동 투입해야 한다 — 출력 유휴로 추측해 투입하는 방식은 빌드 로그가 잠깐 끊긴 것과 작업 완료를 구분할 수 없어 채택하지 않았다. 투입 전 관문 4개: 유예 시간(`VT_QUEUE_GRACE_SEC`, 기본 3초 — 사용자가 직접 타이핑을 시작했을 수 있다) · safe_mode · 타깃 pane 생존 확인 · 한 번에 한 건. 막히거나 실패한 항목은 **버리지 않고** `blocked` 로 큐에 남는다. 타깃 결정은 음성과 같은 규칙(`server/tmux_target.py`)을 쓴다. 저장은 `~/.vt/queue.json`(0600), 동시 쓰기는 flock으로 직렬화 |
| 프롬프트 스니펫 (L3) | 좌측 rail → 📋, 또는 `Mod+K` → "프롬프트 스니펫". iTerm2 Snippets와 같은 개념 — 자주 쓰는 지시문·명령 뭉치를 저장해뒀다 지금 보고 있는 pane에 바로 투입한다. **큐와 구분된다** — 큐는 "에이전트가 한가해지면 실행해줘"라는 대기열이고, 스니펫은 대기 개념 자체가 없이 즉시 들어간다. 그래서 `snippet_store.py`에는 status/target/drain 같은 상태 기계가 없고 순수 CRUD다. 여러 줄 스니펫은 줄마다 trailing `\n`이 붙어 순차 실행된다. 저장은 `~/.vt/snippets.json`(0600 + flock, 큐와 같은 규칙), 상한 100건 / 8000자. **웹 UI 전용 — `fsh snippet` 서브커맨드는 없다** |
| 포트 대시보드 (P3) | ⋯ 메뉴 → "포트". 맥 앞에 없을 때 "지금 뭐가 떠 있지 / 3000번 죽여줘"를 폰에서 처리한다. 포트·PID·가동시간·CPU·메모리 표시, 원클릭 종료, `fsh tunnel expose` 연동. **VT 서버 자신과 cloudflared/tailscaled/sshd는 종료가 막혀 있다** — 죽이면 이 화면이 끊긴다. 다른 사용자 프로세스도 막는다(sudo 안 씀). 종료 직전 `port→pid`를 재확인해 PID 재사용으로 엉뚱한 프로세스를 죽이는 것을 막고, 불일치면 409. `expose`는 로컬 서버를 **공개 인터넷**에 여는 것이라 `confirm:true` 없이는 428이고, `VT_NETWORK_MODE`가 `all`이 아니면 아예 거부한다(접근 범위를 좁혀놓고 다시 뚫으면 의미가 없다). 판정은 `server/portscan.py` |
| 파일 업로드 | 보이스바 📎 버튼 → `/tmp/vt-uploads/`에 저장 |
| 파일 다운로드 | `GET /api/download?path=...` |
| tmux detach 감지 | PTY EOF 시 `[process exited]` 표시 |
| 추가 포트 터널 | `fsh tunnel expose <port>` — Cloudflare quick tunnel은 호스트↔포트 1:1이라 경로(`/localhost:3000`)로 포트를 바꿀 수 없다. 포트마다 터널을 하나씩 띄우고 fsh가 PID/레지스트리로 추적 |
| 터널 URL 변경 훅 | `VT_TUNNEL_HOOK` — URL이 바뀔 때 임의 명령 실행(stdin: `라벨<TAB>URL`). 게시 대상은 사람마다 다르므로(Notion/Slack/ntfy/파일) fsh는 서비스를 알지 않는다. 예시·주의사항: `fsh help tunnel-hook` |
| 터널 좀비 재연결 자동 복구 | cloudflared는 프로세스가 살아있어도(`kill -0` 성공) 엣지와의 QUIC 컨트롤 스트림만 끊긴 채 재연결을 무한 반복하는 좀비 상태에 빠질 수 있다(정적 파일은 어쩌다 200, API는 503). `server/tunnel_watchdog.py`가 `fsh start`/`voice`/`mobile` 시 자동 기동돼 `/tmp/cloudflared.log`의 재연결 실패 패턴을 감시하다가(기본: 90초 안에 4회 이상) `fsh tunnel restart`를 자동 호출한다. 수동 확인/기동: `fsh tunnel watchdog`, 수동 강제 재시작: `fsh tunnel restart` |
| Tailscale 원격 접속 (D9) | `fsh ssh` — 화면 원격이 막힌 회사망 등에서 SSH로 tmux에 직접 접속. `fsh mobile --network tailscale`은 웹 UI도 tailnet으로만 제한 |
| 클라이언트 접속 알림 (D9) | `VT_NOTIFY_CLIENT_EVENTS=1` — tmux client-attached/detached 훅 → ntfy/Telegram push |

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
  index.html        — xterm.js 멀티 탭 UI (검색, 세션 이름 편집, 파일 업로드)
  voice.js          — 마이크 녹음 + TTS + 알림 + Media Session
  manifest.json     — PWA manifest
  sw.js             — Service Worker
```
