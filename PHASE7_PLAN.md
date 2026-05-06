# Phase 7 — 라이브 프리뷰 + popup 키바인딩 통합

> lunemis/mux 코드 로직 분석 → 우리 코드와 비교 → 우월한 부분 차용 + 우리 컨텍스트(웹·모바일·음성)에 맞춘 개선 플랜.

---

## 0. 분석 대상

| 비교 축 | 우리(voice-terminal) | lunemis/mux | 비고 |
|---------|---------------------|-------------|------|
| 언어 | Python(FastAPI)+JS | Go | mux는 단일 바이너리 |
| UI | 웹(xterm.js)·모바일 PWA | TUI(Bubble Tea) | 우리는 원격 가능, mux는 로컬 only |
| AI 감지 | `agent_detector.py` | `tmux/aitools.go`+`process.go` | 둘 다 4종 동일 |
| popup | `cmd_popup`(bin/vt) | `tmux/popup.go` | 둘 다 `display-popup` 사용 |
| 라이브 프리뷰 | **없음** | `tmux/capture.go`+`ui/preview.go` | 우리에게 없는 기능 |
| 토큰/비용 | 없음 | `tmux/claude.go` | 별도 분석 가치 있음 |

---

## 1. 동일 기능 코드 로직 비교

### 1.1 AI CLI 감지

**우리 (`server/agent_detector.py:17-39`)**

```python
def detect(session_name: str) -> dict | None:
    out = subprocess.check_output(
        TMUX_BASE + ["list-panes", "-t", session_name, "-F", "#{pane_current_command}"],
        ...
    )
    for cmd in out.splitlines():
        cmd_lc = cmd.strip().lower()
        for key in KNOWN_AGENTS:
            if key in cmd_lc:        # ← substring 매치
                return {"agent": key, ...}
```

**lunemis (`tmux/aitools.go` + `tmux/process.go:25-103`)**

```go
func resolveCommand(panePID int, rawCmd string) string {
    if IsAICommand(rawCmd) {           // (1) raw가 정확히 매치 — fast path
        return rawCmd
    }
    if cached, ok := cmdCache[panePID]; ok && time.Now().Before(cached.expiresAt) {
        return cached.command          // (2) 5초 TTL 캐시
    }
    return scanChildProcesses(panePID, rawCmd)  // (3) 자식 스캔
}

func scanChildProcesses(panePID int, rawCmd string) string {
    childPIDs := findChildPIDs(panePID)   // pgrep -P + ps fallback
    for _, pid := range childPIDs {
        args, _ := runner.Output("ps", "-o", "args=", "-p", pid)
        for _, part := range strings.Fields(string(args)) {
            base := filepath.Base(part)         // ← 정확 매치
            if IsAICommand(base) { return base }
        }
    }
    return rawCmd
}
```

**비교 결과**

| 항목 | 우리 | lunemis | 우열 |
|------|------|---------|------|
| 정확도 | substring (`"claude" in cmd_lc`) — `claudewrapper` 같은 false positive 가능 | `filepath.Base(part)` 정확 매치 | **lunemis ↑** |
| 셸 wrapping 감지 | ❌ pane_current_command가 `bash`면 못 잡음 (예: `bash -c "claude --resume"`) | ✅ pgrep + ps로 자식 프로세스 스캔 | **lunemis ↑↑** |
| 캐싱 | ❌ 5초마다 폴링 시 N개 세션 × 매번 tmux fork | ✅ 5초 TTL, sync.Mutex | **lunemis ↑** |
| macOS 호환 | ✅ pgrep | ✅ pgrep + `ps -eo pid,ppid` fallback | mux가 더 견고 |
| 구현 줄 수 | 60 | 100+ (분리됨) | 우리가 단순함 |

→ **우리 약점:** `bash -c "claude --resume"` 같은 wrapping을 못 잡음. 실제 우리 `_open_tmux_terminal`이 `bash -lc "$cmd"`로 띄우면 `pane_current_command`는 `bash`로 잡힐 수 있음.

### 1.2 popup 키바인딩

**우리 (`bin/vt:680-706`)**

```bash
cmd_popup() {
  local action="${1:-status}"
  if [ -z "${TMUX:-}" ]; then
    cmd_${action} 2>/dev/null    # tmux 밖이면 직접 실행
    return
  fi
  # tmux 3.2+ 체크
  local first_ver=$(printf '3.2\n%s' "$tmux_ver" | sort -V | head -1)
  if [ "$first_ver" = "3.2" ]; then
    "${TMUX_BASE[@]}" display-popup -E -w 80% -h 70% \
      "$0 $action; echo; echo '엔터를 눌러 닫기'; read -r"
  fi
}
```

→ tmux 키바인딩(`~/.tmux.conf`) **자동 등록 없음**. 사용자가 직접 `bind v display-popup ...` 추가해야.

**lunemis (`tmux/popup.go:140-178`)**

```go
func SetupKeybind(key string) error {
    confPath, _ := findTmuxConf()    // XDG → ~/.config/tmux/tmux.conf → ~/.tmux.conf
    bindLine := fmt.Sprintf(`bind %s display-popup -E -w 85%% -h 80%% "%s"`, key, muxPath)

    if isOhMyTmux(confPath) {                        // (A) oh-my-tmux 감지
        localPath := findTmuxConfLocal(confPath)
        writeBindToLocal(localPath, bindLine)        // .tmux.conf.local로 라우팅
        stripMarkerLines(confPath)                   // 옛 잘못된 라인 청소
        return nil
    }
    upsertBindLine(confPath, bindLine, true)         // (B) 일반 conf
}

func isOhMyTmux(confPath string) bool {
    // 전략 1: symlink 타겟이 .tmux/ 아래
    // 전략 2: 첫 줄이 "# : << 'EOF'" (heredoc 시그니처)
}

func upsertBindLine(...) {
    // "# mux popup keybinding" 마커로 멱등 갱신
    for line in lines: if marker in line: replace
    if not replaced: append
}
```

**비교 결과**

| 항목 | 우리 | lunemis | 우열 |
|------|------|---------|------|
| `display-popup` 기본 동작 | ✅ | ✅ | 동등 |
| tmux 버전 체크 | ✅ 3.2+ | ✅ 3.2+ | 동등 |
| 키바인딩 자동 등록 | ❌ | ✅ `mux setup-keybind` | **lunemis ↑↑** |
| oh-my-tmux 호환 | ❌ | ✅ heredoc/symlink 두 전략 | **lunemis ↑** |
| 멱등성 (마커 기반) | ❌ | ✅ `# mux popup keybinding` | **lunemis ↑** |
| legacy 청소 | ❌ | ✅ 옛 잘못된 라인 자동 제거 | **lunemis ↑** |
| sentinel 보호 | ❌ | ✅ `# "$@"` 앞 삽입 | **lunemis ↑** |
| 기본 키 | popup 키 미지정 | `prefix+m` (변경 가능) | mux ↑ |

→ **우리 약점:** `vt popup`이 있어도 사용자가 매번 입력해야 함. 진짜 가치는 `prefix+v`로 어디서나 띄우는 것.

### 1.3 라이브 프리뷰

**우리** — 없음. 탭 전환 시 즉시 attach만. 다른 세션 상태를 알려면 직접 전환 필수.

**lunemis (`tmux/capture.go:20-26` + `ui/app.go:120-128, 157-174`)**

```go
// 1. 캡처 — 단순 wrapper
func CapturePaneTarget(target string) (string, error) {
    out, _ := runner.Output("tmux", "capture-pane", "-t", target, "-p", "-e")
    return strings.TrimRight(string(out), "\n"), nil
}

// 2. 폴링 — Bubble Tea ticker (500ms)
const refreshInterval = 500 * time.Millisecond
func tick() tea.Cmd {
    return tea.Tick(refreshInterval, func(t time.Time) tea.Msg { return tickMsg(t) })
}

// 3. tickMsg 처리 — 활성 항목 + 펼친 서브트리 모두 갱신
case tickMsg:
    cmds := []tea.Cmd{loadSessions, tick()}
    if it := m.currentItem(); it != nil {
        cmds = append(cmds, refreshPreview(previewKeyForItem(*it)))
        if tmux.IsAICommand(it.session.ActiveCommand) {
            cmds = append(cmds, loadTokenUsage(...))
        }
    }
    return m, tea.Batch(cmds...)

// 4. 커서 이동 시 즉시 갱신
case "j": if m.cursor < len-1 { m.cursor++; return m, m.refreshCurrentPreview() }
```

**핵심 옵션:** `capture-pane -p -e`
- `-p`: stdout으로 출력
- `-e`: ANSI escape 보존 → 색깔/굵기 유지
- (선택) `-S -<N>`: 마지막 N줄로 제한 가능

**렌더링 (`ui/preview.go:76-81`)**

```go
capLines := strings.Split(captured, "\n")
if len(capLines) > contentLines {
    capLines = capLines[len(capLines)-contentLines:]   // 마지막 N줄만
}
```

---

## 2. TODO #1 — 라이브 프리뷰 구현

### 목표

웹/모바일에서 **다른 탭(=다른 tmux 세션)의 화면을 ANSI 색깔 그대로 1초 주기로 미리보기**. 우리 컨텍스트는 TUI가 아닌 웹이므로 lunemis 로직을 백엔드 API + 프론트 폴링으로 변환.

### 사용자 가치
- 탭 4개 띄워놨을 때 어디서 Claude가 input 대기 중인지 한눈에
- 모바일에서 다른 세션 진행 상황 확인 시 탭 전환 비용 0
- "새 탭 만들기 vs 기존 탭 재사용" 결정 빨라짐

### 설계

```
┌─────────────────────────────────────────────────────────┐
│ frontend/index.html                                       │
│  - "그리드 뷰" 토글 버튼                                   │
│  - 그리드 모드: 모든 세션 카드, 각 카드에 미니 프리뷰        │
│  - 카드 hover 또는 활성 카드 1초 폴링                       │
└────────────────────────┬────────────────────────────────┘
                         │ WS/HTTP
                         ▼
┌─────────────────────────────────────────────────────────┐
│ server/main.py                                            │
│  GET /api/tmux/preview/{name}?lines=15                    │
│   → preview.capture(name, lines, ansi=True)               │
│  WS /ws-preview (옵션, 효율 모드)                          │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ server/preview.py (신규)                                  │
│  - capture_pane(name, lines=15, ansi=True)                │
│  - TTL 1초 캐시 (LRU 16) — 다중 클라이언트 폴러 비용 절감   │
│  - tmux -L vt capture-pane -t name -p -e -S -<lines>      │
└─────────────────────────────────────────────────────────┘
```

### 구현 체크리스트

**서버**
- [ ] **`server/preview.py` 신규**
  - [ ] `capture_pane(session_name: str, lines: int = 15, ansi: bool = True) -> str`
  - [ ] `TMUX_BASE = ["tmux", "-L", VT_TMUX_SOCKET]` (단일 tmux 서버 원칙 R5)
  - [ ] 명령: `capture-pane -t <name> -p [-e] -S -<lines>`
  - [ ] 1초 TTL 캐시 (`{name: (timestamp, content)}`)
  - [ ] `clear_cache(name)` — 명시적 무효화 (tmux send-keys 직후 호출 가능)
  - [ ] 타임아웃 1초, 실패 시 빈 문자열 + 로그
- [ ] **`server/main.py` 추가**
  - [ ] `GET /api/tmux/preview/{name}?lines=15&ansi=1` → `{content: "...", lines: N}`
  - [ ] 토큰 인증 미들웨어 통과 (이미 있음)
  - [ ] (옵션) `WS /ws-preview` — 클라이언트가 구독한 세션만 broadcast

**프론트**
- [ ] **`frontend/index.html`**
  - [ ] 새 버튼 `⊞ Grid` (탭 바 우측)
  - [ ] 그리드 모드 진입 시 `.grid-view` 컨테이너 표시, 각 세션을 카드로
  - [ ] 카드 마크업: `<div.session-card><div.preview><pre>...</pre></div><div.label>{name}</div></div>`
  - [ ] xterm.js 사용 옵션 vs `<pre>` + `ansi-to-html` 라이브러리 — 후자가 가벼움
  - [ ] 1초 폴링: `Promise.all(sessions.map(fetchPreview))`
  - [ ] 카드 클릭 → 해당 세션 attach + 그리드 모드 종료
- [ ] **`frontend/voice.js`**
  - [ ] (옵션) "현재 어느 세션이 input 대기 중?" 자동 음성 안내 — Claude 응답 끝에 `> ` 프롬프트가 있으면

**최적화**
- [ ] 그리드 모드 아닐 때는 폴링 안 함 (가시성 기반)
- [ ] WebSocket으로 변경분만 전송 (diff) — 1단계는 단순 풀 폴링으로 OK
- [ ] 모바일에서는 카드 6개까지만 (메모리)

### 검증
- [ ] 4개 세션 동시 띄워서 1초 마다 갱신되는지
- [ ] ANSI 색깔(예: claude의 옅은 회색 thinking 블록)이 보이는지
- [ ] 비활성 탭으로 옮긴 후 변경 이벤트가 즉시 카드에 반영되는지
- [ ] 100개 세션 시 폴링 비용 (capture-pane은 매우 가벼움 — mux가 4Hz로도 잘 돌아감)
- [ ] 토큰 인증 모드에서 401 안 뜨는지

---

## 3. TODO #2 — `vt setup-keybind` 구현

### 목표

`vt setup-keybind v` 한 줄로 `~/.tmux.conf`에 `prefix+v → display-popup vt`를 멱등 등록. lunemis의 oh-my-tmux 감지·sentinel 보호 로직을 그대로 차용.

### 사용자 가치
- `Ctrl+b → v` 한 번에 어디서나 vt status 팝업
- p10k·oh-my-tmux 사용자도 안전 (sentinel 보호)
- 옛 잘못된 라인 자동 청소

### 설계

```
vt setup-keybind [key] [action]
        │           │      └─ default: "status" (vt status를 popup으로)
        │           └─ default: "v"
        └─ 명령
                ↓
   _find_tmux_conf  → XDG → ~/.config/tmux/tmux.conf → ~/.tmux.conf
                ↓
   _is_oh_my_tmux?  → symlink || 첫 줄 == "# : << 'EOF'"
                ├─ Y → _write_to_local_conf (.tmux.conf.local, sentinel 앞)
                │       + _strip_marker_lines (메인 conf 청소)
                └─ N → _upsert_bind_line (마커 기반 멱등)
                ↓
   "Reload: tmux source-file <path>"
   "Then press: prefix + <key>"
```

### 구현 체크리스트

**`bin/vt` 추가 함수**

- [ ] `cmd_setup_keybind <key> [action]`
  - [ ] 인자 검증: key 1글자 또는 special (Space, F1 등) — 빈 입력 시 default `v`
  - [ ] tmux 버전 체크 (3.2+)
  - [ ] `_self_path` — `realpath "$0"` (심링크 따라가서 절대경로)
  - [ ] `bind_line="bind $key display-popup -E -w 85% -h 80% \"$_self_path $action\""`

- [ ] `_find_tmux_conf()` — 후보 탐색
  ```
  $XDG_CONFIG_HOME/tmux/tmux.conf
  $HOME/.config/tmux/tmux.conf
  $HOME/.tmux.conf  (default if none exists)
  ```

- [ ] `_find_tmux_conf_local(conf_path)` — `.local` 변형 (oh-my-tmux 짝)
  - `tmux.conf` → `tmux.conf.local`
  - `.tmux.conf` → `.tmux.conf.local`

- [ ] `_is_oh_my_tmux(conf_path)` — 두 전략 OR
  - 전략 A: `[ -L "$conf_path" ]` && `readlink` 결과에 `/.tmux/` 포함
  - 전략 B: `head -1 "$conf_path"` == `# : << 'EOF'`

- [ ] `_upsert_bind_line(path, line, marker)` — 멱등 갱신
  - 마커: `# vt popup keybinding`
  - 마커 포함 라인 있으면 in-place 교체, 없으면 append
  - 파일 없으면 생성 (`touch`)

- [ ] `_write_bind_to_local(local_path, line)` — sentinel 보호
  - `# "$@"` 라인 인덱스 검색
  - 마커 라인 이미 있으면 in-place 교체
  - sentinel 있으면 그 앞에 빈 줄+라인+빈 줄 삽입
  - sentinel 없으면 EOF append

- [ ] `_strip_marker_lines(path)` — legacy/잘못된 라인 청소
  - `# vt popup keybinding` 마커 라인 제거
  - 옛 install.sh 패턴 라인도 제거 (예: `display-popup -E -w 80% -h 70% "vt status"`)
  - 변경 있으면 사용자에게 알림

- [ ] dispatcher case 추가: `setup-keybind) shift; cmd_setup_keybind "$@" ;;`
- [ ] help 메시지: `vt setup-keybind [key] [action]   tmux 키바인딩 자동 등록`

### 검증
- [ ] 일반 `~/.tmux.conf` — `bind v display-popup -E -w 85% -h 80% "/abs/path/vt status"` 추가 확인
- [ ] 두 번 실행 — 라인 중복 없이 갱신만 (`grep -c marker` == 1)
- [ ] oh-my-tmux 감지 — heredoc 시그니처 있는 conf 만들고 테스트, `.tmux.conf.local`로 가는지
- [ ] sentinel 앞 삽입 — `# "$@"` 위치 보호되는지
- [ ] legacy 청소 — 옛 마커 없이 install된 라인이 새로 등록 시 제거되는지
- [ ] tmux 2.x 환경에서 명확한 에러 메시지

---

## 4. 부가 발견 — `agent_detector.py` 개선

### 현재 한계 (분석 1.1 참조)

1. **셸 wrapping 못 잡음** — `bash -lc "claude --resume"`이 PTY로 떠 있으면 `pane_current_command`는 `bash`. 우리 `_open_tmux_terminal_*`가 정확히 이 형태로 띄움. → false negative 다수.
2. **substring 매치 부정확** — `"claude" in "claudewrapper"` 통과.
3. **캐싱 없음** — 5초 폴링 × 4세션 = 4 tmux fork/sec. lunemis는 1 fork/5sec/세션.

### 개선안 (lunemis 차용 + 우리에 맞춤)

**`server/agent_detector.py` 재작성**

```python
import filepath, time
from threading import Lock

_CACHE_TTL = 5.0   # seconds
_cmd_cache: dict[int, tuple[float, str]] = {}   # panePID → (expiresAt, cmd)
_cache_lock = Lock()

def _resolve_command(pane_pid: int, raw_cmd: str) -> str:
    if raw_cmd in KNOWN_AGENTS:           # (1) fast path
        return raw_cmd

    with _cache_lock:
        cached = _cmd_cache.get(pane_pid)
        if cached and cached[0] > time.time():
            return cached[1]

    resolved = _scan_child_processes(pane_pid, raw_cmd)
    with _cache_lock:
        _cmd_cache[pane_pid] = (time.time() + _CACHE_TTL, resolved)
    return resolved

def _scan_child_processes(pane_pid: int, raw_cmd: str) -> str:
    children = _find_child_pids(pane_pid)
    for pid in children:
        try:
            args = subprocess.check_output(
                ["ps", "-o", "args=", "-p", str(pid)],
                stderr=subprocess.DEVNULL, timeout=1,
            ).decode()
        except Exception:
            continue
        for part in args.split():
            base = os.path.basename(part)
            if base in KNOWN_AGENTS:
                return base
    return raw_cmd

def _find_child_pids(parent_pid: int) -> list[int]:
    # Try pgrep (Linux 견고)
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(parent_pid)],
            stderr=subprocess.DEVNULL, timeout=1,
        ).decode()
        if out.strip():
            return [int(x) for x in out.split()]
    except Exception:
        pass
    # macOS fallback: ps -eo pid,ppid
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid"], stderr=subprocess.DEVNULL, timeout=1,
        ).decode()
        return [int(line.split()[0]) for line in out.splitlines()[1:]
                if len(line.split()) == 2 and int(line.split()[1]) == parent_pid]
    except Exception:
        return []

def detect(session_name: str) -> dict | None:
    # tmux로 (cmd, pid) 같이 받기 — pane_pid 추가!
    out = subprocess.check_output(
        TMUX_BASE + ["list-panes", "-t", session_name,
                     "-F", "#{pane_current_command}\t#{pane_pid}"],
        ...
    ).decode()
    for line in out.splitlines():
        cmd, pid_str = line.split("\t")
        resolved = _resolve_command(int(pid_str), cmd)
        if resolved in KNOWN_AGENTS:
            return {"agent": resolved, **KNOWN_AGENTS[resolved]}
    return None
```

### 개선 포인트
1. `#{pane_pid}` 추가 추출 — 자식 프로세스 스캔 가능
2. `os.path.basename(part)` 정확 매치 (substring → exact)
3. 5초 TTL 캐시 (Lock 안전)
4. macOS `ps -eo pid,ppid` fallback
5. 우리 `_open_tmux_terminal`이 `bash -lc "claude --resume"` 형태로 띄워도 잘 잡힘

### 검증
- [ ] tmux 안에서 `bash -c "sleep 999"` 띄우고 `detect` — None
- [ ] tmux 안에서 `bash -c "claude --version; sleep 999"` → 자식 claude 잡힘
- [ ] 4세션 동시 실행 + 5초 폴링 × 60초 → tmux fork 횟수 측정 (캐시 효과 확인)
- [ ] frontend agent 배지가 `vt agent claude` 직후 즉시 갱신되는지

---

## 5. 구현 순서 (의존성 + ROI 기반)

```
Step 1: agent_detector 리팩터 (1시간, 회귀 위험 낮음)
        └─ 이게 정확해야 라이브 프리뷰의 "AI 표시"도 의미 있음
        └─ TTL 캐시 도입으로 즉시 폴링 비용 감소

Step 2: vt setup-keybind (1.5시간, 외부 영향 中)
        └─ 사용자 환경(.tmux.conf) 수정 → dry-run 옵션 필수
        └─ Phase 6 옵트인 원칙(R8) 준수

Step 3: 라이브 프리뷰 백엔드 (1.5시간)
        └─ server/preview.py + /api/tmux/preview/{name}
        └─ 캐시 + 인증 + 단위 테스트

Step 4: 라이브 프리뷰 프론트 — 그리드 뷰 (3시간)
        └─ ansi-to-html 의존성 추가 (또는 자체 CSS 변환)
        └─ 모바일 반응형 (2열 → 1열)
        └─ "Grid" 토글 버튼 + localStorage 저장

Step 5: 문서화 + 회귀 테스트 + 서브에이전트 검증
        └─ README "기능" 표 갱신
        └─ ARCHITECTURE.md "라이브 프리뷰 데이터 흐름" 추가
        └─ CHANGELOG.md v1.2.0 항목

총 예상: 7-9시간
```

---

## 6. 진행 상황 추적

| Step | 항목 | 상태 |
|------|------|------|
| 1 | agent_detector pid 기반 + 캐시 | ☐ |
| 2 | `vt setup-keybind` (oh-my-tmux 호환) | ☐ |
| 3 | `server/preview.py` + `/api/tmux/preview/{name}` | ☐ |
| 4 | 프론트 그리드 뷰 + 1초 폴링 | ☐ |
| 5 | 문서화 + 검증 | ☐ |

---

## 7. 가져오지 않을 lunemis 기능

명시적 비채택 — 우리 컨텍스트와 안 맞거나 비용 대비 가치 낮음.

| 기능 | 미채택 사유 |
|------|-----------|
| Bubble Tea TUI | 우리는 웹/모바일이 1순위. TUI 추가하면 코드베이스 분리됨 |
| Go 단일 바이너리 | Python+JS 스택과 다름. PyInstaller로 비슷하게 가능하지만 ROI 낮음 |
| 토큰/비용 추적 (`tmux/claude.go`) | **별도 Phase로 검토 가치 있음** — `~/.claude/projects/{cwd-encoded}/{sessionId}.jsonl` 파싱 로직 자체는 단순. 우리 PWA 탭에 "$0.42 / 8.3k" 표시 ROI 높을 수 있음. Phase 8 후보. |
| `prefix+m` 통합 | 키 충돌 회피 위해 우리는 `v` 권장 (voice-terminal의 v). 사용자 변경 가능. |

---

## 8. 위험 요소 및 완화

| 위험 | 영향 | 완화 |
|------|------|------|
| 사용자 `~/.tmux.conf` 손상 | 높음 | dry-run 옵션 + 마커 기반 + 자동 backup (`.bak.<ts>`) |
| `pgrep -P` 권한 부족 (컨테이너) | 중 | 두 fallback 후 silent return (raw_cmd 반환) |
| 1초 폴링 × N세션 부하 | 중 | TTL 캐시 + 그리드 모드일 때만 폴링 + WS 변환 옵션 |
| ANSI 변환 XSS 가능성 | 높음 | `ansi-to-html`은 escape 보장 라이브러리 사용 (자체 정규식 X) |
| `pane_pid` tmux 버전 | 낮음 | tmux 1.5+ 지원, 우리 최소 요건 충분 |

---

## 9. 완료 정의 (DoD)

- [ ] agent_detector 자식 프로세스 스캔 + 캐시 + macOS fallback
- [ ] `vt setup-keybind` 일반 + oh-my-tmux 환경 모두 통과
- [ ] `/api/tmux/preview/{name}` ANSI 보존 + 1초 캐시
- [ ] 그리드 뷰 모바일 반응형 + 카드 클릭 attach
- [ ] 문서 3종(README/CLAUDE/ARCHITECTURE) 갱신 + CHANGELOG v1.2.0
- [ ] 서브에이전트 검증 ≥ 95%
- [ ] 회귀 테스트 (Phase 6 시나리오 9개 + Phase 7 신규 12개)
