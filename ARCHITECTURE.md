# voice-terminal 아키텍처

> **버전:** v1.1.0 (2026-05-06) — 변경 이력은 [CHANGELOG.md](./CHANGELOG.md)

이 문서는 기여자와 LLM이 레포 구조를 빠르게 이해하기 위한 지도입니다. 모노레포 전환 대신 **논리적 경계**만 명시합니다.

---

## 1. 3-Plane 모델

```
┌──────────────────────────────────────────────────────────────┐
│ Control Plane — 시작/정지/진단 (사용자 한정 동작)              │
│   bin/vt, install.sh, ~/.vt.env                 │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Work Plane — 실제 작업이 벌어지는 곳                           │
│   tmux 세션 (dev) ← Claude / aider / codex / 쉘 / psql ...    │
│   ↑ Voice Daemon이 send-keys로 키 주입                        │
│   ↑ 모바일 브라우저가 WebSocket으로 attach                     │
└──────────────────────────────────────────────────────────────┘
                            ▲                       ▲
                            │                       │
┌───────────────────────────┴─────┐   ┌─────────────┴──────────┐
│ Voice Plane — STT/TTS          │   │ Network Plane           │
│   server/voice_handler.py      │   │   cloudflared 터널      │
│   server/voice_daemon.py       │   │   토큰 인증 미들웨어     │
│   server/local_mic.py          │   │   ntfy/Telegram 푸시    │
│   frontend/voice.js            │   │                         │
└────────────────────────────────┘   └─────────────────────────┘
```

**핵심 아이디어**: tmux 세션이 **단일 진실의 원천(single source of truth)**. 데스크톱 iTerm, 모바일 PWA, Voice Daemon이 모두 같은 tmux에 붙어서 동작한다.

### 1.1 단일 tmux 서버 원칙 (Phase 6)

vt의 모든 클라이언트는 격리된 tmux 소켓 `-L vt` (`VT_TMUX_SOCKET` 환경변수로 오버라이드 가능)에 접속한다. 사용자의 기존 `tmux ls` 세션과 분리되며, 4개 클라이언트가 같은 서버를 공유한다:

| 클라이언트 | 호출 형태 | 출처 |
|------------|-----------|------|
| `bin/vt` (CLI) | `${TMUX_BASE[@]} ...` (`tmux -L vt`) | `bin/vt` 상단 정의 |
| `server/main.py` (PTY) | `tmux -L vt attach-session ...` | `pty_manager` 경로 |
| `server/voice_daemon.py` | `TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]` | Phase 6 #6-1 |
| Stop hook (`tts_hook.sh`) | (TTS만, tmux 직접 호출 없음) | — |

소켓을 통일하지 않으면 Voice Daemon 입력이 모바일·웹과 분리되어 "왜 안 들어가지?" 류 디버깅이 발생한다.

---

## 2. 디렉토리별 책임

### `bin/` — CLI 진입점 (Control Plane)
| 파일 | 책임 |
|---|---|
| `vt` | macOS/Linux. CLI — 서브커맨드 라우팅, 프로세스 수명 관리 (서버·터널·음성 데몬), iTerm 자동 오픈, 진단 |
| `vt.ps1` | Windows PowerShell 버전 |

서브커맨드: `voice` · `mobile` · `start` · `stop` · `status` · `claude` · `handoff` · `doctor`

### `server/` — FastAPI 백엔드 (Work + Voice Plane)
| 파일 | 책임 | 주요 의존 |
|---|---|---|
| `main.py` | FastAPI 앱, REST/WS 라우팅, 토큰 미들웨어 | pty_manager, session_store, output_watcher, voice_handler, notify, platform_utils |
| `pty_manager.py` | PTY fork, WebSocket broadcast, scrollback 버퍼 | — |
| `session_store.py` | 세션 메타데이터 (이름, tmux_name), `new_session_id()` | secrets |
| `output_watcher.py` | idle 감지 → TTS + 푸시 알림 | voice_handler, notify |
| `voice_handler.py` | STT (mlx-whisper → faster-whisper) · TTS (Kokoro → edge-tts → say) | platform_utils |
| `voice_daemon.py` | macOS 핫키(Ctrl+Shift+V) → 녹음 → STT → tmux send-keys | pynput, sounddevice, whisper |
| `local_mic.py` | 데스크톱 로컬 마이크 REST API | sounddevice |
| `notify.py` | ntfy/Telegram 비동기 푸시 브릿지 | urllib, asyncio |
| `platform_utils.py` | OS 감지, 기본 셸, tmux 경로, 로컬 IP, TTS fallback | platform, shutil |
| `tts_hook.sh` | Claude Code Stop hook — 응답 완료 시 TTS + ntfy | server/voice/output |

### `frontend/` — xterm.js PWA
| 파일 | 책임 |
|---|---|
| `index.html` | UI 레이아웃, xterm.js 멀티탭, tmux 자동 attach, `#tmux=<name>` hash 처리 |
| `voice.js` | MediaRecorder 녹음, TTS 재생, Media Session API, 핸즈프리/음성 전용 모드 |
| `manifest.json` | PWA 설정 (아이콘, 홈 화면 추가) |
| `sw.js` | Service Worker (오프라인 캐싱) |

### 루트
| 파일 | 책임 |
|---|---|
| `install.sh` | Python venv 생성, 프로필별 패키지 설치, vt 심링크, ~/.vt.env 초기화 |
| `requirements-core.txt` | 터미널 전용 (~50MB) |
| `requirements-voice.txt` | 음성 추가 의존성 (~1.5GB) |
| `requirements.txt` | 위 둘 합침 (하위 호환) |

### `.claude/skills/` — Claude Code 스킬
| 파일 | 트리거 |
|---|---|
| `vt/SKILL.md` | 전역: "음성 모드", "모바일 접속" 등 |
| `vt-voice.md` | Voice Daemon 수동 설치/실행 |
| `vt-mobile.md` | 모바일 adb 테스트 |
| `vt-start.md` | 서버 수동 시작 |

---

## 3. 주요 데이터 흐름

### 3.1 데스크톱 음성 입력 (Voice Daemon)
```
Ctrl+Shift+V (pynput)
  → sounddevice 16kHz mono 녹음
  → mlx-whisper / faster-whisper STT
  → tmux send-keys <active-pane> "<text>"
```

### 3.2 모바일 음성 입력 (PWA)
```
🎤 버튼 (voice.js)
  → MediaRecorder (webm/opus)
  → POST /voice/input?session_id=...
  → voice_handler.transcribe (ffmpeg 변환 포함)
  → pty_mgr.write(session_id, text)  → PTY → tmux
```

### 3.3 Claude 응답 완료 → TTS + 푸시
```
Claude Code Stop hook → server/tts_hook.sh
  ├─ transcript에서 마지막 assistant 응답 추출
  ├─ POST /voice/output → edge-tts → afplay (로컬 재생)
  └─ POST ntfy (VT_NOTIFY_URL 설정 시) → 폰 푸시
```

### 3.4 모바일 ↔ 데스크톱 핸드오프
```
데스크톱:  tmux 세션 'dev' 생성 (bin/vt)
  ↓ (같은 OS의 tmux server에 등록됨)
데스크톱 iTerm:  tmux attach -t dev
모바일 브라우저:  GET /?...#tmux=dev
  → frontend/index.html이 hash 파싱
  → POST /api/tmux/attach {name:"dev"}
  → 서버: pty.fork() → exec "tmux attach -t dev"
  → WebSocket으로 화면 중계
```

**포인트**: 양쪽이 **같은 tmux 세션의 다른 클라이언트**일 뿐. 버퍼·스크롤백·프로세스 모두 공유.

### 3.5 idle 감지 → 푸시 (OutputWatcher)
```
PTY 출력 → output_watcher.feed_output()
  → 버퍼에 쌓임
  → idle_timeout(3s) 초과 시
  → summary 생성 → TTS 합성
  → notify.send() (ntfy/Telegram 병렬)
```

---

## 4. 확장 포인트

새 기능을 붙일 때 어디를 건드려야 하는지.

### 4.1 새 STT 엔진 추가
- `server/voice_handler.py`의 우선순위 리스트에 삽입
- mlx-whisper → faster-whisper 순서 참고

### 4.2 새 TTS 엔진 추가
- `server/voice_handler.py` synthesize() 함수의 fallback 체인
- 바이트 반환 or 직접 재생 두 경로 모두 지원

### 4.3 새 푸시 알림 채널 (예: Discord, Slack)
- `server/notify.py`에 `_send_xxx()` 함수 추가
- `is_configured()` 및 `send()`에서 병렬 task 리스트에 포함
- 환경변수 규칙: `VT_XXX_TOKEN` / `VT_XXX_WEBHOOK`

### 4.4 새 CLI 서브커맨드
- `bin/vt`의 main switch에 케이스 추가
- 함수명 규칙: `cmd_<이름>()`
- help 섹션 문자열에 한 줄 추가

### 4.5 새 AI 에이전트 (Claude 외)
- **별도 래퍼 불필요.** 사용자가 그냥 tmux 안에서 `aider` / `codex` / 등을 실행하면 음성·모바일이 모두 동작함 (범용 tmux 주입 설계의 이점)
- Claude Code Stop hook과 유사한 완료 알림이 필요하면 해당 도구의 종료 이벤트를 `tts_hook.sh` 스타일로 작성

### 4.6 새 엔드포인트
- `server/main.py`에 `@app.<method>("/api/...")` 추가
- 토큰 인증은 middleware가 자동 처리 (`/sw.js`, `/manifest.json` 등 화이트리스트 제외)
- 위험한 작업은 session_id로 제한

---

## 5. 실행 시 프로세스 맵

```
$ vt start
  ├─ uvicorn server.main:app  (port 7777)                [서버]
  ├─ cloudflared tunnel --url ...                        [터널]
  ├─ python server/voice_daemon.py                       [음성 데몬]
  └─ tmux server (새 session: dev)                       [tmux]
      └─ zsh (또는 claude --resume)                      [작업 셸]
```

PID는 `/tmp/vt-pids/{server,tunnel,voice}.pid`에 저장됨. `vt stop`이 모두 정리.

---

## 6. 보안 모델 (현재 상태)

| 계층 | 메커니즘 | 한계 |
|---|---|---|
| 전송 | cloudflared HTTPS 터널 | — |
| 인증 | `VT_TOKEN` 쿼리/Bearer 헤더 | 평문 토큰, QR에 노출 |
| WebSocket 인증 | 미들웨어가 accept 전 검증 | — |
| 세션 ID | `secrets.token_urlsafe(12)` — 16자, ~96비트 | — |
| E2E | **없음** (서버가 평문 봄) | TODO: D3 |
| 업로드 | `/tmp/vt-uploads/` 격리 | 디스크 쿼터 없음 |

**D3 (E2E 라이트)**가 구현되면 `libsodium SecretBox`로 WebSocket 페이로드 자체를 암호화하여 cloudflared URL이 노출돼도 코드 평문 유출을 막을 계획.

---

## 7. 로드맵 (요약)

`/Users/neo/.claude/plans/adaptive-leaping-cray.md`에 상세. 현재 완료된 개선:
- ✅ D1 원라인 설치 스크립트
- ✅ D2 ntfy/Telegram 푸시 브릿지
- ✅ D4 `vt claude` / `vt handoff` 서브커맨드
- ✅ D5 세션 ID 확장 (`secrets.token_urlsafe(12)`)
- ✅ D6 본 문서
- ✅ D7 `vt doctor` 진단

남은 작업:
- ⏳ D3 터널 페이로드 E2E 암호화
- ⏳ D8 barge-in + 언어 감지
