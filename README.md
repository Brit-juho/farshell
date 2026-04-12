# 랄프톤 — Voice Terminal

맥북(또는 WSL2)을 서버로 두고, 어디서든 음성으로 터미널을 조작하는 시스템.

- **모바일에서 터미널 접속** — QR 스캔하면 바로 연결
- **음성으로 코딩** — 노션 작업 중에도 핫키(Ctrl+Shift+V)로 음성 입력
- **Claude Code 연동** — 작업 완료 시 TTS로 결과 요약 알림
- **전부 무료** — API 키, 구독 없음. 오픈소스 STT/TTS

---

## 설치

Claude Code에서 아래 명령어를 붙여넣으세요:

```
랄프톤 설치해줘: git clone https://github.com/NeTrioGit/ralphton.git ~/ralphton && cd ~/ralphton
```

Claude가 CLAUDE.md를 읽고 대화형으로 설치를 진행합니다:

1. **OS 감지** — macOS / WSL2 / Linux 자동 판별
2. **설치 구성 선택** — 필요한 기능만 골라서 설치
   - **터미널만** (모바일 연동) — ~500 MB
   - **터미널 + 음성 모드** — ~3 GB (Whisper STT + TTS 포함)
3. **환경 설정** — conda, 패키지, CLI 등록, 설정 파일 생성

> Whisper 모델은 첫 실행 시 Hugging Face에서 자동 다운로드됩니다 (~141MB).
> STT/TTS 모두 무료 오픈소스이며, API 키나 구독이 필요 없습니다.

---

## 사용법

```bash
ralph mobile   # 모바일 접속 — QR코드 + URL
ralph voice    # 음성 모드 — 백그라운드 실행
ralph start    # 전체 시작 (서버 + 터널 + 음성)
ralph stop     # 종료
ralph status   # 상태 확인
```

### Windows (WSL2)

```powershell
# PowerShell에서
.\bin\ralph.ps1 voice
.\bin\ralph.ps1 mobile
.\bin\ralph.ps1 stop
```

> - 서버 + tmux는 WSL2 내부에서 실행
> - 브라우저는 Windows에서 `localhost:7777`로 접속 (포트 포워딩 자동)
> - 음성 핫키(Ctrl+Shift+V)는 WSLg 필요 (Windows 11)
> - WSLg 없으면 브라우저 🎤 버튼 사용

---

## 사용 시나리오

### 1. 노션 작업 중 음성 코딩 (macOS)

```
1. ralph voice              ← 터미널 어디서든 실행
2. 노션으로 돌아가서 작업 계속
3. Ctrl+Shift+V → "git status"  ← 말하면 tmux에 자동 입력
4. 결과를 TTS로 이어폰에서 들음
5. ralph stop               ← 끝나면 종료
```

Voice Daemon은 **백그라운드 프로세스**로 동작합니다.
- Claude 세션이나 터미널을 닫아도 계속 실행
- `ralph stop` 또는 맥 재시작 전까지 유지
- 아무 터미널에서나 `ralph stop`으로 종료 가능

### 2. 모바일에서 터미널 조작

```
1. ralph mobile             ← URL + QR코드 출력
2. 폰 카메라로 QR 스캔 (또는 URL 직접 입력)
3. tmux 세션에 자동 연결
4. 하단 버튼으로 조작:
   🎤 음성 입력  — 탭하여 녹음/중지
   🔄 핸즈프리  — 연속 음성 인식 (자동 반복)
   🎧 음성 전용 — 터미널 숨기고 큰 마이크만 표시
   📎 파일      — 파일 업로드
```

### 3. Claude Code + TTS 알림

Claude Code 작업이 완료되면 자동으로 TTS 요약을 읽어줍니다.
이어폰 착용 시 눈을 떼지 않고도 결과를 들을 수 있습니다.

- `tts_hook.sh`가 Claude Code Stop hook으로 등록됨
- 마지막 assistant 응답(최대 200자)을 추출 → TTS 재생
- 서버 미실행 시 macOS `say -v Yuna`로 fallback

### 4. iTerm2 + tmux 연동

iTerm2에서 tmux Control Mode를 사용하면 네이티브 탭/스크롤을 유지하면서 모바일에서도 같은 세션에 접속할 수 있습니다.

```bash
# iTerm2에서 tmux 세션에 연결
tmux -CC new -A -s dev

# .zshrc에 추가하면 자동 연결
if [[ "$TERM_PROGRAM" == "iTerm.app" && -z "$TMUX" && -z "$CLAUDE_CODE" && "$-" == *i* ]]; then
  tmux -CC new -A -s dev
fi
```

---

## 설정

`~/.ralph.env`:

```bash
RALPH_PORT=7777                    # 포트 (기본 7777, 충돌 시 변경)
RALPH_PYTHON=/path/to/python       # Python 경로 (setup.sh가 자동 설정)
RALPH_TOKEN=my-secret-token        # 원격 접속 시 인증 토큰 (선택사항)
```

### 수동 설치

```bash
# macOS
conda create -n whisper python=3.10
conda activate whisper
pip install fastapi uvicorn faster-whisper edge-tts sounddevice numpy pynput python-multipart websockets

# 서버 직접 실행
cd server
/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python -m uvicorn main:app --host 0.0.0.0 --port 7777
```

---

## 접속 방법

| 환경 | 방법 |
|------|------|
| 데스크톱 | `http://localhost:7777` |
| 같은 네트워크 모바일 | `http://<맥북IP>:7777` (`ipconfig getifaddr en0`으로 IP 확인) |
| adb 연결 Android | `adb reverse tcp:7777 tcp:7777` → `http://localhost:7777` |
| 원격 (어디서든) | `ralph mobile` → Cloudflare Tunnel HTTPS URL 사용 |

---

## 아키텍처

```
                  ┌──────────────────────────────────────┐
                  │  MacBook / WSL2 (서버)                 │
                  │                                      │
  [ralph voice]   │  ┌────────────────┐  ┌────────────┐  │
  Ctrl+Shift+V ──►│  │ Voice Daemon   │  │ FastAPI     │  │
  → STT → tmux   │  │ (독립, 서버무관) │  │ :7777       │  │
                  │  └────────────────┘  └─────┬──────┘  │
                  │                            │         │
                  │  ┌────────────────┐  ┌─────┴──────┐  │
                  │  │ tmux sessions  │◄─┤ PTY Manager │  │
                  │  │ (세션 공유)     │  │ + Scrollback│  │
                  │  └────────────────┘  └────────────┘  │
                  └──────────────┬───────────────────────┘
                                 │ Cloudflare Tunnel (HTTPS)
                  ┌──────────────┴───────────────────────┐
                  │  모바일 / 원격 브라우저                  │
                  │                                      │
                  │  xterm.js 터미널 + 음성 전용 모드       │
                  │  🎤 STT → 서버 → tmux                 │
                  └──────────────────────────────────────┘
```

### 음성 입력 흐름

**데스크톱 (Voice Daemon):**
```
핫키 Ctrl+Shift+V (또는 이어폰 Play/Pause)
  → sounddevice 마이크 녹음 (16kHz)
  → mlx-whisper / faster-whisper STT
  → tmux send-keys (활성 pane에 직접 주입)
```

**모바일 (웹 브라우저):**
```
🎤 버튼 탭
  → MediaRecorder (webm/opus)
  → POST /voice/input → 서버 STT
  → PTY write (터미널에 입력)
```

### STT/TTS 엔진 우선순위

| STT | TTS |
|-----|-----|
| 1. mlx-whisper (Apple Silicon, 가장 빠름) | 1. Kokoro (최고 품질) |
| 2. faster-whisper (범용) | 2. edge-tts (온라인, 다양한 음성) |
| | 3. macOS `say` / Windows Speech API (fallback) |

---

## 프로젝트 구조

```
랄프톤/
├── bin/
│   ├── ralph              # CLI 진입점 (bash, macOS/Linux)
│   └── ralph.ps1          # CLI 진입점 (PowerShell, Windows)
├── setup.sh               # 원클릭 설치 스크립트
├── server/
│   ├── main.py            # FastAPI (WS + REST + Voice + 토큰 인증)
│   ├── pty_manager.py     # PTY 세션 (broadcast, scrollback, EOF 감지)
│   ├── voice_handler.py   # STT (faster-whisper) + TTS (edge-tts/Kokoro)
│   ├── voice_daemon.py    # macOS 핫키 음성 데몬 (독립 실행)
│   ├── tts_hook.sh        # Claude Code Stop hook (TTS 요약)
│   ├── output_watcher.py  # 출력 감시 (기본 비활성, API로 토글)
│   ├── local_mic.py       # MacBook 마이크 (sounddevice)
│   ├── session_store.py   # 세션 메타데이터
│   └── platform_utils.py  # 크로스 플랫폼 유틸리티 (macOS/Linux/WSL2)
├── frontend/
│   ├── index.html         # xterm.js UI (탭, 검색, 온보딩, 음성 바)
│   ├── voice.js           # 마이크 + TTS + 핸즈프리 + Media Session
│   ├── manifest.json      # PWA manifest
│   └── sw.js              # Service Worker
├── .claude/skills/        # Claude Code 프로젝트 스킬
├── CLAUDE.md              # Claude Code 가이드
├── DESIGN.md              # 디자인 시스템 (Catppuccin Mocha)
└── README.md
```

---

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 새 터미널 생성 |
| DELETE | `/api/sessions/{id}` | 세션 삭제 (tmux는 detach만) |
| PATCH | `/api/sessions/{id}` | 세션 이름 변경 |
| WS | `/ws/{id}` | 터미널 WebSocket |
| WS | `/ws-notify` | 작업 완료 알림 |
| POST | `/api/tmux/create` | tmux 세션 생성 + attach |
| POST | `/api/tmux/attach` | 기존 tmux 세션 attach |
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| DELETE | `/api/tmux/kill/{name}` | tmux 세션 완전 종료 |
| POST | `/voice/input?session_id=X` | 음성 → STT → 터미널 |
| POST | `/voice/output` | 텍스트 → TTS |
| POST | `/api/upload?session_id=X` | 파일 업로드 |
| GET | `/api/download?path=X` | 파일 다운로드 |
| POST | `/api/watch/{session_id}` | 출력 감시 ON/OFF (기본 OFF) |

인증: `RALPH_TOKEN` 설정 시 `?token=xxx` 또는 `Authorization: Bearer xxx` 필요.

---

## 모바일 테스트 (adb)

```bash
# 포트 포워딩
adb reverse tcp:7777 tcp:7777

# Chrome 열기
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome

# 스크린샷 캡처
adb shell screencap -p /sdcard/test.png && adb pull /sdcard/test.png /tmp/test.png
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| Voice Daemon | macOS 핫키(Ctrl+Shift+V) 또는 이어폰 Play/Pause → STT → tmux 직접 입력 |
| 핸즈프리 모드 | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| 음성 전용 모드 | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 조작용) |
| Claude Code TTS | 작업 완료 시 Stop hook으로 결과 요약 TTS 재생 |
| API 토큰 인증 | `RALPH_TOKEN` 환경변수 설정 시 활성화 |
| tmux 세션 관리 | 웹에서 tmux 생성/attach/detach/kill |
| Scrollback 버퍼 | WebSocket 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js 검색 |
| 세션 이름 편집 | 탭 더블클릭 → 이름 변경 |
| 파일 업로드 | 보이스바 📎 버튼 → `/tmp/ralphton_uploads/`에 저장 |
| Media Session | 무선 이어폰 Play/Pause로 녹음 토글 (모바일) |
| PWA | 홈 화면에 추가하여 앱처럼 사용 |

---

## 트러블슈팅

### macOS

| 문제 | 해결 |
|------|------|
| `ralph: command not found` | `source ~/.zshrc` 또는 새 터미널 열기. `~/.local/bin`이 PATH에 있는지 확인 |
| 핫키 안 먹힘 | 시스템 설정 → 개인정보 → 접근성 → 터미널 앱 허용 |
| 포트 충돌 | `~/.ralph.env`에서 `RALPH_PORT` 변경 |
| 모바일 소리 안 남 | 화면 한 번 터치 (브라우저 autoplay 정책) |
| tmux 세션 없음 | `tmux new -s dev` 먼저 실행 |
| 서버 시작 실패 | `cat /tmp/ralphton-server.log` 확인 |
| 터널 URL 안 뜸 | `cat /tmp/cloudflared.log` 확인. `brew install cloudflared` |

### Windows (WSL2)

| 문제 | 해결 |
|------|------|
| 핫키 안 먹힘 | WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용 |
| localhost 접속 안 됨 | `wsl --shutdown` 후 WSL 재시작 |
| 사운드 안 됨 | WSLg 설정 확인 또는 브라우저 TTS 사용 |
| conda 못 찾음 | WSL2 내에서 Miniforge 설치: `wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh` |

---

## 지원 플랫폼

| 플랫폼 | 서버 | Voice Daemon | 브라우저 접속 |
|--------|------|-------------|-------------|
| macOS (Terminal/iTerm2/Warp) | ✅ | ✅ 핫키 + 이어폰 | ✅ |
| Windows (WSL2) | ✅ | ✅ WSLg 필요 | ✅ |
| Linux | ✅ | ✅ X11 필요 | ✅ |
| iOS (Safari/Chrome) | — | — | ✅ 음성 전용 + Media Session |
| Android (Chrome) | — | — | ✅ 음성 전용 + 핸즈프리 |

---

## 라이선스

MIT
