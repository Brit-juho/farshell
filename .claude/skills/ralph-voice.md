---
name: ralph-voice
description: |
  Voice Daemon 설치 및 실행. macOS에서 핫키(Ctrl+Shift+V)로 음성 입력 → STT → tmux 주입.
  서버 없이 독립 동작. Use when asked to "음성 모드", "voice daemon", "음성 데몬",
  "핫키 음성", "voice mode", "음성 입력 시작".
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

## Voice Daemon 스킬

macOS에서 글로벌 핫키로 음성 → STT → tmux 주입하는 데몬을 설정하고 실행합니다.
**서버 없이 독립 동작** — tmux만 있으면 됩니다.

### 1. Python 경로 확인

사용자의 `~/.ralph.env`에서 `RALPH_PYTHON` 값을 읽으세요 (install.sh가 설정):

```bash
source ~/.ralph.env 2>/dev/null
PY="${RALPH_PYTHON:-$(which python3)}"
echo "Python: $PY"
```

### 1-1. 의존성 확인

```bash
"$PY" -c "import pynput, sounddevice, numpy; print('DEPS_OK')" 2>/dev/null || echo "DEPS_MISSING"
```

없으면 설치 (install.sh 권장):
```bash
cd "$CLAUDE_PROJECT_DIR" && ./install.sh voice
```

또는 수동:
```bash
"$PY" -m pip install pynput sounddevice numpy faster-whisper
```

STT 엔진 확인:
```bash
"$PY" -c "
try:
    import mlx_whisper; print('STT: mlx-whisper')
except:
    from faster_whisper import WhisperModel; print('STT: faster-whisper')
"
```

### 2. macOS 접근성 권한

Voice Daemon은 글로벌 핫키를 감지하기 위해 macOS 접근성 권한이 필요합니다.

**시스템 설정 → 개인정보 보호 및 보안 → 접근성** 에서 터미널 앱(Terminal, iTerm2 등)을 허용해야 합니다.

이미 허용되었는지 확인하려면 데몬을 실행해보세요 — 권한이 없으면 macOS가 팝업을 띄웁니다.

### 3. tmux 세션 준비

```bash
# tmux 세션이 있는지 확인
tmux list-sessions 2>/dev/null || tmux new-session -d -s dev
```

### 4. 데몬 실행

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python "$CLAUDE_PROJECT_DIR/server/voice_daemon.py" &
echo "DAEMON_PID: $!"
```

### 5. 사용법

| 동작 | 설명 |
|------|------|
| **Ctrl+Shift+V** (1회) | 녹음 시작 (🔔 Tink 효과음) |
| **Ctrl+Shift+V** (2회) | 녹음 종료 → STT → tmux 입력 (🔔 Pop 효과음) |
| 음성 확인 | STT 결과를 macOS `say`로 읽어줌 |

### 6. 동작 확인

```bash
# 데몬 실행 중 확인
ps aux | grep voice_daemon | grep -v grep

# tmux에 입력되었는지 확인
tmux capture-pane -t dev -p -S -5
```

### 7. 설정 변경

`voice_daemon.py` 상단 설정:

```python
HOTKEY = {keyboard.Key.ctrl_l, keyboard.Key.shift, keyboard.KeyCode.from_char("v")}  # 핫키
TTS_CONFIRM = True   # STT 후 음성 확인 ON/OFF
SAMPLE_RATE = 16000  # 녹음 샘플레이트
```

### 8. 종료

```bash
pkill -f voice_daemon.py
```

### 트러블슈팅

| 문제 | 해결 |
|------|------|
| 핫키 안 먹힘 | macOS 접근성 권한 확인 |
| STT 느림 | mlx-whisper 설치: `pip install mlx-whisper` |
| "활성 tmux pane 없음" | `tmux new -s dev` 실행 |
| 녹음 안 됨 | macOS 마이크 권한 확인 (시스템 설정 → 마이크) |
