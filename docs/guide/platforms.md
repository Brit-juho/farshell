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

Enter the Linux shell with `wsl` from PowerShell, then run from the FarShell repository root:

```bash
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
run the session under X11 if you need the hotkey.

## Voice engine selection

Speech recognition (STT) runs **on the machine that runs FarShell** — phones and other PCs only record and
upload audio. Pick the model in Settings → Voice & notifications; it shows which models this server can run and
marks one "Recommended".

| Server | Models |
|---|---|
| Apple Silicon Mac | Qwen3-ASR 0.6B / 1.7B (MLX), Qwen3-ASR (llama.cpp), Apple speech recognition (macOS 26+) |
| Intel Mac · Linux · WSL2 | Qwen3-ASR (llama.cpp, CPU) |

Nothing is downloaded automatically: the engine program (llama.cpp) and the model files are fetched only after
you confirm in Settings. TTS: edge-tts (online) → macOS `say` / Linux `espeak-ng`.
