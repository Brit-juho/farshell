# FarShell Architecture

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./ARCHITECTURE.ko.md)

> **Version:** v2.0.0 — the frontend was substantially restructured in September 2026
> (see [`docs/plan-2.0/`](./docs/plan-2.0/) locally, gitignored — not on GitHub).
> See [CHANGELOG.md](./CHANGELOG.md) for the release history and [API.md](./API.md)
> for the full REST/WebSocket reference (this document intentionally does not
> duplicate the endpoint table — that caused drift before).

This document is a map for contributors and LLMs to quickly understand the repo
structure. Instead of switching to a monorepo, it only spells out the
**logical boundaries**.

---

## 1. The 3-Plane Model

```
┌──────────────────────────────────────────────────────────────┐
│ Control Plane — start/stop/diagnostics (user-only actions)     │
│   bin/fsh, install.sh, ~/.vt.env, server/tui/ ("fsh manage")   │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Work Plane — where the actual work happens                    │
│   tmux session (dev) ← Claude / aider / codex / shell / psql ...│
│   ↑ Voice Daemon injects keys via send-keys                    │
│   ↑ Mobile/desktop browser attaches over WebSocket (E2E-capable)│
└──────────────────────────────────────────────────────────────┘
                            ▲                       ▲
                            │                       │
┌───────────────────────────┴─────┐   ┌─────────────┴──────────┐
│ Voice Plane — STT/TTS          │   │ Network Plane           │
│   server/voice_handler.py      │   │   cloudflared tunnel   │
│   server/voice/ (daemon pkg)   │   │   Tailscale (D9)       │
│   server/local_mic.py          │   │   token/password auth  │
│   frontend/js/voice/ (own bundle)│  │   ntfy/Telegram push   │
└────────────────────────────────┘   └─────────────────────────┘
```

**Core idea**: the tmux session is the **single source of truth**. Desktop
iTerm, the mobile PWA, and the Voice Daemon all attach to and operate on the
same tmux session.

### 1.1 Single tmux server principle (Phase 6)

Every fsh client connects to the isolated tmux socket `-L fsh` (overridable
via the `VT_TMUX_SOCKET` environment variable). It's kept separate from the
user's own existing `tmux ls` sessions.

| Client | Invocation form | Source |
|------------|-----------|------|
| `bin/fsh` (CLI) | `${TMUX_BASE[@]} ...` (`tmux -L fsh`) | Defined at the top of `bin/fsh` |
| `server/main.py` (PTY) | `tmux -L fsh attach-session ...` | `pty_manager.py` |
| `server/voice/daemon.py` | `TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]` | Phase 6 #6-1 |
| `server/tui/` (`fsh manage`) | tmux calls via `tmux_runner.py`/`helpers.py` | W4-1 |
| Stop hook (`tts_hook.sh`) | (TTS only, no direct tmux call) | — |

Without a unified socket, Voice Daemon input ends up disconnected from
mobile/web, leading to "why isn't this going in?" style debugging.

---

## 2. Responsibilities by directory

### `bin/` — CLI entry point (Control Plane)
| File | Responsibility |
|---|---|
| `fsh` | macOS/Linux. CLI — subcommand routing, process lifecycle management (server/tunnel/voice daemon), auto-opening iTerm, diagnostics |
| `fsh.ps1` | Windows PowerShell version (native Windows itself isn't supported — this is for WSL2 users driving fsh from PowerShell) |

Run `fsh help` (or see [CLI.md](./CLI.md)) for the full subcommand list —
it's grown well past the original `voice`/`mobile`/`start`/`stop` set (queue,
snippets, hotkeys, device management, template management, tunnel management,
`fsh manage` TUI, etc.).

### `server/` — FastAPI backend (Work + Voice Plane)

`main.py` is the FastAPI entry point; it wires middleware/auth and mounts 11
routers from `server/routes/` (below), which own both the REST endpoints and
the WebSocket endpoints (`/ws/{id}` and `/ws-notify` in `routes/pty.py`,
`/ws-preview/{tmux_name}` in `routes/tmux.py`, `/ws-agent` in
`routes/agents.py`, `/ws-workspace` in `routes/system.py`).

**Session / PTY / tmux**
| File | Responsibility |
|---|---|
| `pty_manager.py` | PTY fork, WebSocket broadcast, scrollback buffer (the core tmux-alternative module) |
| `session_store.py` | Session metadata (name, tmux_name), `secrets.token_urlsafe(12)` id generation |
| `tmux_runner.py` | Shared tmux command execution helper |
| `tmux_target.py` | Single source of truth for "which tmux pane" resolution + text injection (shared by voice, queue, hotkeys) |
| `preview.py` | Read-only tmux pane live preview (WS push, doesn't count as a tmux client) |
| `workspace.py` | Per-device workspace sync (tab order/active session/UI settings) → `~/.config/vt/workspace.json` |

**Auth / safety**
| File | Responsibility |
|---|---|
| `auth.py` | Web login (scrypt password hash + HMAC session cookie), device whitelist + TOTP gate, one-time registration ticket |
| `crypto_channel.py` | E2E WebSocket encryption (X25519 ephemeral keys + Ed25519 identity signing + NaCl SecretBox) — server side of the handshake whose client side is `frontend/js/term/e2e.js` |
| `fsguard.py` | Single source of truth for the code-viewer's file-browse root confinement, path resolution, and denylist |
| `safe_mode.py` | Pre-blocks a configurable list of dangerous shell commands |
| `network_access.py` | `localhost`/`lan`/`tailscale`/`all` network mode → CIDR whitelist, bind host determination |

**Voice**
| File | Responsibility |
|---|---|
| `voice_handler.py` | STT (mlx-whisper → faster-whisper) · TTS (Kokoro → edge-tts → say) |
| `output_watcher.py` | Idle detection on PTY output → TTS + push notification |
| `local_mic.py` | Desktop local-microphone REST API (voice profile only; imported unconditionally so a core-profile install degrades gracefully) |
| `voice_daemon.py` | Thin shim — real implementation lives in the `server/voice/` package below |
| `server/voice/` (package) | `daemon.py` (hotkey + media-key main loop) · `config.py` (`~/.vt.env` parsing) · `media_keys.py` (macOS earbud Play/Pause) · `recorder.py` (record → STT → tmux inject) · `stt.py` (mlx-whisper primary, faster-whisper fallback) · `tmux_target.py` (compat shim over the top-level module, avoids importing `pynput` just for target resolution) |

**Agent status**
| File | Responsibility |
|---|---|
| `agent_detector.py` | Detects which AI CLI (Claude Code, etc.) is running in a pane |
| `agent_status.py` | **Agent state machine** (`idle`/`working`/`waiting`/`done`, `error` reserved). `post` never changes state (another tool may follow); `stop` keeps the entry and sets `done` so it survives a refresh. TTL sweeping (working 15m / waiting 2m / done 30m) is lazy — it runs on reads and writes rather than a background task, so the same rules hold where there is no event loop |
| `pane_resolve.py` | Three-tier answer to "which tmux session is this hook event from?": self-reported `$TMUX_PANE` → cwd (only when unique) → give up. Validates `$TMUX`'s socket first, because pane ids are per-socket and a personal tmux's `%12` can collide with ours |
| `agent_prompt_detect.py` | Approval-prompt (`waiting`) detection off the PTY stream. Same sliding-window shape as `auto_responder`, but writes nothing — it only reports state. Patterns live in `server/detect/*.toml` so a CLI wording change doesn't need a code edit |
| `claude_hooks.py` | Idempotent registrar for `~/.claude/settings.json` (`fsh hooks install`). Preserves hooks you added yourself, updates our entries in place when the repo moves, backs up before writing |
| `usage/` | Usage provider abstraction (`base`/`clauth`/`null` + factory). Reads only `~/.clauth/status.json`, through a **field whitelist** — unknown fields are dropped rather than passed through, so a future clauth field can't leak over a public tunnel |
| `auto_responder.py` | Opt-in auto-responder for trust prompts |

**Network / tunnel**
| File | Responsibility |
|---|---|
| `tunnel.py` | Cloudflare Tunnel status detection |
| `tunnel_watchdog.py` | Detects cloudflared's zombie-reconnect state (process alive, QUIC control stream dead) → auto-restarts the tunnel |
| `tunnel_registry.py` | Registry of extra-port tunnels opened via `fsh tunnel expose` |
| `tailscale.py` | Tailscale status detection (`tailscale status --json`) — same pattern as `tunnel.py` |
| `clipboard_daemon.py` | Polls macOS `NSPasteboard.changeCount` → pushes changes to the web clipboard |

**Ports / queue / snippets**
| File | Responsibility |
|---|---|
| `portscan.py` | Listening-port scan + safe kill logic for the ports dashboard |
| `queue_runner.py` | Drains the prompt queue into a tmux pane, sequentially, gated by grace period/safe-mode/pane-liveness |
| `queue_store.py` | Prompt queue storage (`~/.vt/queue.json`) |
| `snippet_store.py` | Prompt snippet library storage |

**Push / notify**
| File | Responsibility |
|---|---|
| `notify.py` | ntfy.sh/Telegram async push bridge |
| `push.py` | Web Push (VAPID) subscription management |

**Misc / utility**
| File | Responsibility |
|---|---|
| `platform_utils.py` | macOS/Linux/WSL2 cross-platform utilities (default shell, tmux path, local IP, TTS fallback) |
| `vt_env.py` | `~/.vt.env` parser, shared by `server/voice/config.py` and `clipboard_daemon.py` |
| `ttl_cache.py` | Generic TTL cache utility |
| `deps.py` | Shared singleton instances (a light DI substitute) |
| `tts_hook.sh` | Claude Code Stop hook — TTS + ntfy on response completion |

**`server/tui/` — the `fsh manage` TUI (Textual)**
| File | Responsibility |
|---|---|
| `app.py` | Main Textual `App` — sessions/target/hotkeys/status screens |
| `helpers.py` | Server HTTP calls, tmux calls, state-file I/O shared by the screens |
| `modals.py` | Rename + confirm modal dialogs |
| `server/tui_manager.py` | Thin shim entry point, mirrors `voice_daemon.py`'s relationship to `server/voice/` |

**`server/routes/` — REST/WS route modules mounted by `main.py`**
| File | Mounts |
|---|---|
| `pty.py` | `/api/sessions` CRUD, `/api/upload`, `/api/download` — PTY session lifecycle + file transfer |
| `tmux.py` | `/api/tmux/*` — session CRUD + live preview + open-on-mac |
| `voice.py` | `/voice/*` — STT/TTS I/O, cancel, status/preload |
| `agents.py` | `/api/agents*`, `/api/agent/event`, `/api/agent/status` — agent detection + hook state |
| `files.py` | `/api/fs/*`, `/api/git/*` — read-only file browser + git status/diff/stage/commit (P2, D16) |
| `ports.py` | `/api/ports*` — port dashboard (blocking calls offloaded via `asyncio.to_thread`) |
| `queue.py` | `/api/queue*` — prompt queue |
| `snippets.py` | `/api/snippets*` — prompt snippet library |
| `push.py` | `/api/push/*` — Web Push subscription |
| `clipboard.py` | `/api/clipboard/push` — clipboard daemon → `/ws-notify` broadcast |
| `system.py` | `/api/capabilities`, `/api/tunnel/status`, `/api/tailscale/status`, `/api/safe-mode`, `/api/notify/client-event` — status/diagnostics |

**`server/hooks/`**
| File | Responsibility |
|---|---|
| `tmux_client_notify.sh` | tmux client-attached/detached hook (D9) → POSTs to `/api/notify/client-event` |

### `frontend/` — xterm.js PWA, built by Vite

Since the September 2026 restructure, **every frontend script is a real ES
module** (`import`/`export`), bundled by Vite in library mode. There are two
build outputs, both with fixed (non-hashed) filenames so `sw.js`'s offline
precache stays valid:

- `frontend/dist/app.js` + `frontend/dist/app.css` — the main entry
  (`frontend/js/main.js`), which statically imports essentially everything
  below.
- `frontend/dist/voice.js` — `frontend/js/voice/` built as a **separate,
  fully independent** Vite entry. `frontend/js/agent/status.js` lazy-loads it
  with a `<script type="module">` tag only after `/api/capabilities` reports
  voice is installed — so a terminal-only user pays zero bytes for it. It's
  independent specifically because a *shared* dynamic-import chunk between
  the two entries would get a content-hashed filename from Rollup, breaking
  the fixed-filename guarantee; see `vite.config.js` for the full story
  (including a real bug this caused and fixed: importing `core/store.js`
  from the voice bundle silently duplicated its module state and corrupted
  `window.sessions` after voice.js loaded).

| Directory | Owns |
|---|---|
| `js/core/` | `env.js` (API_BASE/token/cookie exchange), `api.js` (`apiFetch`/`vtFetch`/`vtEsc`), `store.js` (session state), `dom.js` (the `data-action` click-delegation registry that replaced 29 inline `onclick` attributes), `settings.js` (**settings store — the server's `/api/workspace.settings` is the source of truth, localStorage is only a pre-render cache**), `keymap.js` (keybinding registry with `passthrough`, so a shell key like `Mod+F` can be handed back to the terminal) |
| `js/term/` | Everything that used to be the 2000-line `terminal.js`: `e2e.js` (client side of the E2E handshake), `session.js` (`addSession`/`switchTo`/tab lifecycle — the riskiest split, kept as one orchestrator over `tab-dom.js`/`xterm-setup.js`/`ws.js` "assembly parts" rather than force-split), `clipboard.js`, `touch.js`, `links.js`, `selection.js`, `resize.js`, `workspace.js` (tab order persistence), `conn-overlay.js`, `keybar.js`, `tmux-panel.js`, `guide.js`, `boot.js` (`bootApp()`) |
| `js/agent/` | What used to be `grid.js`, plus the 2.0 state machine's client half: `badges.js` (which icon), `status.js` (`/ws-agent`, capability gating), `preview.js` (session preview cards), `state.js` (**the single client-side source of the four states**, keyed by tmux session name — a web session id changes on every re-attach), `paint.js` (**the single place that paints them**: tabs, pane headers, favicon, app badge) |
| `js/panels/` | `panel.js` (shared modal-panel shell for code viewer/ports/queue/snippets) + `viewer/` (what used to be `viewer.js`: `state.js`, `shell.js`, `tree.js`, `file.js`, `diff.js`, `git.js` — `shell.js`↔`tree.js` and `shell.js`↔`git.js` have intentional circular imports, same reasoning as `picker.js`↔`term/session.js` below) |
| `js/voice/` | What used to be the top-level `voice.js`: `recording.js`, `tts.js`, `notify.js`, `media-session.js`, `index.js` (the entry point). Built as its own bundle (see above) |
| `js/ui/` | `toast.js` (unified toast — still window-bridged since `voice.js`'s separate bundle needs it), `favicon.js` (dynamic tab-badge canvas, UMD-style), `moreMenu.js` |
| `js/push/` | `swreg.js` — Service Worker registration for Web Push |
| `js/lib/` | `ansilex.js`, `difflex.js`, `keyseq.js` — pure logic, UMD-wrapped so both the browser (`window.VTAnsiLex`) and the Node test suite (`require(...)`) can use them |
| `js/layout/` | The 2.0 shell. `store.js`/`tree.js` (split-pane tree — pure functions + the single place that holds it), `panes.js` (recursive renderer; moves existing session wrappers instead of recreating them), `dnd.js` (5-zone drop targets), `resizer.js`, `compact.js` (<720px + coarse pointer render mode), `pane-picker.js`, `rail.js` (left rail), `right-rail.js` (usage rail, ≥1024px), `clients.js` ("Connected screens"), `persist.js` (layout persistence — leaves store `{id, tmux}` so a tmux session survives a new PTY id), `breakpoints.js` |
| `js/theme.js`, `search.js`, `picker.js`, `ports.js`, `queue.js`, `snippets.js`, `quickopen.js`, `pushui.js`, `gate.js`, `main.js` | Top-level feature modules and the app entry point. `gate.js` is the one deliberately-classic (non-module) script — it runs the login gate before any ES module (which is deferred) could paint |
| `sw.js` | Service Worker — offline caching, precache list |
| `manifest.json` | PWA manifest |

### Root
| File | Responsibility |
|---|---|
| `vite.config.js` | Frontend build config — library mode, two independent entries (see above), no hashing, minify off |
| `styles/` | `main.css` (Tailwind entry) + `layers/legacy.css` (the pre-Tailwind hand-written CSS, kept unlayered so it always wins the cascade) |
| `install.sh` | Creates Python venv, installs per-profile packages, symlinks fsh, initializes ~/.vt.env, runs `npm ci && npm run build` |
| `requirements-core.txt` | Terminal-only (~50MB) |
| `requirements-voice.txt` | Additional voice dependencies (~1.5GB) |
| `.github/workflows/ci.yml` | CI — `npm test`/`pytest` on PR/push, frontend build-artifact shape verification (no hashed filenames, size cap) |

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
Ctrl+Shift+V (pynput, server/voice/daemon.py)
  → sounddevice 16kHz mono recording (recorder.py)
  → mlx-whisper / faster-whisper STT (stt.py)
  → tmux_target.py resolves the pane → tmux send-keys "<text>"
```

### 3.2 Mobile/desktop voice input (PWA)
```
🎤 button (frontend/js/voice/recording.js, separate bundle)
  → MediaRecorder (webm/opus)
  → apiFetch POST /voice/input?session_id=...
  → server/routes/voice.py → voice_handler.transcribe (ffmpeg conversion included)
  → pty_manager.write(session_id, text) → PTY → tmux
```

### 3.3 Claude response completion → TTS + push
```
Claude Code Stop hook → server/tts_hook.sh
  ├─ Extract the last assistant response from the transcript
  ├─ POST /voice/output → edge-tts → afplay (local playback)
  └─ POST to ntfy (if configured) → phone push
```

### 3.4 Mobile ↔ desktop handoff
```
Desktop:  create tmux session 'dev' (bin/fsh)
  ↓ (registered with the tmux server on the same OS)
Desktop iTerm:  tmux attach -t dev
Mobile browser:  GET /?...#tmux=dev
  → frontend/js/term/boot.js parses the hash
  → apiFetch POST /api/tmux/attach {name:"dev"}
  → server: pty.fork() → exec "tmux attach -t dev"
  → screen relayed over WebSocket (E2E-encrypted if ?e2e=1)
```

**Point**: both sides are simply **different clients of the same tmux
session**. Buffer, scrollback, and process are all shared.

### 3.5 Idle detection → push (OutputWatcher)
```
PTY output → output_watcher.feed_output()
  → accumulates in buffer
  → once idle_timeout(3s) is exceeded
  → generate summary → synthesize TTS
  → notify.send() (ntfy/Telegram in parallel)
```

### 3.6 E2E-encrypted WebSocket handshake (D3)
```
Client (?e2e=1): frontend/js/term/e2e.js wrapE2E()
  ← server/crypto_channel.py sends e2e-hello: ephemeral pubkey signed by a
    stable Ed25519 identity key
  → client verifies the signature, TOFU-pins the identity key per hostname
    (localStorage), warns + requires explicit re-trust if it ever changes
  → both sides derive a shared key (X25519) → NaCl SecretBox for every frame
```
This defends against passive eavesdropping *and* active MITM on the
cloudflared/Tailscale path, at the cost of the client having to trust the
identity key on first use — see §6.

### 3.7 Live preview grid → agent status
```
frontend/js/agent/preview.js opens /ws-preview/{tmux_name} per visible card
  (server/preview.py — read-only, does not count as a real tmux client)
frontend/js/agent/status.js opens /ws-agent once
  (server/agent_status.py — Claude Code Pre/Post/Stop hooks feed this via
   /api/agent/event, matched to a tmux pane by cwd since hooks don't report
   the pane directly — see CLAUDE.md's caveat on this)
```

---

## 4. Extension points

Where to make changes when adding a new feature.

### 4.1 Adding a new STT engine
- Insert into the priority list in `server/voice_handler.py` (or `server/voice/stt.py` for the daemon path)
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
- Add one line to the help section string (and to [CLI.md](./CLI.md))

### 4.5 Adding a new AI agent (besides Claude)
- **No separate wrapper needed** for the terminal itself — `fsh agent <name>`
  already generalizes this, and if the user just runs `aider` / `codex` /
  etc. inside tmux, voice and mobile work automatically (the benefit of the
  general-purpose tmux injection design).
- For agent-status detection (badges, working/done state), extend
  `server/agent_detector.py`'s manifest-style rules.
- If a completion notification similar to the Claude Code Stop hook is
  needed, write the tool's exit-event handling in the `tts_hook.sh` style.

### 4.6 Adding a new endpoint
- Add a route to the appropriate module under `server/routes/` (or a new
  module + `app.include_router(...)` in `main.py` if it's a genuinely new
  category)
- Token/password auth is handled automatically by middleware (excluding
  whitelisted paths like `/sw.js`, `/manifest.json`)
- Restrict dangerous operations by session_id; update [API.md](./API.md)
- If the frontend needs it, add a `vtFetch`/`apiFetch` call in the relevant
  ES module — there's no more central "API client" file to edit, each
  feature module owns its own calls

### 4.7 Adding a new remote access path (D9: Tailscale + SSH example)
- In environments where remote desktop/browser access is blocked (e.g.
  corporate networks), since tmux is the "single source of truth," access
  paths can be added **just by adding a new client type** — SSH is just a
  fifth client on par with web/voice, requiring no separate protocol
  implementation (just `tmux -L fsh attach`).
- To add a new CIDR range to the network policy, add a keyword/mode to
  `_expand_keyword()` + `network_mode_to_spec()` in `network_access.py`
  (Tailscale maps `tailscale` → CGNAT `100.64.0.0/10`).
- Status lookups for the range itself (installed/running/own IP) should
  follow the same pattern as `tunnel.py` (Cloudflare) and be split into an
  independent module (`server/tailscale.py`) — the convention is that
  `network_access.py` only makes CIDR decisions while a separate module
  handles status lookups.
- To surface connections from clients the server naturally can't see (e.g.
  plain SSH), catch the event with a tmux hook
  (`client-attached`/`client-detached`) and POST to an internal-only
  endpoint like `/api/notify/client-event`, reusing the existing `notify.py`
  bridge. Follow the pattern where `bin/fsh` registers/unregisters the hook
  via an opt-in environment variable.

### 4.8 Splitting or adding a new frontend module (post-2.0)
- New feature modules go under a topic directory (`js/<topic>/`) if they'll
  grow past one file, or as a top-level `js/<name>.js` if they're small and
  self-contained (see `search.js`/`ports.js` for the pattern).
- Import shared state/utilities from `core/*.js` — never re-derive
  `API_BASE`/token handling/session state locally.
- If two modules need to call into each other, a circular `import` is fine
  as long as every cross-call happens inside a function body, never at
  module-evaluation time (see `js/panels/viewer/shell.js`'s header comment
  for the reasoning). Don't force an artificial one-directional split just
  to avoid the cycle.
- **Never** import a stateful `core/*.js` module (`store.js`, `dom.js`) from
  `js/voice/*.js` — that bundle is built completely independently and an
  `import` there silently duplicates the module's state instead of sharing
  it (§2's `frontend/` note, and the real bug it caused). Read shared live
  state as `window.X` there instead.

---

## 5. Process map at runtime

```
$ fsh start
  ├─ uvicorn server.main:app  (port 7777)                [server]
  ├─ cloudflared tunnel --url ...                        [tunnel]
  ├─ python -m server.voice.daemon                       [voice daemon]
  ├─ python server/tunnel_watchdog.py                    [tunnel watchdog]
  └─ tmux -L fsh server (new session: dev)                [tmux]
      └─ zsh (or claude --resume)                        [work shell]

$ fsh manage                                             [server/tui/, standalone]
```

PIDs are stored in `/tmp/vt-pids/{server,tunnel,voice}.pid`. `fsh stop`
cleans them all up.

---

## 6. Security model (current state)

| Layer | Mechanism | Limitation |
|---|---|---|
| Transport | cloudflared HTTPS tunnel | — |
| Transport (alternative) | Tailscale WireGuard VPN + IP whitelist (D9, `--network tailscale`) | Requires trusting Tailscale itself; tailnet ACLs must be managed separately |
| Human auth | Password (scrypt hash) → 24h HMAC-signed session cookie (`server/auth.py`) | — |
| Machine auth | `VT_AUTH_TOKEN` via `?token=` or `Authorization: Bearer` — for daemons/QR/URLs | Exchanged for a cookie and stripped from the URL on first use (Phase 9 #8) |
| Device gate | First-time-device TOTP gate (opt-in, `fsh otp setup`), 90-day `vt_device` cookie after | Fully disabled — and devices quietly accumulate — until OTP is explicitly set up |
| Cross-site | `OriginGuardMiddleware` rejects any request/WS whose Origin isn't self, before auth even runs | The one thing password/OTP alone can't block |
| WebSocket auth | Middleware validates before accept | — |
| Session ID | `secrets.token_urlsafe(12)` — 16 chars, ~96 bits | — |
| **E2E encryption** | **Implemented** (`?e2e=1`) — X25519 + Ed25519 identity signing + NaCl SecretBox, TOFU-pinned identity key (§3.6) | Opt-in, not the default; identity-key rotation requires explicit user re-trust |
| Code viewer | Fixed root (`VT_BROWSE_ROOTS`) + `Path.resolve()`/`is_relative_to` (never `startswith`) + denylist, all in one place (`fsguard.py`); read-only, no write API | Exposed over the public tunnel, so this is the primary blast-radius control |
| Ports dashboard | Can't kill the FarShell server itself or `cloudflared`/`tailscaled`/`sshd`; can't kill other users' processes (no sudo); re-checks port→pid right before killing to avoid PID-reuse mistakes | `expose` opens a port to the public internet — requires `confirm:true` and `VT_NETWORK_MODE=all` |
| Command safety | `safe_mode.py` pre-blocks a configurable dangerous-command list | Best-effort pattern matching, not a sandbox |
| Upload | Isolated to `/tmp/vt-uploads/` | No disk quota |
| Connection visibility | `VT_NOTIFY_CLIENT_EVENTS=1` → tmux client-attached/detached push (D9) | Off by default; `who`-based remote host extraction is best-effort |

---

## 7. Status

For what's shipped and when, see [CHANGELOG.md](./CHANGELOG.md) — it's the
single source of truth for release history; this document intentionally
doesn't duplicate a roadmap or a "done/remaining" list (that drifted from
reality before). For frontend-restructure-specific history (the September
2026 ES-module migration referenced throughout §2), see
`docs/plan-2.0/10-frontend-restructure.md` locally (gitignored, not on
GitHub).
