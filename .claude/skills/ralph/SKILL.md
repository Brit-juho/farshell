---
name: ralph
description: |
  랄프톤 Voice Terminal 제어. 음성 모드, 모바일 접속, 서버 관리.
  어디서든 실행 가능한 전역 스킬. Use when asked to "음성 모드", "voice mode",
  "모바일 접속", "mobile mode", "ralph", "랄프톤 시작", "음성으로 코딩",
  "voice coding", "터미널 음성".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## 랄프톤 Voice Terminal 스킬

이 스킬은 `ralph` CLI를 통해 Voice Terminal을 제어합니다.
**어느 디렉토리에서든 실행 가능합니다.**

### 실행 전 필수: 기존 실행 상태 확인

**모든 모드를 실행하기 전에 반드시 `ralph status`를 먼저 실행하세요.**

```bash
ralph status
```

- 서버/터널이 **이미 실행 중**이면: 새로 시작하지 말고 기존 정보를 안내하세요.
  - 터널 URL: `cat /tmp/cloudflared.log | grep -o 'https://[^ ]*trycloudflare.com'`
  - "이미 실행 중입니다. 접속 URL: ..." 형태로 알려주세요.
- 서버/터널이 **중지 상태**일 때만 새로 시작하세요.

### 사용 가능한 모드

사용자가 요청하면 해당하는 `ralph` 명령을 실행하세요.

#### 음성 모드 ("음성 모드", "voice mode", "음성으로 코딩")

노션/브라우저 작업을 계속하면서 음성으로 코딩하는 모드.
서버 + tmux + Voice Daemon을 백그라운드로 시작합니다.

```bash
ralph voice
```

시작 후 사용자에게 알려주세요:
- Ctrl+Shift+V로 녹음 시작/종료
- 말한 내용이 자동으로 tmux 터미널에 입력됨
- 다른 작업을 계속해도 됨 (백그라운드 동작)

#### 모바일 모드 ("모바일", "mobile", "폰에서 접속")

모바일 브라우저에서 터미널에 접속할 수 있는 URL을 제공합니다.

```bash
ralph mobile
```

- Cloudflare Tunnel URL이 생성됨
- adb 연결 시 자동으로 Chrome에서 열림
- QR 코드도 표시 (qrencode 설치 시)

#### 전체 시작 ("ralph 시작", "전부 시작")

서버 + 터널 + 음성 데몬 전체를 시작합니다.

```bash
ralph start
```

#### 상태 확인

```bash
ralph status
```

#### 종료

```bash
ralph stop
```

### 프로세스 수명

- 서버/터널은 **백그라운드 프로세스**로 실행됨
- Claude 세션을 닫아도 계속 실행됨
- `ralph stop` 또는 맥 재시작 전까지 유지
- 아무 터미널에서나 `ralph stop`으로 종료 가능

### 트러블슈팅

| 문제 | 해결 |
|------|------|
| `ralph: command not found` | `~/.local/bin`이 PATH에 있는지 확인. 없으면: `export PATH="$HOME/.local/bin:$PATH"` |
| Voice Daemon 핫키 안 먹힘 | macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 |
| 서버 시작 실패 | `cat /tmp/ralphton-server.log` 확인 |
| 터널 URL 안 뜸 | `cat /tmp/cloudflared.log` 확인. cloudflared 설치: `brew install cloudflared` |

### 사용자 시나리오: 노션 작업 중 음성 코딩

1. 터미널에서 `ralph voice` 실행
2. 노션으로 돌아가서 작업 계속
3. 코딩이 필요할 때 Ctrl+Shift+V → 말하기 ("git status" 등)
4. tmux에 자동 입력 → 결과를 TTS로 들음
5. 다시 노션 작업 계속
