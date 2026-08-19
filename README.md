# voice-terminal

[![Version](https://img.shields.io/badge/version-1.7.0-blue.svg)](./CHANGELOG.md)
[![Changelog](https://img.shields.io/badge/changelog-Keep%20a%20Changelog-orange.svg)](./CHANGELOG.md)

macOS / Linux 머신을 서버로 두고, 어디서든 음성 또는 브라우저로 터미널을 조작하는
시스템입니다. (Windows는 WSL2 환경에서만 동작 — 네이티브 미지원)

- 모바일에서 터미널 접속 — QR 스캔하면 바로 tmux 연결
- 음성으로 코딩 — 다른 작업 중에도 핫키(Ctrl+Shift+V)로 음성 입력
- 원격에서도 읽기 전용 코드 뷰어/diff, 포트 대시보드로 상태 확인
- Claude Code 연동 — 작업 완료 시 TTS 요약 + 프롬프트 큐
- 전부 무료 — API 키, 구독 없음. 오픈소스 STT/TTS

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

설치 후 새 터미널 창을 자동으로 tmux 세션에 진입시키는 통합 방법, 전체 `vt`
명령어와 옵션은 [CLI.md](./CLI.md)를 참고하세요.

---

## 빠른 시작

### 노션 등 다른 작업 중 음성 코딩 (macOS)

```
1. vt voice              어느 터미널에서나 실행
2. 새로 열린 창에서 claude --resume으로 대화 선택
3. 다른 작업으로 돌아가서 계속
4. Ctrl+Shift+V → "git status" → tmux에 자동 입력
5. 결과를 TTS로 이어폰에서 들음
6. vt stop                끝나면 종료
```

### 모바일에서 터미널 조작

```
1. vt mobile             URL + QR 코드 출력
2. 폰 카메라로 QR 스캔
3. tmux 세션에 자동 연결
4. 음성 입력 / 핸즈프리 / 음성 전용 모드 / 파일 업로드 사용
```

원격 접속 방식별 요구사항, 회사망 등 화면 원격이 막힌 환경에서 Tailscale + SSH로
접속하는 방법은 [CLI.md의 Tailscale 섹션](./CLI.md#tailscale--ssh-원격-접속)을 참고하세요.

---

## 보안

기본값은 **무인증**입니다 — 원격에 노출하기 전 반드시 `vt password`로 비밀번호를
설정하세요. `vt mobile`은 인증이 설정되지 않은 상태에서 공개 터널(`--network all`,
기본값)을 열려고 하면 실행을 거부합니다.

| 계층 | 방식 |
|------|------|
| 로그인 | 비밀번호(scrypt 해시) 또는 기계용 토큰(`VT_AUTH_TOKEN`). 세션은 HMAC 서명 쿠키(24h) |
| 새 기기 등록 | 90일 장기 쿠키로 기기별 신뢰. OTP를 켜면(`vt otp setup`) 신규 기기에만 6자리 코드 요구 |
| 기기 폐기 | `vt device revoke <id>` — 해당 기기의 세션까지 즉시 무효화 |
| 크로스 사이트 차단 | Origin이 자기 자신이 아니면 HTTP/WS 모두 403. CORS 와일드카드 없음 |
| 코드 뷰어 | 읽기 전용, 거부 목록(`.env*`/`*.pem`/`.ssh/` 등)이 파일 열람과 `git diff` 양쪽에 동일 적용 |
| E2E 암호화 | `--e2e` 플래그 — X25519 세션 키교환 + NaCl SecretBox, 장기 Ed25519 신원키로 세션 키를 서명해 TOFU(첫 접속 신뢰) 방식으로 능동적 중간자 공격 방어 |

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| Voice Daemon | macOS 핫키(Ctrl+Shift+V) 또는 이어폰 Play/Pause로 STT → tmux 직접 입력 |
| 클립보드 동기화 | OSC52(터미널 내부 복사) + 폴링 데몬(`vt clip`, 터미널 밖 복사) → 웹 클립보드 push |
| 핸즈프리 / 음성 전용 모드 | 연속 녹음 자동 반복, 또는 터미널을 숨기고 마이크만 크게 표시(이어폰 조작용) |
| barge-in | 마이크 탭 또는 핫키로 재생 중인 TTS 즉시 중단 |
| Claude Code TTS | Stop 훅으로 응답 완료 시 요약 TTS 자동 재생 |
| 프롬프트 큐 | 작업 중 지시를 쌓아뒀다 순차 투입 — 음성 모드와 짝 (`vt queue`, [CLI.md](./CLI.md#프롬프트-큐-vt-queue)) |
| 라이브 프리뷰 Grid 뷰 | tmux 세션을 카드로 한눈에 훑어보는 화면. 에이전트 배지, 작업 중/완료 표시 |
| 코드 뷰어 / diff | 파일 트리 · 문법 하이라이팅 · `git diff` 렌더링 (읽기 전용) |
| 포트 대시보드 | 리스닝 포트 조회, 원클릭 종료, `vt tunnel expose` 연동 |
| Web Push | 앱이 닫혀 있어도 작업 완료 알림 (명명 터널 권장 — quick tunnel은 URL이 바뀌어 구독이 끊길 수 있음) |
| tmux 세션 관리 | 웹에서 생성/attach/detach/kill. 데스크톱과 동시 접속 가능 |
| Scrollback 버퍼 | WebSocket 재접속 시 이전 출력 복원 (최대 5000 청크) |
| 터미널 검색 | Ctrl+F / Cmd+F → xterm.js 검색 addon |
| 세션 이름 편집 | 탭 더블클릭 → 이름 변경 (tmux 세션명도 함께 변경) |
| 파일 업로드/다운로드 | 보이스바에서 업로드, `/api/download`로 다운로드 |
| Media Session | 무선 이어폰 Play/Pause로 녹음 토글 (iOS/Android) |
| PWA | manifest + Service Worker → 홈 화면 추가 후 앱처럼 사용 |
| Tailscale 원격 접속 | `vt ssh` / `vt mobile --network tailscale` — 화면 원격이 막힌 회사망 등에서 SSH로 tmux에 직접 접속 |
| 클라이언트 접속 알림 | `VT_NOTIFY_CLIENT_EVENTS=1` — SSH 등 서버가 못 보는 클라이언트의 attach/detach를 push로 알림 |
| 터널 좀비 재연결 자동 복구 | cloudflared가 프로세스는 살아있지만 응답 없는 상태에 빠지면 자동 감지 후 재시작 |

전체 vt CLI 명령어는 [CLI.md](./CLI.md), REST/WebSocket API는 [API.md](./API.md)를 참고하세요.

---

## 설정 (`~/.vt.env`)

`install.sh`가 자동 생성합니다. 원하는 항목만 추가하면 됩니다. 전체 키 목록은
`config/vt.defaults.env`(커밋된 기본값)를 참고하세요.

```bash
# 기본
VT_PORT=7777                                 # 서버 포트 (기본값)
VT_PYTHON=~/voice-terminal/.venv/bin/python  # Python 경로 (자동 감지)

# 원격 인증 (공개 터널 사용 시 필수 권장)
# VT_AUTH_TOKEN=my-secret-token              # 기계용 토큰. 사람 로그인은 `vt password` 사용

# 보안
VT_E2E=1                                     # 모든 WebSocket 강제 E2E (기본: opt-in)
VT_SAFE_MODE=1                               # 위험 명령(rm -rf /, sudo 등) 사전 차단
VT_TMUX_SOCKET=vt                            # tmux 격리 소켓 이름 (기본: vt)
VT_NETWORK_MODE=all                          # localhost | lan | tailscale | all

# 음성
VT_STT_LANG=ko                               # STT 언어 고정 (미설정 시 자동 감지)
```

---

## Claude Code 연동

### 스킬

`.claude/skills/`에 등록된 프로젝트 전용 스킬과 `~/.claude/skills/vt/`의 전역 스킬(`/vt`)이
"음성 모드", "모바일 접속" 같은 자연어 발화로 vt를 제어합니다. 목록은 `CLAUDE.md`를 참고하세요.

### 훅

| 훅 | 파일 | 동작 |
|---|---|---|
| Pre/Post/StopToolUse | `server/agent_hook.sh` | 도구 사용 시작/종료/완료를 모바일 UI에 실시간 반영 |
| Stop | `server/tts_hook.sh` | 응답 완료 시 마지막 메시지를 TTS로 요약 재생 (서버 미실행 시 macOS `say` 폴백) |

`~/.claude/settings.json`에 등록:

```json
{
  "hooks": {
    "PreToolUse":  [{ "command": "<repo>/server/agent_hook.sh pre"  }],
    "PostToolUse": [{ "command": "<repo>/server/agent_hook.sh post" }],
    "Stop":        [{ "command": "<repo>/server/tts_hook.sh" }]
  }
}
```

---

## 아키텍처

자세한 내용은 [ARCHITECTURE.md](./ARCHITECTURE.md) 참조 (control / work / voice / network
4-plane 모델). 디자인 시스템(테마·토큰·레이아웃)은 [DESIGN.md](./DESIGN.md) 참고.

```
                  +----------------------------------------+
                  |  MacBook / WSL2 (서버)                   |
                  |                                          |
  [vt voice]      |  +----------------+  +----------------+  |
  Ctrl+Shift+V -->|  | Voice Daemon   |  | FastAPI :7777  |  |
  -> STT -> tmux  |  | (독립, 서버무관) |  | 인증/큐/push 등 |  |
                  |  +----------------+  +-------+--------+  |
                  |                              |           |
                  |  +----------------+  +-------+--------+  |
                  |  | tmux sessions  |<-+ PTY Manager    |  |
                  |  | (데스크/폰 공유) |  | + Scrollback   |  |
                  |  +----------------+  +-------+--------+  |
                  |                              |           |
                  |        +---------------------+--------+  |
                  |        | Push -> Web Push / ntfy /     |  |
                  |        | Telegram (idle / 완료 감지)     |  |
                  |        +--------------------------------+  |
                  +----------------+-------------------------+
                                   | Cloudflare Tunnel (HTTPS + opt-in E2E)
                  +----------------+-------------------------+
                  |  모바일 / 원격 브라우저                    |
                  |  xterm.js + 코드 뷰어 + Grid 뷰            |
                  |  STT -> 서버 -> tmux                     |
                  |  E2E: X25519 + Ed25519 서명 (TOFU)       |
                  +------------------------------------------+
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
│   ├── vt                    CLI 진입점 (bash, macOS/Linux)
│   └── vt.ps1                CLI 진입점 (PowerShell, Windows/WSL2 wrapper)
├── server/
│   ├── main.py                FastAPI 앱, 미들웨어(인증/Origin 가드)
│   ├── auth.py                비밀번호/세션/기기/OTP/티켓 인증
│   ├── fsguard.py             코드 뷰어 경로 검증 (루트 확정 + 거부 목록)
│   ├── crypto_channel.py      E2E: X25519 세션 키 + Ed25519 장기 신원키 서명
│   ├── pty_manager.py         PTY 세션 (broadcast, scrollback, EOF 감지)
│   ├── queue_store.py / queue_runner.py   프롬프트 큐 저장/자동 투입
│   ├── push.py                Web Push 구독 관리 + 발송
│   ├── portscan.py            포트 대시보드 (lsof/ps 스캔, kill/expose 가드)
│   ├── voice_handler.py       STT (faster-whisper) + TTS (edge-tts/Kokoro)
│   ├── voice_daemon.py        핫키 음성 데몬 (독립 실행)
│   ├── clipboard_daemon.py    macOS 클립보드 폴링 데몬
│   ├── tunnel_watchdog.py     cloudflared 좀비 재연결 감시
│   ├── routes/                엔드포인트 모듈 (pty/tmux/files/push/queue/ports/...)
│   └── tests/                 pytest 스위트
├── frontend/
│   ├── index.html             xterm.js UI (탭, 검색, 코드 뷰어, Grid 뷰)
│   ├── js/                    theme.js, terminal.js, grid.js, viewer.js 등
│   ├── voice.js                마이크 + TTS + 핸즈프리 + Media Session
│   └── tests/                 node --test 단위 테스트
├── install.sh                 원라인 설치 스크립트
├── requirements-core.txt      FastAPI, uvicorn 등
├── requirements-voice.txt     faster-whisper, edge-tts, sounddevice 등
├── CLAUDE.md                  Claude Code 가이드 (기능/명령/API 전체 원장)
├── CLI.md                     vt CLI 전체 레퍼런스
├── API.md                     REST/WebSocket API 전체 레퍼런스
├── DESIGN.md                  디자인 시스템 (테마·토큰·레이아웃)
├── ARCHITECTURE.md            4-plane 아키텍처 상세
├── TODOS.md                   후속 작업 백로그
└── CHANGELOG.md                전체 변경 이력
```

---

## 지원 플랫폼

| 플랫폼 | 서버 | Voice Daemon | TUI (`vt manage`) | 브라우저 접속 |
|--------|------|-------------|-------|-------------|
| macOS (iTerm2/Ghostty/Warp 등) | 지원 | 핫키 + 이어폰 | 지원 | 지원 |
| Linux (X11) | 지원 | 글로벌 핫키 | 지원 | 지원 |
| Linux (Wayland) | 지원 | 핫키 보안 차단 — 모바일 마이크 권장 | 지원 | 지원 |
| Windows (WSL2 = Linux로 동작) | 지원 | WSLg 필요 | 지원 | 지원 |
| Windows 네이티브 | 미지원 | 미지원 | 미지원 | — |
| iOS (Safari/Chrome) | — | — | — | Media Session 지원 |
| Android (Chrome) | — | — | — | 지원 |

### Windows (WSL2)

Windows 네이티브는 지원하지 않습니다 — WSL2를 통해 Linux 환경으로 사용하세요.

```powershell
wsl
./install.sh voice
vt voice
```

서버·tmux는 WSL2 내부에서 실행되고 브라우저는 Windows에서 `localhost:7777`로
접속합니다. 음성 핫키는 WSLg가 필요합니다(Windows 11) — 없으면 브라우저 마이크를
사용하세요. `bin/vt.ps1`은 WSL2 내부 vt를 호출하는 PowerShell wrapper입니다.

---

## 트러블슈팅

먼저 `vt doctor`로 설치/환경을 자동 진단하세요. 자주 발생하는 문제와 해결법은
[docs/help/troubleshoot.md](./docs/help/troubleshoot.md) (`vt help troubleshoot`와 동일 내용)에
정리되어 있습니다.

---

## 버전 / 변경 이력

현재 버전: **v1.7.0** (2026-08-04)

> v1.7.0 이후에도 개발이 계속 진행 중입니다 — 인증 하드닝, 프롬프트 큐, Web Push,
> 포트 대시보드, 코드 뷰어/diff 패널 등은 아직 정식 버전 태그 전(`[Unreleased]`)
> 단계로 [CHANGELOG.md](./CHANGELOG.md) 상단에 기록되어 있습니다.

전체 변경 이력은 [CHANGELOG.md](./CHANGELOG.md) 참고.

| 버전 | 날짜 | 주요 내용 |
|------|------|-----------|
| [v1.7.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.7.0) | 2026-08-04 | 보안 하드닝: 기기 화이트리스트 + OTP 관문 · 1회용 등록 티켓 · access log 자격증명 마스킹 · OriginGuardMiddleware · CORS 와일드카드 제거 · 세션 쿠키 Secure 수정 |
| [v1.6.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.6.0) | 2026-07-12 | 웹 로그인 비밀번호(`vt password`): scrypt 해시 + HMAC 서명 세션 쿠키, 기계 토큰과 병존 |
| [v1.5.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.5.0) | 2026-07-07 | Tailscale + SSH 원격 접속: `vt ssh` · `vt mobile --network tailscale` · 클라이언트 접속 알림 |
| [v1.4.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.4.0) | 2026-05-09 | UX overhaul + Linux 1급 동등화: `vt manage` TUI · `vt attach` · `vt voice-target` · `vt hotkey` |
| [v1.3.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.3.0) | 2026-05-08 | 안정성/네트워크 효율: `/ws-preview` push · cookie 인증 · vendor 자체 호스팅 · WS heartbeat |
| [v1.2.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.2.0) | 2026-05-07 | 라이브 프리뷰 · `--network` 모드 · Cloudflare 명명 터널 · WS 백프레셔 |
| [v1.1.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.1.0) | 2026-05-06 | ralph→vt 리네이밍, 격리 소켓 · AI 인식 · 명령 확장 · 훅 · 안전 모드 · 크로스 플랫폼 통합 |
| [v1.0.0](https://github.com/NeTrioGit/voice-terminal/releases/tag/v1.0.0) | 2026-04-14 | 초기 안정 버전 (PWA · Voice Daemon · STT/TTS · 터널 · `vt` CLI · `install.sh`) |

---

## 라이선스

MIT
