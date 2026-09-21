---
name: fsh-start
description: |
  FarShell 서버 시작 + 공개 입구(터널) 설정. 서버 실행, tmux 세션 준비, 접속 URL까지
  원스텝. 이미 돌고 있는 인스턴스에 코드를 반영하는 방법도 여기 있다.
  Use when asked to "서버 시작", "voice terminal 시작", "start fsh", "start vt", "원격 접속 설정",
  "서버 재시작", "코드 반영".
allowed-tools:
  - Bash
  - Read
---

## FarShell 서버 시작 스킬

**원시 명령(`uvicorn`·`cloudflared`·`tmux`)을 직접 치지 마세요.** `fsh`가 이미 하는 일이고,
직접 치면 `bin/fsh`에 쌓인 것들을 전부 우회합니다 — 터널 재사용, 제공자 분기
(cloudflare/ngrok/none), pid 관리, URL 변경 훅, 인스턴스별 프로세스 격리. 실제로
이 스킬의 예전 판이 그렇게 적혀 있었고, 아래 사고들을 그대로 재생산했습니다.

### 1. 먼저 상태를 본다

```bash
fsh status
```

서버·터널·워치독·tmux·Tailscale이 한 화면에 나옵니다. **이미 실행 중이면 새로 시작하지
말고** 그 URL을 안내하세요.

### 2. 시작

```bash
fsh start            # 서버 + 공개 입구
fsh start --voice    # 음성 데몬까지 함께
```

감지된 터미널 앱(iTerm/Ghostty/WezTerm/Kitty/Alacritty/Warp/Terminal.app)에 tmux + Claude
창도 함께 엽니다. 이미 `dev` 세션이 있으면 새 창을 열지 않습니다.

### 3. 코드를 반영할 때 — `fsh restart`

**`fsh stop && fsh start`를 쓰지 마세요.**

```bash
fsh restart            # 서버만 재기동, 입구 유지 → 접속 URL이 그대로다
fsh restart --voice
```

익명 터널의 주소는 **cloudflared 프로세스 수명**에 묶여 있습니다. `stop`이 터널까지 죽이면
그 주소는 영영 죽고, 북마크·열어둔 탭·폰 홈화면 아이콘이 전부 같이 죽습니다.
실측(2026-09-21): cloudflared를 살려 둔 채 백엔드만 죽였다 살리면 내려가 있는 동안 502를
내다가 200으로 복구되고 **URL은 그대로**입니다.

이 실수가 특히 나쁜 건 **양쪽 어디에서도 안 보인다**는 점입니다. `frontend/sw.js`가 fetch
실패 시 캐시해 둔 앱 껍데기를 내주기 때문에 죽은 주소에서도 UI가 통째로 그려지고, 서버
로그에는 요청이 한 줄도 안 남습니다(도달을 못 하니까). 사용자에게는 "접속은 됐는데
아무것도 안 된다"로 보입니다.

### 4. 접속 정보 안내

주소는 **`fsh status`가 말하는 것**을 쓰세요. `/tmp/cloudflared.log`를 직접 grep하면
제공자가 cloudflare가 아닐 때(ngrok, 명명 터널, none) 틀린 답을 냅니다 — `_main_tunnel_url()`이
단일 진실의 원천입니다.

```bash
fsh status                        # 표 안에 현재 URL
fsh mobile                        # 접속 URL + QR 코드
```

### 5. 종료

```bash
fsh stop                 # 전부 종료 (터널도 내려가므로 주소가 죽는다)
fsh stop --keep-tunnel   # 서버만 내리고 입구는 유지
fsh stop --purge         # tmux 세션까지 완전 종료
```

`fsh stop`은 **이 인스턴스의 프로세스만** 정확히 골라 죽입니다. 직접 `pkill -f "cloudflared
tunnel"`이나 `pkill -f voice_daemon.py`를 치지 마세요 — 파일 이름만으로 매칭하면 한 머신에서
두 인스턴스를 돌릴 때 **다른 인스턴스의 데몬과 터널까지 같이 죽습니다.** `bin/fsh`의
`cmd_stop` 주석이 실제로 그렇게 겪은 사고를 기록하고 있습니다.

### 서버를 포그라운드로 직접 띄워야 할 때

디버깅 등으로 로그를 그 자리에서 봐야 하면 저장소의 스크립트를 쓰세요. Python 경로
(`VT_PYTHON` → `.venv` → `python3`), 설정 파서, `VT_NETWORK_MODE` 기반 bind 주소를 전부
올바르게 처리합니다.

```bash
./run_server.sh
```

**Python 절대경로를 손으로 적지 마세요.** 사람마다 다르고, 리포에 들어가면 안 되는
개인 값입니다(AGENTS.md 계약 7).

### 문제 해결

```bash
fsh doctor                  # 13개 항목 진단 (Python·venv·패키지·tmux·cloudflared·포트·토큰…)
cat /tmp/vt-server.log      # 서버 로그
cat /tmp/cloudflared.log    # 터널 로그
```

tmux 세션이 안 보인다면: FarShell은 격리된 `-L fsh` 소켓을 씁니다. 맨 `tmux ls`는
"no server running"이라고 답하는 게 정상입니다.

```bash
tmux -L fsh list-sessions
```
