## gstack (REQUIRED — global install)

**Before doing ANY work, verify gstack is installed:**

```bash
test -d ~/.claude/skills/gstack/bin && echo "GSTACK_OK" || echo "GSTACK_MISSING"
```

If GSTACK_MISSING: STOP. Do not proceed. Tell the user:

> gstack is required for all AI-assisted work in this repo.
> Install it:
> ```bash
> git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
> cd ~/.claude/skills/gstack && ./setup --team
> ```
> Then restart your AI coding tool.

Do not skip skills, ignore gstack errors, or work around missing gstack.

Using gstack skills: After install, skills like /qa, /ship, /review, /investigate,
and /browse are available. Use /browse for all web browsing.
Use ~/.claude/skills/gstack/... for gstack file paths (the global path).

---

## 랄프톤 프로젝트 가이드

### 서버 실행

```bash
# 방법 1: 스크립트
./run_server.sh

# 방법 2: 직접 실행
cd server
/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

- conda 환경: `whisper` (faster-whisper, fastapi, edge-tts, sounddevice 포함)
- Python 경로: `/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python`

### 접속

| 환경 | URL |
|------|-----|
| 데스크톱 | `http://localhost:8000` |
| 같은 네트워크 모바일 | `http://맥북-IP:8000` (IP는 `ipconfig getifaddr en0`) |
| adb 연결 모바일 | `adb reverse tcp:8000 tcp:8000` → `http://localhost:8000` |
| 원격 (어디서든) | `cloudflared tunnel --url http://localhost:8000` → 생성된 HTTPS URL 사용 |

### 모바일 테스트 (adb)

```bash
# 1. 포트 포워딩
adb reverse tcp:8000 tcp:8000

# 2. Chrome 열기
adb shell am start -a android.intent.action.VIEW -d "http://localhost:8000" com.android.chrome

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

### E2E 테스트 방법

```bash
# 1. 세션 생성
SID=$(curl -s -X POST http://localhost:8000/api/sessions -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. WebSocket으로 명령 실행 (Python)
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect(f'ws://localhost:8000/ws/$SID') as ws:
        await ws.send(b'echo hello\n')
        for _ in range(5):
            try:
                d = await asyncio.wait_for(ws.recv(), timeout=1)
                if b'hello' in d: print('OK'); break
            except: break
asyncio.run(t())
"

# 3. TTS 테스트
curl -s -X POST http://localhost:8000/voice/output \
  -H 'Content-Type: application/json' \
  -d '{"text":"테스트"}' -o /tmp/tts.mp3 -w "bytes: %{size_download}"

# 4. 좀비 프로세스 확인
curl -s -X DELETE "http://localhost:8000/api/sessions/$SID"
ps aux | grep defunct | grep -v grep || echo "No zombies"
```

### 아키텍처

```
server/
  main.py           — FastAPI (WS + REST + Voice)
  pty_manager.py    — PTY 세션 (broadcast 패턴, subscribe/unsubscribe)
  voice_handler.py  — STT (faster-whisper) + TTS (edge-tts / macOS say)
  output_watcher.py — 출력 감시 → 작업 완료 TTS 알림
  local_mic.py      — MacBook 로컬 마이크 (sounddevice)
  session_store.py  — 세션 메타데이터

frontend/
  index.html        — xterm.js 멀티 탭 UI (모바일 최적화)
  voice.js          — 마이크 녹음 + TTS + 알림 + Media Session
  manifest.json     — PWA manifest
  sw.js             — Service Worker
```
