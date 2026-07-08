# Changelog

All notable changes to voice-terminal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.5.1] — 2026-07-08

### Fixed
- **★ 입력/사용 중 메모리 지속 증가 — 리사이즈마다 TUI 전체 재도색 + screenReaderMode 증폭
  (`frontend/js/terminal.js`).** 라이브 세션 1개를 정상적으로 쓰는데도 입력할 때마다 Chrome
  메모리가 계속 늘던 문제. 두 원인이 겹쳤다.
  - `fitAndResize`(fb827a6의 "라인 깨짐/정렬" 수정)가 resize·focus·탭전환마다 **크기 변화가
    없어도** PTY에 resize를 보냈다. PTY는 SIGWINCH를 받아 Claude 같은 TUI가 **화면 전체를
    다시 그린다**(대량 출력). 모바일은 키보드가 뜰 때 `visualViewport` resize가 연속으로 쏟아져
    입력 중 재도색 폭탄이 됐다.
  - xterm `screenReaderMode: true`가 매 write마다 접근성 hidden DOM/live-region을 유지하는데,
    이 버퍼가 **총 출력량에 비례**해 커진다. CDP 실측: 동일 75,000줄 출력에 힙 증가가
    **off +1.6MB vs on +13.6MB (~8.4배)**.
  - 수정 ①: `sendResize`가 cols/rows가 실제로 바뀐 경우에만 전송(무변경 가드) — 실측상
    동일크기 resize 40회 → PTY 전송 0건. ②: resize 핸들러 120ms 디바운스로 키보드 thrash 흡수.
    ③: `screenReaderMode`를 기본 off(opt-in)로 — 스크린리더 사용자는
    `localStorage.setItem('vt-a11y','1')` 후 새로고침으로 켠다.
- **★ 로드 즉시 메모리 폭증 — 유령(phantom) 세션 대량 생성 (`server/routes/tmux.py`).**
  `POST /api/tmux/attach`가 **tmux 세션 존재 여부를 확인하지 않고** 무조건 PTY
  (`tmux attach-session -t <name>`)를 만들고 유효한 web 세션 id를 반환했다. 존재하지 않는
  이름이면 `tmux attach`가 즉시 실패하지만 그 전에 유령 세션이 이미 등록돼 `/api/sessions`에
  남았다. `restoreWorkspace`가 localStorage에 쌓인 **stale 탭마다** 이 attach를 호출하므로,
  페이지를 열면 유령 tmux 이름 개수만큼 **세션 + xterm 터미널(screenReaderMode) + WebSocket**이
  무더기로 생성돼 메모리가 폭증했다("세션이 없는데 접속하자마자 폭발").
  - 재현(Playwright): 유령 5 + 실제 1 탭으로 로드 시 **터미널 WS 6개·서버 세션 6개** 생성
    → 수정 후 **WS 1개·세션 1개** (유령은 404로 스킵). 유령 세션은 서버에도 누적되지 않는다.
  - 수정: `_attach_tmux`가 PTY를 만들기 전에 `tmux_runner.has_session()`으로 존재를 확인하고,
    없으면 `404 {"error":"tmux session not found"}`를 반환해 복원 루틴이 깔끔히 건너뛰게 한다.
    (클라이언트의 4004 재연결-중단 수정과 함께 스톰·폭증을 이중으로 차단.)

- **웹 클라이언트 메모리 폭증 / 무한 재연결 스톰 (회귀 fb827a6).** 접속 시 Chrome 메모리가
  계속 불어나 결국 탭이 멈추던 문제. 1.5.0의 네트워크 커밋(fb827a6)에서 WebSocket 재연결
  상한(`retries>=15`, notify는 20)을 없애 무한 재시도로 바꾸면서, `onopen`에서 백오프
  카운터를 **즉시 0으로 리셋**하는 로직을 그대로 남겨둔 것이 원인.
  - 서버는 세션이 없으면 `ws.accept()` **직후** code 4004로 닫는다(half-open flap). 이때
    `onopen`이 먼저 발화해 카운터가 0으로 리셋되므로 지수 백오프가 절대 자라지 못하고
    **2초마다 영구 재연결**. 매 사이클 소켓 생성 + scrollback(최대 256KB) 재주입 +
    접근성 DOM 재도색이 누적돼 메모리가 폭증했다. (서버 재시작·터널 flap·죽은 세션 탭 복원 시 발생)
  - 수정 ①: `4001`(인증 실패)/`4004`(세션 없음)처럼 재시도해도 결과가 같은 코드는 재연결하지
    않고 중단하고 사용자에게 안내한다. `frontend/js/terminal.js`, `frontend/voice.js`.
  - 수정 ②: 연결이 **3초 이상 안정적으로 유지된 뒤에만** 백오프 카운터를 리셋 —
    accept 직후 닫히는 flap에서도 지수 백오프(최대 30s)가 정상적으로 자란다.
    terminal / notify(voice.js) / agent(grid.js) WS 모두 동일 적용.
- **그리드 프리뷰 WebSocket keepalive 인터벌 누수 (`grid.js`).** 닫힌 소켓에 `send()`는 예외를
  던지지 않아(스펙: CLOSING/CLOSED 무음 폐기) `catch` 기반 정리가 동작하지 않았다. 프리뷰
  소켓이 닫힐 때마다 30초 인터벌이 죽은 소켓을 붙잡은 채 영구히 남았다 → `onclose`에서 명시적
  `clearInterval` + 매 tick `readyState` 방어.
- 탭 닫기(`removeSession`) 시 대기 중인 재연결 타이머를 취소해 지연 후 깨어나는 죽은 타이머 제거.
- **알림 권한 수락 후 알림 채널이 깨지던 문제 (`voice.js`).** 모바일 브라우저(Android Chrome 등)는
  `new Notification()` 생성자를 금지(`TypeError: Illegal constructor`)하고 SW의
  `showNotification()`만 허용한다. 권한이 `granted`가 되면 `showNotification`의 생성자 경로가
  활성화되는데, 여기서 던진 예외가 `notifyWs.onmessage`의 try/catch 부재로 새어나가 첫
  `task_complete`부터 notify 처리가 죽었다("권한 수락하면 멈춤").
  - `ServiceWorkerRegistration.showNotification()`을 우선 사용하고 `new Notification`은 폴백으로만,
    전 경로를 try/catch로 감쌌다. `onmessage` 전체도 try/catch로 방어(잘못된 JSON 등 포함).
  - `summary`가 없을 때 `summary.length` 접근으로 던지던 것도 정규화로 방어.
  - `sw.js`에 `notificationclick` 핸들러 추가 — 알림 클릭 시 열린 앱 탭 포커스/새 창.

---

## [1.5.0] — 2026-07-07

D9: Tailscale + SSH 원격 접속. 회사망처럼 화면 원격(크롬 원격 데스크톱/TeamViewer/RDP/VNC)이
막힌 환경에서도 Tailscale은 대개 통과하므로, 터미널만 필요하면 화면 원격 없이 Tailscale+SSH로
tmux 세션에 직접 붙을 수 있게 지원.

### Added
- `vt ssh [session] [--user <name>] [--add-key "<pubkey>"]` — 이 머신의 Tailscale IP/MagicDNS
  호스트명을 조회해 다른 기기에서 그대로 실행할 SSH / `tailscale ssh` 원클릭 attach 명령을 출력.
  `--add-key`로 접속할 기기의 공개키를 `~/.ssh/authorized_keys`에 등록 가능 (중복 스킵).
- `vt mobile --network tailscale` — Cloudflare Tunnel 없이 자신의 tailnet IP로만 서버 노출.
- `network_access.py`에 `tailscale` 키워드/모드 추가 — CGNAT 대역(`100.64.0.0/10`)을 LAN과
  구분해 화이트리스트. `VT_NETWORK_MODE=tailscale` → `localhost,tailscale` 스펙.
- `server/tailscale.py` — `tunnel.py`(Cloudflare)와 동일한 패턴으로 `tailscale status --json`을
  파싱해 설치/실행/자기 tailnet IP/MagicDNS 호스트명 노출. `GET /api/tailscale/status`,
  `/api/capabilities`의 `tailscale` 필드로 웹 UI에도 노출.
- `VT_NOTIFY_CLIENT_EVENTS=1` (옵트인, 기본 OFF) — tmux `client-attached`/`client-detached` 훅
  (`server/hooks/tmux_client_notify.sh`)이 `POST /api/notify/client-event`를 호출해 기존
  ntfy/Telegram 브릿지로 "누가 언제 접속했는지" push. SSH처럼 web/voice 경로 밖에서 붙는
  클라이언트는 서버가 원래 알 방법이 없었던 것을 보완 — `who` 출력에서 원격 호스트를
  best-effort로 추출.
- `bin/vt`의 `_ensure_tmux()`가 호출될 때마다 (옵트인 시) 훅을 재등록하는
  `_maybe_register_client_hooks()` — `vt voice`/`mobile`/`start`/`ssh` 어디서 세션을 만들어도
  자동 적용.
- `vt doctor` — Tailscale 설치/연결 상태 체크 항목 추가. `vt status` — 현재 tailnet IP 표시.
- `vt help ssh` — Tailscale+SSH 시나리오 전용 도움말 (`docs/help/ssh.md`).

### Docs
- README: "Tailscale + SSH 원격 접속" 섹션, 접속 방법/API/주요 기능 표 갱신, 새 시나리오 추가.
- ARCHITECTURE.md: 4.7 확장 포인트(새 원격 접속 경로), 보안 모델 표, 로드맵에 D9 반영.
- CLAUDE.md: `vt ssh` 커맨드, API 엔드포인트, 아키텍처 트리, 주요 기능 표 갱신.

---

## [1.4.0] — 2026-05-09

UX overhaul + Linux 1급 동등화. [docs/PLAN_UX_OVERHAUL.md](./docs/PLAN_UX_OVERHAUL.md) 의 Wave 1-5 적용.

### Added
- `vt manage` — Textual 기반 TUI 관리 도구 (cross-platform). 세션 목록/rename/kill/attach + 음성 타깃 lock + 서버 상태 + 핫키 표시. 의존성: `textual>=0.50`.
- `vt attach <name>` — 임의 tmux 세션을 새 OS 터미널 창에 attach. 인자 없으면 fzf 또는 텍스트 prompt.
- `vt voice-target <name|--auto>` — Voice Daemon 타깃 세션 lock/해제. IPC 파일 `~/.vt/voice_target` (재시작 불필요, daemon이 매 발화 시 읽음).
- `vt hotkey [list|set|reset|disable] <action> <key>` — 핫키 조회/변경. `~/.vt.env`의 `VT_HOTKEY_VOICE` 등.
- `vt help <topic>` — 토픽별 도움말. `concepts`/`voice`/`hotkeys`/`target`/`troubleshoot` 5종 (`docs/help/*.md`).
- `vt stop --purge` — tmux `kill-server`까지 완전 종료. 디폴트는 tmux 세션 유지(영속성 보장).
- `platform_utils.notify(title, msg)` — 크로스 플랫폼 데스크톱 알림 (macOS osascript / Linux notify-send).
- `platform_utils.spawn_linux_terminal(cmd)` — gnome-terminal/konsole/alacritty/kitty/wezterm/xfce4-terminal/xterm 분기.
- `platform_utils.open_terminal_with_command(cmd)` — macOS/Linux 통합 진입점.
- `voice_daemon.py`의 `resolve_voice_target_pane()` — lock 우선 + AUTO 폴백. `_parse_hotkey()` — `ctrl+shift+v` 형식 문자열 → pynput 키 set.
- `vt doctor`에 Linux 항목: 터미널 emulator, TTS chain (espeak-ng/spd-say), notify-send, XDG_SESSION_TYPE(Wayland 가드), textual 설치 여부, fzf 가용성.
- `install.sh` 끝에 `vt install-profiles` 자동 권유 (TTY일 때만, 사용자 동의 후).
- `vt voice` 첫 실행 시 onboarding 안내 (셸 init / iTerm Dynamic Profile 미등록 감지).

### Changed
- `PATCH /api/sessions/{id}`가 tmux 세션 이름도 같이 변경 (이전엔 메타데이터만). 안전 문자(`[A-Za-z0-9_-]`) 검증 + 충돌 체크 + tmux `rename-session` 호출.
- `POST /api/tmux/create` 디폴트 이름이 `web-XXXX` 랜덤 → `{cwd basename}` + 충돌 시 `-2`, `-3` 순번. 사람이 외울 수 있는 이름.
- `voice_daemon.py`의 `HOTKEY` 하드코딩 제거 → `~/.vt.env`의 `VT_HOTKEY_VOICE` 읽음. `VT_HOTKEY_VOICE_DISABLED=true`로 비활성. `VT_VOICE_MEDIA_KEYS=off`로 이어폰 미디어 키 트리거 비활성.
- `voice_daemon.py`가 `~/.vt/voice_target` 파일 우선 읽음 → lock 모드. 없으면 most-recent (AUTO).
- `platform_utils.tts_speak`에 Linux fallback 추가: espeak-ng → spd-say → espeak.
- README "Windows (WSL2)" 섹션 명시화 — Windows 네이티브 미지원, WSL2는 Linux로 동작.
- 지원 플랫폼 매트릭스에 Linux X11/Wayland 분리 + TUI 컬럼 추가.

### Removed
- 모바일 보이스바의 🔄 핸즈프리 버튼 — VAD 미구현 상태에서 이름과 동작 불일치. `voice.js`의 `handsFreeModeOn` 상태/`toggleHandsFree`/자동 재시작 분기 제거.

### Frontend
- 보이스바에 🎵 "이어폰" 토글 버튼 추가 — Media Session API hijack ON/OFF. OFF 시 OS가 기본 미디어 컨트롤(음량/재생) 가져감. localStorage `vt_mediakey_trigger`로 영구 저장.
- `setupMediaSession()` 첫 호출 가드 — `mediaKeyTriggerOn=false`면 등록 스킵.

---

## [1.3.0] — 2026-05-08

Phase 9 — 안정성·네트워크 효율 일괄 패치 ([PLAN_PHASE9.md](./docs/PLAN_PHASE9.md), [TEST_REPORT_V3.md](./docs/TEST_REPORT_V3.md)). 10건 적용.

### Added
- `/ws-preview/{name}` (`server/routes/tmux.py`) — grid view용 push 채널. `preview.py`에 watcher + subscribe/unsubscribe.
- `/api/auth` POST + `vt_session` HttpOnly cookie — 토큰을 query string에서 분리해 로그/공유 노출 차단.
- `frontend/vendor/` — xterm.js·addon-fit·addon-search·lucide·tweetnacl 자체 호스팅 (~1.5MB). `install.sh`가 자동 다운로드.
- `_etag_response` 헬퍼 (`server/routes/system.py`) — capabilities/safe-mode/tunnel-status에 ETag/304. `stable_for_etag`로 timestamp 같은 동적 필드 제외 hash.
- `_convert_to_wav_pyav` (`server/voice_handler.py`) — pyav in-process audio decoding. ffmpeg subprocess fallback 유지.
- Service Worker stale-while-revalidate 캐시 (`frontend/sw.js`).
- PTY 출력 query 가로채기 (DA1/DA2/OSC10/11) — `server/pty_manager.py` `PTY_OUT_QUERY_REPLIES`. stdin 정규식 필터와 이중 방어.
- WS heartbeat 기본값 15/45초 (`server/routes/pty.py`, `server/routes/agents.py`).

### Changed
- `frontend/index.html`의 agents 폴링 `setInterval` 제거 → `/ws-agent` push 단일화.
- grid view 1초 폴링 제거 → 카드별 `/ws-preview` 구독.
- `pty_manager.PTYManager.get_scrollback`을 256KB cap (마지막 N 바이트만 반환).
- `requirements-voice.txt`에 `av>=11.0` 추가.

### Security
- 토큰 cookie 전환: 액세스 로그·브라우저 history·공유 URL에서 토큰 평문 노출 차단.
- HttpOnly + SameSite=Strict + Secure (HTTPS 시) 적용.

---

## [1.2.1] — 2026-05-07

종합 테스트(`docs/TEST_REPORT.md`)에서 발견된 13건 이슈 일괄 수정.

### Fixed
- **P0 — PTY ANSI escape query 응답 누수**: stdin에서 DA1/DA2/CPR/OSC10/OSC11 응답 패턴(`\x1b[?…c`, `\x1b[…R`, `\x1b]10;…`)을 정규식으로 영구 차단 + PTY 부팅 후 0.5s 동안 ESC 입력 추가 차단. 2차 점검에서 모바일 ws 재연결 시 회귀가 발견되어 정규식 필터를 1순위 방어선으로 추가 (`server/pty_manager.py` `TERMINAL_AUTO_REPLY_RE`). 사용자 화살표/Ctrl+C 등 일반 입력은 통과.
- **P1 — 좀비 프로세스 누적**: process-wide `SIGCHLD` 핸들러 + `destroy_session`의 reaper를 blocking `waitpid`로 변경 (`server/pty_manager.py`).
- **P1 — `install.sh` 비대화형 환경에서 로컬 레포 무시**: `[ -t 0 ]` 가드를 제거하고 스크립트 위치의 `bin/vt` 존재만으로 판정 (`install.sh`).
- **P1 — 모바일 음성바가 시스템 네비 바와 충돌**: `padding-bottom: env(safe-area-inset-bottom)` + 터치 타겟 48px 보장 (`frontend/index.html`).
- **P1 — `/api/agents` 폴링 빈도 과다**: 5s → 8s 완화 (`frontend/index.html`).
- **P2 — `/favicon.ico` 404**: PNG 아이콘으로 라우팅 (`server/main.py`).
- **P2 — TTS 빈 텍스트 200 + 0 bytes**: 400 + `{"error":"empty text"}`로 거절 (`server/routes/voice.py`).
- **P2 — STT 무음 입력 → "You?" Whisper 환각**: 16-bit PCM 평균 절대값 임계값(<600)으로 무음 차단 (`server/voice_handler.py`).
- **P2 — `HEAD /api/sessions` 405**: `methods=["GET","HEAD"]` 명시 (`server/routes/pty.py`).
- **P2 — xterm.js Canvas → 접근성 트리에 텍스트 미노출**: `screenReaderMode: true` (`frontend/index.html`).
- **P2 — `~/.vt.env`의 `VT_PYTHON` 절대경로 → 이식성 ↓**: `${VT_DIR}/.venv/bin/python` 형태 변수화 (`install.sh`).

### Docs
- `docs/TEST_CHECKLIST.md` — 9개 섹션 종합 테스트 체크리스트.
- `docs/TEST_REPORT.md` — 1차 13건 발견 사항 + 네트워크 분석 + 보안 점검.
- `docs/TEST_REPORT_V2.md` — 2차 점검 결과: 13/13 항목 해결 검증 + 정규식 단위 테스트 매트릭스.
- `TEST_CHECKLIST.md`에 모바일 원격 검증 전 `adb reverse --remove tcp:7777` 안내 추가.

---

## [1.2.0] — 2026-05-07

Phase 7-8 통합 릴리스. lunemis/mux·purplemux·claude-mux·reminder-watch 코드 비교 분석 후 도출된 10개 개선 항목 일괄 적용. 서브에이전트 검증 100%.

### Added
- **Phase 7 — 라이브 프리뷰 + setup-keybind + agent_detector 강화**:
  - `vt setup-keybind [key] [action]` — `~/.tmux.conf` 자동 등록 (oh-my-tmux 호환·sentinel 보호·legacy 청소·마커 멱등)
  - `server/preview.py` + `GET /api/tmux/preview/{name}` — `capture-pane -p -e -S` ANSI 보존 + 1초 TTL 캐시
  - 프론트 그리드 뷰: 모든 tmux 세션을 카드로, 1초 폴링, ANSI→HTML 변환, 카드 클릭으로 attach
  - `agent_detector` 자식 프로세스 스캔 (`pgrep -P` + `ps -eo` fallback) → `bash -c "claude --resume"` wrapping 케이스도 정확 감지
  - 5초 TTL 캐시 + `os.path.basename` 정확 매치 (substring → exact)
- **Phase 8 G1 — 네트워크 정책 + Cloudflare Tunnel 자동 감지·재사용·명명 터널**:
  - `vt mobile --network localhost|lan|all` — 보안 모드 분리 (CIDR 화이트리스트 미들웨어)
  - `server/network_access.py` — IPv4/IPv6 CIDR 파싱·매칭, `resolve_bind_host` 자동 결정
  - `server/tunnel.py` + `GET /api/tunnel/status` — cloudflared 자동 감지·재사용
  - `vt tunnel [status|setup|teardown|switch]` — 명명 터널 옵트인 (`VT_TUNNEL_NAME`/`VT_TUNNEL_HOSTNAME`)
- **Phase 8 G2 — WS 안정성**:
  - `asyncio.Queue` 자체 send 큐 + 백프레셔 (qsize 200/50 → PTY pause/resume)
  - 30초/90초 ping/pong 하트비트 + 자동 close
  - 연결 한도: 세션당 8 / 전체 32
- **Phase 8 G3 — tmux 효율**:
  - `server/tmux_runner.py` 공통 헬퍼 + 일관 timeout
  - `config/vt-tmux.conf` 격리 config (`tmux -u -L vt -f conf`) — 사용자 `.tmux.conf` 영향 차단
  - `get_all_panes_info` batch — N개 세션을 1회 호출로 처리
- **Phase 8 G4 — 보안 2중 방어**:
  - `vt agent claude`, `vt run` 호출 시 `--disallowedTools` 자동 주입 (Claude 도구 호출 단계 차단)
  - `_DEFAULT_DISALLOWED`/`_SAFE_DISALLOWED` 정책 + `VT_DISALLOWED_TOOLS` 사용자 override
  - `vt run` lockfile (prompt 해시 기반, stale 자동 청소)
- **Phase 8 G5 — trust prompt 자동 응답 (옵트인)**:
  - `server/auto_responder.py` + `VT_AUTO_TRUST=1` — Claude 첫 진입 시 "Yes, I trust this folder" 자동 처리
  - 5초 cooldown + 윈도우 매처 (큰 출력 분할 안전)
- **Phase 8 G6 — 메타 효율**:
  - `session_store` `tmux_name` 역인덱스 (O(N) → O(1))
  - `server/ttl_cache.py` — thread-safe TTL 캐시 일반화 유틸
- **Phase 8 G7 — UX**:
  - localStorage 워크스페이스 자동 저장/복원 (탭 목록·순서·활성 탭)
  - HTML5 DnD 탭 드래그 정렬

### Changed
- `vt status` Cloudflare Tunnel 상태 4줄 정밀 표시 (설치/실행/모드/URL)
- `bin/vt`의 `TMUX_BASE`에 `-u` UTF-8 강제 + `-f` 격리 config 자동 탐색

### Fixed
- `agent_detector` substring 매치(`"claude" in "claudewrapper"`) 같은 false positive

---

## [1.1.0] — 2026-05-06

5/6 진행분. ralph → vt 리네이밍 후 9개 개선 항목(Phase 1-5)과 크로스 플랫폼 터미널 통합 강화(Phase 6) 추가.

### Added
- **Phase 1 — 격리 tmux 소켓**: `bin/vt`·`server/main.py`가 `tmux -L vt` 사용 → 사용자 기존 tmux 세션과 완전 분리.
- **Phase 2 — AI 인식**: `server/agent_detector.py` (claude/codex/aider/gemini 감지), `GET /api/agents`·`/api/agents/{name}` 엔드포인트, `vt agent <name>` 일반화, frontend 탭 agent 배지 폴링.
- **Phase 3 — 명령 확장**: `vt template [save|apply|list|rm]`, `vt popup <action>` (tmux 3.2+ display-popup), `vt run "..."` (headless `claude -p` 백그라운드 + TTS·ntfy 알림).
- **Phase 4 — Pre/PostToolUse 훅**: `server/agent_hook.sh` 통합 훅 진입점, `server/agent_status.py` in-memory 상태 추적, `POST /api/agent/event`·`GET /api/agent/status`·`WS /ws-agent`, frontend 도구 사용 토스트.
- **Phase 5 — 안전 모드 + 워크스페이스**: `server/safe_mode.py` 위험 명령 11개 패턴 차단, `vt mobile --safe` 옵션 (`VT_SAFE_MODE=1`).
- **Phase 6 — 크로스 플랫폼 터미널 통합 강화**:
  - `vt install-profiles [--dry-run]` — iTerm2 Dynamic Profile 자동 등록 + Ghostty/WezTerm/Kitty/Alacritty/Windows Terminal/Terminal.app snippet 안내.
  - `vt shell-init [zsh|bash|fish|pwsh]` — 5중 TTY 가드(interactive + TTY + `$TMUX` + IDE 환경변수 + tmux 존재) 셸 init 스니펫 출력.
  - `_ensure_tmux` 3단계 분리 (`_ensure_tmux_session` / `_tmux_populate` / `_tmux_attach_or_switch`) — 비-TTY 경로에서도 `tcgetattr` 경고 없음.
  - `voice_daemon.py`가 `TMUX_BASE = ["tmux", "-L", "vt"]` 통일 — 단일 tmux 서버 원칙.
- `VERSION` 파일 + 본 `CHANGELOG.md` 추가.

### Changed
- ralph → vt 전체 리네이밍 (CLI, 스킬, 문서).
- README/CLAUDE.md/ARCHITECTURE.md를 v1.1 기준으로 갱신 (단일 tmux 서버 원칙, 클라이언트 매트릭스, 설치 후 통합 가이드).

### Fixed
- TTS 훅이 마지막 assistant 응답의 마지막 text 블록 끝부분만 읽도록 개선.

---

## [1.0.0] — 2026-04-14

초기 안정 버전. 데스크톱·모바일·음성 기능의 기본 골격 완성.

### Added
- 대규모 개선 8종 — 설치·알림·E2E 암호화·핸드오프·barge-in.
- xterm.js 멀티탭 PWA, Voice Daemon (macOS Ctrl+Shift+V), faster-whisper STT + edge-tts TTS.
- Claude Code Stop hook 기반 자동 TTS 요약.
- Cloudflare Tunnel 원격 접속, 토큰 인증 미들웨어, ntfy/Telegram 푸시 알림.
- `vt` CLI 통합 진입점, `install.sh` 원라인 설치 스크립트.

### Changed
- 모바일 UI 개선, OutputWatcher 비활성화, README 전면 재작성.
- 설치 방식을 Claude 주도 인터랙티브에서 `install.sh` 원라인으로 전환.

### Fixed
- WebSocket 재연결 버그.
- 이모지 → Lucide 아이콘 교체.
- 음성 UI 조건부 표시.
