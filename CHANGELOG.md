# Changelog

All notable changes to voice-terminal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
