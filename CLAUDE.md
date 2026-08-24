[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./CLAUDE.ko.md)

> **FarShell v1.7.0** (2026-08-20) — see [CHANGELOG.md](./CHANGELOG.md) for release history

## fsh CLI (runs anywhere)

Control FarShell from any terminal with the `fsh` command:

```bash
fsh voice              # voice mode (background, usable while working in Notion)
fsh clip               # clipboard sync daemon (Mac clipboard change → web, OSC52 fallback)
fsh mobile [--e2e]     # mobile access URL + QR (--e2e: encrypt payload)
fsh start [--voice]    # start everything (server+tunnel, --voice also starts the voice daemon)
fsh stop [--purge]     # stop (--purge: also fully kill tmux sessions)
fsh status             # check current status
fsh manage             # TUI management tool (sessions/target/hotkeys/status) — Wave 4
fsh attach [name]      # attach any tmux session in a new window
fsh voice-target [name|--auto]  # lock/unlock the voice daemon target
fsh queue [list|add "content" [session]|run|rm <id>|unblock <id>|clear]  # prompt queue (P4)
fsh hotkey [list|set|reset|disable]  # view/change hotkeys
fsh password [clear]   # set web login password (stores a hash) / clear=unset
fsh otp [status|setup|disable]   # require OTP when registering a new device (fully disabled until setup)
fsh device [list|revoke <id>]    # list registered devices / revoke (also invalidates sessions if a phone is lost)
fsh help <topic>       # concepts/voice/hotkeys/target/troubleshoot
fsh claude             # open new terminal window with tmux dev + claude --resume (internally fsh agent claude)
fsh agent <name>       # start with any agent — claude/codex/aider/gemini (generalization of fsh claude)
fsh template [save|apply|list|rm] <name>  # save/apply CLAUDE.md templates
fsh popup <action>     # quick fsh command invocation via tmux 3.2+ popup
fsh run "..."          # run headless `claude -p` in background + TTS notification on completion
fsh handoff mobile     # hand off the current tmux session to your phone (QR + #tmux=)
fsh handoff desktop    # bring a phone session back to the Mac terminal
fsh tunnel expose 3000 "app name"  # expose another local port through a separate Cloudflare tunnel
fsh tunnel unexpose 3000          # stop the tunnel for that port
fsh tunnel list                   # list all open tunnels (main + extra ports)
fsh tunnel hook                   # check + immediately run the URL-change hook (fsh help tunnel-hook)
fsh tunnel restart                # force a new tunnel even in a zombie-reconnect (unresponsive) state + rerun the hook
fsh tunnel watchdog               # check/start the zombie-reconnect auto-detection daemon (normally auto-started by fsh start/voice/mobile)
fsh ssh [session]      # guidance for connecting directly to a tmux session via Tailscale + SSH (D9, corporate networks, etc.)
fsh doctor             # installation/environment diagnostics (includes Linux checks)
fsh install-profiles   # auto-register terminal app profiles (iTerm2 Dynamic Profile + other snippets)
fsh shell-init zsh     # print the shell init snippet (eval "$(fsh shell-init zsh)" >> ~/.zshrc)
```

> **Supported OS**: macOS / Linux (X11) / WSL2 (behaves as Linux). Native Windows is not supported.

**Phase 6 — single tmux server principle:** the fsh CLI, server, Voice Daemon, and hooks all use the `-L vt` isolated socket (the socket name stays `vt` regardless of the CLI's name). The Voice Daemon can override it via the `VT_TMUX_SOCKET` environment variable. This is kept separate from the user's own `tmux ls`.

**Automatic behavior when running `voice` / `mobile` / `start`:** a new window opens in your current terminal app (iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app) and runs `tmux new -A -s dev 'claude --resume'` inside it. If you're already inside tmux, no new window is opened.

**Voice-coding workflow while working in Notion:**
1. `fsh voice` → starts in the background (+ auto-opens a new iTerm window with `tmux dev` + `claude --resume`)
2. Pick the current conversation from the resume list in the new window → voice/mobile then connects to that Claude
3. Leave the original window as-is and go back to Notion to work
4. Ctrl+Shift+V → speak ("git status") → automatically typed into tmux dev
5. `fsh stop` → shut down

> Calling an `fsh` command from inside tmux already won't open a new window (checked via `$TMUX`).
> Auto-open is limited to macOS + iTerm. Elsewhere, it prints guidance for the manual command (`tmux new -A -s dev 'claude --resume'`).

### Claude global skill

| Command | Description |
|--------|------|
| `/fsh` | Global skill (formerly `/vt`). Invokable from anywhere with phrases like "voice mode", "mobile access" |

### Project skills

| Command | Description |
|--------|------|
| `/fsh-start` | Start server + prepare tmux + remote access via Cloudflare Tunnel |
| `/fsh-mobile` | Mobile testing (adb port forwarding, opening Chrome, screenshots) |
| `/fsh-voice` | Install/run the Voice Daemon (hotkey → STT → tmux injection) |

### Installing for new users

**The default path is `./install.sh`** (one-line installer, added 2026-04-14). The steps below are only for when interactive guidance is needed.

```bash
# One-line install (recommended)
./install.sh            # terminal only (~50MB)
./install.sh voice      # terminal + voice mode (~1.5GB)
```

`install.sh` automatically: creates a Python venv → installs packages per profile → symlinks the fsh CLI → creates `~/.vt.env` → updates PATH.

---

### Legacy: interactive install (manual)

Only follow the steps below if install.sh doesn't work, or if you prefer a different environment such as conda/pyenv.

> **Python environment management:** all execution-related paths/ports are managed via `~/.vt.env` (user-local, gitignored) and `config/vt.defaults.env` (committed defaults). When asking the user to choose their environment, have them pick among venv/conda/pyenv/system Python, then record the result in `VT_PYTHON` in `~/.vt.env`.

#### Step 1: Detect OS

```bash
uname -s  # Darwin=macOS, Linux=Linux/WSL2
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL2" || echo "Native"
```

Confirm with the user: "Is this macOS / WSL2 / Linux?"

#### Step 2: Choose install profile

Ask the user:

> Which features would you like to install?
>
> 1. **Terminal only** — terminal access from mobile (~500MB)
>    - FastAPI server + xterm.js web terminal + Cloudflare Tunnel
>    - No voice features
>
> 2. **Terminal + voice mode** — code by voice (~3GB)
>    - Everything above + Whisper STT + edge-tts TTS + Voice Daemon
>    - macOS hotkey (Ctrl+Shift+V), mobile voice input

#### Step 3: Prepare Python environment

Ask the user which environment they want to use (venv / conda / pyenv / system Python). Record the result in `VT_PYTHON` in Step 6.

**Default recommendation — venv:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**If preferring conda:**
```bash
conda create -n fsh python=3.11 -y && conda activate fsh
```

**If preferring pyenv:**
```bash
pyenv install 3.11.7 && pyenv local 3.11.7
```

#### Step 4: Install packages (per profile)

**Terminal only (option 1):**
```bash
pip install -r requirements-core.txt
```

**Terminal + voice (option 2):**
```bash
pip install -r requirements-core.txt -r requirements-voice.txt
```

Additional package for macOS voice mode:
```bash
pip install pyobjc-framework-Cocoa
```

#### Step 5: Register the fsh CLI

```bash
mkdir -p ~/.local/bin
chmod +x bin/fsh
ln -sf "$(pwd)/bin/fsh" ~/.local/bin/fsh
ln -sf "$(pwd)/bin/vt" ~/.local/bin/vt   # backward compatibility — bin/vt is a symlink pointing to bin/fsh
```

Check PATH:
```bash
echo "$PATH" | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

#### Step 6: Create the config file (`~/.vt.env`, gitignored)

Record the Python path chosen in Step 3. See `config/vt.defaults.env` for the full list of keys.

```bash
# Use the absolute python path from the environment you created in Step 3 (example)
PY_PATH="$(pwd)/.venv/bin/python"   # for venv
# PY_PATH="$(conda info --base)/envs/vt/bin/python"   # if using conda
# PY_PATH="$(pyenv which python)"                       # if using pyenv

cat > ~/.vt.env << EOF
VT_PORT=7777
VT_PYTHON=$PY_PATH
# VT_TOKEN=my-secret-token  # auth for remote access (optional)
EOF
```

#### Step 7: Install cloudflared (for mobile remote access)

```bash
# macOS
brew install cloudflared

# Linux/WSL2
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared
```

#### Step 8: Register the Claude Code skill (optional)

```bash
mkdir -p ~/.claude/skills/fsh
cp .claude/skills/fsh/SKILL.md ~/.claude/skills/fsh/SKILL.md 2>/dev/null || true
```

#### Step 9: Verify the install

```bash
fsh status
```

Guidance for the user:
- `fsh mobile` — mobile access (QR code)
- `fsh voice` — voice mode (if option 2 was chosen)
- `fsh stop` — shut down

#### Platform-specific notes

**macOS:** for voice mode, the terminal app must be allowed under System Settings → Privacy → Accessibility
**WSL2:** the voice hotkey requires WSLg (Windows 11). If unavailable, use the browser 🎤. PowerShell: `.\bin\fsh.ps1 voice`

---

## FarShell project guide

### Running the server

```bash
# Method 1: script (automatically uses VT_PYTHON from ~/.vt.env)
./run_server.sh

# Method 2: run directly
cd server
"$VT_PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 7777
```

- The Python path differs per environment — check the currently detected value with `fsh doctor`
- Packages: `requirements-core.txt` (required) + `requirements-voice.txt` (voice mode)

### Access

| Environment | URL |
|------|-----|
| Desktop | `http://localhost:7777` |
| Mobile on the same network | `http://macbook-IP:7777` (get the IP with `ipconfig getifaddr en0`) |
| Mobile via adb | `adb reverse tcp:7777 tcp:7777` → `http://localhost:7777` |
| Remote (from anywhere) | `cloudflared tunnel --url http://localhost:7777` → use the generated HTTPS URL |

### Mobile testing (adb)

```bash
# 1. Port forwarding
adb reverse tcp:7777 tcp:7777

# 2. Open Chrome
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome

# 3. Capture a screenshot
adb shell screencap -p /sdcard/test.png && adb pull /sdcard/test.png /tmp/test.png

# 4. Wake the screen (if locked)
adb shell input keyevent KEYCODE_WAKEUP && adb shell input swipe 540 2000 540 1000 300
```

### API endpoints

See **[API.md](./API.md)** for the full REST/WebSocket reference — keeping a separate table
here in CLAUDE.md caused drift whenever only one side got updated (actually caught and fixed
on 2026-08-20), so this file keeps only the category list and API.md is the single source of detail.

| Category | Representative paths |
|----------|-----------|
| Sessions / PTY | `/api/sessions`, `/ws/{id}` |
| tmux | `/api/tmux/*` (sessions·attach·create·kill·open-on-mac·preview) |
| Voice | `/voice/input`, `/voice/output`, `/voice/cancel`, `/voice/local/*`, `/voice/stt/*` |
| Auth | `/api/auth`, `/api/auth/status`, `/api/auth/logout` |
| Code viewer / diff / Git actions | `/api/fs/*`, `/api/git/status`·`diff`·`stage`·`unstage`·`commit` (D16) |
| Prompt queue | `/api/queue*` (P4) |
| Port dashboard | `/api/ports*` (P3) |
| Web Push | `/api/push/*` (P5) |
| Agent status / notifications / diagnostics | `/api/agent*`, `/api/notify/*`, `/api/safe-mode`, `/api/tailscale/status`, `/api/tunnel/status` |
| Workspace / misc | `/api/workspace`, `/api/capabilities`, `/api/upload`, `/api/download`, `/api/clipboard/push` |
| WebSocket | `/ws/{id}`, `/ws-notify`, `/ws-preview/{name}`, `/ws-agent`, `/ws-workspace` |

### E2E test procedure

```bash
# 1. Create a session
SID=$(curl -s -X POST http://localhost:7777/api/sessions -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. Run a command over WebSocket (Python)
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect(f'ws://localhost:7777/ws/$SID') as ws:
        await ws.send(b'echo hello\n')
        for _ in range(5):
            try:
                d = await asyncio.wait_for(ws.recv(), timeout=1)
                if b'hello' in d: print('OK'); break
            except: break
asyncio.run(t())
"

# 3. TTS test
curl -s -X POST http://localhost:7777/voice/output \
  -H 'Content-Type: application/json' \
  -d '{"text":"test"}' -o /tmp/tts.mp3 -w "bytes: %{size_download}"

# 4. Check for zombie processes
curl -s -X DELETE "http://localhost:7777/api/sessions/$SID"
ps aux | grep defunct | grep -v grep || echo "No zombies"

# 5. File upload test
echo "hello" > /tmp/test_upload.txt
curl -s -X POST "http://localhost:7777/api/upload?session_id=$SID" \
  -F "file=@/tmp/test_upload.txt"

# 6. Session rename test
curl -s -X PATCH "http://localhost:7777/api/sessions/$SID" \
  -H 'Content-Type: application/json' -d '{"name":"my-session"}'

# 7. Scrollback test — check that previous output is visible after a browser refresh
```

### Claude Code Stop hook (automatic TTS summary)

Automatically plays a TTS summary when a Claude Code response completes.

- Script: `server/tts_hook.sh`
- Config: registered under `hooks.Stop` in `~/.claude/settings.json`
- Behavior: extracts the last assistant response (up to 200 chars) from the transcript → server TTS → plays via `afplay`
- Fallback: uses macOS `say -v Yuna` if the server isn't running

```bash
# Test the hook (with the server running)
echo '{"transcript_path":"/tmp/test_transcript.jsonl"}' | ./server/tts_hook.sh
```

### Voice Daemon (standalone macOS voice input)

A daemon that types voice input directly into tmux via a hotkey, without needing the server.

```bash
# Run
"$VT_PYTHON" server/voice_daemon.py &

# Usage: Ctrl+Shift+V (toggle) → speak → STT → typed into the active tmux pane
# Requires allowing the terminal app under macOS System Settings → Privacy → Accessibility
```

### Clipboard Daemon (macOS clipboard sync)

When connecting to the web terminal remotely/from mobile, the browser can only access
"that device's" clipboard, so copies made on the Mac (server) side don't automatically
carry over. Two paths cover this:

- **OSC52** (no separate process needed) — copies made inside terminal programs like
  `vim` or `tmux copy-mode` are already carried in the PTY output stream, so
  `frontend/js/terminal.js` intercepts them via
  `term.parser.registerOscHandler(52, ...)` and applies them to the clipboard of
  whatever device has the web page open.
- **Polling daemon** (`fsh clip`) — copies made outside the terminal (Safari, Finder,
  etc.) can't be caught via OSC52, so `server/clipboard_daemon.py` polls
  `NSPasteboard.changeCount` and, on a change, delivers it to the web via
  `POST /api/clipboard/push` → `/ws-notify` broadcast.

```bash
# Run (or use fsh clip)
"$VT_PYTHON" server/clipboard_daemon.py &
```

### tmux-centric session management

The web UI uses tmux sessions as its default:
- On startup, tmux sessions are auto-detected → attaches to the first one
- "+ New" → creates a tmux session (`POST /api/tmux/create`)
- Closing a tab → only detaches (the tmux session stays alive). Kill is done via `DELETE /api/tmux/kill/{name}`
- Duplicate-attach prevention: a tmux session already open in the web switches to its existing tab
- iTerm2 and the web can be attached to the same tmux session at the same time

### Key features

| Feature | Description |
|------|------|
| Voice Daemon | macOS hotkey (Ctrl+Shift+V) → STT → direct input into tmux |
| Clipboard sync | OSC52 (in-terminal copies) + `fsh clip` polling daemon (copies outside the terminal) → pushed to web clipboard |
| Hands-free mode | Mobile 🔄 button → continuous record/STT loop |
| Voice-only mode | 🎧 button → hides the terminal and shows only a large mic (for earbud operation) |
| Web login password | Set via `fsh password` → stores only an scrypt hash (`VT_AUTH_PASSWORD_HASH`); the plaintext is never stored. On login, issues a 24h session cookie signed with `VT_AUTH_SESSION_KEY` (not the plaintext or a token). Human-facing auth. `server/auth.py` |
| Device registration + OTP gate | Login is **always** by password. OTP is a gate required only "when registering a device seen for the first time." A registered device gets a `vt_device` long-lived cookie (90 days) and afterward passes with just the password — since it's per-device rather than per-IP, a phone switching between LTE and wifi doesn't get disconnected. **OTP stays fully disabled until `fsh otp setup`**, and device registrations quietly accumulate in the meantime, so turning it on later doesn't lock out devices already in use. Stored at `~/.vt/devices.json` (0600, sha256 hashes only). `fsh device revoke <id>` immediately invalidates that device's session cookie as well |
| One-time device registration ticket | The QR/URL from `fsh mobile`/`fsh handoff` carries a 5-minute one-time ticket (`?ticket=`) instead of a persistent token. Physical access to the Mac is already proven at the moment the QR is shown, so scanning it equals approving registration. The old approach of embedding a persistent token in the URL left that value permanently sitting in logs, history, and QR images |
| Cross-site blocking | `OriginGuardMiddleware` (`server/main.py`) — returns 403 for both HTTP and WS if the Origin isn't itself. The only path that auth/OTP alone can't block (if the browser already has a cookie, auth passes). Also removes the default `*` CORS — opt in via `VT_ALLOWED_ORIGINS` if needed |
| API token auth | The `VT_AUTH_TOKEN` environment variable is a machine-facing token (for daemons/QR/URLs). Via URL `?token=xxx` or `Authorization: Bearer xxx`. Coexists with password login. (Legacy names `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY` are also recognized as fallbacks) |
| tmux session management | Create/attach/detach/kill tmux sessions from the web |
| Scrollback buffer | Restores previous output on WS reconnect (up to 5000 chunks) |
| Terminal search | Ctrl+F / Cmd+F → xterm.js search addon |
| Session name editing | Double-click a tab → rename (PATCH API; the tmux session name is also changed via `rename-session` — if the new name isn't alphanumeric/dash/underscore, tmux is left untouched and only the web label changes) |
| Live preview grid view | Grid button in the top bar. A screen for scanning tmux sessions at a glance as cards. Cards carry an agent badge (🟣Claude etc., from `GET /api/agents`), pulse gently while a tool is running (`.working`), and switch from the pulse to a ✓ done badge when the Stop hook fires (`.done`, cleared once clicked to acknowledge). **Claude Code hooks don't directly report the tmux pane** — the card is identified by matching the hook payload's `cwd` against the `pane_current_path` from `/api/tmux/sessions` (`server/agent_status.py`). When multiple sessions share the same directory (e.g. both at `$HOME`), cwd isn't unique, and in that case **nothing is highlighted** instead of picking one — safer than confidently lighting up the wrong card. A session already open as a web tab is marked with a left border on the card (`web_session_id`) — so the click outcome (switch tab vs. new attach) is predictable in advance |
| Code viewer / diff (P2) | ⋯ menu → "Code Viewer". Solves the problem of not being able to see code visually when developing remotely via CLI only. File tree · syntax highlighting (highlight.js, 36 languages) · `git diff` rendering. **Read-only, with no write API.** Since it's exposed over a public tunnel, it has three layers of defense: ① a fixed root (`VT_BROWSE_ROOTS`, default `~/GitHub` — opening `$HOME` would put `~/.ssh`·`~/.aws` in scope) ② `Path.resolve()` + `is_relative_to` (startswith is banned — sibling directories would pass. Since `resolve()` expands symlinks, links pointing outside the root are also caught) ③ a denylist (`.env*`·`*.pem`·`id_rsa`·`.ssh/`·`.aws/` etc., checked against every path component). The check lives in one place only: `server/fsguard.py` |
| Web Push (P5) | ⋯ menu → "Push Notifications". Existing notifications (`/ws-notify` → Notification API) only work **while a PWA tab is alive**, so turning off the phone screen meant missing "waiting for approval." This fills that gap. No push is sent while at least one WS client is connected (to avoid the same notification arriving twice). **Requirements**: ① https — Service Workers don't even register over plain http ② iOS requires adding to the home screen as a PWA (16.4+; a subscription can't be created from a Safari tab — no workaround). **A subscription is bound to its origin** — if the trycloudflare URL changes, existing subscriptions all die, so each subscription stores its origin, mismatches are excluded from sending, and 404/410 responses are cleaned up on the spot. Notification bodies never contain commands, paths, or code (they'd show on the lock screen). The VAPID key is auto-generated at `~/.vt/vapid.json` (0600) — **deleting it invalidates every existing subscription**. SW registration is handled by `js/swreg.js` (it used to live inside `voice.js`, so the SW never registered at all when voice wasn't installed) |
| Prompt queue (P4) | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. **Pairs with voice mode** — right now, speaking while the agent is working gets swallowed, but with the queue you can walk around dropping in 3 instructions and have them run in order. Automatic feeding is only triggered **by Claude Code's stop hook** (`POST /api/agent/event`). codex/aider/gemini have no hook, so they need manual feeding via `fsh queue run` / "run now" — feeding based on guessing output idleness was not adopted, since it can't tell a brief pause in build logs apart from actual completion. Four gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 3 sec — the user may have started typing directly) · safe_mode · confirming the target pane is alive · one item at a time. Blocked or failed items are **not discarded** — they stay in the queue as `blocked`. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock |
| Port dashboard (P3) | ⋯ menu → "Ports". Handles "what's running right now / kill port 3000" from the phone when you're away from the Mac. Shows port·PID·uptime·CPU·memory, one-click kill, integrates with `fsh tunnel expose`. **Killing the VT server itself, or cloudflared/tailscaled/sshd, is blocked** — killing them would cut off this very screen. Other users' processes are also blocked (no sudo used). Right before killing, `port→pid` is re-checked to prevent PID reuse from killing the wrong process, returning 409 on a mismatch. `expose` opens the local server to the **public internet**, so it returns 428 without `confirm:true`, and is refused outright unless `VT_NETWORK_MODE` is `all` (there's no point narrowing access scope and then reopening it). Checked in `server/portscan.py` |
| File upload | Voice bar 📎 button → saved to `/tmp/vt-uploads/` |
| File download | `GET /api/download?path=...` |
| tmux detach detection | Shows `[process exited]` on PTY EOF |
| Extra port tunnels | `fsh tunnel expose <port>` — a Cloudflare quick tunnel is a 1:1 host↔port mapping, so a port can't be switched via a path (`/localhost:3000`). One tunnel is spun up per port, tracked by fsh via PID/registry |
| Tunnel URL change hook | `VT_TUNNEL_HOOK` — runs an arbitrary command when the URL changes (stdin: `label<TAB>URL`). Since publishing targets differ per person (Notion/Slack/ntfy/file), fsh doesn't know about the service itself. Examples and caveats: `fsh help tunnel-hook` |
| Automatic tunnel zombie-reconnect recovery | cloudflared can fall into a zombie state where the process is alive (`kill -0` succeeds) but only the QUIC control stream to the edge is cut, endlessly retrying reconnection (static files occasionally return 200, API returns 503). `server/tunnel_watchdog.py` auto-starts with `fsh start`/`voice`/`mobile`, watches `/tmp/cloudflared.log` for reconnect-failure patterns (default: 4+ times within 90 seconds), and automatically calls `fsh tunnel restart`. Manual check/start: `fsh tunnel watchdog`; manual forced restart: `fsh tunnel restart` |
| Tailscale remote access (D9) | `fsh ssh` — connect directly to tmux via SSH on networks where screen remoting is blocked (e.g. corporate networks). `fsh mobile --network tailscale` also restricts the web UI to the tailnet only |
| Client connection notifications (D9) | `VT_NOTIFY_CLIENT_EVENTS=1` — tmux client-attached/detached hook → ntfy/Telegram push |

### Architecture

```
server/
  main.py           — FastAPI (WS + REST + Voice + file upload/download)
  auth.py           — web login auth (scrypt password hash + HMAC-signed session cookie
                      + device whitelist + TOTP gate + one-time registration ticket).
                      bin/fsh calls this directly via the `python auth.py <cmd>` CLI without the server.
                      Runtime state lives in ~/.vt/{devices,totp,tickets}.json (0600) —
                      kept separate from config (~/.vt.env) so it takes effect immediately without a server restart.
  pty_manager.py    — PTY sessions (broadcast, scrollback buffer, EOF detection)
  voice_handler.py  — STT (faster-whisper) + TTS (edge-tts / macOS say)
  output_watcher.py — output monitoring → task-completion TTS notification
  local_mic.py      — MacBook local microphone (sounddevice)
  session_store.py  — session metadata (supports renaming)
  tts_hook.sh       — Claude Code Stop hook (automatic TTS summary)
  voice_daemon.py   — standalone voice input daemon (hotkey → STT → tmux)
  clipboard_daemon.py — macOS clipboard polling daemon (changeCount → /api/clipboard/push)
  tunnel_watchdog.py — cloudflared zombie-reconnect watchdog daemon (log pattern detection → auto-calls fsh tunnel restart)
  routes/clipboard.py — POST /api/clipboard/push → /ws-notify broadcast
  platform_utils.py — cross-platform utilities (macOS/Linux/WSL2)
  tailscale.py      — Tailscale status detection (D9, same pattern as tunnel.py)
  vt_env.py         — ~/.vt.env parser (interpreted the same way as bash source). Shared by voice/config.py and clipboard_daemon
  hooks/tmux_client_notify.sh — tmux client-attached/detached → /api/notify/client-event (D9)

lib/
  vt_env.sh         — defines the ~/.vt.env format + a single reader/writer
                      (vt_env_load/get/set/unset/lint). Parses the config file rather than sourcing it
                      — no executable syntax support, distinguishes 'literal' vs "expanded", enforces 0600 permissions.
                      ⚠ Never touch the config file directly with echo/sed.

frontend/
  index.html        — xterm.js multi-tab UI (search, session name editing, file upload)
  voice.js          — mic recording + TTS + notifications + Media Session
  manifest.json     — PWA manifest
  sw.js             — Service Worker
```
