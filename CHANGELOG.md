# Changelog

All notable changes to voice-terminal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
