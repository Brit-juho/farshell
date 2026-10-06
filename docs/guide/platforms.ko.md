# 지원 플랫폼

Windows 네이티브는 지원하지 않습니다 — Linux와 동일하게 동작하는 WSL2를 쓰세요.

| 플랫폼 | 서버 | Voice Daemon | TUI (`fsh manage`) | 브라우저 접속 |
|---|---|---|---|---|
| macOS (iTerm2 / Ghostty / Warp 등) | 지원 | 핫키 + 이어폰 조작 | 지원 | 지원 |
| Linux (X11) | 지원 | 전역 핫키 | 지원 | 지원 |
| Linux (Wayland) | 지원 | 컴포지터 보안 정책상 핫키 차단 — 모바일 마이크 사용 | 지원 | 지원 |
| Windows (WSL2) | 지원 | WSLg 필요 | 지원 | 지원 |
| Windows (네이티브) | 미지원 | 미지원 | 미지원 | — |
| iOS (Safari / Chrome) | — | — | — | 지원 (Media Session 포함) |
| Android (Chrome) | — | — | — | 지원 |

## Windows (WSL2)

PowerShell에서 `wsl`로 Linux 셸에 들어간 뒤, FarShell 저장소 루트에서 실행합니다:

```bash
./install.sh voice
fsh voice
```

서버와 tmux는 WSL2 안에서 돌고, Windows 쪽 브라우저는 `localhost:7777`로 붙습니다.
음성 핫키는 WSLg(Windows 11)가 필요합니다 — 없으면 브라우저 마이크를 쓰세요.
`bin/fsh.ps1`은 WSL2 안의 `fsh`를 호출하는 PowerShell 래퍼입니다.

## Linux (Wayland)

Wayland 컴포지터는 설계상 전역 핫키 가로채기를 거부하므로 `Ctrl+Shift+M`이
동작하지 않습니다. 나머지는 전부 동작합니다. 웹 UI의 마이크 버튼을 쓰거나,
핫키가 꼭 필요하면 X11 세션에서 실행하세요.

## 음성 엔진 선택

음성 인식(STT)은 **FarShell을 돌리는 컴퓨터에서** 실행됩니다. 폰이나 다른 PC는 녹음해서 올리기만 합니다.
설정 → 음성 및 알림에서 모델을 고르면, 이 서버가 쓸 수 있는 모델만 보이고 하나에 「권장」이 표시됩니다.

| 서버 | 쓸 수 있는 모델 |
|---|---|
| 애플 칩 맥 | Qwen3-ASR 0.6B / 1.7B (MLX), Qwen3-ASR (llama.cpp), Apple 음성 인식(macOS 26+) |
| Intel 맥 · Linux · WSL2 | Qwen3-ASR (llama.cpp, CPU) |

자동으로 받는 것은 없습니다. 엔진 프로그램(llama.cpp)과 모델 파일은 설정에서 확인 창을 거친 뒤에만 내려받습니다.
TTS는 edge-tts(온라인) → macOS `say` / Linux `espeak-ng` 순입니다.
