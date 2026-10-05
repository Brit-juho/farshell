# API Reference

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
| GET | `/api/sessions?screen=S` | List sessions (each with `tmux_name` and `origin`, same values as `/api/tmux/sessions`); `screen` limits tmux web attachments to one browser tab. Without it, lists all sessions for CLI/legacy callers |
| POST | `/api/sessions` | Create a session (JSON: cols, rows, name) |
| DELETE | `/api/sessions/{session_id}` | Delete a session |
| PATCH | `/api/sessions/{session_id}` | Rename a session (JSON: name) — also renames the tmux session (alphanumeric/dash/underscore only) |
| POST | `/api/sessions/{session_id}/keys` | Write text straight into the PTY (JSON: text) — same privilege as typing in the terminal WS. 404 for an unknown session. Used by the mobile fleet home's inline approval buttons (N38) |
| POST | `/api/sessions/{session_id}/paste` | Paste-only entry point (JSON: text) — the server decides bracketed-paste markers, newline normalization, and unsafe control-char stripping instead of the browser guessing (N24). Same function as the WS `{"type":"paste"}` message. 404 for an unknown session |
| POST | `/api/watch/{session_id}` | Toggle output watching ON/OFF (JSON: enabled, timeout) |

## tmux

| Method | Path | Description |
|--------|------|------|
| GET | `/api/tmux/sessions` | List tmux sessions. Each item carries `origin` — where the session was created: `local` (this UI, incl. worktree open), `peer` (another Mac via `/api/peer/sessions/create`), `agent` (reserved — no creation path exists yet), `external` (no record: `fsh` CLI or plain tmux). Read from tmux option `@fsh_origin` in the same `list-sessions` call; the same field is in `/ws-workspace` `tmux_sessions`. Also `webScreens` (web screens attached — PTYs this server holds for the session, one per browser tab/phone) and `otherClients` (`attached` minus `webScreens`, floor 0: non-web attaches such as a Mac terminal); both come from the same `list-sessions` call plus the session store, no extra tmux call |
| POST | `/api/tmux/attach` | Attach to a tmux session (JSON: name, optional 32-hex `screen`). Each screen gets its own PTY; the same screen reuses its attachment after reload |
| POST | `/api/tmux/create` | Create a tmux session + auto-attach (JSON: name, cols, rows, cwd, optional `screen`) |
| DELETE | `/api/tmux/kill/{tmux_name}` | Fully kill a tmux session |
| POST | `/api/tmux/kick` | tmux hooks only — nudges the session-list watcher. Needs the `X-VT-Kick` secret the server makes at start (not the login); 204, or 403 without it |
| POST | `/api/tmux/open-on-mac` | Attach an existing tmux session in a new window on the server's (macOS) terminal (JSON: name). Returns 400 if the server isn't macOS |
| GET | `/api/tmux/preview/{tmux_name}?lines=20&ansi=1` | Capture recent tmux pane output for the Grid view |
| GET | `/api/tmux/clients?session=X&me=Y` | Clients attached to a session (C1). Each web tab has a distinct tmux client. `me` is that tab's web session id — the server derives its tty and marks `is_me` |
| POST | `/api/tmux/detach-client` | Detach one client (C1, JSON: tty, me). Detaching yourself returns 400 — there'd be no way back |
| POST | `/api/tmux/clients/solo` | "Keep only this screen" (C2, JSON: session, me). The client never sends a tty to keep — the server derives it. Returns 400 if it can't, rather than detaching everything |

## Voice

| Method | Path | Description |
|--------|------|------|
| POST | `/voice/input?session_id=X` | Voice → STT → session input. Body is the recording (`Content-Type`: `audio/webm` · `audio/mp4` · `audio/ogg` · `audio/wav`). Optional `X-Voice-Id` header: a re-sent recording with the same id is answered from memory (`duplicate: true`) and typed into the terminal only once. Returns `{text, injected, session_id, timing: {decode_ms, wait_ms, load_ms, stt_ms, total_ms}}`; `error` when the model is not ready (nothing is ever downloaded here); 429 when too many requests are queued |
| POST | `/voice/output` | Text → TTS → returns audio |
| POST | `/voice/cancel` | Immediately stop playing TTS (barge-in) |
| POST | `/voice/local/start` | Start MacBook microphone recording |
| POST | `/voice/local/stop?session_id=X` | Stop recording → STT → session input |
| GET | `/voice/stt/status` | STT state — selected model, loaded model/engine, memory, current setup job, last error. Does not load a model |
| GET | `/voice/stt/catalog` | Models this server can run (recommended one first, readiness, requirements, measured numbers) and the ones it cannot (with the reason), plus usable engines and the current selection. `?custom=<custom:engine:owner/repo>` adds that custom model |
| POST | `/voice/stt/setup` | Start ONE preparation step in the background — body `{"model": "<id>", "step": "model" \| "req:<requirement id>"}`. The settings UI calls this only after the user confirms the download. 400 for unknown models/steps or items the app does not install itself (pip packages), 409 while another step runs. Progress is `setup` in `GET /voice/stt/status` |
| GET | `/voice/notify/clients` | Number of web clients attached to `/ws-notify` — the Stop hook uses it for "don't notify while the web UI is open" |
| POST | `/voice/stt/preload` | Load the selected model into memory (never downloads; `error` explains what is missing) |
| POST | `/voice/stt/unload` | Unload the model and give the memory back (a llama.cpp engine process is terminated) |
| GET | `/voice/stt/model` | Downloaded models (size in bytes, which one is selected) and engine programs the app downloaded |
| DELETE | `/voice/stt/model` | Delete a downloaded model — body `{"model_id": "..."}` — or the app-downloaded llama.cpp — body `{"binary": "llama-server"}`. Shared weight files outside the repo folder are removed too (unless another model uses them); returns `freed_bytes` |

## Auth

| Method | Path | Description |
|--------|------|------|
| POST | `/api/auth` | Login — password (+ `otp` for a new device) or a one-time `ticket` → issues `vt_session`/`vt_device` HttpOnly cookies. Optional `remember` (`90d` default / `24h` / `1h`) sets a **new** device's lifetime; a registered device ignores it, a ticket follows its own bound lifetime. The session never outlives its device (`min(24h, device remaining)`). 400 `invalid_remember`, 401 `otp_required`/`otp_invalid`, 429 `otp_locked` |
| GET | `/api/auth/status` | Whether auth is active / OTP is linked / this device is registered and not expired (accessible unauthenticated, no secrets included). The login screen shows the lifetime choice only when `device_known` is false |
| POST | `/api/auth/logout` | Clears the session only (device registration is kept) |
| POST | `/api/auth/elevate` | Re-verify password (+ OTP if linked) → reissues `vt_session` with a 15-minute `elev` claim (N31). Session-scoped, not device-scoped. 401 `invalid`/`otp_required`/`otp_invalid`, 429 `*_locked` |
| GET | `/api/auth/elevation` | Current session's elevation state (`elevated`, `elevated_until`) + `unused: true` / `unused_reason: "ADR-27"` — no path requires elevation today. Read-only (Settings → Security) |
| GET | `/api/devices` | Registered devices (`~/.vt/devices.json`): id (first 8 chars only), label, added_at, last_seen, `current` for the requesting device, `expires_at`/`expired`/`kind` (`persistent`/`temporary`)/`method`, and display-only `os`/`browser`/`ip_last`/`via` (`tunnel`/`tailscale`/`lan`/`local`/`direct`). Expired devices stay listed for 7 days. The raw UA is not returned. Read-only — registration/revocation is `fsh device` |

## Code Viewer / Diff (read-only)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/fs/roots` | List of browsable roots (default `~/GitHub`) |
| GET | `/api/fs/tree?path=X` | Directory listing. Excludes `.git`/`node_modules` etc. |
| GET | `/api/fs/search?q=X` | Fuzzy filename search (N5/N40 — command palette `/` mode). `path=` scopes to a subtree, max 50 results |
| GET | `/api/fs/file?path=X` | File content. Binaries return only `binary:true`; anything over 512KB is truncated. Images also carry `image:true`, `mime`, and `too_large` — the bytes come from `/api/fs/raw` below |
| GET | `/api/fs/raw?path=X` | [T3] Raw **image** bytes for inline preview. Same browse boundary as `/api/fs/file`, plus a type allowlist: png/jpeg/gif/webp/bmp/ico only, and only when extension **and** magic bytes agree. SVG, HTML and PDF are excluded — served same-origin they are scriptable documents, i.e. XSS. Over 8MB → 413; not an image → 403. Sent with `nosniff` and `Content-Disposition: inline` |
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
| GET | `/api/git/show?repo=X&sha=Y` | Diff of one commit |

## Git accounts & binding (N30, elevated session required except GET)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/git/accounts` | List accounts — token is never sent, only `auth.masked` (`ghp_…3f2a`) |
| POST | `/api/git/accounts` | Requires elevation (`require_elevated`). Verifies the PAT against GitHub/GitLab `GET /user` before storing, auto-filling `login` |
| DELETE | `/api/git/accounts/{account_id}` | Requires elevation. Also removes any bindings pointing at this account |
| GET | `/api/git/binding?repo=X` | Resolved account id for a repo: `byRepo` → remote URL's `host/owner` in `byHostOrg` → exactly one account on that host → `null` |
| PUT | `/api/git/binding` | Requires elevation. Body `{repo, account_id}` — sets the explicit `byRepo` binding |

## MCP servers (97, stage 1 — toggling requires an elevated session)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/mcp?worktree=<id>` | MCP servers defined across claude/codex/agy, for the global scope plus that worktree's local scope. `worktree` may also be `session:<FarShell session id>`: the server asks tmux for that session's active-pane cwd, climbs to the git root, and checks it against the browse roots (outside → global only; the client never sends a path). The response's `worktree.session` names the tmux session used. Every entry carries `level` (`user` / `local` / `project` — who it applies to, for display and filtering) next to `scope` (`global` / `local`, the write-routing key, unchanged): Claude's `~/.claude.json` project block is `local`, `.mcp.json` is `project`. **Nothing is cached or stored** — the real config files are read on every call, so edits made in a terminal show up immediately. Values are never returned: `env`/`headers` come back as `{key, ref, literal}` only. Also returns `facts` (per-tool hot-reload behaviour) and `errors` (per-file parse failures, which never abort the whole scan). `url_host` (http/sse only, ADR-48) is the **hostname alone** — the verify screen names it when asking before a network request; the full URL is never returned because query strings can carry tokens |
| GET | `/api/mcp/skills?worktree=<id>` | Direct Claude and Codex skills at user and project locations. `session:<id>` uses the focused pane cwd; Codex scans `.agents/skills` from cwd through the repository root. Rows carry an opaque ID, tool, source, level, enabled state when supported, and writable capability. Plugin bundled skills remain in `/api/mcp/plugins` |
| POST | `/api/mcp/skills/toggle` | Requires elevation. Body `{id, enabled, worktree?}`. Codex local skills are enabled or disabled by editing `[[skills.config]]` in its global TOML; unsupported tools return 409 |
| POST | `/api/mcp/skills/preview` | Body `{id, tool, scope, worktree?}`. Returns a destination, file count and token bound to the source contents and destination; refuses an existing name or symlinked source |
| POST | `/api/mcp/skills/import` | Requires elevation. Body `{id, tool, scope, worktree?, token}`. Rechecks the preview, copies a scanned skill to the selected tool's user or current project directory, and atomically exposes the completed copy without overwriting an existing name |
| GET | `/api/mcp/library` | FarShell's saved MCP definitions only (not installed CLI entries), as metadata: ID, name, source tool, transport, required environment variable names, compatible tools, configured targets and `audience` (`common` or a single tool). Definition values never leave the server |
| GET | `/api/mcp/library/candidates?worktree=<id>` | Existing tool config definitions that can be saved to the FarShell library, with opaque IDs, tool, scope, eligible/saved flags and a reason for entries needing manual work. No commands, arguments, URLs or literal secret values are returned |
| POST | `/api/mcp/library/import-candidates` | Requires elevation. Body `{ids: [...], worktree?}` (up to 64). Re-reads selected CLI definitions and saves only portable ones; returns per-item status and count. Existing tool settings are not changed |
| POST | `/api/mcp/library/save` | Requires elevation. Body `{tool, scope, name, worktree?}`. Saves a server from its real CLI configuration into `~/.vt/mcp.json` after refusing literal env/header values, OAuth and URLs with credential-bearing parts |
| POST | `/api/mcp/library/targets` | Requires elevation. Body `{id, targets: [tool, ...]}`. Sets the saved MCP's management targets to a nonempty subset of compatible tools; existing entries without targets default to all compatible tools |
| POST | `/api/mcp/library/preview` | Body `{id, tool, scope, worktree?}`. Returns destination, required variable names and a token bound to the saved item and current destination file. Existing same-name entries return 409 |
| POST | `/api/mcp/library/apply` | Requires elevation. Body `{id, tool, scope, worktree?, token}`. Rechecks the preview token before writing; a changed file or a name conflict returns 409. Results are re-read after writing |
| POST | `/api/mcp/toggle` | Requires elevation. Body `{tool, name, enabled, scope?, worktree?, shared?}` — **declares the target state rather than flipping**, so retrying the same request is safe. Returns `status`: `ok` (written and verified), `unknown` (written but verification failed — 200, never reported as success), or `failed` (409, file left untouched) |
| POST | `/api/mcp/toggle-batch` | Requires elevation. Body `{tool, scope?, worktree?, changes: [{name, enabled, shared?}, ...]}`, up to 64 items. Same target-state contract as the single toggle, but for `claude` all changes to `~/.claude.json` land in **one** read→verify→write cycle instead of one per item (ADR-37) — codex/agy/opencode still apply per-item internally. Returns `{ok, results: [{name, status, ...}, ...]}`; `ok` is false if any item didn't reach `status:"ok"` |
| POST | `/api/mcp/group` | Requires elevation. Body `{tag, enabled, worktree?, tool?}` — brings tagged servers in the selected tool to the target state (not a flip). Omit `tool` for the legacy all-tool behaviour; the UI always sends a tool. Entries already in the target state are never opened at all (`results[].skipped`). Returns `status`: `ok` / `partial` (some failed — **`results` names exactly which**) / `unknown` / `failed` (409). A tag with no tagged servers returns 409 |
| POST | `/api/mcp/tags` | Body `{name, tags:[…]}` — replaces the group tags on one server name wholesale; an empty array means "no tags". **Does not require elevation**: tags are a FarShell-side label and never touch a CLI config file (`~/.vt/mcp.json`, 0600). Tags attach to the **server name**, not to each per-tool/per-scope entry |
| POST | `/api/mcp/tags/rename` | Body `{from, to}` — renames a tag across every server at once, so a typo can't split a group in two and leave "turn group on" flipping only half of it. Merges if the target name already exists |
| POST | `/api/mcp/tags/delete` | Body `{tag}` — removes the tag from every server. The servers themselves are untouched |
| GET | `/api/mcp/creds` | Stored credentials plus the locations of references we planted (`refs`). **The secret is never returned** — only `masked` (`sk-1…9f2a`). `refs` is the basis for bulk revocation (§2-5): it records the *fact that we wrote*, not a name convention, so tracking survives the user overriding an env var name |
| POST | `/api/mcp/creds` | Requires elevation (this path receives a secret). Body `{server, key, secret, env?, fingerprint?}`. A blank `env` defaults to `FSH_MCP_<SERVER>_<KEY>`. The value is stored **in plaintext** in `~/.vt/mcp.json` (0600) — not dressed up as encrypted (§2-1: unattended operation and at-rest protection cannot both hold). The real defence is never writing values into CLI config files |
| POST | `/api/mcp/creds/delete` | Requires elevation. Body `{id}` |
| POST | `/api/mcp/deploy` | Requires elevation. Body `{name, defn, tool, scope?, shared?, env_map?, worktree?}` — brings one definition into that tool/scope. Values never travel: the fields named by `env_map` become references instead (Claude `${VAR}`; Codex drops the value slot and lists the name under `env_vars` — §0-3). **If any value would remain after substitution it returns 409 and never opens the file.** Codex targets accept a restricted set of fields, add one TOML table while preserving the rest of the file, and require a trusted project for local scope. Existing different definitions are never overwritten |
| GET | `/api/mcp/plugins` | Installed plugins for Claude Code and Codex. Claude's installed list comes from `~/.claude/plugins/installed_plugins.json` (user installs everywhere, project/local installs only at their `projectPath`), and on/off from `enabledPlugins` in `~/.claude/settings.json` (`level:user`), `<project>/.claude/settings.json` (`level:project`, returned only when set, `writable:false`) and `<project>/.claude/settings.local.json` (`level:local`). Each row carries `effective` (local > project > user), `installed`, `writable`, `version`, `skills`, `bundles_mcp`; Codex is read from `codex plugin list --json` and includes `version`, bundled `skills`/`skill_count`, `bundles_mcp`, and auth policy when available. **"absent" and "false" are different**: the former was never explicitly configured and follows the tool's default, the latter was explicitly turned off (`explicit` distinguishes them). CLI/config parse failures are returned in `errors` without hiding the other tool's plugins. **`?refresh=1`** (ADR-48): the Codex list queries remote marketplaces (19 s measured), so the installed list is cached in memory for 10 minutes (30 s timeout, concurrent calls share one run, a failed refresh keeps the previous list); `refresh=1` drops the cache. On/off is read from `config.toml` on every call, never from the cache |
| POST | `/api/mcp/plugins/toggle` | Requires elevation (a plugin can bundle MCP servers, so this changes what the agent can do). Body `{name, enabled, tool?, scope?, worktree?}`. Supports Claude Code (`scope:global` → `~/.claude/settings.json`, `scope:local` → `<project>/.claude/settings.local.json`; `scope:project` is refused with 409 because that file is committed and shared with the team; a new `settings.local.json` that git does not ignore comes back with a `warning`) and globally installed Codex plugins; Codex changes only `[plugins."<id>"].enabled` while preserving the rest of `config.toml`. **It never installs** — an unknown name returns 409, because flipping `enabled` on an uninstalled plugin does nothing and claiming otherwise would be a lie |
| POST | `/api/mcp/servers/{scope}/{name}/verify` | Requires elevation (P3-2, ADR-36) — a one-shot health check that actually spawns the configured command (stdio, its own process group, killed+group-killed when done) or makes one network request (http/sse). Body `{tool, worktree?, confirm_network?}`. stdio never needs `confirm_network`; http/sse is refused without it (a network call to a URL from the config file). Returns `{state: "connected"\|"error"\|"unverified", tool_count, error, checked_at}` (+ `missing` on `unverified`) — **never stored**, the caller only holds it for the session. 404 if the server isn't found, 400 for a bad `scope`/`tool`. **`unverified`** (ADR-46): a value the server needs could not be found here (a Codex `env_vars` name not in this server's environment, or an unresolvable `${VAR}` reference) — nothing is spawned, and `missing` lists the variable **names** only. It is not a failure: the agent's own shell may have the variable. The spawn mirrors each CLI: Codex passes only its allowlist (`HOME`, `PATH`, … + `env_vars`) plus its literal `env`; agy passes `env` literally and inherits the whole environment |

## Scrollback Search

| Method | Path | Description |
|--------|------|------|
| GET | `/api/search/scrollback?q=X[&sessions=all\|id1,id2]` | Greps the live scrollback ring buffer of open sessions (N40 — command palette `~` mode). `sessions=all` (default) covers every open session; a comma list restricts to those ids. Up to 50 matches total (20 per session) with 3 lines of context. Each result carries `source: "live"` (fixed — kept for older peer hosts' response shape, ADR-43 removed the once-persisted-log alternative) |

## Prompt Queue

| Method | Path | Description |
|--------|------|------|
| GET | `/api/queue` | List the queue |
| POST | `/api/queue` | Add to the queue (JSON: text, target). Cap of 50, returns 409 if exceeded |
| DELETE | `/api/queue/{item_id}` | Delete an item. `id=all` clears everything |
| POST | `/api/queue/{item_id}/unblock` | Resume an item blocked by safe_mode |
| POST | `/api/queue/run` | Manual drain — dispatch one item |

## Worktrees (N8/N44)

| Method | Path | Description |
|--------|------|------|
| GET | `/api/worktrees?include_hidden` | List every discovered git worktree across all repos under `VT_BROWSE_ROOTS` (+ `~/.worktrees`), with session mapping, diff summary and `remote: {host, owner, name}`. 20s cache. Rail-hidden repos are filtered out; `include_hidden=1` keeps them with `hidden: true`. Also returns `truncated` (scan hit the 200-repo cap), `hiddenCount` and `roots` |
| GET | `/api/worktrees/precheck?repo=X&base=Y` | Lockfile-mismatch banner check ahead of the create dialog (`warnings: ["lockfile_mismatch"]`) |
| POST | `/api/worktrees` | Create a worktree (`git worktree add` + node_modules/`.env`/port-band/agent steps). Rolls back on failure |
| DELETE | `/api/worktrees/{wt_id}` | Remove a worktree. 409 + `dirty:true` if it has changes unless `force:true`; `killSessions:true` also kills its tmux sessions |
| POST | `/api/worktrees/hidden` | Hide or unhide a repo in the rail (`{path, hidden}`). Stored server-side in `~/.vt/repos.json` (2.1 D1; migrated once from the old `rail-repos.json`), so it applies on every device |
| POST | `/api/worktrees/{wt_id}/open` | Attach to its existing tmux session, or create `wt-<repoName>-<branch>` if none exists |
| GET | `/api/repos?include_hidden` | 2.1 D1 groundwork — the same worktrees grouped by repo (`{id, host, path, name, remote, hidden, worktrees: [...]}`). Not yet used by the rail; exists for the repo-as-tab work to build on |

## Port Dashboard

| Method | Path | Description |
|--------|------|------|
| GET | `/api/ports[?fresh=1]` | List listening ports (3-second cache). Internal proxy listeners (2.2) are hidden — see `docs/ref/tunnel.md` |
| DELETE | `/api/ports/{port}[?pid=N]` | Kill the process. Returns 409 on a `pid` mismatch (the VT server itself, cloudflared/tailscaled/sshd, and a port's own internal proxy listener, cannot be killed) |
| POST | `/api/ports/{port}/expose` | **Elevated** (2.2). Expose via a Cloudflare tunnel routed through FarShell's own reverse proxy. Body `{"confirm":true, "mode":"public"\|"session"\|"pin", "ttl":3600\|86400\|604800\|2592000}` — `mode` defaults to `session`, `ttl` to `3600`. 428 without `confirm`. Ports that are remote-control surfaces once public (Chrome DevTools 9222, Node inspector 9229, Docker API 2375/2376, ngrok agent API 4040–4043, cloudflared metrics 20241–20245, FarShell's own proxy range) return 409 `dangerous_port` with a `reason` unless the body also has `"force_danger": true` (the literal boolean). Response includes `pin` (shown once, only for `mode:"pin"`) and `share` (public view, no secret) |
| DELETE | `/api/ports/{port}/expose` | **Elevated.** Tear down that port's tunnel and share policy — a re-expose issues a new share id and PIN |
| POST | `/api/ports/{port}/share/pin` | **Elevated.** Reissue the PIN without touching the tunnel (URL survives). Also rotates the share id, so everyone who got in with the old PIN must enter the new one |
| PATCH | `/api/ports/{port}/share` | **Elevated.** Body `{"mode":...}` — change who can access without touching the tunnel. An actual change rotates the share id, so anyone admitted under the old mode must authenticate again |
| POST | `/api/ports/_proxy/sync` | Not elevated — tells the running server to (re)start listeners for whatever `port_share_store` currently says. `fsh tunnel expose/pin/mode` call this after writing the policy directly (CLI trust level, not the web session's elevation gate) |
| GET | `/api/tunnel/list` | N22 — summary of every currently open tunnel (main + `fsh tunnel expose`d ports), each with `mode`/`broken` (2.2). Unexposing reuses `DELETE /api/ports/{port}/expose` above |

## Prompt Snippets

| Method | Path | Description |
|--------|------|------|
| GET | `/api/snippets` | List saved prompt snippets |
| GET | `/api/snippets/project?cwd=` | Resolve a cwd to its snippet project key (repo top, or null) |
| POST | `/api/snippets` | Add a snippet (JSON: text, label, scope: global\|project, cwd) |
| DELETE | `/api/snippets/{item_id}` | Delete a snippet |
| PATCH | `/api/snippets/{item_id}` | Change a saved snippet's run mode (JSON: mode) — auto-run-in-new-section vs paste-only. Text/label can't be edited (delete and re-save) |

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
download API — `/api/files/{file_id}/download` is id-only by design.

| Method | Path | Description |
|--------|------|------|
| POST | `/api/upload?session_id=X` | Upload a file (multipart/form-data) → stored in file_store, response includes `id` |
| GET | `/api/files?filter=all\|shared\|expiring` | List stored files → `{items, quota:{used,max,ttl_days}}` (quota is always the whole store, not the filtered subset) |
| GET | `/api/files/{file_id}/download` | Download by id (`attachment` + `nosniff` + `no-store`) |
| GET | `/api/files/{file_id}/path` | Disk path of a stored file (dock file tab's "copy path"; id-only input, so no traversal surface) |
| DELETE | `/api/files/{file_id}` | Delete a stored file |
| POST | `/api/files/delete` | Bulk delete (JSON: `{ids:[...]}`, max 500) → `{ok, deleted:[...], missing:[...]}`. Not elevated — same reason single delete isn't (repeated single calls would bypass it anyway) |
| POST | `/api/files/{file_id}/insert` | Type the file's path into a tmux session's pane (JSON: `session`), no Enter |
| POST | `/api/files/{file_id}/send` | Copy the file to a remote host (JSON: `host`, optional `session`) and, with `session`, have that host type its own path into the pane. Ordinary login auth here; the outbound leg carries the peer signature. Re-sending the same file is skipped by origin |
| POST | `/api/files/{file_id}/share` | **Elevated.** Issue a share link (JSON: `mode:"device"\|"pin"`, `ttl`, `once`, `pin?`) → `{share, token, url}` |
| DELETE | `/api/files/{file_id}/share/{share_id}` | **Elevated.** Cancel a share — the URL 404s immediately even with a still-valid signature |

Public download entry point (not under the api prefix, and not behind the usual session/token auth —
`server/routes/share.py` does its own token/mode verification):

| Method | Path | Description |
|--------|------|------|
| GET | `/s/{token}` | Share download. `device` mode: serves if `vt_device`+`vt_session` are valid, else 302 to `/?next=/s/{token}`. `pin` mode: PIN entry page unless a valid one-time download cookie is present |
| POST | `/s/{token}/pin` | Verify a PIN (form: `pin`) → 60s one-time download cookie + redirect. 5 failed attempts cancels the share |
| GET | `/p/authorize?share=&next=` | **2.2** — cross-origin handshake for an exposed port's `session` mode (`docs/ref/tunnel.md`). Verifies login + registered device on the **main** origin, then redirects to the exposed host's `/__fsh/cb` with a 60-second one-time ticket bound to that share and device. Takes no destination parameter from the client — the target URL is looked up from the tunnel registry by `share` id, so there is no open-redirect surface. Since 2026-10 this is **the entry point** for a `session` share: the exposed host no longer redirects cookie-less visitors here (it shows a notice instead, so the main URL isn't revealed), and the ports panel / `fsh tunnel expose` link to this route |

## Multi-host peering (N7/N39, stage 1)

Endpoints another Mac's FarShell server calls. Deliberately a **separate namespace**:
a peer credential reaches only what is defined here, never the rest of the API.
`TokenAuthMiddleware` skips the /api/peer prefix (a peer has no browser session cookie);
`server/routes/peer.py` verifies HMAC signatures itself.

Auth headers on every call except `pair`: `X-Peer-Id`, `X-Peer-Ts`, `X-Peer-Nonce`,
`X-Peer-Sig` = `HMAC(secret, "METHOD\npath\nts\nnonce")`. **The secret is never sent.**
60-second window, one-time nonce (replay-blocked), and the signature is bound to
method+path so a `view` GET signature can't be reused on a `control` POST.

| Method | Path | Description |
|--------|------|------|
| POST | `/api/peer/pair` | Redeem a one-time pairing ticket (JSON: ticket, id, label, version) → per-connection secret. The only unsigned peer route — the ticket is the auth, since no shared secret exists yet. Rejects reserved ids (`local`/`self`/`me`) **without consuming the ticket** |
| GET | `/api/peer/ping` | Signed liveness check → id, label, version, `serverTime` (the caller derives clock skew from it), and the caller's granted level |
| GET | `/api/peer/sessions` | Signed, `view` level — this host's tmux sessions + agent status. Each item carries `origin` (`local`/`peer`/`agent`/`external`, the same values and `@fsh_origin` source as `GET /api/tmux/sessions`; an older peer omits it and the receiver treats the session as local). **Never relays other peers' sessions** (hop 0): A↔B mutual pairing would otherwise recurse A→B→A |
| POST | `/api/peer/input` | Signed, **`control` level** — type text into a tmux session on this host (JSON: `session`, `data`, optional `enter`). Default types without Enter (same contract as file insert); `enter: true` presses it, which is what the prompt queue needs (A3) — the caller says which, the server never guesses. A `view` peer gets 403 with the exact command to enable it |
| WS | `/api/peer/ws/{tmux_name}` | Signed, `view` streams output; input is applied only at `control` level. Opened by the **other server's proxy**, never by a browser (a browser WebSocket can't send the signature headers). Each connection gets its own PTY on this host and it is destroyed on disconnect — sharing the owner's PTY would make the two screens fight over the terminal size |
| ANY | `/api/peer/relay/{path}` | Host switch, receiving side (2026-09-28). Runs **this host's own** `/{path}` in-process for the peer and streams the response back (tagged `x-peer-relay: 1`). Signed over path **including the query** plus a **required** body hash; **`control` level only**; `{path}` must be in the allowlist in `server/relay_policy.py` (login/devices/OTP, host and peer management, share links, port expose, git accounts, MCP writes, push, clipboard, voice and hook events are never relayed → 403 `relay_forbidden`). Mutations are audited; GETs are not (polling would drown the log) |
| WS | `/api/peer/relay-ws/{path}` | Same, for `/ws/{id}`, `/ws-agent`, `/ws-workspace`, `/ws-preview/{name}`. Closes 4401 (bad signature) / 4403 (level or allowlist). Re-checks the grant every 30s and closes 4401 if it was revoked or dropped to `view` |
| POST | `/api/peer/file` | Signed **with a body hash** (`X-Peer-Body`), **`control` level** — raw bytes in, stored in this host's file store; with `X-Peer-File-Session` the path is typed into that pane (no Enter). Re-sends of the same file are deduped by origin (sender host id + their file id), not by content hash. Over this host's `VT_MAX_UPLOAD_MB` → 413 |
| POST | `/api/peer/agent/message` | Signed **with a body hash**, **`control` level plus the per-peer agent switch** (`fsh host allow-agent <id>`; without it 403 `agent_messages_off`). JSON `{id, conv, hop, from, to, text, replyTo}` — a message from the other Mac's agent to session `to` here. Unknown session → 404. The sender host comes from the grant, never from the body. The message is queued (prompt queue, delivered after the agent's stop hook or at once if it is known idle); hop limit 10 per conversation (429 `hop_limit`, counted from this side's own records too) and 20/min per sender host (429) |
| GET | `/api/peer/clients?session=&screen=` | Signed, `view` level — clients attached to that tmux session on this host. `screen` is the token the caller chose when opening the WS; the server only ever resolves it as the PTY `peer-<caller id>-<screen>`, so **the prefix makes it impossible to claim someone else's screen** |
| POST | `/api/peer/clients/detach` | Signed, **`control` level** — detach one client (JSON: `tty`, `screen`). Detaching your own screen (identified by `screen`) is refused with 400 |
| POST | `/api/peer/clients/solo` | Signed, **`control` level** — "keep only this screen" (JSON: `session`, `screen`). If `screen` doesn't resolve to a tty, **nothing is detached** and it returns 400 — detaching everything has no way back |
| GET | `/api/peer/search?q=` | Signed, `view` level — scrollback search on this host (same caps as the local `/api/search/scrollback`). `session_id` is stripped from the results: it only means something inside this host, and the caller would open the wrong session with it |
| POST | `/api/peer/sessions/create` | Signed, **`control` level** — create a tmux session on this host (JSON: optional `name`). Empty name → the same default-name rule as local `POST /api/tmux/create`. Names are `[A-Za-z0-9_][A-Za-z0-9_-]{0,31}` (a leading `-` would be read as a tmux option). **No `cwd`, no command** — the session starts in this host's default directory with the default shell, so "create a session" never becomes a second way to run an arbitrary command. Existing name → 409. The server records the new session's `origin` as `peer` itself; nothing in the request body can set it |
| POST | `/api/peer/sessions/kill` | Signed, **`control` level** — end a tmux session on this host (JSON: `name`). Matched **exactly** (`-t =name`): plain `-t name` falls back to a prefix match, so `dev` would kill `dev-2`. Missing → 404 (`error: session_not_found`). Also cleans up this host's own web tab on that session (reuses the local kill) |
| POST | `/api/peer/sessions/rename` | Signed, **`control` level** — rename a tmux session on this host (JSON: `name`, `new`). Exact match on the target, 409 if `new` exists, and this host's own web-session record follows the new name |

## Hosts (N7/N39, stage 2 — what the browser calls)

The outbound counterpart to the peer namespace, aggregated into one list for the local UI.
Ordinary authenticated API (logged-in session only) — unlike the peer namespace, which only
a peer signature opens.

| Method | Path | Description |
|--------|------|------|
| GET | `/api/hosts[?fresh=1]` | Local host (always first, id `local`) + every registered peer, each with its session list. Each session carries `name`, `windows`, `attached`, `command`, `cwd`, `status` and `origin` (`local`/`peer`/`agent`/`external`; the local entry reads it from the pane list, a peer that predates it omits it). Remote lookups run in parallel and are cached 30s; an unreachable host degrades to `online:false` + `reason` instead of failing the list |
| GET | `/api/hosts/self` | This host's id/label (pairing guidance, settings screen) |
| POST | `/api/hosts/{host_id}/ping` | Liveness + clock-skew/latency refresh. A failed connection returns **200 with `online:false`** — it's a state, not a server error |
| GET | `/api/hosts/{host_id}/clients?session=&screen=` | Remote "connected screens" list — proxied to the peer route of the same name. The remote's status code (403 for insufficient level, etc.) is passed through as-is |
| POST | `/api/hosts/{host_id}/clients/detach` | Proxies a remote detach (JSON: `tty`, `screen`). 403 if the peer only granted `view` — the UI then hides the detach buttons and says why |
| POST | `/api/hosts/{host_id}/clients/solo` | Proxies remote "keep only this screen" (JSON: `session`, `screen`) |
| GET | `/api/hosts/{host_id}/search?q=` | Proxies remote scrollback search. `host`/`host_label` are attached to each row **here** — whatever the remote calls itself, the label comes from this side's registry |
| POST | `/api/hosts/{host_id}/sessions` | Proxies remote session creation (JSON: optional `name`) → `{ok, name}`. Drops that host's 30s session-list cache so the new session shows up immediately. The remote's refusal passes through (403 `view`-only, 409 name taken, 404 no such session); a peer whose FarShell predates these routes → **501 `peer_outdated`** so the UI can say "update that Mac" instead of "not found" |
| DELETE | `/api/hosts/{host_id}/sessions/{name}` | Proxies a remote kill. Same cache drop |
| PATCH | `/api/hosts/{host_id}/sessions/{name}` | Proxies a remote rename (JSON: `name` = the new name). Same cache drop |
| WS | `/ws/remote/{host_id}/{tmux_name}` | Remote pane proxy: the browser's usual login auth on this side, the peer signature on the other. **Creates no PTY, feeds no output watcher, writes no scrollback** — the host that owns the PTY is the one that notifies and persists, so nothing is duplicated here |
| ANY | `/api/hosts/{host_id}/relay/{path}` | Host switch, sending side: the browser's whole app talks to the chosen Mac through this (`frontend/js/core/host-route.js`). Signs and forwards to that peer's `/api/peer/relay/{path}`; strips `token` from the query and never forwards cookies/Authorization or returns `Set-Cookie`. Pre-checks the same allowlist (403). Peer unreachable → 502 `peer_unreachable`; peer without relay routes → 501 `peer_outdated` |
| WS | `/ws/hosts/{host_id}/relay/{path}` | Same, for WebSockets. The inner route's close codes pass through (4001/4004 keep their meaning); the peer's 4401 becomes 4001 |

## Misc

| Method | Path | Description |
|--------|------|------|
| GET | `/api/capabilities` | Server capability info (TTS/STT/tunnel/version, etc.) |
| GET | `/api/workspace` | Fetch workspace sync state (tabs/UI state) |
| PUT | `/api/workspace` | Save workspace state |
| GET | `/api/device-settings` | Fetch this device's settings (N3 — scoped by the `vt_device` cookie, `local` without it) |
| PUT | `/api/device-settings` | Save this device's settings |
| GET | `/api/agents` | Full list of active agents (claude, etc.) per tmux session |
| GET | `/api/agents/{tmux_name}` | Active agent info for a specific tmux session |
| GET | `/api/agents/coverage` | N9/N45 — per-CLI approval-wait detection coverage: `[{cli, path:"hook"\|"pty"\|"none", patternLines, states, trust:"high"\|"mid"\|"low", kinds, kindSources, badges}]`, read live from `detect/*.toml`. `kinds` = the waiting kinds this CLI can tell apart (`approval`/`choice`/`plan`/`question`, empty when it cannot express waiting); `kindSources` maps each kind to `hook`/`screen`/`fallback`; `badges` = auxiliary done-badges it can raise |
| GET | `/api/agent/status` | Agent state machine (A1) — `idle/working/waiting/done` per session, with TTL sweeping. A `waiting` entry carries `kind` (`approval`/`choice`/`plan`/`question`; removed when waiting ends — priority: hook `tool_name` > screen pattern of the detected CLI > `question`). A done entry may carry `badge` (`question` when the last assistant message ends with `?`, `plan` for agy's `/artifact to review`) — a helper mark, never a waiting state. The same fields ride on `/ws-agent` entries |
| POST | `/api/agent/report` | Pane self-report (A2) — for agents without hooks (`fsh pane report`) |
| POST | `/api/agent/message` | Agent-to-agent message from this Mac (`fsh mcp` / `fsh msg`). JSON `{to: "session" or "host/session", text, replyTo?, pane?, tmux?, cwd?}` — the **sender session is resolved by the server** from `pane`/`tmux`/`cwd` (same 3-step resolution as hooks), never claimed. Local target → queued here; remote target → signed to that peer's `/api/peer/agent/message`. 404 unknown host/session, 400 sending to yourself, 501 `peer_outdated` |
| GET | `/api/agent/messages?conv=&limit=` | Sent and received agent messages, newest last, with delivery status (`queued`/`delivered`/`blocked`/`sent`/`failed`) |
| GET | `/api/agent/directory?fresh=` | Where a message can go: this Mac and paired Macs, each tmux session with its foreground command and agent status |
| GET | `/api/hooks/status` | Claude Code and Codex hook registration status (A0/S4). `tools.claude.events` contains `PreToolUse`/`PostToolUse`/`Stop`/`SessionStart`; `tools.codex.events` contains those plus `PermissionRequest`/`UserPromptSubmit`/`SessionEnd`. Top-level `events` remains the Claude map for older clients |
| GET | `/api/usage` | Usage snapshot (U1) — `{available:false, reason}` when no source. Tokens/credentials are excluded by a field whitelist |
| GET | `/api/usage/counter` | N41 — CounterProvider (unlimited, e.g. local LLM) snapshot. `?since=<epoch>` filters totals; the 7-day sparkline is a fixed window regardless |
| POST | `/api/usage/counter` | N41 — record a usage event (`{model, tokens, seconds}`) — same store as `fsh usage add` |
| POST | `/api/agent/event` | Endpoint called by Claude Code and Codex lifecycle hooks. Body may include `agent`; prompt/tool start maps to `working`, permission request to `waiting`, and stop/session end to `done` |
| GET | `/api/safe-mode` | Whether prompt queue safe_mode is active |
| GET | `/api/tailscale/status` | Tailscale install/connection/IP/MagicDNS hostname |
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
| `/ws-agent` | Pushes agent activity state: `agent_snapshot` on connect (`active`, `all`, and `agents` — same value as `GET /api/agents`), `agent_event` per hook/report, `agent_status_sync` with every heartbeat (15s), and `agents_change` whenever the detected CLI per tmux session changes (one shared server-side check every `VT_AGENTS_PUSH_INTERVAL` seconds, default 5, while any client is connected; a hook event triggers an immediate check). The same `visibility` message pauses the name check while every screen is hidden. The rail and fleet read agent state from this channel and keep only a 60s HTTP safety poll |
| `/ws-workspace` | Pushes workspace changes (`workspace_snapshot`, `workspace_updated`, `worktrees_changed`) and the tmux session list as `tmux_sessions` — same value as `GET /api/tmux/sessions`, sent on connect and afterwards only when it changes (tmux hooks installed by the server report create/close/rename/attach/detach immediately — including ones made straight in a terminal — and agent events do too; a shared server-side check every `VT_SESSIONS_PUSH_INTERVAL` seconds, default 30, covers what has no hook: the running command and current folder). The client may send `{"type":"visibility","hidden":bool}`; while every connected screen reports hidden the check pauses, and a screen becoming visible triggers an immediate catch-up. Screens that never report count as visible. The rail, fleet and agent tab badges keep only a 60s HTTP safety poll |
| `/ws-fs-watch` | Code viewer auto-refresh. The client sends `{"type":"watch","subs":[{kind,path},…]}` (always the full list; `kind` is `file`, `dir` or `repo`), the server answers `{"type":"watching","rejected":[…]}` and later pushes `{"type":"changed","subs":[…]}` with the client's own subscription strings — never file contents or the changed file's path. Every path goes through `fsguard.resolve_under_roots`; `repo` is widened to the enclosing work-tree top and refused outside a git repo; changes to deny-listed names are not reported. At most 64 subscriptions per connection. All connections share one flat and one recursive `watchfiles` watcher (`server/fs_watch.py`). Relayed on host switch |
