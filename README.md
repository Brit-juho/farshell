# FarShell

[![Version](https://img.shields.io/badge/version-1.7.0-blue.svg)](./CHANGELOG.md)
[![Changelog](https://img.shields.io/badge/changelog-Keep%20a%20Changelog-orange.svg)](./CHANGELOG.md)
[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./README.ko.md)

**Your macOS/Linux terminal, from anywhere — full tmux sessions, voice input,
a mobile PWA, and Claude Code integration.
Free and open source, self-hosted on your own machine.**

A system that turns a macOS / Linux machine into a server so you can drive
your terminal from anywhere, by voice or by browser. (Windows works only
through WSL2 — no native support.)

- Access your terminal from mobile — scan a QR code and you're connected to tmux
- Code by voice — a hotkey (Ctrl+Shift+V) triggers voice input even while doing other work
- Check status remotely with a read-only code viewer/diff and a port dashboard
- Claude Code integration — TTS summaries when a task finishes, plus a prompt queue
- Entirely free — no API keys, no subscriptions. Open-source STT/TTS

```
https://github.com/Brit-juho/farshell
```

---

## Installation

```bash
# Terminal only (lightweight, ~50MB)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash

# Terminal + voice mode (~1.5GB, Whisper STT + edge-tts TTS)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash -s voice
```

Or clone and run locally:

```bash
git clone https://github.com/Brit-juho/farshell.git ~/farshell
cd ~/farshell
./install.sh            # terminal only
./install.sh voice      # with voice mode
```

What `install.sh` does:
1. Creates a Python `venv` (`.venv/`, no conda required)
2. Installs the packages for the profile you chose
3. Registers a `~/.local/bin/fsh` symlink (`vt` is also registered for backward compatibility)
4. Auto-generates the `~/.vt.env` config file
5. Updates PATH (zsh/bash)

> The Whisper model is downloaded automatically from Hugging Face on first run (~141MB).

For the integrated way to have a new terminal window drop straight into a
tmux session after install, and the full `fsh` command/option reference, see
[CLI.md](./CLI.md).

---

## Quick Start

### Voice coding while doing other work, like Notion (macOS)

```
1. fsh voice              run from any terminal
2. In the new window, pick your conversation with claude --resume
3. Switch back to your other work
4. Ctrl+Shift+V -> "git status" -> auto-typed into tmux
5. Hear the result via TTS in your earbuds
6. fsh stop                shut down when you're done
```

### Controlling your terminal from mobile

```
0. fsh password           (first time only) set up remote auth — fsh mobile refuses without it
1. fsh mobile             prints a URL + QR code
2. Scan the QR with your phone's camera
3. Auto-connects to a tmux session
4. Use voice input / hands-free / voice-only mode / file upload
```

For requirements per remote-access method, and how to connect via
Tailscale + SSH in environments where screen sharing is blocked (e.g.
corporate networks), see [the Tailscale section of CLI.md](./CLI.md#tailscale--ssh-remote-access).

---

## Security

The default is **no authentication** — set a password with `fsh password`
before exposing this remotely. `fsh mobile` refuses to run if you try to
open a public tunnel (`--network all`, the default) without auth configured.

| Layer | Method |
|------|------|
| Login | Password (scrypt hash) or a machine token (`VT_AUTH_TOKEN`). Sessions use an HMAC-signed cookie (24h) |
| New device registration | Per-device trust via a 90-day long-lived cookie. Turning on OTP (`fsh otp setup`) requires a 6-digit code only for new devices |
| Device revocation | `fsh device revoke <id>` — immediately invalidates that device's sessions too |
| Cross-site blocking | Both HTTP and WS return 403 if the Origin isn't itself. No CORS wildcard |
| Code viewer | Read-only; a deny list (`.env*`/`*.pem`/`.ssh/`, etc.) applies equally to file viewing and `git diff` |
| E2E encryption | `--e2e` flag — X25519 session key exchange + NaCl SecretBox, with the session key signed by a long-lived Ed25519 identity key for TOFU (trust-on-first-use) defense against active man-in-the-middle attacks |

---

## Key Features

| Feature | Description |
|------|------|
| Voice Daemon | STT via macOS hotkey (Ctrl+Shift+V) or earbud Play/Pause -> typed directly into tmux |
| Clipboard sync | OSC52 (copies inside the terminal) + a polling daemon (`fsh clip`, copies outside the terminal) -> pushed to the web clipboard |
| Hands-free / voice-only mode | Continuous recording that auto-repeats, or hide the terminal and show just a large mic (for earbud-only control) |
| Barge-in | Tap the mic or hit the hotkey to instantly stop TTS playback |
| Claude Code TTS | A Stop hook auto-plays a TTS summary when a response finishes |
| Prompt queue | Queue up instructions while a task is running and feed them in sequentially — pairs with voice mode (`fsh queue`, [CLI.md](./CLI.md#prompt-queue-fsh-queue)) |
| Live preview grid view | A screen that shows tmux sessions as cards at a glance, with agent badges and working/done indicators |
| Code viewer / diff | File tree, syntax highlighting, `git diff` rendering (read-only) |
| Port dashboard | View listening ports, kill with one click, integrates with `fsh tunnel expose` |
| Web Push | Task-complete notifications even when the app is closed (a named tunnel is recommended — quick tunnels can change URL and break subscriptions) |
| tmux session management | Create/attach/detach/kill from the web. Simultaneous access from desktop is fine |
| Scrollback buffer | Restores prior output on WebSocket reconnect (up to 5000 chunks) |
| Terminal search | Ctrl+F / Cmd+F -> xterm.js search addon |
| Session renaming | Double-click a tab to rename it (also renames the tmux session) |
| File upload/download | Upload from the voice bar, download via `/api/download` |
| Media Session | Toggle recording with wireless earbud Play/Pause (iOS/Android) |
| PWA | manifest + Service Worker -> add to home screen and use it like an app |
| Tailscale remote access | `fsh ssh` / `fsh mobile --network tailscale` — connect straight to tmux over SSH in environments where screen sharing is blocked (e.g. corporate networks) |
| Client connection notifications | `VT_NOTIFY_CLIENT_EVENTS=1` — push notifications for attach/detach from clients the server can't otherwise see, like SSH |
| Automatic tunnel zombie-reconnect recovery | Detects and auto-restarts when cloudflared's process is alive but unresponsive |

For the full fsh CLI command list see [CLI.md](./CLI.md), and for the REST/WebSocket API see [API.md](./API.md).

---

## Configuration (`~/.vt.env`)

Auto-generated by `install.sh`. Add only the settings you need. See
`config/vt.defaults.env` (the committed defaults) for the full key list.

```bash
# Basics
VT_PORT=7777                                 # server port (default)
VT_PYTHON=~/farshell/.venv/bin/python  # Python path (auto-detected)

# Remote auth (strongly recommended when using a public tunnel)
# VT_AUTH_TOKEN=my-secret-token              # machine token. Use `fsh password` for human login

# Security
VT_E2E=1                                     # force E2E on all WebSockets (default: opt-in)
VT_SAFE_MODE=1                               # block dangerous commands up front (rm -rf /, sudo, etc.)
VT_TMUX_SOCKET=vt                            # tmux isolated socket name (default: vt, unrelated to the CLI name)
VT_NETWORK_MODE=all                          # localhost | lan | tailscale | all

# Voice
VT_STT_LANG=ko                               # pin the STT language (auto-detected if unset)
```

---

## Claude Code Integration

### Skills

Project-specific skills registered under `.claude/skills/`, plus the global
skill (`/vt`) in `~/.claude/skills/vt/`, let you control fsh with natural
language like "voice mode" or "mobile access." See `CLAUDE.md` for the full list.

### Hooks

| Hook | File | Behavior |
|---|---|---|
| Pre/Post/StopToolUse | `server/agent_hook.sh` | Reflects tool-use start/end/completion live in the mobile UI |
| Stop | `server/tts_hook.sh` | Plays a TTS summary of the last message when a response finishes (falls back to macOS `say` if the server isn't running) |

Register in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse":  [{ "command": "<repo>/server/agent_hook.sh pre"  }],
    "PostToolUse": [{ "command": "<repo>/server/agent_hook.sh post" }],
    "Stop":        [{ "command": "<repo>/server/tts_hook.sh" }]
  }
}
```

---

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for details (the control / work / voice /
network 4-plane model). For the design system (theme, tokens, layout), see
[DESIGN.md](./DESIGN.md).

```
                  +----------------------------------------+
                  |  MacBook / WSL2 (server)                |
                  |                                          |
  [fsh voice]      |  +----------------+  +----------------+  |
  Ctrl+Shift+V -->|  | Voice Daemon   |  | FastAPI :7777  |  |
  -> STT -> tmux  |  | (standalone,   |  | auth/queue/push |  |
                  |  |  server-independent) |  etc.        |  |
                  |  +----------------+  +-------+--------+  |
                  |                              |           |
                  |  +----------------+  +-------+--------+  |
                  |  | tmux sessions  |<-+ PTY Manager    |  |
                  |  | (shared by     |  | + Scrollback   |  |
                  |  |  desktop/phone)|  |                |  |
                  |  +----------------+  +-------+--------+  |
                  |                              |           |
                  |        +---------------------+--------+  |
                  |        | Push -> Web Push / ntfy /     |  |
                  |        | Telegram (idle / done detect)  |  |
                  |        +--------------------------------+  |
                  +----------------+-------------------------+
                                   | Cloudflare Tunnel (HTTPS + opt-in E2E)
                  +----------------+-------------------------+
                  |  Mobile / remote browser                  |
                  |  xterm.js + code viewer + Grid view        |
                  |  STT -> server -> tmux                    |
                  |  E2E: X25519 + Ed25519 signature (TOFU)   |
                  +------------------------------------------+
```

### STT / TTS Engine Priority

| STT | TTS |
|-----|-----|
| 1. mlx-whisper (optimized for Apple Silicon) | 1. Kokoro (highest quality) |
| 2. faster-whisper (general purpose) | 2. edge-tts (online, many voices) |
| | 3. macOS `say` / Windows Speech API (fallback) |

### Project Structure

```
farshell/
├── bin/
│   ├── fsh                   CLI entry point (bash, macOS/Linux; vt is a backward-compat symlink)
│   └── fsh.ps1               CLI entry point (PowerShell, Windows/WSL2 wrapper; vt.ps1 is backward-compat)
├── server/
│   ├── main.py                FastAPI app, middleware (auth/Origin guard)
│   ├── auth.py                password/session/device/OTP/ticket auth
│   ├── fsguard.py             code viewer path validation (root confinement + deny list)
│   ├── crypto_channel.py      E2E: X25519 session key + Ed25519 long-lived identity key signing
│   ├── pty_manager.py         PTY sessions (broadcast, scrollback, EOF detection)
│   ├── queue_store.py / queue_runner.py   prompt queue storage/auto-dispatch
│   ├── push.py                Web Push subscription management + sending
│   ├── portscan.py            port dashboard (lsof/ps scan, kill/expose guards)
│   ├── voice_handler.py       STT (faster-whisper) + TTS (edge-tts/Kokoro)
│   ├── voice_daemon.py        hotkey voice daemon (runs standalone)
│   ├── clipboard_daemon.py    macOS clipboard polling daemon
│   ├── tunnel_watchdog.py     cloudflared zombie-reconnect watchdog
│   ├── routes/                endpoint modules (pty/tmux/files/push/queue/ports/...)
│   └── tests/                 pytest suite
├── frontend/
│   ├── index.html             xterm.js UI (tabs, search, code viewer, Grid view)
│   ├── js/                    theme.js, terminal.js, grid.js, viewer.js, etc.
│   ├── voice.js                mic + TTS + hands-free + Media Session
│   └── tests/                 node --test unit tests
├── install.sh                 one-line install script
├── requirements-core.txt      FastAPI, uvicorn, etc.
├── requirements-voice.txt     faster-whisper, edge-tts, sounddevice, etc.
├── CLAUDE.md                  Claude Code guide (the full ledger of features/commands/API)
├── CLI.md                     full fsh CLI reference
├── API.md                     full REST/WebSocket API reference
├── DESIGN.md                  design system (theme/tokens/layout)
├── ARCHITECTURE.md            4-plane architecture in detail
├── CHANGELOG.md                full change history
└── docs/TODOS.md               follow-up backlog (local only, gitignored)
```

---

## Supported Platforms

| Platform | Server | Voice Daemon | TUI (`fsh manage`) | Browser access |
|--------|------|-------------|-------|-------------|
| macOS (iTerm2/Ghostty/Warp, etc.) | Supported | Hotkey + earbud | Supported | Supported |
| Linux (X11) | Supported | Global hotkey | Supported | Supported |
| Linux (Wayland) | Supported | Hotkey blocked by security policy — mobile mic recommended | Supported | Supported |
| Windows (WSL2, behaves as Linux) | Supported | Requires WSLg | Supported | Supported |
| Windows native | Not supported | Not supported | Not supported | — |
| iOS (Safari/Chrome) | — | — | — | Media Session supported |
| Android (Chrome) | — | — | — | Supported |

### Windows (WSL2)

Windows native isn't supported — use WSL2 for a Linux environment instead.

```powershell
wsl
./install.sh voice
fsh voice
```

The server and tmux run inside WSL2, and the browser on Windows connects via
`localhost:7777`. The voice hotkey requires WSLg (Windows 11) — without it,
use the browser mic instead. `bin/fsh.ps1` is a PowerShell wrapper that calls
fsh inside WSL2 (`vt.ps1` also still works for backward compatibility).

---

## Troubleshooting

Start with `fsh doctor` to auto-diagnose your install/environment. Common
issues and fixes are collected in
[docs/help/troubleshoot.md](./docs/help/troubleshoot.md) (same content as `fsh help troubleshoot`).

---

## Version / Changelog

Current version: **v1.7.0** (2026-08-04)

> Development continues past v1.7.0 — auth hardening, the prompt queue, Web
> Push, the port dashboard, and the code viewer/diff panel are still
> pre-tag (`[Unreleased]`) and tracked at the top of
> [CHANGELOG.md](./CHANGELOG.md).

See [CHANGELOG.md](./CHANGELOG.md) for the full change history.

| Version | Date | Highlights |
|------|------|-----------|
| [v1.7.0](https://github.com/Brit-juho/farshell/releases/tag/v1.7.0) | 2026-08-04 | Security hardening: device whitelist + OTP gate · one-time registration ticket · access-log credential masking · OriginGuardMiddleware · removed CORS wildcard · fixed session cookie Secure flag |
| [v1.6.0](https://github.com/Brit-juho/farshell/releases/tag/v1.6.0) | 2026-07-12 | Web login password (`fsh password`): scrypt hash + HMAC-signed session cookie, coexists with machine tokens |
| [v1.5.0](https://github.com/Brit-juho/farshell/releases/tag/v1.5.0) | 2026-07-07 | Tailscale + SSH remote access: `fsh ssh` · `fsh mobile --network tailscale` · client connection notifications |
| [v1.4.0](https://github.com/Brit-juho/farshell/releases/tag/v1.4.0) | 2026-05-09 | UX overhaul + first-class Linux parity: `fsh manage` TUI · `fsh attach` · `fsh voice-target` · `fsh hotkey` |
| [v1.3.0](https://github.com/Brit-juho/farshell/releases/tag/v1.3.0) | 2026-05-08 | Stability/network efficiency: `/ws-preview` push · cookie auth · self-hosted vendor assets · WS heartbeat |
| [v1.2.0](https://github.com/Brit-juho/farshell/releases/tag/v1.2.0) | 2026-05-07 | Live preview · `--network` modes · Cloudflare named tunnels · WS backpressure |
| [v1.1.0](https://github.com/Brit-juho/farshell/releases/tag/v1.1.0) | 2026-05-06 | ralph->fsh rename, isolated socket · AI awareness · command expansion · hooks · safe mode · cross-platform unification |
| [v1.0.0](https://github.com/Brit-juho/farshell/releases/tag/v1.0.0) | 2026-04-14 | Initial stable release (PWA · Voice Daemon · STT/TTS · tunnel · `fsh` CLI · `install.sh`) |

---

## License

MIT
