# API Reference

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./API.ko.md)

Full list of REST/WebSocket endpoints served by the FarShell server (`server/main.py`).
See [README.md](./README.md) for an overview and [ARCHITECTURE.md](./ARCHITECTURE.md) for
the architecture.

**Auth:** If a password (`fsh password`) or token (`VT_AUTH_TOKEN`) is set, every
endpoint requires authentication. Humans authenticate with the `vt_session` cookie
issued after login; daemons/scripts authenticate with a `?token=xxx` query param or an
`Authorization: Bearer xxx` header. See the
[Security section of README.md](./README.md#security) for the full auth model.

---

## Sessions / PTY

| Method | Path | Description |
|--------|------|------|
| GET | `/api/sessions` | List sessions |
| POST | `/api/sessions` | Create a session (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{id}` | Delete a session |
| PATCH | `/api/sessions/{id}` | Rename a session (JSON: name) — also renames the tmux session (alphanumeric/dash/underscore only) |
| POST | `/api/watch/{id}` | Toggle output watching ON/OFF (JSON: enabled, timeout) |

## tmux

| Method | Path | Description |
|--------|------|------|
| GET | `/api/tmux/sessions` | List tmux sessions |
| POST | `/api/tmux/attach` | Attach to a tmux session (JSON: name) |
| POST | `/api/tmux/create` | Create a tmux session + auto-attach (JSON: name, cols, rows) |
| DELETE | `/api/tmux/kill/{name}` | Fully kill a tmux session |
| POST | `/api/tmux/open-on-mac` | Attach an existing tmux session in a new window on the server's (macOS) terminal (JSON: name). Returns 400 if the server isn't macOS |
| GET | `/api/tmux/preview/{name}?lines=20&ansi=1` | Capture recent tmux pane output for the Grid view |

## Voice

| Method | Path | Description |
|--------|------|------|
| POST | `/voice/input?session_id=X` | Voice → STT → session input |
| POST | `/voice/output` | Text → TTS → returns audio |
| POST | `/voice/cancel` | Immediately stop playing TTS (barge-in) |
| POST | `/voice/local/start` | Start MacBook microphone recording |
| POST | `/voice/local/stop?session_id=X` | Stop recording → STT → session input |
| GET | `/voice/stt/status` | Check STT model readiness (does not load the model) |
| POST | `/voice/stt/preload` | Preload the STT model — removes first-input latency when turning voice mode on |
| POST | `/voice/stt/unload` | Unload the STT model — reclaims memory (~150MB) when turning voice mode off |

## Auth

| Method | Path | Description |
|--------|------|------|
| POST | `/api/auth` | Login — password (+ `otp` for a new device) or a one-time `ticket` → issues `vt_session`/`vt_device` HttpOnly cookies. 401 `otp_required`/`otp_invalid`, 429 `otp_locked` |
| GET | `/api/auth/status` | Whether auth is active / OTP is linked / this device is registered (accessible unauthenticated, no secrets included) |
| POST | `/api/auth/logout` | Clears the session only (device registration is kept) |

## Code Viewer / Diff (read-only)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/fs/roots` | List of browsable roots (default `~/GitHub`) |
| GET | `/api/fs/tree?path=X` | Directory listing. Excludes `.git`/`node_modules` etc. |
| GET | `/api/fs/file?path=X` | File content. Binaries return only `binary:true`; anything over 512KB is truncated |
| GET | `/api/git/status?repo=X` | Parsed result of `git status --porcelain` |
| GET | `/api/git/diff?repo=X[&file=Y][&staged=1]` | Raw `git diff` output. Protected paths (`.env`/`*.pem`/`id_rsa`, etc.) have their content redacted (`[content redacted — protected path]`) |

Paths that hit the deny list (`.env*`, `*.pem`, `id_rsa`, `.ssh/`, `.aws/`, etc.) are
redacted identically in both `/api/fs/file` and `/api/git/diff` — the check lives in
exactly one place, `server/fsguard.py`.

Non-read-only Git actions (for stage/commit in the code viewer):

| Method | Path | Description |
|--------|------|------|
| POST | `/api/git/stage` | Stage files (JSON: repo, files). Response is the updated status |
| POST | `/api/git/unstage` | Unstage — only reverts the index, leaves the working tree untouched (JSON: repo, files) |
| POST | `/api/git/commit` | Commit staged changes (JSON: repo, message). Returns 400 if nothing is staged |

## Prompt Queue

| Method | Path | Description |
|--------|------|------|
| GET | `/api/queue` | List the queue |
| POST | `/api/queue` | Add to the queue (JSON: text, target). Cap of 50, returns 409 if exceeded |
| DELETE | `/api/queue/{id}` | Delete an item. `id=all` clears everything |
| POST | `/api/queue/{id}/unblock` | Resume an item blocked by safe_mode |
| POST | `/api/queue/run` | Manual drain — dispatch one item |

## Port Dashboard

| Method | Path | Description |
|--------|------|------|
| GET | `/api/ports[?fresh=1]` | List listening ports (3-second cache) |
| DELETE | `/api/ports/{port}[?pid=N]` | Kill the process. Returns 409 on a `pid` mismatch (the VT server itself, and cloudflared/tailscaled/sshd, cannot be killed) |
| POST | `/api/ports/{port}/expose` | Expose via a Cloudflare tunnel. Requires body `{"confirm":true}` (428 without it) |
| DELETE | `/api/ports/{port}/expose` | Tear down that port's tunnel |

## Web Push

| Method | Path | Description |
|--------|------|------|
| GET | `/api/push/key` | VAPID public key (for browser subscription) |
| POST | `/api/push/subscribe` | Register a subscription (JSON: subscription, label) |
| DELETE | `/api/push/subscribe` | Unsubscribe (JSON: endpoint) |
| POST | `/api/push/test` | Send a test notification |
| GET | `/api/push/status` | Subscription count / current origin / count of origin-mismatched subscriptions |

## Misc

| Method | Path | Description |
|--------|------|------|
| POST | `/api/upload?session_id=X` | Upload a file (multipart/form-data) |
| GET | `/api/download?path=X` | Download a file from the server |
| GET | `/api/capabilities` | Server capability info (TTS/STT/tunnel, etc.) |
| GET | `/api/workspace` | Fetch workspace sync state (tabs/UI state) |
| PUT | `/api/workspace` | Save workspace state |
| GET | `/api/agents` | Full list of active agents (claude, etc.) per tmux session |
| GET | `/api/agents/{name}` | Active agent info for a specific tmux session |
| GET | `/api/agent/status` | Tool-use state tracked via Claude Code hooks (for the Grid view's pulse/done badges) |
| POST | `/api/agent/event` | Endpoint called by the Claude Code Pre/Post/StopToolUse hooks |
| GET | `/api/safe-mode` | Whether prompt queue safe_mode is active |
| GET | `/api/tailscale/status` | Tailscale install/connection/IP/MagicDNS hostname |
| GET | `/api/tunnel/status` | Cloudflare tunnel (main) connection status |
| GET | `/api/notify/status` | Whether ntfy/Telegram notifications are configured |
| POST | `/api/notify/test` | Send a test notification (JSON: title, message, priority) |
| POST | `/api/notify/client-event` | For the tmux client-attached/detached hook only — surfaces SSH connections |
| POST | `/api/clipboard/push` | For `clipboard_daemon.py` only — broadcasts to `/ws-notify` clients |

## WebSocket

| Path | Description |
|------|------|
| `/ws/{id}` | Terminal WebSocket (xterm.js connection). `?e2e=1` for E2E encryption |
| `/ws-notify` | Receives job-completion notifications |
| `/ws-preview/{name}` | Pushes tmux pane output for the Grid view |
| `/ws-agent` | Pushes agent activity state |
| `/ws-workspace` | Pushes workspace changes |
