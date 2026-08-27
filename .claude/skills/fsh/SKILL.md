---
name: fsh
description: |
  FarShell(fsh) 제어. 터미널을 웹/모바일로 원격 접속시키는 전역 스킬(서버/터널 관리,
  음성 입력 포함). 어디서든 실행 가능. Use when asked to "모바일 접속", "mobile mode",
  "fsh", "음성 모드", "voice mode".
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

#### 파일 다운로드/업로드 ("파일 다운로드", "파일 옮겨줘", "다운로드 링크 줘")

터미널이 떠 있는 컴퓨터의 파일을 웹/모바일로 받아오는 흐름. 임의 경로를 직접
다운로드하는 API는 없고, `/tmp/vt-uploads/` 폴더에 들어간 파일만 `/api/download`로
받을 수 있다(fsguard 스타일의 경로 화이트리스트 — 다른 경로는 403).

```bash
cp <원본경로> /tmp/vt-uploads/<파일명>   # 대상 파일을 다운로드 가능 영역으로 복사
chmod 600 /tmp/vt-uploads/<파일명>
```

이후 다운로드 URL을 안내:

```
<현재 접속 URL>/api/download?path=/tmp/vt-uploads/<파일명>
```

- 로컬 접속이면 인증 세션(로그인 쿠키)이 이미 있으면 그대로 열림
- Cloudflare Tunnel 등 원격 접속이면 `VT_AUTH_TOKEN`(`~/.vt.env`)이 필요 —
  `?token=<VT_AUTH_TOKEN>` 쿼리 또는 `Authorization: Bearer <VT_AUTH_TOKEN>` 헤더
- 업로드는 반대 방향: 웹 UI 보이스바 📎 버튼 → `/tmp/vt-uploads/`에 저장(`POST /api/upload`)

#### 다른 로컬 포트 외부 공개 ("포트 3000 열어줘", "다른 앱도 터널 연결")

FarShell 자신(기본 7777)이 아닌 **다른 로컬 서버**를 Cloudflare Tunnel로 별도 공개.
Cloudflare quick tunnel은 호스트당 포트 1개만 연결되므로, 포트마다 독립적인
터널 프로세스 + URL이 생긴다(경로로 포트를 구분하는 방식이 아님).

```bash
fsh tunnel expose <port> "<앱 이름>"   # 예: fsh tunnel expose 3000 "내 앱"
fsh tunnel list                       # 열려 있는 터널 전부 (메인 + 추가 포트)
fsh tunnel unexpose <port>            # 해당 포트 터널만 종료
```

웹 UI 포트 대시보드(⋯ 메뉴 → "포트")에서도 같은 기능이 있지만, 로컬 서버를
공개 인터넷에 여는 행위라 `confirm:true` 없이는 428로 막히고
`VT_NETWORK_MODE=all`이 아니면 아예 거부된다.

전체 REST/WebSocket API 레퍼런스는 저장소의 `API.md` 참고.

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
