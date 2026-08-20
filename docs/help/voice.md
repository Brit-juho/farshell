# fsh help voice — 음성 입력 동작

FarShell은 3가지 음성 입력 경로가 있습니다.

## 1. Voice Daemon (데스크톱 전역 핫키)

    fsh voice

- 백그라운드 실행. 노션/브라우저 작업 중에도 동작
- 디폴트 핫키: `Ctrl+Shift+V` (변경 가능, `fsh help hotkeys` 참고)
- 이어폰 Play/Pause 버튼으로도 토글 가능 (macOS Media Key)
- STT 후 텍스트 + Enter로 활성 tmux pane에 직접 입력 → 즉시 실행됨
- 종료: `fsh stop`

음성 타깃은 디폴트로 tmux의 most-recent active pane이지만,
`fsh voice-target <세션명>`으로 특정 세션에 lock 가능 (자세히는 `fsh help target`).

포커스가 다른 앱(브라우저 등)에 있어도 daemon은 계속 동작하기 때문에, 녹음을
시작하는 순간 "→ dev:0.0" 같은 데스크톱 알림으로 이번 발화가 어느 pane으로
갈지 미리 보여준다(lock된 경우 🔒 표시). 끄려면 `VT_VOICE_TARGET_NOTIFY=off`.

## 2. 모바일 🎤 버튼

웹 UI 보이스바의 마이크 버튼:
- 누르면 녹음 시작, 다시 누르면 종료
- STT → 현재 보고 있는 탭의 세션에 입력
- 모바일은 "현재 보는 탭 = 타깃"이라 lock 불필요

## 3. 음성 전용 모드 (🎧)

웹 UI에서 `음성 전용` 버튼. 터미널 화면을 숨기고 큰 마이크 버튼만 표시.
이어폰 운전 중 / 화면 안 보고 손으로만 조작할 때.

## TTS (음성 출력)

- Claude Code 응답 완료 시 자동 TTS 재생 (Stop hook)
- macOS: `say -v Yuna` 또는 edge-tts mp3
- Linux: espeak-ng → spd-say → espeak
- 서버 미실행 시 fallback으로 동작

## STT 엔진

우선순위:
1. mlx-whisper (Apple Silicon 최적화)
2. faster-whisper (cross-platform)

첫 실행 시 모델 자동 다운로드 (~141MB).

## 자주 묻는 질문

- **Q. STT가 텍스트만 입력? 아니면 Enter도?**
  - A. 텍스트 + Enter. 즉시 실행됨. 셸에서 위험 명령 발화 주의.

- **Q. claude가 안 떠있어도 동작?**
  - A. 동작함. 활성 tmux pane이 셸이면 셸로 들어감.

- **Q. codex CLI에서도 동작?**
  - A. 동작함. claude/codex 구분 없이 활성 pane에 보냄.

- **Q. 잡음이 자동으로 입력되나?**
  - A. ❌ 안 됨. 명시적으로 핫키/버튼 토글 후 시작-종료.
