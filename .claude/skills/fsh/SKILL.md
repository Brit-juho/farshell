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

### 설치를 도울 때

`./install.sh`(또는 `curl ... | bash`)를 실행해 주는 경우 **사용자와 대화하는 언어를 `--lang ko|en` 으로 넘긴다**
(`./install.sh --lang en`, 파이프면 `bash -s -- --lang en`). 화면 언어는 서버 전체에 하나(`~/.vt.env` 의
`VT_LANG`)라 웹 화면·`fsh`·설치 출력이 모두 그 언어를 따른다. 이미 설치돼 있으면 설정 → 모양 → 화면 언어에서 바꾼다.

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
서버 + tmux + Voice Daemon을 백그라운드로 시작합니다.
터미널 창은 자동으로 열리지 않습니다 — 터미널에서 `tmux -L fsh new -A -s dev` 로 들어가 `claude --resume`을 실행하세요
(`fsh attach` / `fsh handoff desktop`은 고른 메인 터미널에 창을 엽니다, 아래 「메인 터미널 선택」).

```bash
fsh voice
```

시작 후 사용자에게 알려주세요:
- Ctrl+Shift+M로 녹음 시작/종료
- 말한 내용이 자동으로 tmux 터미널에 입력됨
- 다른 작업을 계속해도 됨 (백그라운드 동작)
- **tmux dev 안의 Claude에서 resume 목록의 현재 대화 선택** → 이후 음성/모바일이 그 Claude로 연결됨
- 이미 tmux 안이면 새 창을 열지 않음 (`$TMUX` 체크)

#### 모바일 모드 ("모바일", "mobile", "폰에서 접속")

서버가 떠 있는 상태에서 폰으로 접속할 URL과 QR을 보여줍니다. 서버와 터널은 `fsh start`가 시작합니다.
음성 모드와 마찬가지로 터미널 창은 자동으로 열리지 않습니다.

```bash
fsh start      # 서버 + 터널 (이미 떠 있으면 건너뜀)
fsh mobile     # 접속 URL + QR
```

- 접속 URL은 `fsh start`가 만든 터널 주소 (1회용 등록 티켓 포함)
- adb 연결 시 자동으로 Chrome에서 열림
- QR 코드도 표시 (qrencode 설치 시)
- 폰이 attach하는 `dev` 세션 = tmux dev 안의 Claude가 동작하는 세션 (단일 진실의 원천)

#### 전체 시작 ("fsh 시작", "전부 시작")

서버 + 터널을 시작합니다(터미널 창은 열지 않음).
음성 데몬은 기본으로 켜지지 않는다 — 필요하면 `--voice`를 붙이거나 `fsh voice`를 따로 실행.
접근 범위는 `fsh start --network <localhost|lan|tailscale|all>`(기본 all = 공개 터널 + 인증, 인증이 없으면 거부).
자세히: `fsh help access`.

```bash
fsh start          # 서버 + 터널만
fsh start --voice  # 서버 + 터널 + 음성 데몬
```

#### 메인 터미널 선택 ("터미널 바꿔줘", "고스티로", "iterm으로")

fsh가 창을 직접 여는 명령(`fsh attach`, `fsh handoff desktop`)이 쓸 터미널을
사용자가 고른다. 특정 앱으로 고정돼 있지 않다. 값은 `~/.vt.env`의 `VT_TERMINAL`
(`auto` 기본 | `ghostty` | `iterm` | `terminal` | `wezterm` | `warp` | `kitty` | `alacritty`).

```bash
fsh terminal                  # 현재 설정 + 설치된 앱 확인
fsh terminal set ghostty      # 메인 터미널 지정 (설치 안 된 앱·오타는 거부)
fsh terminal unset            # 자동 선택으로 되돌리기
```

- `auto`: 지금 쓰는 터미널(`TERM_PROGRAM`) → 설치된 앱 순서
- 지정한 앱을 열지 못하면 조용히 바꾸지 않고 알린 뒤 자동 선택으로 넘어간다
- Ghostty는 이미 켜진 앱에 창만 추가한다(AppleScript, 1.3+). 실패하면 `open -na`로 폴백

#### 기기 간 핸드오프 ("폰으로 넘겨", "맥으로 가져와")

```bash
fsh handoff mobile    # 현재 tmux 세션을 폰으로 (QR + URL hash)
fsh handoff desktop   # 폰 세션을 맥 터미널로
```

#### 파일 다운로드/업로드 ("파일 다운로드", "파일 옮겨줘", "다운로드 링크 줘")

터미널이 떠 있는 컴퓨터의 파일을 웹/모바일로 받아오는 흐름. 저장소는
`~/.vt/files/`(id 기반) — 임의 경로를 직접 다운로드하는 API는 없고, 업로드로
발급된 `id`로만 받을 수 있다.

```bash
curl -s -X POST "<현재 접속 URL>/api/upload" -F "file=@<원본경로>"
# → {"ok": true, "id": "8f3kq2", ...}
```

이후 다운로드 URL을 안내:

```
<현재 접속 URL>/api/files/<id>/download
```

- 로컬 접속이면 인증 세션(로그인 쿠키)이 이미 있으면 그대로 열림
- Cloudflare Tunnel 등 원격 접속이면 `VT_AUTH_TOKEN`(`~/.vt.env`)이 필요 —
  `?token=<VT_AUTH_TOKEN>` 쿼리 또는 `Authorization: Bearer <VT_AUTH_TOKEN>` 헤더
- 업로드는 반대 방향: 웹 UI 키바 📎 슬롯(모바일) 또는 `Mod+K` → "파일 업로드",
  터미널에 이미지 붙여넣기도 같은 경로(`POST /api/upload`)
- 파일 목록/삭제는 `GET /api/files`, `DELETE /api/files/{id}`

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

#### 재시작 ("재시작", "서버만 다시", "코드 반영")

**`fsh stop && fsh start` 대신 이걸 쓰세요.** 서버만 다시 띄우고 터널은 그대로
두므로 **접속 주소가 안 바뀝니다** — 열어둔 탭·북마크·폰 홈화면이 그대로 삽니다.

```bash
fsh restart          # 서버만 재기동 (입구 유지)
fsh restart --voice  # 음성 데몬까지 함께
```

익명 터널은 주소가 cloudflared 프로세스 수명에 묶여 있어서, `fsh stop`으로 터널을
내리면 그 주소는 영영 죽습니다. tmux 세션은 두 경우 모두 살아남습니다.

#### 종료

```bash
fsh stop                 # 전부 종료 (터널도 내려가 주소가 죽음)
fsh stop --keep-tunnel   # 서버만 내리고 입구는 유지
fsh stop --purge         # tmux 세션까지 완전 종료
```

#### 그 밖의 명령 ("명령어 뭐 있어", "도움말")

전체 목록과 설명은 `fsh help`, 항목별 상세는 `fsh help <토픽|명령>` (예: `fsh help access`, `fsh help terminal`).
용도별로 정리하면:

| 용도 | 명령 |
|---|---|
| 접속 보안 | `fsh password`(웹 비밀번호), `fsh otp`(새 기기 OTP), `fsh device`(등록 기기 조회·폐기), `fsh status`(접속 보안 현황) |
| 터널 | `fsh tunnel list/expose/unexpose/pin/mode/restart/setup/switch/hook` (`fsh help tunnel`) |
| 세션·터미널 | `fsh attach`, `fsh handoff mobile|desktop`, `fsh terminal`, `fsh ssh`, `fsh manage`(TUI) |
| 에이전트 | `fsh hooks`(훅 등록), `fsh pane report`, `fsh queue`(프롬프트 큐), `fsh msg`, `fsh mcp`, `fsh worktree`(별칭 `wt`), `fsh usage`, `fsh clauth`, `fsh run`, `fsh template` |
| 음성·입력 | `fsh voice`, `fsh voice-target`, `fsh hotkey`, `fsh clip` |
| 다른 맥·파일 | `fsh host`(페어링), `fsh files`(파일 저장소·공유 링크), `fsh git-account` |
| 설치 보조 | `fsh doctor`, `fsh shell-init`, `fsh install-profiles`, `fsh setup-keybind`, `fsh popup` |

### 프로세스 수명

- 서버/터널은 **백그라운드 프로세스**로 실행됨
- Claude 세션을 닫아도 계속 실행됨
- `fsh stop` 또는 맥 재시작 전까지 유지
- 아무 터미널에서나 `fsh stop`으로 종료 가능
- **터널 URL은 cloudflared 프로세스가 사는 동안만 유효** — `fsh stop` 후
  `fsh start`하면 주소가 바뀐다. 주소를 지키려면 `fsh restart`

### 트러블슈팅

| 문제 | 해결 |
|------|------|
| `fsh: command not found` | `~/.local/bin`이 PATH에 있는지 확인. 없으면: `export PATH="$HOME/.local/bin:$PATH"` |
| Voice Daemon 핫키 안 먹힘 | macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 |
| 서버 시작 실패 | `cat /tmp/vt-server.log` 확인 |
| 터널 URL 안 뜸 | `cat /tmp/cloudflared.log` 확인. cloudflared 설치: `brew install cloudflared` |
| 새 터미널 창이 안 열림 | 고른 터미널(`fsh terminal`)이 설치돼 있지 않거나 osascript(자동화) 권한이 없음. 출력된 수동 명령(`tmux new -A -s dev 'claude --resume'`)을 다른 터미널에서 실행 |
| `claude --resume`에서 대화 못 찾음 | resume 목록은 시간순. 가장 최근 항목을 고르거나, ID로 직접: `claude --resume <conversation-id>` |

### 사용자 시나리오: 노션 작업 중 음성 코딩

1. 터미널에서 `fsh voice` 실행
2. 노션으로 돌아가서 작업 계속
3. 코딩이 필요할 때 Ctrl+Shift+M → 말하기 ("git status" 등)
4. tmux에 자동 입력 → 결과를 TTS로 들음
5. 다시 노션 작업 계속
