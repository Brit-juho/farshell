---
name: fsh-voice
description: |
  Voice Daemon 설치 및 실행. macOS에서 핫키로 음성 입력 → STT → tmux 주입.
  서버 없이 독립 동작. Use when asked to "음성 모드", "voice daemon", "음성 데몬",
  "핫키 음성", "voice mode", "음성 입력 시작".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## Voice Daemon 스킬

핫키로 음성 → STT → tmux 주입하는 데몬. **서버 없이 독립 동작** — tmux만 있으면 됩니다.

### 1. 실행

```bash
fsh voice            # 음성 데몬만 (서버가 필요하면 함께 띄운다)
fsh start --voice    # 서버 + 입구 + 음성 데몬
```

`fsh`가 Python 경로(`~/.vt.env`의 `VT_PYTHON` → `.venv` → `python3`), pid 파일, tmux
세션 준비를 전부 처리합니다. **`python .../voice_daemon.py &`를 직접 치지 마세요** —
경로는 사람마다 다르고, 절대경로를 문서에 적으면 리포에 개인 값이 들어갑니다
(AGENTS.md 계약 7). pid 파일도 안 남아 `fsh stop`이 정리하지 못합니다.

### 2. 상태 확인

```bash
fsh status     # "Voice Daemon: ✓ 실행 중" 줄을 본다
```

### 3. 설치·환경 진단

```bash
fsh doctor     # Python·venv·Node·frontend/dist·core/voice 패키지·tmux·경계값
```

의존성이 빠졌으면 설치 스크립트를 쓰세요. 수동 `pip install`보다 이쪽이 맞습니다 —
같은 venv에 같은 조합을 넣습니다.

```bash
./install.sh voice
```

### 4. macOS 접근성 권한

글로벌 핫키 감지에 필요합니다. **시스템 설정 → 개인정보 보호 및 보안 → 접근성**에서
터미널 앱(Terminal, iTerm2 등)을 허용하세요. 권한이 없으면 데몬 실행 시 macOS가 팝업을
띄웁니다 — **`fsh doctor`는 이 항목을 보지 않으므로**(코드에 검사가 없다) 팝업이나
핫키 무반응으로 판단해야 합니다.

### 5. 사용법

| 동작 | 설명 |
|------|------|
| **Ctrl+Shift+M** (1회) | 녹음 시작 |
| **Ctrl+Shift+M** (2회) | 녹음 종료 → STT → tmux 입력 |
| 음성 확인 | STT 결과를 TTS로 읽어줌 |

핫키·TTS 확인·샘플레이트 등 설정은 **웹 UI의 설정 화면**이나 `~/.vt.env`에서 바꾸세요.
`voice_daemon.py` 소스를 직접 고치라고 안내하지 마세요 — 업데이트 때 덮어써집니다.

### 6. 입력 대상 확인

```bash
fsh voice-target              # 현재 대상 세션 확인
fsh voice-target <name>       # 특정 세션으로 고정
fsh voice-target --auto       # 자동 선택으로 되돌림
```

실제로 들어갔는지 보려면 **격리 소켓**을 지정해서 읽습니다. FarShell은 `-L fsh` 소켓을
쓰므로 맨 `tmux capture-pane`은 엉뚱한(또는 없는) 서버를 봅니다.

```bash
tmux -L fsh capture-pane -t dev -p -S -5
```

### 7. 종료

```bash
fsh stop                 # 전부 종료
fsh stop --keep-tunnel   # 서버·데몬만 내리고 공개 입구는 유지
```

**`pkill -f voice_daemon.py`를 쓰지 마세요.** 파일 이름만으로 매칭하면 한 머신에서 두
인스턴스를 돌릴 때 다른 인스턴스의 데몬까지 죽습니다 — `bin/fsh`의 `cmd_stop` 주석이
실제로 그렇게 겪은 사고를 기록하고 있습니다. `fsh stop`은 `$VT_DIR/server/*.py` 절대경로로
매칭해 인스턴스를 가릅니다.

### 트러블슈팅

| 문제 | 해결 |
|------|------|
| 핫키 안 먹힘 | macOS 접근성 권한 확인 (4절) |
| STT 느림 | Apple Silicon이면 mlx-whisper가 빠르다. `fsh doctor`는 패키지 존재만 보고 엔진 종류는 알려주지 않는다 |
| "활성 tmux pane 없음" | `fsh status`로 세션 확인 후 `fsh voice-target <name>` |
| 녹음 안 됨 | macOS 마이크 권한 확인 (시스템 설정 → 마이크) |
| 데몬이 안 뜸 | `cat "${VT_RUN_DIR:-/tmp}/vt-voice-daemon.log"` 확인 |
