# Supported platforms

Native Windows is not supported — use WSL2, which behaves as Linux.

| Platform | Server | Voice Daemon | TUI (`fsh manage`) | Browser access |
|---|---|---|---|---|
| macOS (iTerm2 / Ghostty / Warp, …) | Yes | Hotkey + earbud controls | Yes | Yes |
| Linux (X11) | Yes | Global hotkey | Yes | Yes |
| Linux (Wayland) | Yes | Hotkey blocked by the compositor's security policy — use the mobile mic | Yes | Yes |
| Windows (WSL2) | Yes | Requires WSLg | Yes | Yes |
| Windows (native) | No | No | No | — |
| iOS (Safari / Chrome) | — | — | — | Yes (Media Session supported) |
| Android (Chrome) | — | — | — | Yes |

## Windows (WSL2)

```powershell
wsl
./install.sh voice
fsh voice
```

The server and tmux run inside WSL2; the browser on the Windows side connects to
`localhost:7777`. The voice hotkey needs WSLg (Windows 11) — without it, use the
browser microphone instead. `bin/fsh.ps1` is a PowerShell wrapper that calls `fsh`
inside WSL2.

## Linux (Wayland)

Wayland compositors refuse global hotkey grabs by design, so `Ctrl+Shift+M` does
nothing there. Everything else works. Use the microphone button in the web UI, or
run the session under X11/Xwayland if you need the hotkey.

## Voice engine selection

Picked automatically at runtime, in this order:

| STT | TTS |
|---|---|
| 1. mlx-whisper (Apple Silicon) | 1. Kokoro (best quality) |
| 2. faster-whisper (everywhere else) | 2. edge-tts (online, many voices) |
| | 3. macOS `say` / Windows Speech API (fallback) |

The Whisper weights are downloaded from Hugging Face on first use (~141MB) and
cached on disk. Settings → Voice → "Downloaded models" lists what is cached and
lets you delete it.
