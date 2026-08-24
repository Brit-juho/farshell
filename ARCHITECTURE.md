# FarShell Architecture

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./ARCHITECTURE.ko.md)

> **Version:** v1.5.0 (2026-07-07) — see [CHANGELOG.md](./CHANGELOG.md) for the change history

This document is a map for contributors and LLMs to quickly understand the repo structure. Instead of switching to a monorepo, it only spells out the **logical boundaries**.

---

## 1. The 3-Plane Model

```
┌──────────────────────────────────────────────────────────────┐
│ Control Plane — start/stop/diagnostics (user-only actions)     │
│   bin/fsh, install.sh, ~/.vt.env                │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Work Plane — where the actual work happens                    │
│   tmux session (dev) ← Claude / aider / codex / shell / psql ...│
│   ↑ Voice Daemon injects keys via send-keys                    │
│   ↑ Mobile browser attaches over WebSocket                     │
└──────────────────────────────────────────────────────────────┘
                            ▲                       ▲
                            │                       │
┌───────────────────────────┴─────┐   ┌─────────────┴──────────┐
│ Voice Plane — STT/TTS          │   │ Network Plane           │
│   server/voice_handler.py      │   │   cloudflared tunnel   │
│   server/voice_daemon.py       │   │   token auth middleware│
│   server/local_mic.py          │   │   ntfy/Telegram push   │
│   frontend/voice.js            │   │                         │
└────────────────────────────────┘   └─────────────────────────┘
```

**Core idea**: the tmux session is the **single source of truth**. Desktop iTerm, the mobile PWA, and the Voice Daemon all attach to and operate on the same tmux session.

### 1.1 Single tmux server principle (Phase 6)

Every fsh client connects to the isolated tmux socket `-L fsh` (overridable via the `VT_TMUX_SOCKET` environment variable). It's kept separate from the user's own existing `tmux ls` sessions, and 4 clients share the same server:

| Client | Invocation form | Source |
|------------|-----------|------|
| `bin/fsh` (CLI) | `${TMUX_BASE[@]} ...` (`tmux -L fsh`) | Defined at the top of `bin/fsh` |
| `server/main.py` (PTY) | `tmux -L fsh attach-session ...` | `pty_manager` path |
| `server/voice_daemon.py` | `TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]` | Phase 6 #6-1 |
| Stop hook (`tts_hook.sh`) | (TTS only, no direct tmux call) | — |

Without a unified socket, Voice Daemon input ends up disconnected from mobile/web, leading to "why isn't this going in?" style debugging.

---

## 2. Responsibilities by directory

### `bin/` — CLI entry point (Control Plane)
| File | Responsibility |
|---|---|
| `fsh` | macOS/Linux. CLI — subcommand routing, process lifecycle management (server/tunnel/voice daemon), auto-opening iTerm, diagnostics |
| `fsh.ps1` | Windows PowerShell version |

Subcommands: `voice` · `mobile` · `start` · `stop` · `status` · `claude` · `handoff` · `ssh` · `doctor`

### `server/` — FastAPI backend (Work + Voice Plane)
| File | Responsibility | Key dependencies |
|---|---|---|
| `main.py` | FastAPI app, REST/WS routing, token middleware | pty_manager, session_store, output_watcher, voice_handler, notify, platform_utils |
| `pty_manager.py` | PTY fork, WebSocket broadcast, scrollback buffer | — |
| `session_store.py` | Session metadata (name, tmux_name), `new_session_id()` | secrets |
| `output_watcher.py` | Idle detection → TTS + push notification | voice_handler, notify |
| `voice_handler.py` | STT (mlx-whisper → faster-whisper) · TTS (Kokoro → edge-tts → say) | platform_utils |
| `voice_daemon.py` | macOS hotkey (Ctrl+Shift+V) → record → STT → tmux send-keys | pynput, sounddevice, whisper |
| `local_mic.py` | Desktop local microphone REST API | sounddevice |
| `notify.py` | ntfy/Telegram async push bridge | urllib, asyncio |
| `platform_utils.py` | OS detection, default shell, tmux path, local IP, TTS fallback | platform, shutil |
| `tts_hook.sh` | Claude Code Stop hook — TTS + ntfy on response completion | server/voice/output |
| `network_access.py` | `localhost`/`lan`/`tailscale`/`all` mode → CIDR whitelist, bind host determination | ipaddress |
| `tailscale.py` (D9) | Parses `tailscale status --json` — install/running/tailnet IP/MagicDNS hostname | subprocess |
| `tunnel.py` | Cloudflare Tunnel status detection (same pattern, Cloudflare version) | subprocess |
| `hooks/tmux_client_notify.sh` (D9) | tmux client-attached/detached hook → POST to `/api/notify/client-event` | curl, who |

### `frontend/` — xterm.js PWA
| File | Responsibility |
|---|---|
| `index.html` | UI layout, xterm.js multi-tab, tmux auto-attach, `#tmux=<name>` hash handling |
| `voice.js` | MediaRecorder recording, TTS playback, Media Session API, hands-free/voice-only mode |
| `manifest.json` | PWA settings (icons, add to home screen) |
| `sw.js` | Service Worker (offline caching) |

### Root
| File | Responsibility |
|---|---|
| `install.sh` | Creates Python venv, installs per-profile packages, symlinks fsh, initializes ~/.vt.env |
| `requirements-core.txt` | Terminal-only (~50MB) |
| `requirements-voice.txt` | Additional voice dependencies (~1.5GB) |
| `requirements.txt` | Combines both above (for backward compatibility) |

### `.claude/skills/` — Claude Code skills
| File | Trigger |
|---|---|
| `fsh/SKILL.md` | Global: "voice mode", "mobile access", etc. |
| `fsh-voice.md` | Manual Voice Daemon install/run |
| `fsh-mobile.md` | Mobile adb testing |
| `fsh-start.md` | Manual server startup |

---

## 3. Key data flows

### 3.1 Desktop voice input (Voice Daemon)
```
Ctrl+Shift+V (pynput)
  → sounddevice 16kHz mono recording
  → mlx-whisper / faster-whisper STT
  → tmux send-keys <active-pane> "<text>"
```

### 3.2 Mobile voice input (PWA)
```
🎤 button (voice.js)
  → MediaRecorder (webm/opus)
  → POST /voice/input?session_id=...
  → voice_handler.transcribe (includes ffmpeg conversion)
  → pty_mgr.write(session_id, text)  → PTY → tmux
```

### 3.3 Claude response completion → TTS + push
```
Claude Code Stop hook → server/tts_hook.sh
  ├─ Extract the last assistant response from the transcript
  ├─ POST /voice/output → edge-tts → afplay (local playback)
  └─ POST to ntfy (if VT_NOTIFY_URL is set) → phone push
```

### 3.4 Mobile ↔ desktop handoff
```
Desktop:  create tmux session 'dev' (bin/fsh)
  ↓ (registered with the tmux server on the same OS)
Desktop iTerm:  tmux attach -t dev
Mobile browser:  GET /?...#tmux=dev
  → frontend/index.html parses the hash
  → POST /api/tmux/attach {name:"dev"}
  → server: pty.fork() → exec "tmux attach -t dev"
  → screen relayed over WebSocket
```

**Point**: both sides are simply **different clients of the same tmux session**. Buffer, scrollback, and process are all shared.

### 3.5 Idle detection → push (OutputWatcher)
```
PTY output → output_watcher.feed_output()
  → accumulates in buffer
  → once idle_timeout(3s) is exceeded
  → generate summary → synthesize TTS
  → notify.send() (ntfy/Telegram in parallel)
```

---

## 4. Extension points

Where to make changes when adding a new feature.

### 4.1 Adding a new STT engine
- Insert into the priority list in `server/voice_handler.py`
- Follow the mlx-whisper → faster-whisper ordering as a reference

### 4.2 Adding a new TTS engine
- The fallback chain in the synthesize() function of `server/voice_handler.py`
- Support both paths: returning bytes or playing directly

### 4.3 Adding a new push notification channel (e.g. Discord, Slack)
- Add a `_send_xxx()` function to `server/notify.py`
- Include it in the parallel task list in `is_configured()` and `send()`
- Environment variable convention: `VT_XXX_TOKEN` / `VT_XXX_WEBHOOK`

### 4.4 Adding a new CLI subcommand
- Add a case to the main switch in `bin/fsh`
- Function naming convention: `cmd_<name>()`
- Add one line to the help section string

### 4.5 Adding a new AI agent (besides Claude)
- **No separate wrapper needed.** If the user just runs `aider` / `codex` / etc. inside tmux, both voice and mobile work automatically (this is the benefit of the general-purpose tmux injection design)
- If a completion notification similar to the Claude Code Stop hook is needed, write the tool's exit event handling in the `tts_hook.sh` style

### 4.6 Adding a new endpoint
- Add `@app.<method>("/api/...")` to `server/main.py`
- Token auth is handled automatically by the middleware (excluding whitelisted paths like `/sw.js`, `/manifest.json`)
- Restrict dangerous operations by session_id

### 4.7 Adding a new remote access path (D9: Tailscale + SSH example)
- In environments where remote desktop/browser access is blocked (e.g. corporate networks), since tmux is the "single source of truth,"
  access paths can be added **just by adding a new client type** — SSH is just a fifth client on par with
  web/voice, requiring no separate protocol implementation (just `tmux -L fsh attach`).
- To add a new CIDR range to the network policy, add a keyword/mode to `_expand_keyword()` +
  `network_mode_to_spec()` in `network_access.py` (Tailscale maps `tailscale` → CGNAT `100.64.0.0/10`).
- Status lookups for the range itself (installed/running/own IP) should follow the same pattern as
  `tunnel.py` (Cloudflare) and be split into an independent module (`server/tailscale.py`) — the convention is that
  `network_access.py` only makes CIDR decisions while a separate module handles status lookups.
- To surface connections from clients the server naturally can't see (e.g. plain SSH), catch the event with a tmux hook
  (`client-attached`/`client-detached`) and POST to an internal-only endpoint like `/api/notify/client-event`,
  reusing the existing `notify.py` bridge. Following the pattern where `bin/fsh` registers/unregisters
  the hook via an opt-in environment variable (`_maybe_register_client_hooks`) lets you add this
  without touching the default behavior.

---

## 5. Process map at runtime

```
$ fsh start
  ├─ uvicorn server.main:app  (port 7777)                [server]
  ├─ cloudflared tunnel --url ...                        [tunnel]
  ├─ python server/voice_daemon.py                       [voice daemon]
  └─ tmux server (new session: dev)                       [tmux]
      └─ zsh (or claude --resume)                      [work shell]
```

PIDs are stored in `/tmp/vt-pids/{server,tunnel,voice}.pid`. `fsh stop` cleans them all up.

---

## 6. Security model (current state)

| Layer | Mechanism | Limitation |
|---|---|---|
| Transport | cloudflared HTTPS tunnel | — |
| Transport (alternative) | Tailscale WireGuard VPN + IP whitelist (D9, `--network tailscale`) | Requires trusting Tailscale itself; tailnet ACLs must be managed separately |
| Auth | `VT_TOKEN` query/Bearer header | Plaintext token, exposed in QR code |
| WebSocket auth | Middleware validates before accept | — |
| Session ID | `secrets.token_urlsafe(12)` — 16 chars, ~96 bits | — |
| E2E | **None** (server sees plaintext) | TODO: D3 |
| Upload | Isolated to `/tmp/vt-uploads/` | No disk quota |
| Connection visibility | `VT_NOTIFY_CLIENT_EVENTS=1` → tmux client-attached/detached push (D9) | Off by default; `who`-based remote host extraction is best-effort |

Once **D3 (lightweight E2E)** is implemented, the plan is to encrypt the WebSocket payload itself with `libsodium SecretBox`, so that even if the cloudflared URL is exposed, plaintext code leakage is prevented.

---

## 7. Roadmap (summary)

Details in `/Users/neo/.claude/plans/adaptive-leaping-cray.md`. Improvements completed so far:
- ✅ D1 one-line install script
- ✅ D2 ntfy/Telegram push bridge
- ✅ D4 `fsh claude` / `fsh handoff` subcommands
- ✅ D5 extended session ID (`secrets.token_urlsafe(12)`)
- ✅ D6 this document
- ✅ D7 `fsh doctor` diagnostics
- ✅ D9 Tailscale + SSH remote access (`fsh ssh`, `--network tailscale`, client connection notifications)

Remaining work:
- ⏳ D3 tunnel payload E2E encryption
- ⏳ D8 barge-in + language detection
