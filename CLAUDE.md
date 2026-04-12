## ralph CLI (어디서든 실행)

터미널 어디서든 `ralph` 명령으로 Voice Terminal을 제어합니다:

```bash
ralph voice    # 음성 모드 (백그라운드, 노션 작업 중에도 사용)
ralph mobile   # 모바일 접속 URL + QR코드 + 자동 Chrome 열기
ralph start    # 전체 시작 (서버+터널+음성)
ralph stop     # 모든 프로세스 종료
ralph status   # 현재 상태 확인
```

**노션 작업 중 음성 코딩 워크플로:**
1. `ralph voice` → 백그라운드 시작
2. 노션으로 돌아가서 작업
3. Ctrl+Shift+V → 말하기 ("git status") → tmux에 자동 입력
4. `ralph stop` → 종료

### Claude 전역 스킬

| 커맨드 | 설명 |
|--------|------|
| `/ralph` | 전역 스킬. 어디서든 "음성 모드", "모바일 접속" 등으로 호출 |

### 프로젝트 스킬

| 커맨드 | 설명 |
|--------|------|
| `/ralph-start` | 서버 시작 + tmux 준비 + Cloudflare Tunnel 원격 접속 |
| `/ralph-mobile` | 모바일 테스트 (adb 포트포워딩, Chrome 열기, 스크린샷) |
| `/ralph-voice` | Voice Daemon 설치/실행 (핫키 → STT → tmux 주입) |

### 신규 사용자 설치 (Claude 주도 인터랙티브)

사용자가 이 레포를 클론하고 Claude Code를 열면, 아래 절차에 따라 **대화형으로 설치를 안내**하세요.
setup.sh를 실행하지 말고, 아래 단계를 하나씩 질문하며 진행합니다.

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

#### Step 3: conda 환경 생성

```bash
conda create -n whisper python=3.10 -y
```

conda가 없으면:
- macOS: `brew install --cask miniforge`
- Linux/WSL2: `wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh && bash Miniforge3-Linux-x86_64.sh`

#### Step 4: 패키지 설치 (선택에 따라)

**터미널만 (옵션 1):**
```bash
conda run -n whisper pip install fastapi uvicorn python-multipart websockets
```

**터미널 + 음성 (옵션 2):**
```bash
conda run -n whisper pip install fastapi uvicorn faster-whisper edge-tts sounddevice numpy pynput python-multipart websockets
```

macOS 추가:
```bash
conda run -n whisper pip install pyobjc-framework-Cocoa
```

#### Step 5: ralph CLI 등록

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/ralph" ~/.local/bin/ralph
chmod +x bin/ralph
```

PATH 확인:
```bash
echo "$PATH" | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

#### Step 6: 설정 파일 생성

```bash
# RALPH_PYTHON 경로를 감지
WHISPER_PY="$(conda info --base)/envs/whisper/bin/python"

cat > ~/.ralph.env << EOF
RALPH_PORT=7777
RALPH_PYTHON=$WHISPER_PY
# RALPH_TOKEN=my-secret-token  # 원격 접속 시 인증 (선택사항)
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
mkdir -p ~/.claude/skills/ralph
cp .claude/skills/ralph/SKILL.md ~/.claude/skills/ralph/SKILL.md 2>/dev/null || true
```

#### Step 9: 설치 확인

```bash
ralph status
```

사용자에게 안내:
- `ralph mobile` — 모바일 접속 (QR코드)
- `ralph voice` — 음성 모드 (옵션 2 선택 시)
- `ralph stop` — 종료

#### 플랫폼별 참고

**macOS:** 음성 모드 시 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
**WSL2:** 음성 핫키는 WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용. PowerShell: `.\bin\ralph.ps1 voice`

---

## 랄프톤 프로젝트 가이드

### 서버 실행

```bash
# 방법 1: 스크립트
./run_server.sh

# 방법 2: 직접 실행
cd server
/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python -m uvicorn main:app --host 0.0.0.0 --port 7777
```

- conda 환경: `whisper` (faster-whisper, fastapi, edge-tts, sounddevice 포함)
- Python 경로: `/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python`

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
/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python server/voice_daemon.py &

# 사용: Ctrl+Shift+V (토글) → 말하기 → STT → 활성 tmux pane에 입력
# macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요
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
| 핸즈프리 모드 | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| 음성 전용 모드 | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 조작용) |
| API 토큰 인증 | `RALPH_TOKEN` 환경변수 설정 시 활성화. URL `?token=xxx` 또는 `Authorization: Bearer xxx` |
| tmux 세션 관리 | 웹에서 tmux 생성/attach/detach/kill |
| Scrollback 버퍼 | WS 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js search addon |
| 세션 이름 편집 | 탭 더블클릭 → 이름 변경 (PATCH API) |
| 파일 업로드 | 보이스바 📎 버튼 → `/tmp/ralphton_uploads/`에 저장 |
| 파일 다운로드 | `GET /api/download?path=...` |
| tmux detach 감지 | PTY EOF 시 `[process exited]` 표시 |

### 아키텍처

```
server/
  main.py           — FastAPI (WS + REST + Voice + 파일 업로드/다운로드)
  pty_manager.py    — PTY 세션 (broadcast, scrollback 버퍼, EOF 감지)
  voice_handler.py  — STT (faster-whisper) + TTS (edge-tts / macOS say)
  output_watcher.py — 출력 감시 → 작업 완료 TTS 알림
  local_mic.py      — MacBook 로컬 마이크 (sounddevice)
  session_store.py  — 세션 메타데이터 (이름 변경 지원)
  tts_hook.sh       — Claude Code Stop hook (TTS 자동 요약)
  voice_daemon.py   — 독립 음성 입력 데몬 (핫키 → STT → tmux)
  platform_utils.py — 크로스 플랫폼 유틸리티 (macOS/Linux/WSL2)

frontend/
  index.html        — xterm.js 멀티 탭 UI (검색, 세션 이름 편집, 파일 업로드)
  voice.js          — 마이크 녹음 + TTS + 알림 + Media Session
  manifest.json     — PWA manifest
  sw.js             — Service Worker
```
