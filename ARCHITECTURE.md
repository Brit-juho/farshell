# FarShell Architecture

See [API.md](./API.md) for endpoints and [CHANGELOG.md](./CHANGELOG.md) for release history.

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
│   server/voice/ (daemon pkg)   │   │   Tailscale            │
│   server/stt_service.py        │   │   token/password auth  │
│   frontend/js/voice/ (own bundle)│  │   ntfy/Telegram push   │
└────────────────────────────────┘   └─────────────────────────┘
```

**Core idea**: the tmux session is the **single source of truth**. Desktop
iTerm, the mobile PWA, and the Voice Daemon all attach to and operate on the
same tmux session.

### 1.1 Single tmux server principle

Every fsh client connects to the isolated tmux socket `-L fsh` (overridable
via the `VT_TMUX_SOCKET` environment variable). It's kept separate from the
user's own existing `tmux ls` sessions.

| Client | Invocation form | Source |
|------------|-----------|------|
| `bin/fsh` (CLI) | `${TMUX_BASE[@]} ...` (`tmux -L fsh`) | Defined at the top of `bin/fsh` |
| `server/main.py` (PTY) | `tmux -L fsh attach-session ...` | `pty_manager.py` |
| `server/voice/daemon.py` | `TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]` | `server/voice/daemon.py` |
| `server/tui/` (`fsh manage`) | tmux calls via `tmux_runner.py`/`helpers.py` | `server/tui/` |
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
hotkeys, device management, template management, tunnel management,
`fsh manage` TUI, etc.). Prompt snippets are web-UI only — there is no
`fsh snippet` subcommand.

### `server/` — FastAPI backend (Work + Voice Plane)

`main.py` is the FastAPI entry point; it wires middleware/auth and mounts the
routers from `server/routes/` (the main ones are listed below), which own both the REST endpoints and
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
| `auth/` | Web login (scrypt password hash + HMAC session cookie), device whitelist + TOTP gate, one-time registration ticket |
| `authz/` | Unified authorization engine. **Vocabulary**: `scopes.py` holds 17 scopes plus per-scope conditions (`modes`, `ops`, `groups`, `role` …) with least-privilege defaults, the table that converts the earlier 67 finer names (`LEGACY_TO_COARSE`, applied when records are read), the four presets (View/Collaborate/Drive/Control) and the elevated/escalation rules; a decision whose context lacks a condition value the scope requires is denied. **Principals** (`principals.py`): `human`, `local` (CLI and service daemons, attribute `name`), `agent` (a hook is the same agent with `via=hook`), `peer`, `legacy_token` (temporary). Shadow mode (`shadow.py`) compares the engine with the old decisions without changing responses. `sock.py` — second uvicorn server on `~/.vt/run/fsh.sock` (peer uid/pid from the kernel, self-test that fails closed, route allowlist), `legacy_use.py` — counter of machine-token requests for `fsh doctor`. `store.py` keeps grants/approvals with `schema: 2` (old-name records are converted on read; a `pre-schema2` backup precedes the first overwrite; `fsh migrate-auth --status/--verify/--backup/--restore`). `VT_AUTHZ_ENFORCE=share` (off by default): `flags.py`, `share_svc.py` + `approvals.py` + `shares.py` take over agent session sharing (decisions, grants/requests, share policy, audit chain) with the old `agent_grants.json` written in parallel. `VT_AUTHZ_ENFORCE=deleg` (off by default): `deleg_svc.py` takes over delegation decisions (`deleg.spawn`, `deleg.control` with `role=parent|child`, spawn directory = `fs.read` inside the browse roots, killswitches incl. safe_mode), derives grants from the `delegations.json` ownership ledger (still written; lock order authz -> ledger), records the folder-grant inheritance, and audits into the chain. `VT_AUTHZ_ENFORCE=msg` (off by default): `msg_svc.py` takes over agent-to-agent messages — the sender is the authenticated caller (socket peer / verified pane), delegation parent/child and siblings (open delegations only) send freely, any other pair needs a per-pair grant a person approves in the shared inbox (HTTP 202 until then), the receiving side decides remote messages from the peer's `hosts.json` grant, and the per-host rate limit is counted by the engine. `VT_AUTHZ_ENFORCE=peer` (off by default): `peer_svc.py` takes over other Macs — HMAC only authenticates `peer:<id>`, the grants are translated from `hosts.json` on the spot, every `/api/peer/*` route declares its (scope, conditions) in one table (`NEEDS`), open terminal sockets re-check per input frame and every 2 seconds, relay maps each allowlist rule to a group; `host_gate.py` requires `host:<id>` scopes on the routes that operate another Mac (people yes, `fsh` list/ping only, agents and the machine token no); `host_grant_svc.py` turns `fsh host allow-*` into an approval request a person confirms. `VT_AUTHZ_ENFORCE=pair` (master switch, off by default; off = every `/api/pairs` route is 409 and nothing else changes): `pairs.py` (+ `routes/pairs.py`, `pair_cli.py`) — a pair is a bundle of grants, not a principal: code ceremony, one inbox approval of kind `pair`, driver/navigator presets, handoff with a generation number, member relation for messages and leases, ending triggers, and the pair steps of the revocation saga. `VT_AUTHZ_ENFORCE=browser` (master switch, off by default; off = every `/api/browser/*` route is a plain 404 and nothing else changes): `server/browser/` — `broker.py` (decision → ownership → host lease/generation → limits → URL policy → audit → adapter → post-action re-check → redaction), `policy.py` (URL policy and value redaction as pure functions), `tabs.py` (`~/.vt/authz/tabs.json`: tab ownership, host lease + generation), `config.py` (adapter switches and first-use consent), `adapters/` (`BrowserAdapter` contract; `playwright` and `cdp` are real, isolated profiles only, optional imports are lazy; four local CLI adapters are placeholders), with `routes/browser.py`, `authz/browser_cli.py` and the `browser_tabs` revocation-saga step (ownership released, tabs not closed) |
| `fsh_client.py` | stdlib-only client used by MCP, hooks, daemons and the CLI: unix socket first, `VT_AUTH_TOKEN` over TCP as fallback (`lib/fsh_client.sh` is the curl equivalent) |
| `crypto_channel.py` | E2E WebSocket encryption (X25519 ephemeral keys + Ed25519 identity signing + NaCl SecretBox) — server side of the handshake whose client side is `frontend/js/term/e2e.js` |
| `fsguard.py` | Single source of truth for the code-viewer's file-browse root confinement, path resolution, and denylist |
| `fs_read.py` | `/api/fs/file` reads: `rev` from the `open_resolved` fd, ETag/304, line-bounded byte ranges and tail, encoding detection and selection |
| `fs_watch.py` | Code-viewer auto-refresh behind `/ws-fs-watch`: validates subscriptions through `fsguard`, runs one shared flat + one recursive `watchfiles` watcher for all connections, and reports only which subscription changed |
| `safe_mode.py` | Pre-blocks a configurable list of dangerous shell commands |
| `network_access.py` | `localhost`/`lan`/`tailscale`/`all` network mode → CIDR whitelist, bind host determination |

**Voice**
| File | Responsibility |
|---|---|
| `voice_handler.py` | Audio decode (webm/ogg/mp4/wav → 16kHz mono) → STT hand-off · TTS (edge-tts → say) |
| `stt_service.py` | Which STT model is selected (setting `voice.sttModel`), engine load/unload, serialized transcription with per-stage timing, catalog/setup/delete for the settings UI. STT runs **on the server OS only** — browsers just upload audio |
| `stt_engines/` | Engine layer: `mlx_qwen3` (Apple Silicon), `llamacpp_qwen3` (every platform; `llama-server` child process, local-only + API key), `apple_speech` (macOS 26+, Swift helper built on first use). `catalog.py` holds models + measured numbers, `env.py` detects the host, `llama_release.py` pins + sha256-verifies the llama.cpp download |
| `output_watcher.py` | Idle detection on PTY output → TTS + push notification |
| `voice_daemon.py` | Thin shim — real implementation lives in the `server/voice/` package below |
| `server/voice/` (package) | `daemon.py` (hotkey + media-key main loop) · `config.py` (`~/.vt.env` parsing) · `media_keys.py` (macOS earbud Play/Pause) · `recorder.py` (record → STT → tmux inject) · `stt.py` (hands recorded audio to `stt_service`) · `tmux_target.py` (compat shim over the top-level module, avoids importing `pynput` just for target resolution) |

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
| `server_watchdog.py` | Reads `/api/health/ready`; after repeated failures saves an incident bundle and runs `fsh restart` ([reliability](./docs/ref/reliability.md)) |
| `resilience/` | Named thread pools with deadlines, `TimedLock`, `proc.run`, mount detection, path breaker, scan worker + quarantine, watch thread, health verdicts ([reliability](./docs/ref/reliability.md)) |
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
| `pty.py` | `/api/sessions` CRUD, `/api/upload`, `/ws/{id}`, `/ws-notify` — PTY session lifecycle + upload |
| `tmux.py` | `/api/tmux/*` — session CRUD + live preview + open-on-mac |
| `voice.py` | `/voice/*` — STT/TTS I/O, cancel, status/preload |
| `agents.py` | `/api/agents*`, `/api/agent/event`, `/api/agent/status` — agent detection + hook state |
| `files.py` | `/api/fs/*`, `/ws-fs-watch` — read-only file browser |
| `git.py` | `/api/git/*` — git status/diff/stage/commit |
| `ports.py` | `/api/ports*` — port dashboard (blocking calls offloaded via `asyncio.to_thread`) |
| `queue.py` | `/api/queue*` — prompt queue |
| `snippets.py` | `/api/snippets*` — prompt snippet library |
| `push.py` | `/api/push/*` — Web Push subscription |
| `clipboard.py` | `/api/clipboard/push` — clipboard daemon → `/ws-notify` broadcast |
| `system.py` | `/api/capabilities`, `/api/tailscale/status`, `/api/safe-mode`, `/api/notify/client-event` — status/diagnostics |

**`server/hooks/`**
| File | Responsibility |
|---|---|
| `tmux_client_notify.sh` | tmux client-attached/detached hook → POSTs to `/api/notify/client-event` |

### `frontend/` — xterm.js PWA, built by Vite

Since the September 2026 restructure, **every frontend script is a real ES
module** (`import`/`export`), bundled by Vite in library mode. There are two
build outputs, both with fixed (non-hashed) filenames so `sw.js`'s offline
precache stays valid:

- `frontend/dist/app.js` + `frontend/dist/app.css` — the main entry
  (`frontend/js/main.js`). `app.js` is only the boot wiring; everything needed at
  boot is in `core.js`, and **app.js + core.js is the initial payload**, capped at
  358,400 B (`frontend/tests/build-output.test.js`, CI). Everything else is a
  fixed-name lazy chunk chosen by `chunkOf()` in `vite.config.mjs`: `shell`
  (Solid + `js/shell/` — workbench, title bar, status bar, palette, fleet, focus),
  `panels`, `settings`, `files`, `editor`/`editor-*`, `preview`, `panel`, `screens`,
  `share`, `history`, `touch`, `upload`, `select`, `brand`, `tooltip`,
  `resize-overlay`, `voice-status`, `subagents`. A module with no rule falls into
  `core` — when you add one that is not needed at boot, give it a chunk.
- `frontend/dist/voice.js` — `frontend/js/voice/` built as a **separate,
  fully independent** Vite entry. `frontend/js/agent/status.js` lazy-loads it
  with a `<script type="module">` tag only after `/api/capabilities` reports
  voice is installed — so a terminal-only user pays zero bytes for it. It's
  independent specifically because a *shared* dynamic-import chunk between
  the two entries would get a content-hashed filename from Rollup, breaking
  the fixed-filename guarantee; see `vite.config.mjs` for the full story
  (including a real bug this caused and fixed: importing `core/store.js`
  from the voice bundle silently duplicated its module state and corrupted
  `window.sessions` after voice.js loaded).

| Directory | Owns |
|---|---|
| `js/core/` | `env.js` (API_BASE/token/cookie exchange), `api.js` (`apiFetch`/`vtFetch`/`vtEsc`), `store.js` (session state), `dom.js` (the `data-action` click-delegation registry that replaced 29 inline `onclick` attributes), `settings.js` (**settings store — the server's `/api/workspace.settings` is the source of truth, localStorage is only a pre-render cache**), `keymap.js` (keybinding registry with `passthrough`, so a shell key like `Mod+F` can be handed back to the terminal) |
| `js/term/` | The terminal client, split by concern: `e2e.js` (client side of the E2E handshake), `session.js` (`addSession`/`switchTo`/tab lifecycle — the riskiest split, kept as one orchestrator over `xterm-setup.js`/`ws.js` "assembly parts" rather than force-split), `clipboard.js`, `touch.js`, `links.js`, `selection.js`, `resize.js`, `workspace.js` (tab order persistence), `conn-overlay.js`, `keybar.js`, `guide.js`, `boot.js` (`bootApp()`) |
| `js/agent/` | The session grid plus the client half of the agent state machine: `badges.js` (which icon), `status.js` (`/ws-agent`, capability gating), `preview.js` (session preview cards), `state.js` (**the single client-side source of the four states**, keyed by tmux session name — a web session id changes on every re-attach), `paint.js` (**the single place that paints them**: tabs, pane headers, favicon, app badge) |
| `js/shell/` | Solid + TypeScript desktop shell (lazy `shell` chunk; reads core state only through `window` bridges, never by static import — ADR-26): `Workbench.tsx` (activity bar + one shared-width sidebar + the right pinned sidebar, `side-prefs.js` rules), `ActivityBar.tsx`/`activity-data.ts`, `Rail.tsx`/`RailRow.tsx` (sessions view), `Dock.tsx` (tool view, `window.vtDockHost`, pinned-panel hosting), `TitleBar.tsx` (36), `Hud.tsx` (status bar 24), `Palette.tsx`, `Fleet.tsx` (phone home), `Focus.tsx` (desktop focus mode), `GrepView.tsx` |
| `js/files/` | The folder tree and file panel (Solid + TS, lazy `files` chunk) |
| `js/panels/` | `panel.js` (shared panel shell: modal on phones, docked into a sidebar on desktop via `vtDockHost`, or a pane leaf) + `viewer/` (the code viewer: `state.js`, `overlay.js`, `tree.js`, `file.js` + `file-load.js`, `editor.js` (CodeMirror body and the diff highlighter, lazy `editor` chunk), `diff.js` (virtual diff list), `git.js`, `scm.js`, `recent.js`), the tool renderers (`ports/`, `usage.js`, `system.js`, `files/` …; the queue is top-level `js/queue.js`) with small `*-lazy.js` stubs in core that register the action and `import()` the body, and `settings/` (the settings window) |
| `js/voice/` | The voice client: `recording.js`, `capture.js`, `pcm.js`, `notify.js`, `media-session.js`, `index.js` (the entry point). Built as its own bundle (see above) |
| `js/ui/` | `toast.js` (unified toast — still window-bridged since `voice.js`'s separate bundle needs it; routes `info`/`success` into the HUD strip and only floats `warn`/`error`, see DESIGN.md), `okflash.js` (success shown at the trigger — swaps a button's icon for a check), `favicon.js` (dynamic tab-badge canvas, UMD-style), `icons.js` (the icon registry; `icons-shell.js` and `panels/panel-icons.js` hold icons only lazy chunks draw), `tooltip.js` (lazy, behind `tooltip-lazy.js`) |
| `js/push/` | `swreg.js` — Service Worker registration for Web Push |
| `js/lib/` | `ansilex.js`, `difflex.js`, `keyseq.js`, `shell-quote.js` — pure logic, UMD-wrapped so both the browser (`window.VTAnsiLex`) and the Node test suite (`require(...)`) can use them |
| `js/layout/` | The shell. `store.js`/`tree.js` (split-pane tree — pure functions + the single place that holds it), `panes.js` (recursive renderer; moves existing session wrappers instead of recreating them), `dnd.js` (5-zone drop targets), `resizer.js`, `compact.js` (<720px + coarse pointer render mode), `pane-picker.js`, `surface.js` (refit gate + `#vt-term-stage`), `focus.js`/`mobile-focus.js` (focus mode), `resize-overlay.js` (lazy), `clients.js` ("Connected screens"), `persist.js` (layout persistence — leaves store `{id, tmux}` so a tmux session survives a new PTY id), `breakpoints.js` |
| `js/theme.js`, `picker.js`, `queue.js`, `snippets.js`, `pushui.js`, `gate.js`, `main.js` | Top-level feature modules and the app entry point. `gate.js` is the one deliberately-classic (non-module) script — it runs the login gate before any ES module (which is deferred) could paint |
| `sw.js` | Service Worker — offline caching, precache list |
| `manifest.json` | PWA manifest |

### Root
| File | Responsibility |
|---|---|
| `vite.config.mjs` | Frontend build config — library mode, two independent entries (see above), no hashing, whitespace-only minify (identifiers are kept) |
| `styles/` | `main.css` (Tailwind entry), `layers/` (reset/components/patterns/history), `screens/` (the pre-Tailwind hand-written CSS, kept unlayered so it always wins the cascade), `theme/` (`tokens.css`, `skins.css`) |
| `install.sh` | Creates Python venv, installs per-profile packages, symlinks fsh, initializes ~/.vt.env, runs `npm ci && npm run build` (skipped when a prebuilt `frontend/dist/` from a release tarball is present) |
| `requirements-core.txt` | Terminal-only (~50MB) — input of the hash lock, not an install path |
| `requirements-voice.txt` | Additional voice dependencies (hundreds of MB; speech models are downloaded later from Settings) — input of the hash lock |
| `locks/core.txt`, `voice.txt`, `dev.txt` | Hash-locked (`--generate-hashes`, universal) versions of the three requirement files; the only thing `install.sh`, CI and `fsh update` install from (`pip --require-hashes`) |
| `scripts/lock_python.sh`, `scripts/check_python_lock.py` | Regenerate the lock (needs `uv`), and check offline that it matches `requirements-*.txt` |
| `.github/workflows/ci.yml`, `release.yml` | CI — `npm test`/`pytest` on PR/push, frontend build-artifact shape verification (no hashed filenames, size cap); release workflow |

### `.claude/skills/` — Claude Code skills
| File | Trigger |
|---|---|
| `fsh/SKILL.md` | Global: "voice mode", "mobile access", etc. |
| `fsh-voice/SKILL.md` | Manual Voice Daemon install/run |
| `fsh-mobile/SKILL.md` | Mobile adb testing |
| `fsh-start/SKILL.md` | Manual server startup |

---

## 3. Key data flows

### 3.1 Desktop voice input (Voice Daemon)
```
Ctrl+Shift+M (pynput, server/voice/daemon.py)
  → sounddevice 16kHz mono recording (recorder.py)
  → selected STT engine via stt_service (stt.py)
  → tmux_target.py resolves the pane → tmux send-keys "<text>"
```

### 3.2 Mobile/desktop voice input (PWA)
```
🎤 button (frontend/js/voice/recording.js, separate bundle)
  → AudioWorklet → 16kHz PCM16 chunks (~0.5s) → POST /voice/chunk (acked, resent on drop;
    server/voice_chunks.py joins them in seq order) — falls back to:
  → MediaRecorder (webm/opus) → apiFetch POST /voice/input?session_id=...
  → server/routes/voice.py → voice_handler.transcribe (ffmpeg conversion included; chunks arrive as WAV)
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

### 3.6 E2E-encrypted WebSocket handshake
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
- Add an `Engine` subclass under `server/stt_engines/` (see `base.py`), register it in `stt_engines/__init__.py`, add its models to `catalog.py` with measured numbers (`scripts/stt_bench.py`)
- Engines never download anything themselves: `download_model`/`install_requirement` run only from a user-approved setup request

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
- **No separate wrapper needed** for the terminal itself — if the user just runs `aider` / `codex` /
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

### 4.8 Splitting or adding a new frontend module
- New feature modules go under a topic directory (`js/<topic>/`) if they'll
  grow past one file, or as a top-level `js/<name>.js` if they're small and
  self-contained (see `picker.js`/`pushui.js` for the pattern).
- Import shared state/utilities from `core/*.js` — never re-derive
  `API_BASE`/token handling/session state locally.
- If two modules need to call into each other, a circular `import` is fine
  as long as every cross-call happens inside a function body, never at
  module-evaluation time (see `js/panels/viewer/tree.js`
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
  ├─ uvicorn main:app  (port 7777, run from server/)     [server]
  ├─ cloudflared tunnel --url ...                        [tunnel]
  ├─ python server/voice_daemon.py   (only with --voice) [voice daemon]
  ├─ python server/tunnel_watchdog.py                    [tunnel watchdog]
  └─ tmux -L fsh server (new session: dev)                [tmux]
      └─ zsh (or claude --resume)                        [work shell]

$ fsh manage                                             [server/tui/, standalone]
```

PIDs are stored in `~/.vt/run/vt-pids/` (`server.pid`, `tunnel.pid`, `voice.pid`, …). `fsh stop`
cleans them all up.

---

## 6. Security model (current state)

| Layer | Mechanism | Limitation |
|---|---|---|
| Transport | cloudflared HTTPS tunnel | — |
| Transport (alternative) | Tailscale WireGuard VPN + IP whitelist (D9, `--network tailscale`) | Requires trusting Tailscale itself; tailnet ACLs must be managed separately |
| Human auth | Password (scrypt hash) → 24h HMAC-signed session cookie (`server/auth/`) | — |
| Machine auth | `VT_AUTH_TOKEN` via `?token=` or `Authorization: Bearer` — for daemons and scripts | Exchanged for a cookie and stripped from the URL on first use  |
| Local auth (socket) | `~/.vt/run/fsh.sock` (0600): the kernel's peer uid/pid identify the caller (agent / hook / svc / cli — never human); no token needed. In-repo clients prefer it and fall back to the token | Same-uid processes can still use it; only allowlisted routes are reachable (`docs/ref/auth.md`) |
| Device gate | First-time-device TOTP gate (opt-in, `fsh otp setup`), 90-day `vt_device` cookie after | Fully disabled — and devices quietly accumulate — until OTP is explicitly set up |
| Cross-site | `OriginGuardMiddleware` rejects any request/WS whose Origin isn't self, before auth even runs | The one thing password/OTP alone can't block |
| WebSocket auth | Middleware validates before accept | — |
| Session ID | `secrets.token_urlsafe(12)` — 16 chars, ~96 bits | — |
| **E2E encryption** | **Implemented** (`?e2e=1`) — X25519 + Ed25519 identity signing + NaCl SecretBox, TOFU-pinned identity key (§3.6) | Opt-in, not the default; identity-key rotation requires explicit user re-trust |
| Code viewer | Fixed root (`VT_BROWSE_ROOTS`) + `Path.resolve()`/`is_relative_to` (never `startswith`) + denylist, all in one place (`fsguard.py`); read-only, no write API | Exposed over the public tunnel, so this is the primary blast-radius control |
| Ports dashboard | Can't kill the FarShell server itself or `cloudflared`/`tailscaled`/`sshd`; can't kill other users' processes (no sudo); re-checks port→pid right before killing to avoid PID-reuse mistakes | `expose` opens a port to the public internet — requires `confirm:true` and `VT_NETWORK_MODE=all` |
| Command safety | `safe_mode.py` pre-blocks a configurable dangerous-command list | Best-effort pattern matching, not a sandbox |
| Upload | id-based storage in `~/.vt/files/` — API takes no path, only ids | Bounded by `VT_FILES_MAX_GB`/`VT_FILES_TTL_DAYS`; shared/pinned files are excluded from cleanup |
| Connection visibility | `VT_NOTIFY_CLIENT_EVENTS=1` → tmux client-attached/detached push | Off by default; `who`-based remote host extraction is best-effort |

---

## MCP source and review pipeline

`mcp_adapters` discovers native definitions; `mcp_sources` assigns source precedence
and resolves exact discovered IDs. `mcp_library` stores portable snapshots and
provenance. `mcp_plans` holds expiring single-use review plans on the server.
`mcp_sync` compares opt-in source changes with recorded FarShell deployments;
`mcp_write` performs guarded minimal writes. Independent probes (`mcp_verify`)
provide evidence separate from configuration and the unknown live CLI state.
