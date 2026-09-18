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

```powershell
wsl
./install.sh voice
fsh voice
```

서버와 tmux는 WSL2 안에서 돌고, Windows 쪽 브라우저는 `localhost:7777`로 붙습니다.
음성 핫키는 WSLg(Windows 11)가 필요합니다 — 없으면 브라우저 마이크를 쓰세요.
`bin/fsh.ps1`은 WSL2 안의 `fsh`를 호출하는 PowerShell 래퍼입니다.

## Linux (Wayland)

Wayland 컴포지터는 설계상 전역 핫키 가로채기를 거부하므로 `Ctrl+Shift+M`이
동작하지 않습니다. 나머지는 전부 동작합니다. 웹 UI의 마이크 버튼을 쓰거나,
핫키가 꼭 필요하면 X11/Xwayland 세션에서 실행하세요.

## 음성 엔진 선택 순서

런타임에 아래 순서로 자동 선택됩니다.

| STT | TTS |
|---|---|
| 1. mlx-whisper (Apple Silicon) | 1. Kokoro (최고 품질) |
| 2. faster-whisper (그 외 전부) | 2. edge-tts (온라인, 다양한 음성) |
| | 3. macOS `say` / Windows Speech API (폴백) |

Whisper 가중치는 첫 사용 시 Hugging Face에서 자동 다운로드되어(~141MB) 디스크에
캐시됩니다. 설정 → 음성 → 「다운로드된 모델」에서 캐시 목록과 삭제를 볼 수 있습니다.
