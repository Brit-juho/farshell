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
| POST | `/api/sessions/{id}/keys` | Write text straight into the PTY (JSON: text) — same privilege as typing in the terminal WS. 404 for an unknown session. Used by the mobile fleet home's inline approval buttons (N38) |
| POST | `/api/watch/{id}` | Toggle output watching ON/OFF (JSON: enabled, timeout) |

## tmux

| Method | Path | Description |
|--------|------|------|
| GET | `/api/tmux/sessions` | List tmux sessions |
| POST | `/api/tmux/attach` | Attach to a tmux session (JSON: name) |
| POST | `/api/tmux/create` | Create a tmux session + auto-attach (JSON: name, cols, rows, cwd) |
| DELETE | `/api/tmux/kill/{name}` | Fully kill a tmux session |
| POST | `/api/tmux/open-on-mac` | Attach an existing tmux session in a new window on the server's (macOS) terminal (JSON: name). Returns 400 if the server isn't macOS |
| GET | `/api/tmux/preview/{name}?lines=20&ansi=1` | Capture recent tmux pane output for the Grid view |
| GET | `/api/tmux/clients?session=X&me=Y` | Clients attached to a session (C1). `me` is the caller's web session id — the server derives its tty and marks `is_me` |
| POST | `/api/tmux/detach-client` | Detach one client (C1, JSON: tty, me). Detaching yourself returns 400 — there'd be no way back |
| POST | `/api/tmux/clients/solo` | "Keep only this screen" (C2, JSON: session, me). The client never sends a tty to keep — the server derives it. Returns 400 if it can't, rather than detaching everything |

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
| POST | `/api/auth/elevate` | Re-verify password (+ OTP if linked) → reissues `vt_session` with a 15-minute `elev` claim (N31). Session-scoped, not device-scoped. 401 `invalid`/`otp_required`/`otp_invalid`, 429 `*_locked` |
| GET | `/api/auth/elevation` | Current session's elevation state (`elevated`, `elevated_until`) + `unused: true` / `unused_reason: "ADR-27"` — no path requires elevation today. Read-only (Settings → Security) |
| GET | `/api/devices` | Registered devices (`~/.vt/devices.json`): id (first 8 chars only), label, added_at, last_seen, `current` for the requesting device. Read-only — registration/revocation is `fsh device` |

## Code Viewer / Diff (read-only)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/fs/roots` | List of browsable roots (default `~/GitHub`) |
| GET | `/api/fs/tree?path=X` | Directory listing. Excludes `.git`/`node_modules` etc. |
| GET | `/api/fs/search?q=X` | Fuzzy filename search (N5/N40 — command palette `/` mode). `path=` scopes to a subtree, max 50 results |
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
| GET | `/api/git/log?repo=X[&file=Y]` | Recent commit list |
| GET | `/api/git/show?repo=X&rev=Y` | Diff of one commit |

## Git accounts & binding (N30, elevated session required except GET)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/git/accounts` | List accounts — token is never sent, only `auth.masked` (`ghp_…3f2a`) |
| POST | `/api/git/accounts` | Requires elevation (`require_elevated`). Verifies the PAT against GitHub/GitLab `GET /user` before storing, auto-filling `login` |
| DELETE | `/api/git/accounts/{account_id}` | Requires elevation. Also removes any bindings pointing at this account |
| GET | `/api/git/binding?repo=X` | Resolved account id for a repo: `byRepo` → remote URL's `host/owner` in `byHostOrg` → exactly one account on that host → `null` |
| PUT | `/api/git/binding` | Requires elevation. Body `{repo, account_id}` — sets the explicit `byRepo` binding |

## Scrollback Search

| Method | Path | Description |
|--------|------|------|
| GET | `/api/search/scrollback?q=X[&sessions=all\|id1,id2]` | Greps each session's live scrollback ring buffer (N40 — command palette `~` mode; same buffer WS reconnect uses, no separate store). `sessions=all` (default) searches every open session; a comma list restricts to those session ids. Returns up to 50 matches total (20 per session), each with 3 lines of context before/after |

## Prompt Queue

| Method | Path | Description |
|--------|------|------|
| GET | `/api/queue` | List the queue |
| POST | `/api/queue` | Add to the queue (JSON: text, target). Cap of 50, returns 409 if exceeded |
| DELETE | `/api/queue/{id}` | Delete an item. `id=all` clears everything |
| POST | `/api/queue/{id}/unblock` | Resume an item blocked by safe_mode |
| POST | `/api/queue/run` | Manual drain — dispatch one item |

## Worktrees (N8/N44)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/worktrees` | List every discovered git worktree across all repos under `VT_BROWSE_ROOTS` (+ `~/.worktrees`), with session mapping and diff summary. 5s cache |
| GET | `/api/worktrees/precheck?repo&base` | Lockfile-mismatch banner check ahead of the create dialog (`warnings: ["lockfile_mismatch"]`) |
| POST | `/api/worktrees` | Create a worktree (`git worktree add` + node_modules/`.env`/port-band/agent steps). Rolls back on failure |
| DELETE | `/api/worktrees/{id}` | Remove a worktree. 409 + `dirty:true` if it has changes unless `force:true`; `killSessions:true` also kills its tmux sessions |
| POST | `/api/worktrees/{id}/open` | Attach to its existing tmux session, or create `wt-<repoName>-<branch>` if none exists |

## Port Dashboard

| Method | Path | Description |
|--------|------|------|
| GET | `/api/ports[?fresh=1]` | List listening ports (3-second cache) |
| DELETE | `/api/ports/{port}[?pid=N]` | Kill the process. Returns 409 on a `pid` mismatch (the VT server itself, and cloudflared/tailscaled/sshd, cannot be killed) |
| POST | `/api/ports/{port}/expose` | Expose via a Cloudflare tunnel. Requires body `{"confirm":true}` (428 without it) |
| DELETE | `/api/ports/{port}/expose` | Tear down that port's tunnel |
| GET | `/api/tunnel/list` | N22 — summary of every currently open tunnel (main + `fsh tunnel expose`d ports) for the Ports tab's "exposed" section. Unexposing reuses `DELETE /api/ports/{port}/expose` above |

## Prompt Snippets

| Method | Path | Description |
|--------|------|------|
| GET | `/api/snippets` | List saved prompt snippets |
| GET | `/api/snippets/project?cwd=` | Resolve a cwd to its snippet project key (repo top, or null) |
| POST | `/api/snippets` | Add a snippet (JSON: text, label, scope: global\|project, cwd) |
| DELETE | `/api/snippets/{id}` | Delete a snippet |

## Web Push

| Method | Path | Description |
|--------|------|------|
| GET | `/api/push/key` | VAPID public key (for browser subscription) |
| POST | `/api/push/subscribe` | Register a subscription (JSON: subscription, label) |
| DELETE | `/api/push/subscribe` | Unsubscribe (JSON: endpoint) |
| POST | `/api/push/test` | Send a test notification |
| GET | `/api/push/status` | Subscription count / current origin / count of origin-mismatched subscriptions |

## Files (N19)

`/tmp/vt-uploads` (path-based) has been replaced by `~/.vt/files/` (id-based).
Old uploads are migrated automatically on server startup. There is no path-based
download API — `/api/files/{id}/download` is id-only by design.

| Method | Path | Description |
|--------|------|------|
| POST | `/api/upload?session_id=X` | Upload a file (multipart/form-data) → stored in file_store, response includes `id` |
| GET | `/api/files?filter=all\|shared\|expiring` | List stored files |
| GET | `/api/files/{id}/download` | Download by id (`attachment` + `nosniff` + `no-store`) |
| DELETE | `/api/files/{id}` | Delete a stored file |
| POST | `/api/files/{id}/insert` | Type the file's path into a tmux session's pane (JSON: `session`), no Enter |
| POST | `/api/files/{id}/share` | **Elevated.** Issue a share link (JSON: `mode:"device"\|"pin"`, `ttl`, `once`, `pin?`) → `{share, token, url}` |
| DELETE | `/api/files/{id}/share/{shareId}` | **Elevated.** Cancel a share — the URL 404s immediately even with a still-valid signature |

Public download entry point (not under the api prefix, and not behind the usual session/token auth —
`server/routes/share.py` does its own token/mode verification):

| Method | Path | Description |
|--------|------|------|
| GET | `/s/{token}` | Share download. `device` mode: serves if `vt_device`+`vt_session` are valid, else 302 to `/?next=/s/{token}`. `pin` mode: PIN entry page unless a valid one-time download cookie is present |
| POST | `/s/{token}/pin` | Verify a PIN (form: `pin`) → 60s one-time download cookie + redirect. 5 failed attempts cancels the share |

## Misc

| Method | Path | Description |
|--------|------|------|
| GET | `/api/capabilities` | Server capability info (TTS/STT/tunnel/version, etc.) |
| GET | `/api/workspace` | Fetch workspace sync state (tabs/UI state) |
| PUT | `/api/workspace` | Save workspace state |
| GET | `/api/device-settings` | Fetch this device's settings (N3 — scoped by the `vt_device` cookie, `local` without it) |
| PUT | `/api/device-settings` | Save this device's settings |
| GET | `/api/agents` | Full list of active agents (claude, etc.) per tmux session |
| GET | `/api/agents/{name}` | Active agent info for a specific tmux session |
| GET | `/api/agents/coverage` | N9/N45 — per-CLI approval-wait detection coverage: `[{cli, path:"hook"\|"pty"\|"none", patternLines, states, trust:"high"\|"mid"\|"low"}]`, read live from `detect/*.toml` |
| GET | `/api/agent/status` | Agent state machine (A1) — `idle/working/waiting/done` per session, with TTL sweeping |
| POST | `/api/agent/report` | Pane self-report (A2) — for agents without hooks (`fsh pane report`) |
| GET | `/api/hooks/status` | Claude Code hook registration status (A0/S4) — `{ok, events:{PreToolUse,PostToolUse,Stop}}` |
| GET | `/api/usage` | Usage snapshot (U1) — `{available:false, reason}` when no source. Tokens/credentials are excluded by a field whitelist |
| GET | `/api/usage/counter` | N41 — CounterProvider (unlimited, e.g. local LLM) snapshot. `?since=<epoch>` filters totals; the 7-day sparkline is a fixed window regardless |
| POST | `/api/usage/counter` | N41 — record a usage event (`{model, tokens, seconds}`) — same store as `fsh usage add` |
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
