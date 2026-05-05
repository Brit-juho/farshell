# voice-terminal 개선 플랜

competitor 분석(reminder-watch · claude-mux · purplemux · lunemis/mux)을 기반으로 도출된 9개 개선 항목을 의존성 순으로 정리한 구현 계획서.

---

## 진행 순서

```
Phase 1: 기반          → #1
Phase 2: AI 인식       → #2 → #9
Phase 3: 명령 확장     → #4, #7, #6
Phase 4: 훅 강화       → #8
Phase 5: 안전·동기화   → #12, #13
```

각 Phase 종료 시점에 커밋·푸시·회귀 테스트를 수행한다.

---

## Phase 1 — 기반

### #1. 격리된 tmux 소켓 (`tmux -L vt`)

**목표:** 사용자 기존 tmux 세션·설정과 100% 분리. 충돌·오염 방지.

**수정 파일**
- `bin/vt` — 모든 `tmux` 호출에 `-L vt` 적용
- `server/pty_manager.py` — PTY가 spawn하는 `tmux attach` 명령
- `server/main.py` — tmux API 엔드포인트(`/api/tmux/*`)
- `frontend/index.html` — 사용자 안내 문구 갱신

**핵심 구현**

```bash
# bin/vt 상단
TMUX_SOCKET="vt"
_tmux() { tmux -L "$TMUX_SOCKET" "$@"; }
# 이후 tmux ... 호출은 _tmux ...로 치환
```

```python
# server/pty_manager.py
TMUX_BIN = ["tmux", "-L", "vt"]
```

**점검 절차**

1. `vt voice` 후 사용자 기본 `tmux ls` → vt 세션이 보이지 않아야 함
2. `tmux -L vt ls` → `dev` 세션 표시
3. 모바일 접속 후 attach 동작 확인
4. 기존 사용자 tmux 세션이 영향받지 않는지 회귀 테스트

---

## Phase 2 — AI 인식

### #2. AI CLI 감지 배지

**목표:** 세션 탭에 현재 실행 중인 AI CLI(Claude / Codex / Aider / Gemini) 시각 표시.

**수정/생성 파일**
- 신규 `server/agent_detector.py` — `pane_current_command` 폴링·매핑
- `server/main.py` — `GET /api/sessions/{id}/agent` 엔드포인트
- `frontend/index.html` — 탭 배지 렌더링 (5초 주기 폴링)

**핵심 구현**

```python
# server/agent_detector.py
KNOWN_AGENTS = {
    "claude": {"icon": "🟣", "label": "Claude"},
    "codex":  {"icon": "🟢", "label": "Codex"},
    "aider":  {"icon": "🔵", "label": "Aider"},
    "gemini": {"icon": "🟡", "label": "Gemini"},
}

def detect(session_name: str) -> dict | None:
    out = subprocess.check_output(
        ["tmux", "-L", "vt", "list-panes",
         "-t", session_name, "-F", "#{pane_current_command}"]
    ).decode()
    for cmd in out.splitlines():
        cmd_lc = cmd.lower()
        for key, info in KNOWN_AGENTS.items():
            if key in cmd_lc:
                return {"agent": key, **info}
    return None
```

**점검 절차**

1. claude·codex 각각 실행 → 탭 색·아이콘 변화 확인
2. CLI 종료 시 5초 내 배지 사라짐
3. 빈 셸일 때 배지 없음

---

### #9. 다중 AI CLI 일반화 (`vt agent <name>`)

**목표:** `vt claude` 외 codex/aider/gemini 통합. `claude`는 alias 유지.

**수정 파일**
- `bin/vt` — `cmd_agent` 함수 추가, `cmd_claude`는 wrapper로 유지

**핵심 구현**

```bash
cmd_agent() {
  local agent="${1:-claude}"; shift || true
  local exec_cmd
  case "$agent" in
    claude) exec_cmd="claude --resume" ;;
    codex)  exec_cmd="codex" ;;
    aider)  exec_cmd="aider" ;;
    gemini) exec_cmd="gemini" ;;
    *) echo "지원: claude|codex|aider|gemini"; return 1 ;;
  esac
  command -v "${exec_cmd%% *}" >/dev/null || {
    echo "✗ $agent 미설치"; return 1
  }
  _ensure_tmux
  if [ -n "${TMUX:-}" ]; then exec $exec_cmd "$@"; fi
  _open_tmux_terminal_with "$exec_cmd"
}
cmd_claude() { cmd_agent claude "$@"; }
```

**점검 절차**

1. `vt agent codex` → 정상 시작
2. `vt claude` 기존 동작 보존 (회귀)
3. 미설치 CLI 호출 시 명확한 에러 메시지

---

## Phase 3 — 명령 확장

### #4. CLAUDE.md 템플릿

**목표:** 새 프로젝트에 CLAUDE.md / AGENTS.md 빠르게 복사. 부트스트랩 자동화.

**수정 파일**
- `bin/vt` — `cmd_template` 추가
- 신규 디렉토리 `$HOME/.config/vt/templates/`

**핵심 구현**

```bash
cmd_template() {
  local action="${1:-list}"; local name="${2:-default}"
  local DIR="$HOME/.config/vt/templates"
  mkdir -p "$DIR"
  case "$action" in
    save)  cp CLAUDE.md "$DIR/$name.md" && echo "✓ saved $name" ;;
    apply) cp "$DIR/$name.md" ./CLAUDE.md && echo "✓ applied $name" ;;
    list)  ls -1 "$DIR" 2>/dev/null ;;
    rm)    rm "$DIR/$name.md" ;;
    *)     echo "사용법: vt template [save|apply|list|rm] <name>" ;;
  esac
}
```

**점검 절차**

1. `vt template save base` → 파일 존재 확인
2. 빈 디렉토리에서 `vt template apply base` → CLAUDE.md 생성됨
3. `vt template list` 출력 검증
4. `vt template rm base` → 삭제 확인

---

### #7. tmux popup 빠른 호출

**목표:** tmux 3.2+ popup으로 현재 작업을 방해하지 않고 `vt status` / `vt agent` 호출.

**수정 파일**
- `bin/vt` — `cmd_popup` 추가
- `README.md` — macOS 단축키 등록 가이드 (Karabiner / skhd 예시)

**핵심 구현**

```bash
cmd_popup() {
  local action="${1:-status}"
  local tmux_ver
  tmux_ver=$(tmux -V | awk '{print $2}')
  if [ -n "${TMUX:-}" ] && \
     [ "$(printf '3.2\n%s' "$tmux_ver" | sort -V | head -1)" = "3.2" ]; then
    tmux -L vt display-popup -E -w 80% -h 70% "vt $action; read -n1"
  else
    cmd_${action}
  fi
}
```

**점검 절차**

1. tmux 3.2+ 환경에서 `vt popup status` → 팝업 표시
2. tmux 미만 버전에서 fallback 정상
3. ESC / q 로 popup 닫힘

---

### #6. Headless 디스패치 (`vt run "..."`)

**목표:** 음성·CLI에서 짧은 명령 → 백그라운드 `claude -p` 실행 → 완료 시 TTS + ntfy 알림.

**수정/생성 파일**
- `bin/vt` — `cmd_run` 추가
- 신규 `server/dispatch.py` — 백그라운드 작업 큐 (선택)
- `server/main.py` — `POST /api/run` 엔드포인트 (모바일용)

**핵심 구현**

```bash
cmd_run() {
  local prompt="$*"
  [ -z "$prompt" ] && { echo "사용법: vt run \"명령\""; return 1; }
  local LOG="/tmp/vt-run-$$-$(date +%s).log"
  local TIMEOUT="${VT_RUN_TIMEOUT:-3600}"

  echo "  ▸ 백그라운드 실행 ($TIMEOUT초 타임아웃)"
  (
    timeout "$TIMEOUT" claude -p "$prompt" > "$LOG" 2>&1
    local result; result="$(tail -c 400 "$LOG")"
    # TTS
    curl -sf -X POST "http://localhost:${VT_PORT:-7777}/voice/output" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n --arg t "$result" '{text:$t}')" \
      -o /tmp/vt-run.mp3 && afplay /tmp/vt-run.mp3 2>/dev/null
    # ntfy 푸시
    [ -n "${VT_NOTIFY_URL:-}" ] && curl -sf -d "$result" \
      -H "Title: vt run 완료" "$VT_NOTIFY_URL"
  ) &
  echo "  ▸ PID $!  로그: $LOG"
}
```

**점검 절차**

1. `vt run "오늘 날짜 알려줘"` → 즉시 리턴, 백그라운드 진행
2. 완료 시 afplay 재생 + ntfy 도착 확인
3. `VT_RUN_TIMEOUT=10 vt run "오래걸리는작업"` → 타임아웃 발생 + 알림
4. 동시 다중 실행 가능 (PID 충돌 없음)

---

## Phase 4 — 훅 강화

### #8. PreToolUse / PostToolUse 훅

**목표:** Claude가 도구를 사용하는 시작·종료 시점을 모바일 UI·푸시에 실시간 반영.

**수정/생성 파일**
- 신규 `server/agent_hook.sh` — pre / post / stop 단일 진입점
- 신규 `server/agent_status.py` — in-memory 상태 저장
- `server/main.py` — `POST /api/agent/event`, `WS /ws-agent`
- `frontend/index.html` — agent 상태 토스트 / 배지
- `README.md` — `~/.claude/settings.json` 등록 예시

**핵심 구현**

```bash
# server/agent_hook.sh
EVENT="${1:-stop}"   # pre | post | stop
INPUT=$(cat)
curl -sf -X POST "http://localhost:${VT_PORT:-7777}/api/agent/event" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg e "$EVENT" --argjson i "$INPUT" '{event:$e, payload:$i}')"
[ "$EVENT" = "stop" ] && exec "$(dirname "$0")/tts_hook.sh" <<<"$INPUT"
```

```python
# server/agent_status.py
_state: dict[str, dict] = {}   # session_id → {tool, since, count}

def on_event(event: str, payload: dict):
    sid = payload.get("session_id", "default")
    if event == "pre":
        tool = payload.get("tool_name", "?")
        _state[sid] = {
            "tool": tool,
            "since": time.time(),
            "count": _state.get(sid, {}).get("count", 0) + 1,
        }
    elif event == "post":
        _state[sid] = {**_state.get(sid, {}), "tool": None}
    elif event == "stop":
        _state.pop(sid, None)
    return _state.get(sid)
```

**Claude 설정 예시**

```json
// ~/.claude/settings.json
{
  "hooks": {
    "PreToolUse":  [{ "command": "~/voice-terminal/server/agent_hook.sh pre"  }],
    "PostToolUse": [{ "command": "~/voice-terminal/server/agent_hook.sh post" }],
    "Stop":        [{ "command": "~/voice-terminal/server/agent_hook.sh stop" }]
  }
}
```

**점검 절차**

1. ~/.claude/settings.json 등록 후 Claude 실행 → 모바일에서 "Read tool 실행 중..." 토스트
2. WebSocket 브로드캐스트 정상 (다중 클라이언트 동시 표시)
3. Stop 훅 기존 TTS 동작 유지 (회귀)
4. tool_use 빈도 카운트 정확성

---

## Phase 5 — 안전·동기화

### #12. 안전 모드 (`vt mobile --safe`)

**목표:** 모바일 / 공개 터널 사용 시 위험 명령(`rm -rf`, `sudo`, `git push -f`, `dd`, fork bomb 등) Enter 직전 차단.

**수정/생성 파일**
- 신규 `server/safe_mode.py` — 패턴 매칭 + 라인 버퍼링
- `server/pty_manager.py` — `write()`에 검사 훅
- `bin/vt` — `--safe` 플래그 → `VT_SAFE_MODE=1` env 주입
- `frontend/index.html` — 안전 모드 표시 + 차단 메시지 토스트

**핵심 구현**

```python
# server/safe_mode.py
import os, re

DANGEROUS = [
    re.compile(r"\brm\s+-[rRf]+\s+/"),
    re.compile(r"\bsudo\b"),
    re.compile(r"git\s+push\s+(-f|--force)"),
    re.compile(r"\bdd\s+if=.+of=/dev/"),
    re.compile(r":\(\)\{\s*:\|:&\s*\};:"),         # fork bomb
    re.compile(r">\s*/dev/(sda|nvme)"),
    re.compile(r"chmod\s+-R\s+777\s+/"),
    re.compile(r"\bmkfs\."),
    re.compile(r"\b(shutdown|reboot|halt)\b"),
]

def check(cmd: str) -> tuple[bool, str | None]:
    if not os.environ.get("VT_SAFE_MODE"):
        return True, None
    for p in DANGEROUS:
        if p.search(cmd):
            return False, p.pattern
    return True, None
```

```python
# server/pty_manager.py — write() 부분
def write(self, sid, data):
    if os.environ.get("VT_SAFE_MODE"):
        self._line_buf.setdefault(sid, b"")
        self._line_buf[sid] += data
        if b"\r" in data or b"\n" in data:
            line = self._line_buf[sid].split(b"\r")[0].decode(errors="ignore")
            ok, pat = safe_mode.check(line)
            if not ok:
                self._broadcast(
                    sid,
                    f"\r\n\x1b[31m[차단됨] {pat}\x1b[0m\r\n".encode()
                )
                self._line_buf[sid] = b""
                return
            self._line_buf[sid] = b""
    # 기존 write 로직
```

**점검 절차**

1. `vt mobile --safe` → 환경변수 확인
2. 모바일에서 `rm -rf /tmp` Enter → 빨간 차단 메시지 + PTY 미전달
3. 안전 모드 비활성 시 정상 동작 (회귀)
4. **명시적 한계**: 우회(`r''m`, base64 인코딩, 스크립트 파일 실행)는 막을 수 없음 — 1차 방어선임을 README에 명시

---

### #13. 디바이스 워크스페이스 동기화

**목표:** 데스크톱·모바일 탭 순서, 활성 세션, UI 설정(테마 등) 자동 동기화. 현재 LocalStorage 기반이라 디바이스마다 따로 노는 문제 해결.

**수정/생성 파일**
- 신규 `server/workspace.py` — 디스크 저장 (`~/.config/vt/workspace.json`)
- `server/main.py` — `GET/PUT /api/workspace` + WS 브로드캐스트
- `frontend/index.html` — LocalStorage ↔ API 양방향 동기화

**핵심 구현**

```python
# server/workspace.py
import json
from pathlib import Path

WS_PATH = Path.home() / ".config" / "vt" / "workspace.json"

def load() -> dict:
    if WS_PATH.exists():
        return json.loads(WS_PATH.read_text())
    return {"tabs": [], "active": None, "ui": {}}

def save(d: dict):
    WS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WS_PATH.write_text(json.dumps(d, indent=2))
```

```python
# server/main.py
@app.get("/api/workspace")
async def ws_get():
    return workspace.load()

@app.put("/api/workspace")
async def ws_put(data: dict):
    workspace.save(data)
    for client in _notify_clients:
        try:
            await client.send_json({"type": "workspace", "data": data})
        except Exception:
            pass
    return {"ok": True}
```

```javascript
// frontend/index.html — 핵심 부분
async function pushWorkspace() {
  await fetch('/api/workspace', {
    method: 'PUT',
    body: JSON.stringify(state),
  });
}

notifyWS.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.type === 'workspace') applyWorkspace(msg.data);
};

// 탭 변경 시 debounce 300ms 후 pushWorkspace()
```

**점검 절차**

1. 데스크톱에서 탭 순서 드래그 → 1초 내 모바일 반영
2. 모바일에서 활성 탭 변경 → 데스크톱 갱신
3. 서버 재시작 후 디스크에서 복구
4. 오프라인(서버 미응답) 시 LocalStorage fallback 정상

---

## 전체 회귀 테스트 체크리스트

각 Phase 종료 시점에 실행:

```bash
vt doctor                  # 13개 항목 통과
vt status                  # 격리 소켓에 vt 세션 표시
vt voice → Ctrl+Shift+V    # 음성 → tmux 입력 (회귀)
vt mobile                  # QR 정상, 모바일 접속 OK
vt mobile --safe           # rm -rf 차단 토스트 (Phase 5)
vt agent codex             # 신규 (Phase 2)
vt template list           # 신규 (Phase 3)
vt run "ping"              # 백그라운드 + TTS (Phase 3)
vt popup status            # tmux popup (Phase 3)
```

E2E 회귀:
- 기존 음성 입력 흐름 (Voice Daemon · 모바일 🎤)
- 기존 TTS Stop 훅
- 모바일 PWA 설치 + 핸즈프리 모드
- 기존 토큰 인증 (`VT_TOKEN`)
- E2E 암호화 모드 (`vt mobile --e2e`)

---

## 진행 상황 추적

| Phase | 항목 | 상태 |
|-------|------|------|
| 1 | #1 격리 tmux 소켓 | ☐ |
| 2 | #2 AI CLI 감지 배지 | ☐ |
| 2 | #9 `vt agent <name>` 일반화 | ☐ |
| 3 | #4 CLAUDE.md 템플릿 | ☐ |
| 3 | #7 tmux popup | ☐ |
| 3 | #6 `vt run` headless 디스패치 | ☐ |
| 4 | #8 Pre/PostToolUse 훅 | ☐ |
| 5 | #12 `--safe` 모드 | ☐ |
| 5 | #13 디바이스 워크스페이스 동기화 | ☐ |
