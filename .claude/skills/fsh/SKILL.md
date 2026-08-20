---
name: fsh
description: |
  FarShell 제어. 음성 모드, 모바일 접속, 서버 관리.
  어디서든 실행 가능한 전역 스킬. Use when asked to "음성 모드", "voice mode",
  "모바일 접속", "mobile mode", "fsh", "vt", "voice terminal 시작", "음성으로 코딩",
  "voice coding", "터미널 음성".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## FarShell 스킬

이 스킬은 `fsh` CLI를 통해 FarShell을 제어합니다(구 CLI 이름 `vt`도 하위 호환 심링크로 계속 동작).
**어느 디렉토리에서든 실행 가능합니다.**

### 실행 전 필수: 기존 실행 상태 확인

**모든 모드를 실행하기 전에 반드시 `fsh status`를 먼저 실행하세요.**

```bash
fsh status
```

- 서버/터널이 **이미 실행 중**이면: 새로 시작하지 말고 기존 정보를 안내하세요.
  - 터널 URL: `cat /tmp/cloudflared.log | grep -o 'https://[^ ]*trycloudflare.com'`
  - "이미 실행 중입니다. 접속 URL: ..." 형태로 알려주세요.
- 서버/터널이 **중지 상태**일 때만 새로 시작하세요.

### 사용 가능한 모드

사용자가 요청하면 해당하는 `fsh` 명령을 실행하세요.

#### 음성 모드 ("음성 모드", "voice mode", "음성으로 코딩")

노션/브라우저 작업을 계속하면서 음성으로 코딩하는 모드.
서버 + tmux + Voice Daemon을 백그라운드로 시작하고,
**새 iTerm 창에 `tmux dev` + `claude --resume`이 자동으로 열립니다**(macOS).

```bash
fsh voice
```

시작 후 사용자에게 알려주세요:
- Ctrl+Shift+V로 녹음 시작/종료
- 말한 내용이 자동으로 tmux 터미널에 입력됨
- 다른 작업을 계속해도 됨 (백그라운드 동작)
- **새로 열린 iTerm 창에서 resume 목록의 현재 대화 선택** → 이후 음성/모바일이 그 Claude로 연결됨
- 이미 tmux 안이면 새 창을 열지 않음 (`$TMUX` 체크)

#### 모바일 모드 ("모바일", "mobile", "폰에서 접속")

모바일 브라우저에서 터미널에 접속할 수 있는 URL을 제공합니다.
음성 모드와 마찬가지로 **새 iTerm 창에 `tmux dev` + `claude --resume`이 자동으로 열립니다**.

```bash
fsh mobile
```

- Cloudflare Tunnel URL이 생성됨
- adb 연결 시 자동으로 Chrome에서 열림
- QR 코드도 표시 (qrencode 설치 시)
- 폰이 attach하는 `dev` 세션 = 새 iTerm 창의 Claude가 동작하는 세션 (단일 진실의 원천)

#### 전체 시작 ("fsh 시작", "전부 시작")

서버 + 터널을 시작하고, 새 터미널 창(iTerm/Ghostty/WezTerm/Kitty/Alacritty/Warp/Terminal.app 중 감지된 앱)에 tmux + Claude를 자동 오픈.
음성 데몬은 기본으로 켜지지 않는다 — 필요하면 `--voice`를 붙이거나 `fsh voice`를 따로 실행.

```bash
fsh start          # 서버 + 터널만
fsh start --voice  # 서버 + 터널 + 음성 데몬
```

#### Claude 시작 ("클로드 시작", "claude 실행")

tmux dev 세션 안에서 `claude --resume`을 즉시 실행.

```bash
fsh claude
```

#### 기기 간 핸드오프 ("폰으로 넘겨", "맥으로 가져와")

```bash
fsh handoff mobile    # 현재 tmux 세션을 폰으로 (QR + URL hash)
fsh handoff desktop   # 폰 세션을 맥 터미널로
```

#### 진단 ("진단", "fsh 점검", "설치 확인")

13개 항목 체크 (Python · venv · 패키지 · tmux · cloudflared · ffmpeg · 포트 · PATH · 토큰 · 알림 · 터미널 앱).

```bash
fsh doctor
```

#### 모바일 접속 (E2E)

```bash
fsh mobile --e2e      # cloudflared 터널 너머 페이로드 암호화
```

#### 상태 확인

```bash
fsh status
```

#### 종료

```bash
fsh stop
```

### 프로세스 수명

- 서버/터널은 **백그라운드 프로세스**로 실행됨
- Claude 세션을 닫아도 계속 실행됨
- `fsh stop` 또는 맥 재시작 전까지 유지
- 아무 터미널에서나 `fsh stop`으로 종료 가능

### 트러블슈팅

| 문제 | 해결 |
|------|------|
| `fsh: command not found` | `~/.local/bin`이 PATH에 있는지 확인. 없으면: `export PATH="$HOME/.local/bin:$PATH"` |
| Voice Daemon 핫키 안 먹힘 | macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 |
| 서버 시작 실패 | `cat /tmp/vt-server.log` 확인 |
| 터널 URL 안 뜸 | `cat /tmp/cloudflared.log` 확인. cloudflared 설치: `brew install cloudflared` |
| 새 iTerm 창이 안 열림 | iTerm2 미설치이거나 osascript 권한 없음. 출력된 수동 명령(`tmux new -A -s dev 'claude --resume'`)을 다른 터미널에서 실행 |
| `claude --resume`에서 대화 못 찾음 | resume 목록은 시간순. 가장 최근 항목을 고르거나, ID로 직접: `claude --resume <conversation-id>` |

### 사용자 시나리오: 노션 작업 중 음성 코딩

1. 터미널에서 `fsh voice` 실행
2. 노션으로 돌아가서 작업 계속
3. 코딩이 필요할 때 Ctrl+Shift+V → 말하기 ("git status" 등)
4. tmux에 자동 입력 → 결과를 TTS로 들음
5. 다시 노션 작업 계속
