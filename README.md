# voice-terminal

[![Version](https://img.shields.io/badge/version-1.5.0-blue.svg)](./CHANGELOG.md)
[![Changelog](https://img.shields.io/badge/changelog-Keep%20a%20Changelog-orange.svg)](./CHANGELOG.md)

macOS / Linux 머신을 서버로 두고, 어디서든 음성으로 터미널을 조작하는 시스템. (Windows는 WSL2 환경에서만 동작 — 네이티브 미지원)

- **모바일에서 터미널 접속** — QR 스캔하면 바로 tmux 연결
- **음성으로 코딩** — 노션 작업 중에도 핫키(Ctrl+Shift+V)로 음성 입력
- **Claude Code 연동** — 작업 완료 시 TTS로 결과 요약 알림
- **전부 무료** — API 키, 구독 없음. 오픈소스 STT/TTS

```
https://github.com/NeTrioGit/voice-terminal
```

---

## 설치

```bash
# 터미널만 (경량, ~50MB)
curl -fsSL https://raw.githubusercontent.com/NeTrioGit/voice-terminal/master/install.sh | bash

# 터미널 + 음성 모드 (~1.5GB, Whisper STT + edge-tts TTS)
curl -fsSL https://raw.githubusercontent.com/NeTrioGit/voice-terminal/master/install.sh | bash -s voice
```

또는 클론 후 로컬 실행:

```bash
git clone https://github.com/NeTrioGit/voice-terminal.git ~/voice-terminal
cd ~/voice-terminal
./install.sh            # 터미널만
./install.sh voice      # 음성 모드 포함
```

`install.sh`가 하는 일:
1. Python `venv` 생성 (`.venv/`, conda 불필요)
2. 선택 프로필에 맞는 패키지 설치
3. `~/.local/bin/vt` 심링크 등록
4. `~/.vt.env` 설정 파일 자동 생성
5. PATH 갱신 (zsh/bash)

> Whisper 모델은 첫 실행 시 Hugging Face에서 자동 다운로드됩니다 (~141MB).

---

## 설치 후 통합 (선택)

새 터미널 창을 열면 자동으로 `tmux -L vt new -A -s dev` 진입하도록 통합. 둘 중 하나를 선택.

### 방식 A — 터미널 profile 자동 등록 (권장)

```bash
vt install-profiles --dry-run   # 변경 미리보기
vt install-profiles             # 실제 적용
```

iTerm2는 Dynamic Profile 자동 등록. Ghostty / WezTerm / Kitty / Alacritty / Windows Terminal / Terminal.app은 config snippet 안내 출력 (사용자가 복사·붙여넣기). p10k instant prompt와 충돌 없음.

### 방식 B — 셸 init (SSH 원격, profile 불가 환경)

```bash
echo 'eval "$(vt shell-init zsh)"' >> ~/.zshrc      # zsh
echo 'eval "$(vt shell-init bash)"' >> ~/.bashrc    # bash
vt shell-init fish >> ~/.config/fish/config.fish    # fish
vt shell-init pwsh >> $PROFILE                       # PowerShell
```

생성되는 스니펫은 5중 TTY 가드 포함 (`interactive` + TTY + `$TMUX` 비어있음 + IDE 임베디드 셸 차단 + tmux 존재). p10k instant prompt 활성 zsh에서도 콘솔 출력 0건.

### 단일 tmux 서버 원칙

`vt` CLI · server · Voice Daemon · hook이 모두 `-L vt` 격리 소켓을 사용 → 모든 클라이언트(데스크톱·모바일·Voice Daemon)가 같은 세션 공유. 사용자의 기존 `tmux ls` 세션과는 자동 분리.

---

## vt CLI

어디서든 `vt` 명령으로 Voice Terminal을 제어합니다.

### 명령 일람

| 명령 | 설명 |
|------|------|
| `vt voice` | 음성 모드 — 서버 + Voice Daemon 백그라운드 시작 |
| `vt mobile [--e2e] [--safe]` | 모바일 접속 — `--safe` 시 위험 명령 차단 |
| `vt start` | 전체 시작 — 서버 + 터널 + 음성 데몬 |
| `vt stop [--purge]` | 종료 — `--purge`는 tmux 세션까지 완전 종료 |
| `vt status` | 서버·터널·Voice Daemon·tmux 상태 확인 |
| `vt manage` | TUI 관리 도구 — 세션 목록/rename/kill/attach + 음성 타깃 lock |
| `vt attach [name]` | 임의 tmux 세션을 새 OS 터미널 창에 attach |
| `vt ssh [session]` | Tailscale + SSH로 tmux 세션에 직접 접속하는 명령 안내 (회사망 등 화면 원격 차단 환경용) |
| `vt voice-target [name\|--auto]` | Voice Daemon 타깃 세션 lock/해제 |
| `vt hotkey [list\|set\|reset\|disable]` | 핫키 조회/변경 |
| `vt help <topic>` | 토픽별 도움말 (concepts/voice/hotkeys/target/troubleshoot) |
| `vt claude` | 새 터미널 창에 `tmux dev` + `claude --resume` 오픈 |
| `vt agent <name>` | claude/codex/aider/gemini 시작 (일반화) |
| `vt handoff mobile/desktop` | 기기 간 tmux 세션 핸드오프 |
| `vt template [save\|apply\|list\|rm] <name>` | CLAUDE.md 템플릿 관리 |
| `vt popup <action>` | tmux 3.2+ popup으로 빠른 호출 |
| `vt run "..."` | headless `claude -p` 백그라운드 + TTS 알림 |
| `vt doctor` | 설치·환경 진단 (자동 점검) |
| `vt install-profiles [--dry-run]` | 터미널 앱 profile 자동 등록 (iTerm2 Dynamic Profile 등) |
| `vt shell-init [zsh\|bash\|fish\|pwsh]` | 셸별 안전 통합 스니펫 출력 (TTY 5중 가드) |

### 옵션

```bash
vt mobile --e2e                       # X25519 핸드셰이크 + NaCl SecretBox E2E 암호화
vt mobile --network <mode>            # localhost | lan | tailscale | all(default)
```

`tailscale` 모드는 Cloudflare Tunnel 없이 자신의 tailnet IP로만 서버를 열고,
네트워크 정책도 `tailscale` CIDR(`100.64.0.0/10`)+localhost로만 제한합니다.
회사망처럼 LAN 자체를 못 믿는 환경에서 안전하게 원격 접속하고 싶을 때 사용하세요.
자세한 내용: [Tailscale + SSH 원격 접속](#tailscale--ssh-원격-접속-회사망-등-화면-원격이-막힌-환경) 섹션 참고.

### 자동 동작

`vt voice` / `mobile` / `start` 실행 시:
- 현재 쓰는 터미널 앱 자동 감지 → 새 창 오픈 → `tmux new -A -s dev 'claude --resume'` 실행
- 지원 앱: **iTerm2**, **Ghostty**, **WezTerm**, **Kitty**, **Alacritty**, **Warp**, **Terminal.app**
- 이미 tmux 안에 있으면 새 창 없이 현재 창에서 계속 (`$TMUX` 체크, 멱등성 보장)

### `vt doctor` 점검 항목 (13개)

| # | 항목 | 내용 |
|---|------|------|
| 1 | Python | 경로·버전 확인 |
| 2 | venv | `.venv` 또는 legacy conda env |
| 3 | core packages | fastapi, uvicorn |
| 4 | voice packages | faster-whisper, edge-tts, sounddevice |
| 5 | tmux | 설치 여부 및 버전 |
| 6 | cloudflared | 원격 접속 도구 |
| 7 | ffmpeg | 모바일 음성 디코딩 |
| 8 | port | VT_PORT 사용 상태 |
| 9 | vt CLI | `~/.local/bin/vt` 심링크 |
| 10 | PATH | `~/.local/bin` 포함 여부 |
| 11 | .vt.env | 설정 파일 존재 여부 |
| 12 | VT_TOKEN | 인증 토큰 설정 여부 |
| 13 | 터미널 앱 | 감지된 앱 목록 + 현재 `TERM_PROGRAM` |

---

## 설정 (`~/.vt.env`)

`install.sh`가 자동 생성. 원하는 항목만 추가하면 됩니다.

```bash
# 기본
VT_PORT=7777                              # 서버 포트 (기본값)
VT_PYTHON=~/voice-terminal/.venv/bin/python  # Python 경로 (자동 감지)

# 원격 인증 (공개 터널 사용 시 강력 권장)
VT_TOKEN=my-secret-token                  # ?token=xxx 또는 Bearer 헤더

# 푸시 알림 (ntfy / Telegram, 병렬 가능)
VT_NOTIFY_URL=https://ntfy.sh/your-topic  # ntfy.sh 토픽 URL
VT_TELEGRAM_TOKEN=...                     # Telegram Bot 토큰
VT_TELEGRAM_CHAT_ID=...                   # Telegram 수신 채팅 ID

# 보안
VT_E2E=1                                  # 모든 WebSocket 강제 E2E (기본: opt-in)
VT_SAFE_MODE=1                            # 위험 명령(rm -rf /, sudo 등) 사전 차단
VT_TMUX_SOCKET=vt                         # tmux 격리 소켓 이름 (기본: vt)

# 음성
VT_STT_LANG=ko                            # STT 언어 고정 (미설정 시 자동 감지)
```

---

## Claude Code 스킬

`.claude/skills/` 에 등록된 프로젝트 전용 스킬.

### `/vt` (전역 스킬)

`~/.claude/skills/vt/SKILL.md` — **어느 프로젝트에서든 호출 가능**.

| 트리거 | 예시 발화 |
|--------|-----------|
| 음성 모드 | "음성 모드", "voice mode", "음성으로 코딩" |
| 모바일 | "모바일 접속", "mobile mode", "폰에서 접속" |
| 시작/종료 | "vt 시작", "vt 중지" |
| 진단 | "vt 점검", "진단 실행" |
| 핸드오프 | "폰으로 넘겨", "맥으로 가져와" |

실행 전 `vt status`로 중복 시작 여부 자동 체크.

### `/vt-start` (프로젝트 스킬)

`서버 시작 + Cloudflare Tunnel 원격 접속` 원스텝 처리.

1. 기존 프로세스 확인 (`lsof -i :7777`)
2. uvicorn 서버 시작 → 헬스체크 (3초 후)
3. tmux 세션 준비
4. cloudflared 터널 시작 → URL 추출 대기 (최대 30초)
5. 접속 정보 출력 (로컬/네트워크/원격)

### `/vt-mobile` (프로젝트 스킬)

Android adb 연결 + 모바일 테스트 자동화.

- adb devices 확인
- 접속 방식 선택: **로컬 USB** (`adb reverse`) 또는 **원격 Wi-Fi** (Cloudflare Tunnel)
- Chrome 자동 오픈 (`adb shell am start`)
- 스크린샷 캡처 (`adb shell screencap`)
- 동기화 테스트 (tmux send-keys → 스크린샷 비교)

테스트 체크리스트:
- [ ] 웹 UI 로드 (탭 바, 터미널, 보이스바)
- [ ] tmux 세션 자동 attach
- [ ] CLI → 모바일 실시간 동기화
- [ ] TTS "터치하여 재생" 버튼
- [ ] 마이크 녹음 → STT → tmux 입력
- [ ] 핸즈프리 모드 토글
- [ ] 파일 업로드 (📎)

### `/vt-voice` (프로젝트 스킬)

Voice Daemon 의존성 확인·설치·실행.

1. `~/.vt.env`에서 `VT_PYTHON` 읽기
2. pynput · sounddevice · numpy 의존성 확인
3. STT 엔진 감지 (mlx-whisper 우선 → faster-whisper fallback)
4. tmux 세션 준비
5. 데몬 백그라운드 실행 및 macOS 접근성 권한 안내

---

## Claude Code 훅

### Pre / Post / Stop 훅 — Agent 상태 + TTS

`server/agent_hook.sh` — Claude의 도구 사용 시작·종료·완료를 모바일 UI에 실시간 반영 + Stop 시 기존 TTS 동작.

**등록 위치:** `~/.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse":  [{ "command": "<repo>/server/agent_hook.sh pre"  }],
    "PostToolUse": [{ "command": "<repo>/server/agent_hook.sh post" }],
    "Stop":        [{ "command": "<repo>/server/agent_hook.sh stop" }]
  }
}
```

서버는 `POST /api/agent/event`로 받아 `_state` 갱신 + `WS /ws-agent`로 브로드캐스트.

### Stop 훅 — TTS 자동 요약

`server/tts_hook.sh` — Claude Code가 응답을 완료할 때마다 실행.

**등록 위치:** `~/.claude/settings.json` → `hooks.Stop`

**동작 흐름:**
```
Claude 응답 완료
  → transcript_path에서 마지막 assistant 메시지 추출 (최대 200자)
  → POST /voice/output → TTS 오디오 → afplay 재생
  → fallback: 서버 미실행 시 macOS say -v Yuna
  → (VT_NOTIFY_URL 설정 시) ntfy.sh 푸시 알림 병행
```

**테스트:**
```bash
echo '{"transcript_path":"/tmp/test.jsonl"}' | ./server/tts_hook.sh
```

### PreToolUse 훅 — gstack 설치 확인

`.claude/hooks/check-gstack.sh` — Skill 도구 호출 전마다 실행.

`~/.claude/skills/gstack/bin` 디렉토리가 없으면 스킬 실행을 차단하고 설치 안내 출력.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **Voice Daemon** | macOS 핫키(Ctrl+Shift+V) 또는 이어폰 Play/Pause → STT → tmux 직접 입력 |
| **핸즈프리 모드** | 모바일 🔄 버튼 → 연속 녹음/STT 자동 반복 |
| **음성 전용 모드** | 🎧 버튼 → 터미널 숨기고 큰 마이크만 표시 (이어폰 전용 조작) |
| **barge-in** | 🎤 탭 또는 Ctrl+Shift+V 시 재생 중인 TTS 즉시 중단 (`POST /voice/cancel`) |
| **Claude Code TTS** | Stop 훅 → 응답 완료 시 요약 TTS 자동 재생 |
| **E2E 암호화** | `--e2e` 플래그 → X25519 키교환 + NaCl SecretBox WebSocket 암호화 |
| **푸시 알림** | ntfy.sh / Telegram 병렬 브릿지, 작업 완료·세션 idle 시 자동 발송 |
| **API 토큰 인증** | `VT_TOKEN` 설정 시 쿼리스트링·Bearer 헤더 인증 |
| **tmux 세션 관리** | 웹에서 생성/attach/detach/kill. 데스크톱과 동시 접속 가능 |
| **Scrollback 버퍼** | WebSocket 재접속 시 이전 출력 복원 (최대 5000 청크) |
| **터미널 검색** | Ctrl+F / Cmd+F → xterm.js 검색 addon |
| **세션 이름 편집** | 탭 더블클릭 → 이름 변경 (PATCH API) |
| **파일 업로드/다운로드** | 보이스바 📎 → `/tmp/vt-uploads/` 저장. GET /api/download 다운로드 |
| **Media Session** | 무선 이어폰 Play/Pause로 녹음 토글 (iOS·Android) |
| **PWA** | manifest + Service Worker → 홈 화면 추가 후 앱처럼 사용 |
| **세션 ID 96비트** | 추측 난이도 2^64배 (기존 32비트 대비) |
| **Tailscale 원격 접속** | `vt ssh` / `vt mobile --network tailscale` — 화면 원격이 막힌 회사망 등에서 SSH로 tmux에 직접 접속 |
| **클라이언트 접속 알림** | `VT_NOTIFY_CLIENT_EVENTS=1` — SSH 등 서버가 못 보는 클라이언트의 attach/detach를 push로 알림 |

---

## API 레퍼런스

인증: `VT_TOKEN` 설정 시 `?token=xxx` 또는 `Authorization: Bearer xxx` 필요.

### 세션

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 새 터미널 세션 생성 (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{id}` | 세션 삭제 (tmux는 detach만) |
| PATCH | `/api/sessions/{id}` | 세션 이름 변경 (JSON: name) |

### tmux

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/tmux/sessions` | tmux 세션 목록 |
| POST | `/api/tmux/create` | tmux 세션 생성 + 자동 attach (JSON: name, cols, rows) |
| POST | `/api/tmux/attach` | 기존 tmux 세션 attach (JSON: name) |
| DELETE | `/api/tmux/kill/{name}` | tmux 세션 완전 종료 |

### WebSocket

| 경로 | 설명 |
|------|------|
| `/ws/{id}` | 터미널 WebSocket (xterm.js 양방향) |
| `/ws-notify` | 작업 완료 알림 수신 |

### 음성

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/voice/input?session_id=X&lang=ko` | 음성 파일 → STT → 터미널 입력 (lang 생략 시 자동) |
| POST | `/voice/output` | 텍스트 → TTS → 오디오 반환 (JSON: text) |
| POST | `/voice/cancel` | 재생 중인 TTS 즉시 중단 (barge-in) |
| POST | `/voice/local/start` | MacBook 마이크 녹음 시작 |
| POST | `/voice/local/stop?session_id=X` | 녹음 종료 → STT → 터미널 입력 |

### 기타

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/watch/{session_id}` | 출력 감시 ON/OFF (JSON: enabled, timeout) |
| POST | `/api/upload?session_id=X` | 파일 업로드 (multipart/form-data) |
| GET | `/api/download?path=X` | 서버 파일 다운로드 |
| GET | `/api/notify/status` | 푸시 알림 채널 설정 상태 확인 |
| POST | `/api/notify/test` | 테스트 푸시 전송 |
| POST | `/api/notify/client-event` | tmux client-attached/detached 훅 전용 (D9, SSH 접속 가시화) |
| GET | `/api/capabilities` | 서버가 지원하는 기능 목록 |
| GET | `/api/tailscale/status` | Tailscale 설치/연결/IP/MagicDNS 호스트명 (D9) |

---

## 아키텍처

자세한 내용은 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 참조 (control / work / voice 3-plane 모델).

```
                  ┌──────────────────────────────────────┐
                  │  MacBook / WSL2 (서버)                 │
                  │                                      │
  [vt voice]      │  ┌────────────────┐  ┌────────────┐  │
  Ctrl+Shift+V ──►│  │ Voice Daemon   │  │ FastAPI     │  │
  → STT → tmux   │  │ (독립, 서버무관) │  │ :7777       │  │
                  │  └────────────────┘  └─────┬──────┘  │
                  │                            │         │
                  │  ┌────────────────┐  ┌─────┴──────┐  │
                  │  │ tmux sessions  │◄─┤ PTY Manager │  │
                  │  │ (데스크/폰 공유) │  │ + Scrollback│  │
                  │  └────────────────┘  └─────┬──────┘  │
                  │                            │         │
                  │        ┌───────────────────┴──────┐  │
                  │        │ Notify → ntfy / Telegram  │  │
                  │        │ (idle / 완료 감지)         │  │
                  │        └──────────────────────────┘  │
                  └──────────────┬───────────────────────┘
                                 │ Cloudflare Tunnel (HTTPS + opt-in E2E)
                  ┌──────────────┴───────────────────────┐
                  │  모바일 / 원격 브라우저                  │
                  │  xterm.js + 음성 전용 모드              │
                  │  🎤 STT → 서버 → tmux                 │
                  │  🔒 E2E: X25519 + NaCl SecretBox      │
                  └──────────────────────────────────────┘
```

### STT / TTS 엔진 우선순위

| STT | TTS |
|-----|-----|
| 1. mlx-whisper (Apple Silicon 최적) | 1. Kokoro (최고 품질) |
| 2. faster-whisper (범용) | 2. edge-tts (온라인, 다양한 음성) |
| | 3. macOS `say` / Windows Speech API (fallback) |

### 프로젝트 구조

```
voice-terminal/
├── bin/
│   ├── vt                 # CLI 진입점 (bash, macOS/Linux)
│   └── vt.ps1             # CLI 진입점 (PowerShell, Windows)
├── server/
│   ├── main.py            # FastAPI (WS + REST + Voice + 토큰 인증)
│   ├── pty_manager.py     # PTY 세션 (broadcast, scrollback, EOF 감지)
│   ├── voice_handler.py   # STT (faster-whisper) + TTS (edge-tts/Kokoro)
│   ├── voice_daemon.py    # macOS 핫키 음성 데몬 (독립 실행)
│   ├── crypto_channel.py  # X25519 + NaCl SecretBox E2E 암호화
│   ├── notify.py          # ntfy / Telegram 푸시 브릿지
│   ├── tts_hook.sh        # Claude Code Stop 훅 (TTS 요약 + ntfy 알림)
│   ├── output_watcher.py  # 출력 감시 (기본 OFF, API 토글)
│   ├── local_mic.py       # MacBook 마이크 (sounddevice)
│   ├── session_store.py   # 세션 메타데이터 (이름 변경 지원)
│   └── platform_utils.py  # 크로스 플랫폼 유틸 (macOS/Linux/WSL2)
├── frontend/
│   ├── index.html         # xterm.js UI (탭, 검색, 온보딩, 음성 바)
│   ├── voice.js           # 마이크 + TTS + 핸즈프리 + Media Session
│   ├── manifest.json      # PWA manifest
│   └── sw.js              # Service Worker
├── .claude/
│   ├── settings.json      # 프로젝트 Claude 설정 (훅 등록)
│   ├── settings.local.json # 로컬 권한 설정
│   ├── hooks/
│   │   └── check-gstack.sh  # PreToolUse 훅: gstack 설치 여부 확인
│   └── skills/
│       ├── vt/SKILL.md      # 전역 vt 스킬
│       ├── vt-start.md      # 서버 시작 스킬
│       ├── vt-mobile.md     # 모바일 테스트 스킬
│       └── vt-voice.md      # Voice Daemon 스킬
├── install.sh             # 원라인 설치 스크립트
├── requirements-core.txt  # FastAPI, uvicorn 등
├── requirements-voice.txt # faster-whisper, edge-tts, sounddevice 등
├── CLAUDE.md              # Claude Code 가이드
├── DESIGN.md              # 디자인 시스템 (Catppuccin Mocha)
└── ARCHITECTURE.md        # 3-plane 아키텍처 상세
```

---

## 접속 방법

| 환경 | 방법 |
|------|------|
| 데스크톱 | `http://localhost:7777` |
| 같은 네트워크 | `http://<맥북IP>:7777` (`ipconfig getifaddr en0`으로 IP 확인) |
| adb 연결 Android | `adb reverse tcp:7777 tcp:7777` → `http://localhost:7777` |
| 원격 (어디서든) | `vt mobile` → Cloudflare Tunnel HTTPS URL |
| 화면 원격이 막힌 회사망 등 | `vt ssh` → Tailscale + SSH로 tmux에 직접 접속 (터미널 전용, 브라우저 불필요) |

---

## Tailscale + SSH 원격 접속 (회사망 등 화면 원격이 막힌 환경)

회사망이 크롬 원격 데스크톱·TeamViewer·RDP/VNC 같은 화면 공유를 막아두는 경우가 있습니다.
반면 **Tailscale**(WireGuard 기반 VPN 메시)은 UDP 홀펀칭 또는 443 DERP 릴레이 폴백으로
동작해 이런 방화벽도 대부분 통과합니다. 화면 전체가 아니라 **터미널만** 필요하다면
Tailscale + SSH로 집 맥북의 tmux 세션에 직접 붙는 쪽이 화면 원격보다 가볍고 빠릅니다.

voice-terminal의 핵심 설계는 "tmux 세션이 단일 진실의 원천" — 데스크톱 iTerm, 모바일
PWA, Voice Daemon이 전부 같은 `tmux -L vt` 세션에 붙습니다. **SSH도 그 다섯 번째
클라이언트일 뿐**이라, 회사에서 SSH로 붙어도 집에서 보던 것과 완전히 같은 화면·
스크롤백·실행 중인 Claude 세션을 그대로 이어받습니다.

```bash
# 맥북에서 (Tailscale이 이미 tailscale up으로 연결돼 있어야 함)
vt ssh                   # 세션 'dev'로 접속하는 명령을 안내 (복사해서 회사 노트북에서 실행)
vt ssh mysession         # 다른 세션 이름 지정
vt ssh --user alice      # 원격 로그인 계정 지정 (기본: 현재 계정)
```

출력되는 두 줄 중 하나를 회사 노트북에서 그대로 실행하면 됩니다:

```bash
# 1) 일반 SSH — 이 맥북 ~/.ssh/authorized_keys에 공개키 등록 필요
ssh -t user@100.x.x.x 'tmux -L vt attach -t dev || tmux -L vt new -A -s dev'

# 2) Tailscale SSH — tailnet ACL에서 SSH 허용 시 키 등록 없이 접속
tailscale ssh user@100.x.x.x -- 'tmux -L vt attach -t dev || tmux -L vt new -A -s dev'
```

공개키가 등록돼 있지 않다면 회사 노트북에서 `cat ~/.ssh/id_ed25519.pub`으로 공개키를
복사한 뒤, 맥북에서 `vt ssh --add-key "ssh-ed25519 AAAA... user@laptop"`을 실행하면
`~/.ssh/authorized_keys`에 추가됩니다.

이 경로는 순수 텍스트 SSH라 브라우저 마이크/스피커를 못 씁니다 — 음성 대신
키보드로 직접 입력하면 됩니다. 대신 완료·idle 알림은 기존 ntfy/Telegram 푸시
브릿지로 그대로 받을 수 있고, `VT_NOTIFY_CLIENT_EVENTS=1`을 설정해두면 SSH로
누가 언제 접속했는지도 push로 알림받을 수 있습니다 (`vt help ssh`에 자세히 설명).

웹 UI(음성/터치 포함)도 tailnet 안에서만 열고 싶다면 `vt mobile --network tailscale`을
사용하세요 — Cloudflare Tunnel 없이 tailnet CIDR로만 접근을 제한합니다.

| 접속 방식 | 필요한 것 | 용도 |
|---|---|---|
| `vt ssh` | Tailscale + (선택) SSH 키 | 순수 터미널, vim/IDE 등 키 입력 위주 작업 |
| `vt mobile --network tailscale` | Tailscale + 브라우저 | 폰에서 음성 입력, 터치 조작 |
| `vt mobile` (기본, `--network all`) | 아무것도 (공개 URL) | Tailscale 없는 완전 외부 기기 |

셋 다 같은 tmux 세션을 공유하므로 아무 조합이나 섞어 써도 됩니다. 진단은
`vt doctor`(Tailscale 설치/연결 확인)와 `vt status`(현재 tailnet IP 표시)로,
더 자세한 설명은 `vt help ssh`로 확인하세요.

---

## 사용 시나리오

### 노션 작업 중 음성 코딩 (macOS)

```
1. vt voice              ← 어느 터미널에서나 실행
2. 새로 열린 iTerm 창에서 claude --resume으로 대화 선택
3. 노션으로 돌아가서 작업 계속
4. Ctrl+Shift+V → "git status" → tmux에 자동 입력
5. 결과를 TTS로 이어폰에서 들음
6. vt stop               ← 끝나면 종료
```

### 모바일에서 터미널 조작

```
1. vt mobile             ← URL + QR 코드 출력
2. 폰 카메라로 QR 스캔
3. tmux 세션에 자동 연결
4. 🎤 STT 입력  🔄 핸즈프리  🎧 음성 전용  📎 파일 업로드
```

### 회사에서 화면 원격 없이 SSH로 이어서 작업 (Tailscale)

```
1. (집) vt voice 또는 vt start          ← tmux 'dev'에서 평소처럼 작업
2. (집) vt ssh                          ← 접속 명령 두 줄 출력, 하나 복사
3. (회사 노트북) 복사한 ssh / tailscale ssh 명령 실행
4. 집에서 보던 tmux 세션에 그대로 진입 — 스크롤백·실행 중인 Claude 세션 이어받음
5. 키보드로 계속 작업 (음성은 SSH 경로에선 미지원)
6. VT_NOTIFY_CLIENT_EVENTS=1 설정해뒀다면 접속/해제가 폰으로 push됨
```

### Claude Code + TTS 알림

Claude 작업이 완료되면 `tts_hook.sh`가 자동으로 결과를 TTS로 읽어줍니다.
`VT_NOTIFY_URL` 설정 시 ntfy.sh로 푸시 알림도 함께 발송됩니다.

---

## Windows (WSL2 = Linux 환경)

> **Windows 네이티브는 지원하지 않습니다.** WSL2를 통해 Linux 환경으로 사용하세요.

```powershell
# Windows Terminal에서 WSL2 진입 후 일반 Linux 절차 그대로
wsl
./install.sh voice
vt voice
```

- 서버·tmux는 WSL2 내부 실행, 브라우저는 Windows에서 `localhost:7777` 접속
- 음성 핫키(Ctrl+Shift+V)는 WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용
- `bin/vt.ps1`은 WSL2 내부 vt를 호출하는 PowerShell wrapper (선택, 편의용)

---

## 트러블슈팅

### macOS

| 문제 | 해결 |
|------|------|
| `vt: command not found` | `source ~/.zshrc` 또는 새 터미널. `~/.local/bin` PATH 확인 |
| 핫키 안 먹힘 | 시스템 설정 → 개인정보 → 접근성 → 터미널 앱 허용 |
| 포트 충돌 | `~/.vt.env`에서 `VT_PORT` 변경 |
| 모바일 소리 안 남 | 화면 한 번 터치 (브라우저 autoplay 정책) |
| 서버 시작 실패 | `cat /tmp/vt-server.log` |
| 터널 URL 안 뜸 | `cat /tmp/cloudflared.log` · `brew install cloudflared` |
| 새 창 안 열림 | osascript 권한 부족. 수동: `tmux new -A -s dev 'claude --resume'` |

### WSL2

| 문제 | 해결 |
|------|------|
| 핫키 안 먹힘 | WSLg 필요 (Windows 11). 없으면 브라우저 🎤 사용 |
| localhost 접속 안 됨 | `wsl --shutdown` 후 재시작 |
| 사운드 안 됨 | WSLg 설정 확인 또는 브라우저 TTS 사용 |

모든 항목 한 번에 진단: `vt doctor`

---

## 지원 플랫폼

| 플랫폼 | 서버 | Voice Daemon | TUI (`vt manage`) | 브라우저 접속 |
|--------|------|-------------|-------|-------------|
| macOS (iTerm2/Ghostty/Warp 등) | ✅ | ✅ 핫키 + 이어폰 | ✅ | ✅ |
| Linux (X11) | ✅ | ✅ 글로벌 핫키 | ✅ | ✅ |
| Linux (Wayland) | ✅ | ⚠ 핫키 보안 차단 — 모바일 🎤 권장 | ✅ | ✅ |
| Windows (WSL2 = Linux로 동작) | ✅ | ✅ WSLg 필요 | ✅ | ✅ |
| Windows 네이티브 | ❌ 미지원 | ❌ | ❌ | — |
| iOS (Safari/Chrome) | — | — | — | ✅ Media Session |
| Android (Chrome) | — | — | — | ✅ |

---

## 버전 / 변경 이력

현재 버전: **v1.5.0** (2026-07-07)

전체 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고.

| 버전 | 날짜 | 주요 내용 |
|------|------|-----------|
| [v1.5.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.5.0) | 2026-07-07 | Tailscale + SSH 원격 접속(D9): `vt ssh`·`vt mobile --network tailscale`·`VT_NOTIFY_CLIENT_EVENTS`(tmux client-attached/detached push 알림)·`server/tailscale.py`·`/api/tailscale/status` |
| [v1.4.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.4.0) | 2026-05-09 | UX overhaul + Linux 1급 동등화: `vt manage` TUI·`vt attach`·`vt voice-target`·`vt hotkey`·`vt help`·`vt stop --purge`·핸즈프리 제거·이어폰 토글·Linux 터미널/TTS/notify 분기·onboarding |
| [v1.3.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.3.0) | 2026-05-08 | Phase 9 안정성·네트워크 효율: `/ws-preview` push·`/api/auth` cookie·vendor 자체 호스팅·ETag 304·pyav decoding·SW 캐시·PTY query 가로채기·WS heartbeat |
| [v1.2.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.2.0) | 2026-05-07 | Phase 7-8: 라이브 프리뷰·setup-keybind·--network 모드·Cloudflare 명명 터널·WS 백프레셔·--disallowedTools·trust 자동·워크스페이스·tmux batch |
| [v1.1.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.1.0) | 2026-05-06 | ralph→vt 리네이밍, Phase 1-6 (격리 소켓·AI 인식·명령 확장·훅·안전 모드·크로스 플랫폼 통합) |
| [v1.0.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.0.0) | 2026-04-14 | 초기 안정 버전 (PWA·Voice Daemon·STT/TTS·터널·`vt` CLI·`install.sh`) |

---

## 라이선스

MIT
