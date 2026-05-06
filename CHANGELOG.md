# Changelog

All notable changes to voice-terminal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
