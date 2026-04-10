# 랄프톤 — Voice Terminal

맥북을 서버로 두고, 어디서든 브라우저로 터미널에 접속하여 음성으로 작업하는 시스템.

- 키보드 없이 이어폰만으로 Claude Code 포함 터미널 작업
- 모바일 브라우저에서 실시간 터미널 접속
- 기존 iTerm2 tmux 세션도 웹에서 이어서 작업
- 작업 완료 시 TTS로 결과 요약 알림

---

## 빠른 시작

### 1. 필수 요구사항

- macOS (Apple Silicon 권장)
- Python 3.10+ (conda `whisper` 환경 권장)
- ffmpeg (`brew install ffmpeg`)
- tmux (`brew install tmux`) — 기존 세션 연결용, 선택사항

### 2. 설치

```bash
git clone <repo-url> 랄프톤
cd 랄프톤

# Python 의존성 설치 (conda 환경 사용 시)
conda create -n whisper python=3.10
conda activate whisper
pip install -r requirements.txt

# 또는 기존 환경에 설치
pip install fastapi 'uvicorn[standard]' faster-whisper edge-tts sounddevice numpy
```

### 3. 서버 실행

```bash
./run_server.sh
```

또는 직접:

```bash
cd server
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

시작하면 다음 URL이 표시됩니다:

```
랄프톤 Voice Terminal Server
  http://localhost:8000
  http://192.168.x.x:8000
```

### 4. 접속

| 환경 | 방법 |
|------|------|
| **같은 맥북** | `http://localhost:8000` |
| **같은 WiFi 모바일** | `http://맥북-IP:8000` |
| **USB 연결 Android** | `adb reverse tcp:8000 tcp:8000` → `http://localhost:8000` |
| **어디서든 (원격)** | 아래 Cloudflare Tunnel 참고 |

---

## 주요 기능

### 웹 터미널

브라우저에서 실제 터미널이 동작합니다. xterm.js 기반.

- **멀티 탭**: `+ New` 버튼으로 여러 터미널 동시 사용
- **세션 유지**: 같은 세션에 데스크톱 + 모바일 동시 접속 가능
- **자동 재연결**: 네트워크 끊겨도 자동 복구

### tmux 세션 연결

iTerm2나 터미널에서 tmux로 시작한 작업을 웹에서 이어갈 수 있습니다.

```bash
# 맥북 터미널에서 tmux 세션 시작
tmux new -s work

# 작업 후 detach (Ctrl+B, D)
# → 웹 UI에서 [tmux] 버튼 클릭 → "work" 세션 선택 → 이어서 작업
```

### 음성 입력 (STT)

마이크 버튼(🎤)을 눌러 음성으로 터미널에 명령 입력.

- **브라우저 마이크**: 모바일/데스크톱 모두 지원
- **로컬 마이크**: MacBook 마이크 직접 사용 (API 호출)
- **엔진**: faster-whisper (로컬 처리, 네트워크 불필요)

### 작업 완료 TTS 알림

터미널 명령이 끝나면 자동으로 결과를 음성으로 알려줍니다.

- 3초간 출력이 없으면 "작업 완료"로 판단
- 출력 내용을 요약해서 TTS 생성
- 데스크톱: 스피커로 직접 재생
- 모바일: WebSocket으로 push + 토스트 알림

### 이어폰 터치 컨트롤

무선 이어폰의 미디어 컨트롤로 음성 입력을 조작합니다. (Media Session API)

- **한 번 탭** (play/pause): 녹음 시작/중지
- **더블 탭** (next track): 녹음 토글

### PWA 설치

모바일 Chrome에서 "홈 화면에 추가"로 앱처럼 사용 가능합니다.
HTTPS 환경(Cloudflare Tunnel)에서만 설치 가능.

---

## 원격 접속 (Cloudflare Tunnel)

같은 네트워크가 아닌 곳에서도 접속할 수 있습니다. **무료, 계정 불필요.**

```bash
# 설치 (한 번만)
brew install cloudflared

# 터널 시작 (서버 실행 후)
cloudflared tunnel --url http://localhost:8000
```

출력되는 `https://xxx.trycloudflare.com` URL로 어디서든 접속.

> 주의: Quick Tunnel은 서버 재시작마다 URL이 바뀝니다.
> 고정 URL이 필요하면 Cloudflare 계정으로 Named Tunnel을 설정하세요.

---

## 프로젝트 구조

```
랄프톤/
├── server/
│   ├── main.py            # FastAPI 서버 (WS + REST + Voice)
│   ├── pty_manager.py     # PTY 세션 관리 (broadcast 패턴)
│   ├── voice_handler.py   # STT (faster-whisper) + TTS (edge-tts)
│   ├── output_watcher.py  # 출력 감시 → 작업 완료 TTS 알림
│   ├── local_mic.py       # MacBook 로컬 마이크 (sounddevice)
│   └── session_store.py   # 세션 메타데이터
├── frontend/
│   ├── index.html         # xterm.js 멀티 탭 UI
│   ├── voice.js           # 마이크 + TTS + 알림 + Media Session
│   ├── manifest.json      # PWA manifest
│   └── sw.js              # Service Worker
├── requirements.txt
├── run_server.sh          # 서버 실행 스크립트
├── CLAUDE.md              # Claude Code 가이드라인
└── README.md              # 이 파일
```

---

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 새 터미널 세션 생성 |
| DELETE | `/api/sessions/{id}` | 세션 삭제 |
| WS | `/ws/{id}` | 터미널 WebSocket |
| WS | `/ws-notify` | 작업 완료 알림 수신 |
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| POST | `/api/tmux/attach` | tmux 세션에 attach |
| POST | `/voice/input?session_id=X` | 음성 → STT → 터미널 입력 |
| POST | `/voice/output` | 텍스트 → TTS → 오디오 |
| POST | `/voice/local/start` | MacBook 마이크 녹음 시작 |
| POST | `/voice/local/stop?session_id=X` | 녹음 중지 → STT |
| POST | `/api/watch/{id}` | 출력 감시 설정 |

---

## 설정

### 환경 변수

```bash
HOST=0.0.0.0     # 바인드 주소 (기본값)
PORT=8000         # 포트 (기본값)
```

### STT 엔진 우선순위

1. mlx-whisper (Apple Silicon, 가장 빠름)
2. faster-whisper (범용)

### TTS 엔진 우선순위

1. Kokoro (로컬, 설치 필요)
2. edge-tts (Microsoft, 네트워크 필요)
3. macOS say (항상 사용 가능, 한국어: Yuna 음성)

---

## 트러블슈팅

### "서버 연결 끊김" 표시

서버가 재시작되었습니다. 브라우저가 자동 재연결합니다 (최대 15회).

### 모바일에서 소리가 안 남

브라우저 autoplay 정책 때문입니다. 화면을 한 번 터치하면 이후 자동 재생됩니다.
"터치하여 재생" 버튼이 표시되면 클릭하세요.

### tmux 버튼 눌렀는데 "세션 없음"

먼저 터미널에서 `tmux new -s 이름`으로 세션을 생성해야 합니다.

### 음성 인식이 안 됨

- 마이크 권한 확인 (브라우저 설정)
- ffmpeg 설치 확인: `which ffmpeg`
- faster-whisper 설치 확인: `python -c "import faster_whisper"`

### 원격에서 WebSocket 연결 안 됨

Cloudflare Tunnel을 사용하세요. 일반 HTTP 포워딩은 WebSocket을 지원하지 않을 수 있습니다.

---

## 라이선스

MIT
