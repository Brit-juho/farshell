> **voice-terminal v1.6.0** (2026-07-12) — 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고

## vt CLI (어디서든 실행)

터미널 어디서든 `vt` 명령으로 Voice Terminal을 제어합니다:

```bash
vt voice              # 음성 모드 (백그라운드, 노션 작업 중에도 사용)
vt clip               # 클립보드 동기화 데몬 (맥 클립보드 변경 → 웹, OSC52 보완)
vt mobile [--e2e]     # 모바일 접속 URL + QR (--e2e: 페이로드 암호화)
vt start              # 전체 시작 (서버+터널+음성)
vt stop [--purge]     # 종료 (--purge: tmux 세션까지 완전 종료)
vt status             # 현재 상태 확인
vt manage             # TUI 관리 도구 (세션/타깃/핫키/상태) — Wave 4
vt attach [name]      # 임의 tmux 세션을 새 창에 attach
vt voice-target [name|--auto]  # 음성 daemon 타깃 lock/해제
vt hotkey [list|set|reset|disable]  # 핫키 조회/변경
vt password [clear]   # 웹 로그인 비밀번호 설정(해시 저장) / clear=해제
vt help <topic>       # concepts/voice/hotkeys/target/troubleshoot
vt claude             # 새 터미널 창에 tmux dev + claude --resume
vt handoff mobile     # 현재 tmux 세션을 폰으로 넘김 (QR + #tmux=)
vt handoff desktop    # 폰 세션을 맥 터미널로 가져옴
vt tunnel expose 3000 "앱 이름"  # 다른 로컬 포트를 별도 Cloudflare 터널로 공개
vt tunnel unexpose 3000          # 해당 포트 터널 종료
vt tunnel list                   # 열려 있는 터널 전부 (메인 + 추가 포트)
vt tunnel hook                   # URL 변경 훅 확인 + 즉시 실행 (vt help tunnel-hook)
vt tunnel restart                # 좀비 재연결(응답 없음) 상태여도 강제로 새 터널 기동 + 훅 재실행
vt tunnel watchdog               # 좀비 재연결 자동 감지 데몬 상태 확인/시작 (평소엔 vt start/voice/mobile가 자동 기동)
vt ssh [session]      # Tailscale + SSH로 tmux 세션 직접 접속 명령 안내 (D9, 회사망 등)
vt doctor             # 설치/환경 진단 (Linux 항목 포함)
vt install-profiles   # 터미널 앱 profile 자동 등록 (iTerm2 Dynamic Profile + 기타 snippet)
vt shell-init zsh     # 셸 init 스니펫 출력 (eval "$(vt shell-init zsh)" >> ~/.zshrc)
```

> **지원 OS**: macOS / Linux (X11) / WSL2 (Linux로 동작). Windows 네이티브는 미지원.

**Phase 6 — 단일 tmux 서버 원칙:** vt CLI · server · Voice Daemon · hook이 모두 `-L vt` 격리 소켓 사용. Voice Daemon은 `VT_TMUX_SOCKET` 환경변수로 오버라이드 가능. 사용자 기존 `tmux ls`와 분리됨.

**`voice` / `mobile` / `start` 실행 시 자동 동작:** 현재 쓰는 터미널 앱(iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app)에 새 창이 열리고 그 안에서 `tmux new -A -s dev 'claude --resume'`이 실행됩니다. 이미 tmux 안이면 새 창을 열지 않습니다.

**노션 작업 중 음성 코딩 워크플로:**
1. `vt voice` → 백그라운드 시작 (+ 새 iTerm 창에 `tmux dev` + `claude --resume` 자동 오픈)
2. 새 창의 resume 목록에서 현재 대화 선택 → 이후 음성/모바일이 그 Claude로 연결됨
3. 원래 창은 그대로 두고 노션으로 돌아가서 작업
4. Ctrl+Shift+V → 말하기 ("git status") → tmux dev에 자동 입력
5. `vt stop` → 종료

> 이미 tmux 안에서 `vt` 명령을 부르면 새 창을 열지 않습니다 (`$TMUX` 체크).
> 자동 오픈은 macOS + iTerm 환경 한정. 그 외에는 수동 명령(`tmux new -A -s dev 'claude --resume'`) 안내가 출력됩니다.

### Claude 전역 스킬

| 커맨드 | 설명 |
|--------|------|
| `/vt` | 전역 스킬. 어디서든 "음성 모드", "모바일 접속" 등으로 호출 |

### 프로젝트 스킬

| 커맨드 | 설명 |
|--------|------|
| `/vt-start` | 서버 시작 + tmux 준비 + Cloudflare Tunnel 원격 접속 |
| `/vt-mobile` | 모바일 테스트 (adb 포트포워딩, Chrome 열기, 스크린샷) |
| `/vt-voice` | Voice Daemon 설치/실행 (핫키 → STT → tmux 주입) |

### 신규 사용자 설치

**기본 경로는 `./install.sh`** (원라인 설치, 2026-04-14 추가). 아래는 대화형 안내가 필요한 경우에만 사용.

```bash
# 원라인 설치 (추천)
./install.sh            # 터미널만 (~50MB)
./install.sh voice      # 터미널 + 음성 모드 (~1.5GB)
```

`install.sh`가 자동으로: Python venv 생성 → 프로필별 패키지 설치 → vt CLI 심링크 → `~/.vt.env` 생성 → PATH 갱신.

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
conda create -n vt python=3.11 -y && conda activate vt
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

#### Step 5: vt CLI 등록

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/vt" ~/.local/bin/vt
chmod +x bin/vt
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
mkdir -p ~/.claude/skills/vt
cp .claude/skills/vt/SKILL.md ~/.claude/skills/vt/SKILL.md 2>/dev/null || true
```

#### Step 9: 설치 확인

```bash
vt status
```

사용자에게 안내:
- `vt mobile` — 모바일 접속 (QR코드)
- `vt voice` — 음성 모드 (옵션 2 선택 시)
- `vt stop` — 종료

#### 플랫폼별 참고

**macOS:** 음성 모드 시 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
**WSL2:** 음성 핫키는 WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용. PowerShell: `.\bin\vt.ps1 voice`

---

## voice-terminal 프로젝트 가이드

### 서버 실행

```bash
# 방법 1: 스크립트 (~/.vt.env의 VT_PYTHON 자동 사용)
./run_server.sh

# 방법 2: 직접 실행
cd server
"$VT_PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 7777
```

- Python 경로는 환경별로 다름 — `vt doctor`로 현재 감지된 값 확인
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

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 세션 생성 (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{id}` | 세션 삭제 |
| WS | `/ws/{id}` | 터미널 WebSocket (xterm.js 연결) |
| WS | `/ws-notify` | 작업 완료 알림 수신 |
| POST | `/voice/input?session_id=X` | 음성 → STT → 세션 입력 |
| POST | `/voice/output` | 텍스트 → TTS → 오디오 반환 |
| POST | `/voice/local/start` | MacBook 마이크 녹음 시작 |
| POST | `/voice/local/stop?session_id=X` | 녹음 종료 → STT → 세션 입력 |
| POST | `/api/watch/{id}` | 출력 감시 ON/OFF (enabled, timeout) |
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| POST | `/api/tmux/attach` | tmux 세션에 attach (JSON: name) |
| PATCH | `/api/sessions/{id}` | 세션 이름 변경 (JSON: name) |
| POST | `/api/tmux/create` | tmux 세션 생성 + 자동 attach (JSON: name, cols, rows) |
| DELETE | `/api/tmux/kill/{name}` | tmux 세션 완전 종료 |
| POST | `/api/upload?session_id=X` | 파일 업로드 (multipart/form-data) |
| GET | `/api/download?path=X` | 서버 파일 다운로드 |
| POST | `/api/auth` | 토큰 인증 → `vt_session` HttpOnly cookie 발급 (v1.3+) |
| GET | `/api/capabilities` | 서버 capability 정보 (TTS/STT/터널 등) |
| GET | `/api/workspace` | 워크스페이스 동기화 (탭/UI 상태) |
| GET | `/api/agents` | tmux 세션별 활성 에이전트 (claude 등) |
| GET | `/api/tailscale/status` | Tailscale 설치/연결/IP/MagicDNS 호스트명 (D9) |
| POST | `/api/notify/client-event` | tmux client-attached/detached 훅 전용 — SSH 접속 가시화 (D9) |
| POST | `/api/clipboard/push` | `clipboard_daemon.py`(맥 클립보드 폴링) 전용 — `/ws-notify` 클라이언트에 브로드캐스트 |
| WS | `/ws-preview/{name}` | grid view용 tmux pane 출력 push (v1.3+) |
| WS | `/ws-agent` | 에이전트 활성 상태 push |
| WS | `/ws-workspace` | 워크스페이스 변경 push |

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

### Claude Code Stop hook (TTS 자동 요약)

Claude Code 응답 완료 시 자동으로 TTS 요약을 재생한다.

- 스크립트: `server/tts_hook.sh`
- 설정: `~/.claude/settings.json`의 `hooks.Stop`에 등록
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
- **폴링 데몬** (`vt clip`) — Safari/Finder 등 터미널 밖에서 일어난 복사는 OSC52로
  못 잡으므로, `server/clipboard_daemon.py`가 `NSPasteboard.changeCount`를 폴링해
  변경 시 `POST /api/clipboard/push` → `/ws-notify` 브로드캐스트로 웹에 전달.

```bash
# 실행 (또는 vt clip)
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
| Clipboard 동기화 | OSC52(터미널 내부 복사) + `vt clip` 폴링 데몬(터미널 밖 복사) → 웹 클립보드 push |
| 핸즈프리 모드 | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| 음성 전용 모드 | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 조작용) |
| 웹 로그인 비밀번호 | `vt password`로 설정 → scrypt 해시(`VT_AUTH_PASSWORD_HASH`)만 저장, 원문 미저장. 로그인 시 `VT_AUTH_SESSION_KEY`로 서명된 24h 세션 쿠키 발급(원문·토큰 아님). 사람용 인증. `server/auth.py` |
| API 토큰 인증 | `VT_AUTH_TOKEN` 환경변수 = 기계용 토큰(데몬/QR/URL). URL `?token=xxx` 또는 `Authorization: Bearer xxx`. 비밀번호 로그인과 병존. (구 이름 `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY`도 fallback 인식) |
| tmux 세션 관리 | 웹에서 tmux 생성/attach/detach/kill |
| Scrollback 버퍼 | WS 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js search addon |
| 세션 이름 편집 | 탭 더블클릭 → 이름 변경 (PATCH API) |
| 파일 업로드 | 보이스바 📎 버튼 → `/tmp/vt-uploads/`에 저장 |
| 파일 다운로드 | `GET /api/download?path=...` |
| tmux detach 감지 | PTY EOF 시 `[process exited]` 표시 |
| 추가 포트 터널 | `vt tunnel expose <port>` — Cloudflare quick tunnel은 호스트↔포트 1:1이라 경로(`/localhost:3000`)로 포트를 바꿀 수 없다. 포트마다 터널을 하나씩 띄우고 vt가 PID/레지스트리로 추적 |
| 터널 URL 변경 훅 | `VT_TUNNEL_HOOK` — URL이 바뀔 때 임의 명령 실행(stdin: `라벨<TAB>URL`). 게시 대상은 사람마다 다르므로(Notion/Slack/ntfy/파일) vt는 서비스를 알지 않는다. 예시·주의사항: `vt help tunnel-hook` |
| 터널 좀비 재연결 자동 복구 | cloudflared는 프로세스가 살아있어도(`kill -0` 성공) 엣지와의 QUIC 컨트롤 스트림만 끊긴 채 재연결을 무한 반복하는 좀비 상태에 빠질 수 있다(정적 파일은 어쩌다 200, API는 503). `server/tunnel_watchdog.py`가 `vt start`/`voice`/`mobile` 시 자동 기동돼 `/tmp/cloudflared.log`의 재연결 실패 패턴을 감시하다가(기본: 90초 안에 4회 이상) `vt tunnel restart`를 자동 호출한다. 수동 확인/기동: `vt tunnel watchdog`, 수동 강제 재시작: `vt tunnel restart` |
| Tailscale 원격 접속 (D9) | `vt ssh` — 화면 원격이 막힌 회사망 등에서 SSH로 tmux에 직접 접속. `vt mobile --network tailscale`은 웹 UI도 tailnet으로만 제한 |
| 클라이언트 접속 알림 (D9) | `VT_NOTIFY_CLIENT_EVENTS=1` — tmux client-attached/detached 훅 → ntfy/Telegram push |

### 아키텍처

```
server/
  main.py           — FastAPI (WS + REST + Voice + 파일 업로드/다운로드)
  auth.py           — 웹 로그인 인증 (scrypt 비밀번호 해시 + HMAC 서명 세션 쿠키)
  pty_manager.py    — PTY 세션 (broadcast, scrollback 버퍼, EOF 감지)
  voice_handler.py  — STT (faster-whisper) + TTS (edge-tts / macOS say)
  output_watcher.py — 출력 감시 → 작업 완료 TTS 알림
  local_mic.py      — MacBook 로컬 마이크 (sounddevice)
  session_store.py  — 세션 메타데이터 (이름 변경 지원)
  tts_hook.sh       — Claude Code Stop hook (TTS 자동 요약)
  voice_daemon.py   — 독립 음성 입력 데몬 (핫키 → STT → tmux)
  clipboard_daemon.py — macOS 클립보드 폴링 데몬 (changeCount → /api/clipboard/push)
  tunnel_watchdog.py — cloudflared 좀비 재연결 감시 데몬 (로그 패턴 감지 → vt tunnel restart 자동 호출)
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
