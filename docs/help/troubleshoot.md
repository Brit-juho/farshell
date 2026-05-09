# vt help troubleshoot — 트러블슈팅

## 첫 번째 진단

    vt doctor

설치/환경을 자동 검사 (Python, venv, tmux, cloudflared, ffmpeg, 포트, vt CLI, PATH, `~/.vt.env`, VT_TOKEN, push 알림, 터미널 앱, TUI, fzf 등). 대부분 문제는 여기서 발견됨.

## 자주 발생하는 문제

### `vt: command not found`
- `~/.local/bin`이 PATH에 없음
- `source ~/.zshrc` 또는 새 터미널 창
- 안 되면 `~/.local/bin/vt status` 직접 실행해 동작 확인

### `vt voice` 핫키가 안 먹힘
- **macOS**: 시스템 설정 → 개인정보 → 접근성 → 사용 중인 터미널 앱 체크
- **Linux Wayland**: 글로벌 핫키 보안 차단. X11 세션 또는 모바일 🎤
- 시스템 단축키와 충돌: `vt hotkey set voice <다른 키>`
- 데몬이 안 떠 있을 수도: `vt status`로 확인

### 새 iTerm 창이 자동 tmux로 안 들어감
- `vt install-profiles`로 Dynamic Profile 등록 안 됐거나
- `eval "$(vt shell-init zsh)" >> ~/.zshrc` 안 됐거나
- 둘 중 하나만 활성화하면 됨. 양쪽 등록 무해 (가드 있음)
- 기존에 이미 열린 창은 영향 받지 않음 — 새 창부터 적용

### `vt stop` 후에도 tmux 세션이 살아있음
- **이건 정상.** tmux 영속성 = 핵심 가치
- `vt stop --purge`로 tmux 서버까지 완전 종료

### TTS가 안 들림
- 외부 모니터/AirPods로 출력될 수 있음 — 출력 장치 확인
- 서버 미실행 시 `say -v Yuna` 폴백 — 음질 낮음. `vt start`로 서버 띄우면 edge-tts mp3
- Linux: `espeak-ng` 설치 필요

### 모바일 접속 시 소리 안 남
- 화면 한 번 터치 (브라우저 autoplay 정책)
- 음성 전용 모드에서는 자동

### 포트 충돌
    ~/.vt.env에서 VT_PORT 변경

### 서버 시작 실패
    cat /tmp/vt-server.log

### Cloudflare Tunnel URL 안 뜸
- `vt status`로 cloudflared 동작 확인
- `cat /tmp/cloudflared.log`
- 안 깔렸으면: `brew install cloudflared` (macOS) / 공식 바이너리 (Linux)

### tmux 세션 이름 변경했는데 안 바뀜
- v1.4 이후 `PATCH /api/sessions/{id}`가 tmux 세션도 같이 변경 (W1-1)
- 안 바뀌면 이름에 안전하지 않은 문자(공백/특수기호) 포함됐을 가능성
- 영숫자/dash/underscore만 사용

### Voice Daemon이 엉뚱한 세션에 입력
- `vt voice-target <세션명>`으로 lock
- 또는 `vt voice-target` 명령으로 현재 타깃 확인

## 로그 위치

| 항목 | 경로 |
|---|---|
| 서버 | `/tmp/vt-server.log` |
| Cloudflare Tunnel | `/tmp/cloudflared.log` |
| Voice Daemon | stdout (tmux 안에서 실행 시 거기) |
| PID 파일 | `~/.vt/{server,tunnel,voice}.pid` |
| 음성 타깃 lock | `~/.vt/voice_target` |
| 환경 설정 | `~/.vt.env` |
