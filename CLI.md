# fsh CLI Reference

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./CLI.ko.md)

`fsh` is a single entry point you can run from any terminal. This document is the
detailed reference for all commands/options — see [README.md](./README.md) for an
overview and `fsh help concepts` for conceptual background.

---

## Command list

Control FarShell from any terminal with the `fsh` command:

```bash
fsh start [--network <scope>] [--safe] [--force] [--voice]
                       # start the server + its entrance (default: public tunnel + auth);
                       # --voice also starts the voice daemon (see `fsh help access`)
fsh restart [--voice]  # restart the server only, keeping the tunnel — the access URL stays the same
fsh stop [--purge] [--keep-tunnel]
                       # stop (--purge: also fully kill tmux sessions;
                       #       --keep-tunnel: leave the public entrance up)
fsh status             # check current status
fsh mobile [--e2e] [--session <name>] [--temp]
                       # phone access URL + QR (server must be running; --e2e: encrypt payload)
fsh manage             # TUI management tool (sessions/target/hotkeys/status)
fsh attach [name]      # attach any tmux session in a new window
fsh voice              # voice mode (background, usable while using other apps)
fsh voice-bind <claude|codex> <conversation-id>  # bind from the actual target pane
fsh voice-target [name|--auto]  # lock/unlock the voice daemon target
fsh clip               # clipboard sync daemon (Mac clipboard change → web, OSC52 fallback)
fsh queue [list|add "content" [session]|run|rm <id>|unblock <id>|clear|log [id]]  # prompt queue
fsh files [ls|add <path> [--share ttl] [--pin]|rm <id>|share <id> [--ttl] [--pin] [--once]|unshare <id>|insert <id>]  # file store + share links
fsh host [list|pair|add <url> --ticket <t>|ping <id>|rm <id>|rename <id|self> <name>|rotate <id>|allow-control <id>|deny-control <id>|allow-agent <id>|deny-agent <id>|log|revoke-all|help]  # pair another Mac's FarShell
fsh msg [log [conv]|agents|send <[host/]session> "text" [--reply <id>]]  # agent-to-agent messages
fsh delegate [ls|close <id>]  # agent delegations (sub-sessions); close keeps the session
fsh authz [whoami|grants|requests|revoke <id>|audit [--follow] [--since 10m]|panic]  # approval inbox, grants and audit (read-only; see below)
fsh pair [start|code <id>|join <code>|status [<id>]|handoff <id>|accept <id>|end <id>|revoke <id> [--verify]]  # two sessions working one bundle as driver/navigator (see below; needs VT_AUTHZ_ENFORCE=pair)
fsh browser [adapters|enable <name> [--off] [--yes]|tabs|close <id>]  # isolated browser tabs for agents: turn adapters on, list/close tabs (see below; needs VT_AUTHZ_ENFORCE=browser)
fsh lease [ls|acquire <glob> [--write|--read] [--exclusive] [--ttl 30m]|renew <id>|release <id>|check [--staged]|guard install|uninstall|status]  # folder leases + optional pre-commit guard (see below)
fsh mcp                         # MCP server (stdio) for agents: list_agents / send_message / read_messages + shared sessions (list_shared_sessions / read_session / send_to_session) + whoami / request_access / list_my_grants / lease_acquire / lease_renew / lease_release / lease_list
fsh worktree [list|add <name> [--base b] [--ports] [--copy-modules] [--agent claude]|rm <name> [--force]|open <name>]  # git worktrees
fsh git-account [list|add --provider github|gitlab [--host H] --token-stdin|rm <id>|bind <repo> <id>]  # git account store (stored credentials are not used for push)
fsh hotkey [list|set|reset|disable]  # view/change hotkeys
fsh hooks [status|install|uninstall|dedupe [--dry-run]]  # register Claude Code + Codex hooks (status badges/queue; Claude/Codex Stop drive session-specific TTS); dedupe removes FarShell hooks the Codex app copied into ~/.codex/hooks.json
fsh update [--channel stable|dev|devmode] [--to vX.Y.Z] [--check] [--restart] [--rollback]  # update by channel (stable = signed release tag, dev = origin/dev, devmode = manual pull) then re-sync hooks + skill; `update trust [--key <pubkey>]` pins the release-signing key; `--rollback` returns to the previous commit
fsh sync [--dry-run]  # re-sync Claude Code/Codex hooks and the fsh skill link to the current code (use after a manual pull)
fsh pane report [--state ...] [--agent ...]  # report this pane's state (for agents without managed hooks)
fsh clauth [status|which]  # read-only usage view (hidden when clauth isn't installed)
fsh usage [list|add --model <name> --tokens <N> --seconds <N>]  # cumulative usage log (local LLMs etc., no quota)
fsh password [clear]   # set web login password (stores a hash) / clear=unset
fsh otp [status|setup|disable]   # require OTP when registering a new device (fully disabled until setup)
fsh device [list|rename <id> <name>|revoke <id>|prune]  # list/rename registered devices (lifetime, OS/browser, last IP),
                       #   revoke (also invalidates sessions if a phone is lost), prune expired devices now
fsh help <topic>       # concepts/voice/hotkeys/target/troubleshoot
fsh template [save|apply|list|rm] <name>  # save/apply CLAUDE.md templates
fsh popup <action>     # quick fsh command invocation via tmux 3.2+ popup
fsh run "..."          # run headless `claude -p` in background + TTS notification on completion
fsh handoff mobile     # hand off the current tmux session to your phone (QR + #tmux=)
fsh handoff desktop    # bring a phone session back to the Mac terminal
fsh tunnel expose 3000 "app name" [--mode public|session|pin] [--ttl 1h|24h|7d|30d] [--force]
                                   # expose another local port through FarShell's own reverse
                                   # proxy (2.2). Default --mode is session (logged-in devices
                                   # only) — pass --mode public for the old unauthenticated behavior
                                   # Remote-control ports (Chrome DevTools 9222, Node inspector 9229, Docker API,
                                   # ngrok/cloudflared local APIs, FarShell's proxy range) need --force.
                                   # A session share prints a "처음 열기" link that starts at the main origin's
                                   # /p/authorize — the public URL alone shows a notice to cookie-less visitors.
                                   # Expired exposures are torn down (tunnel + listener) within a minute.
                                   # Asks whether to auto re-expose unless --auto-renew/--no-auto-renew is given
                                   # (no prompt off a terminal: off). Auto re-expose extends the share by its --ttl at
                                   # expiry (same share id/PIN) and the server re-opens the tunnel within a minute if it
                                   # goes away (reboot, fsh stop) — a quick tunnel comes back with a new URL.
fsh tunnel unexpose 3000          # stop the tunnel and its share policy for that port
fsh tunnel pin 3000                # reissue the access PIN without touching the tunnel/URL (old-PIN visitors must re-enter)
fsh tunnel mode 3000 session       # change who can access without touching the tunnel/URL (everyone re-authenticates)
fsh tunnel auto 3000 on|off        # turn auto re-expose on/off for an exposed port (credentials unchanged)
fsh tunnel list                   # list all open tunnels (main + extra ports), with mode and PIN-failure count
fsh tunnel hook                   # check + immediately run the URL-change hook (fsh help tunnel-hook)
fsh tunnel restart                # force a new tunnel even in a zombie-reconnect (unresponsive) state + rerun the hook
fsh tunnel watchdog               # check/start the zombie-reconnect auto-detection daemon (normally auto-started by fsh start/voice/mobile)
fsh ssh [session]      # guidance for connecting directly to a tmux session via Tailscale + SSH
fsh doctor             # installation/environment diagnostics (includes Linux checks)
fsh terminal           # show / choose the main terminal app fsh uses when it opens a window
fsh install-profiles   # auto-register terminal app profiles (iTerm2 Dynamic Profile + other snippets)
fsh setup-keybind [key] [action]  # register a tmux popup keybinding (default: prefix+v → status)
fsh shell-init zsh     # print the shell init snippet (add the printed snippet to ~/.zshrc)
```

> **Supported OS**: macOS / Linux (X11) / WSL2 (behaves as Linux). Native Windows is not supported.

**single tmux server principle:** the fsh CLI, server, Voice Daemon, and hooks all use the `-L fsh` isolated socket. The Voice Daemon can override it via the `VT_TMUX_SOCKET` environment variable. This is kept separate from the user's own `tmux ls`.

**Opening a terminal window:** `fsh attach` and `fsh handoff desktop` open a new window in your **main terminal** (see `fsh terminal` below) and attach tmux inside it. `voice` / `mobile` / `start` do not open windows. If you're already inside tmux, no new window is opened, and a session that already has a window attached is not opened twice.

**Voice-coding workflow while using other apps:**
1. `fsh voice` → starts in the background; in a terminal, run `tmux -L fsh new -A -s dev` and start `claude --resume` there
2. Pick the current conversation from the resume list in the new window → voice/mobile then connects to that Claude
3. Leave the original window as-is and continue working in another app
4. Ctrl+Shift+M → speak ("git status") → automatically typed into tmux dev
5. `fsh stop` → shut down

> Calling an `fsh` command from inside tmux already won't open a new window (checked via `$TMUX`).
> If a window can't be opened (app missing, or macOS Automation permission denied), the manual command is printed (`tmux -L fsh new -A -s dev 'claude --resume'`).


## Native CLI launch profiles

Manage dedicated Claude/Codex roots with `fsh profile list`, `create <name> --tool claude|codex [--account <id>] [--test]`, `login <id>`, `status <id>`, `rename <id> <name>`, `archive <id>`, `restore <id>`, `sources` and `retirement`. Login runs interactively on the owner host. Account labels are logical groups, not identity proof. See [Launch profiles](docs/help/en/profiles.md) for reviewed imports, test restrictions and current replacement limits.

## `fsh start` and `fsh mobile` options

`fsh start` decides **who can reach the server**; `fsh mobile` shows the URL and QR for your phone.

```bash
fsh start --network <scope>            # localhost | lan | tailscale | all (default)
fsh start --safe                       # Pre-block dangerous commands (rm -rf /, sudo, etc.)
fsh start --force                      # Force a public tunnel even without auth (password/token) set — not recommended
fsh mobile --e2e                       # X25519 handshake + NaCl SecretBox E2E encryption
                                       #   (signed with the server's long-term identity key — TOFU pinning, trust on first connect)
fsh mobile --session <tmux-session>    # the phone opens straight into this tmux session (also: fsh handoff mobile)
fsh mobile --temp                      # The device registered by this QR expires after 1 day (fixed) — for lending access briefly
```

To open a public tunnel with `--network all` (the default), you must first set up
authentication via `fsh password` or `VT_AUTH_TOKEN` — if unset, `fsh start` is rejected
(to prevent unauthenticated remote code execution). To proceed anyway at your own
risk, use `fsh start --force`.

The options used to live on `fsh mobile`; passing `--network`/`--safe`/`--force` to it still
works (it runs `fsh start` for you) but prints a notice. Details: `fsh help access`.

## Agent-to-agent messages (`fsh msg`, `fsh mcp`)

One agent can hand a message to another agent's session — on this Mac or a paired one.
Delivery rides the prompt queue: the message is typed into the target pane **after the
agent's stop hook** (Claude Code, Codex), or right away if that agent is known to be idle.
Agents without hooks keep it queued until `fsh queue run` / "Run now".

```bash
claude mcp add farshell -- fsh mcp      # give an agent the tools (Codex/agy: add the same command)
fsh msg agents                          # where can I send?
fsh msg send M5/api "run the tests"     # from a shell, e.g. for agents without MCP
fsh msg log                             # history with delivery status
fsh host allow-agent <id>               # on the receiving Mac — needs allow-control too
```

Limits: 10 exchanges per conversation, 20 messages/min per sending Mac, 4000 chars.

**Changing what another Mac may do (`fsh host allow-*` / `deny-*`).** With the server running `VT_AUTHZ_ENFORCE=peer` these commands ask the
server over the unix socket instead of writing `~/.vt/hosts.json`: `deny-control` / `deny-agent` lower access and apply at once (audited);
`allow-control` / `allow-agent` raise it, so they only create an approval request — a person approves it in the web inbox, and this command
cannot. The command exits 0 after creating the request and says that nothing has changed yet. Without the flag, without a socket or against
an older server they write `hosts.json` directly, as before. `control` means that Mac can type into this Mac's terminals — a remote shell.
`fsh host help` prints this.

**Shared sessions (MCP only).** `fsh mcp` also offers `list_shared_sessions`, `read_session` and
`send_to_session`. The user shares an ordinary session with a nickname in the web UI; the agent can then
read it (read-only share) or also type one line into it (read-write share). The first use needs the
user's approval in the browser (a human login — the machine token cannot approve); agents cannot create
or close sessions. Details: `docs/ref/terminal.md`, `fsh help agent-state`.
The receiving agent sees a one-line header saying the text came from another agent.

## Approvals, grants and audit (`fsh authz`)

`fsh authz` asks the running server over the unix socket (the server knows the caller from the kernel, no token). Outside tmux you are a
`cli` caller and may **read**; inside an agent's pane you are that agent and the reads are refused. **The terminal never approves or revokes.**

```bash
fsh authz whoami                  # who the server thinks you are, effective scopes, matching presets (--json)
fsh authz requests                # pending approval requests (id, kind, who, what, age); approve in the web/phone inbox
fsh authz grants [--kind share|msg|peer|deleg|generic] [--subject S] [--resource R]   # every grant with preset, expiry, uses left
fsh authz audit [--since 10m|2h|1d|<epoch>] [--principal P] [--resource R] [--limit N] [--follow] [--json]
fsh authz revoke <grant id>       # prints that a signed-in person revokes it in the web/phone permissions screen (exit 3)
fsh authz panic                   # prints that an elevated person does this in the web/phone screen (exit 3)
```

`audit` shows the hash chain state (`정상`/`깨짐`) and merges the denial log (`denials`) with the chain. For agents, the MCP tools
`whoami`, `request_access {resource, scopes, reason?, ttl?}` and `list_my_grants` do the same from inside the session; `request_access` always
returns «waiting for the user's approval». API and rules: `API.md` «Approval inbox, grants and audit», `docs/ref/auth.md`.

## Browser broker (`fsh browser`)

Agents get **isolated** browser tabs (a fresh profile; your own Chrome and its logins are never touched). The server must run with `browser` in
`VT_AUTHZ_ENFORCE`; otherwise the commands say it is off. Turning an adapter on is for people and the terminal — agents cannot.

```bash
fsh browser adapters              # playwright, cdp, … : on / off / unavailable (with the reason)
fsh browser enable playwright     # first time ever: shows the consent text and asks (non-interactive: add --yes)
fsh browser enable playwright --off
fsh browser tabs [--json]         # open tabs, owner and state (open / orphaned / released)
fsh browser close <id>            # close any tab
```

Agents use the MCP tools `browser_list_adapters`, `browser_open`, `browser_navigate`, `browser_snapshot`, `browser_screenshot`, `browser_click`,
`browser_type`, `browser_close`, `browser_tabs`, `browser_handoff` and (only with a `browser.js` grant) `browser_eval`. Rules: `docs/ref/auth.md` «Browser broker».

## Pairs (`fsh pair`)

A pair lets two sessions — or a person and a session — work the same sessions/folders, one as **driver** (types, writes) and one as
**navigator** (reads, messages). The server needs `VT_AUTHZ_ENFORCE=pair`; otherwise every subcommand says the feature is off. Talks to the server
over the unix socket (no token). A person approves the pairing in the web/phone inbox — the terminal never approves.

```bash
fsh pair start --session reviewer --dir ~/code/app --lease --elevated raw_input   # in an agent's pane; prints a code and a two-word check phrase
fsh pair code <id>                  # show the pending code again (same code until it expires, 5 min)
fsh pair join ABCD-EFGH             # the other agent joins; then wait for the person's approval
fsh pair status [<id>]              # my pairs, or one (the joiner reads the approval result here — no token needed)
fsh pair handoff <id>               # driver offers the wheel (2 minutes)   fsh pair accept <id>   # navigator takes it; generation +1
fsh pair end <id>                   # end it (members)
fsh pair revoke <id> --verify       # end it and wait until grants, leases, queued messages are confirmed gone (exit 1 if not)
```

`start` options: `--session NAME|KEY` and `--dir PATH` (repeatable), `--role driver|navigator` (yours; default driver), `--lease` (the driver gets
a write lease on each folder), `--elevated raw_input,session_manage,relay,…` (asked for, given only if the person ticks them and re-authenticates),
`--with SESSION` (only that session may join). Creating needs a person at the web UI, or an agent holding `pair.manage` (`request_access` on
`pair:*`). A code is single-use and valid 5 minutes; five wrong tries burn it; ten new codes an hour per creator. A pair ends on `end`, a
person's revoke, 2 hours idle (`VT_PAIR_IDLE_MIN`), a member session disappearing or after 8 hours. The agent tools are MCP `pair_create`,
`pair_join`, `pair_status`, `pair_handoff`, `pair_accept` (shown only while the server's pair switch is on). Rules, roles and the same-user
caveat (a process of the same user can edit the permission store): `docs/ref/auth.md` «Pairs»; API: `API.md` «Pairs».

## Folder leases (`fsh lease`)

A lease says «this folder (or glob) is mine for now» so that two agents do not edit the same files. Talks to the server over the unix
socket (no token). Defaults: 30 minutes, at most 8 hours, renewable.

```bash
fsh lease ls                                  # active leases: id, holder (nickname), mode, exclusive, time left, glob
fsh lease acquire ~/code/app/src --write --ttl 2h --reason "refactor"   # --read, --exclusive, --ttl 90|30m|2h
fsh lease renew <id> [--ttl 1h]               # up to 8 h after it was created
fsh lease release <id>
fsh lease check --staged                      # would committing the staged files collide with someone else's lease? (exit 1 = yes)
fsh lease check path/a path/b [--holder <principal id>]
fsh lease guard install | uninstall | status  # git pre-commit hook for the CURRENT repository
```

Holding a lease needs the underlying permission on that path (`fs.write` for write, `fs.read` for read) — a person has it, an agent asks
with `request_access`. A conflicting request prints the holder's nickname and when the lease ends. **Leases are advisory unless the server
runs with `VT_AUTHZ_ENFORCE=lease`**, and even then only writes FarShell performs itself (git stage/commit from the web UI/API, file
delivery to a session, delegation folders) are refused; a process of the same user can ignore a lease. `fsh doctor` shows the active count,
whether blocking is on and the guard state of the current repository.

**Commit guard.** `guard install` writes a pre-commit hook that runs `fsh lease check --staged` (staged names are read NUL-separated,
renames count both names, nothing secret goes on a command line; the machine token is never used). An existing pre-commit hook is not
overwritten: it is renamed to `pre-commit.fsh-chained`, runs first, and `guard uninstall` puts it back. With a conflict the commit stops
(exit 1) and lists the holder and files. If the server cannot be reached the guard prints **one warning line and lets the commit through**
and appends a line (time, reason, file count — no paths) to `~/.vt/authz/guard-skipped.log`. `git commit --no-verify` skips the guard.
The agent-side tools are MCP `lease_acquire`, `lease_renew`, `lease_release`, `lease_list` (shown only to callers whose `whoami` has
`fs.write` or `fs.read`). Rules: `docs/ref/auth.md` «Folder leases and revocation cleanup», API: `API.md` «Folder leases».

## Prompt Queue (`fsh queue`)

Stores instructions while an agent is working and feeds them in one at a time
after the agent finishes.

```bash
fsh queue list                  # list queue
fsh queue add "next instruction" [session]  # add to queue (max 50)
fsh queue run                   # manual drain — feed one item
fsh queue rm <id>                # delete an item (id=all clears everything)
fsh queue unblock <id>          # resume a blocked item (resets retries)
fsh queue log [id]              # state-transition log (pending → sending → sent / blocked)
fsh queue clear                 # clear everything
```

Automatic feeding is triggered by a `done` transition from the installed Claude Code
or Codex lifecycle hooks. aider/agy have no managed hook, so they must feed items
manually via `fsh queue run` or "Run now" in the web UI. Four gates apply before feeding:
grace period (the user may have
started typing directly) → safe_mode (a dangerous command is left as blocked instead
of being fed) → confirming the target pane is still alive → one item at a time.

---

## Settings that change what `fsh` does

Every setting lives in `~/.vt.env` (gitignored, per-machine) and every key is listed with
a comment in [`config/vt.defaults.env`](./config/vt.defaults.env) — that file is the list.
Write it with `fsh` commands or `lib/vt_env.sh`, never with `echo`/`sed` (AGENTS.md
contract 5). The few that decide how you reach this Mac:

| Key | What it does |
|-----|--------------|
| `VT_TUNNEL_PROVIDER` | `cloudflare` (default) · `ngrok` · `none` — who owns the public way in. `start`/`stop`/`status`/`mobile`/`handoff`/`tunnel expose` all follow it |
| `VT_NGROK_DOMAIN` | ngrok reserved domain. Without it every start gets a throwaway URL, which defeats the point of a fixed address |
| `VT_TUNNEL_NAME` + `VT_TUNNEL_HOSTNAME` | Cloudflare named tunnel (needs your own domain on Cloudflare) |
| `VT_NETWORK_MODE` | `localhost` · `lan` · `tailscale` · `all` — who may reach the port at all |
| `VT_TRUST_PROXY` | Trust `X-Forwarded-For` from a proxy you control. Off by default; turning it on while directly exposed lets a client forge its own IP |

**Security-boundary keys are read from the file first, not from your shell environment**
— a stale `export` once kept a narrowed browse root wide open. `fsh status` and
`fsh doctor` say so when they correct one.

## `fsh doctor` checked items

| # | Item | Details |
|---|------|------|
| 1 | Python | Verifies path/version |
| 2 | venv | `.venv` or legacy conda env |
| 3 | core packages | fastapi, uvicorn |
| 4 | voice packages | edge-tts, sounddevice, and whether the selected STT model is ready |
| 5 | tmux | Installed and version |
| 6 | cloudflared | Remote access tool |
| 7 | ffmpeg | Mobile voice decoding |
| 8 | port | VT_PORT usage status |
| 9 | fsh CLI | `~/.local/bin/fsh` symlink (`vt` also works as a backward-compat symlink) |
| 10 | PATH | Whether `~/.local/bin` is included |
| 11 | `.vt.env` | Whether the config file exists |
| 12 | Auth | Whether password/token is configured |
| 13 | Terminal app | Detected app list + current `TERM_PROGRAM` |
| 14 | Tailscale | Install/connection status |
| 15 | unix socket | `~/.vt/run/fsh.sock` state (listening / skipped / failed self-test), permissions, and machine-token requests in the last 24h. `VT_AUTHZ_SOCKET=0` makes clients skip the socket and use the token only. `fsh status` shows the same one line |
| 16 | shared token | Only with `VT_AUTHZ_ENFORCE=legacy`: the number of machine-token requests refused as out of scope (403 `legacy_token_scope`) in the last 24h, listed per route and user agent so you can see which outside script still needs to move to the socket or a login. On a server with a token but no web password it also says that approval-needing agent/peer features are off until `fsh password` is set |
| 17 | Folder leases | Active lease count, whether write blocking (`VT_AUTHZ_ENFORCE=lease`) is on, the pre-commit guard state of the current repository (installed / chained to an existing hook / absent), how many times the guard skipped in the last 24 h because the server was unreachable (`~/.vt/authz/guard-skipped.log`), and a warning if a revocation cleanup (saga) is unfinished |
| 18 | Pairs | Whether the pair switch (`VT_AUTHZ_ENFORCE=pair`) is on, how many pairs are in effect and how many wait for approval; a warning if the switch is off but pairs are still stored (their grants have no effect) |
| 19 | Other-Mac permissions | Whether protocol v2 (`VT_AUTHZ_ENFORCE=peer`) is on; a warning if revoke notices could not be delivered or a secret rotation is unconfirmed (the server retries; `fsh host rotate` settles a rotation) |
| 20 | Browser | Whether the browser broker switch (`VT_AUTHZ_ENFORCE=browser`) is on, how many adapters are enabled out of those available, and how many tabs are open; a warning if tabs can no longer be driven (orphaned, see `fsh browser tabs`) or the settings/tab ledger cannot be read. Read locally, so it answers even when the server is down |

---

## Tailscale + SSH remote access

Some corporate networks block screen sharing (Chrome Remote Desktop, TeamViewer,
RDP/VNC). Tailscale (a WireGuard-based VPN mesh) works via UDP hole punching or
443 DERP relay fallback, getting through most such firewalls. If you only need the
**terminal** and not the full screen, connecting directly to your home Mac's tmux
session via Tailscale + SSH is lighter and faster than full screen sharing.

Since the tmux session is the single source of truth, SSH is just another client
alongside the desktop terminal, mobile PWA, and Voice Daemon — connecting via SSH from
the office picks up exactly the same screen, scrollback, and running Claude session
you were looking at at home.

```bash
# On the Mac (Tailscale must already be connected via tailscale up)
fsh ssh                   # Prints the command to connect to session 'dev' (copy and run it on the office laptop)
fsh ssh mysession         # Specify a different session name
fsh ssh --user alice      # Specify the remote login account (default: current account)
fsh ssh --add-key "ssh-ed25519 AAAA... user@laptop"   # Register a public key
```

This path is pure text SSH, so it can't use the browser's mic/speaker — you type
directly with the keyboard instead of using voice. Completion/idle notifications
still come through the existing push bridge, and if you set
`VT_NOTIFY_CLIENT_EVENTS=1`, you can also get push notifications for SSH
connect/disconnect events.

| Access method | Requires | Use case |
|---|---|---|
| `fsh ssh` | Tailscale + (optional) SSH key | Pure terminal work, keyboard-heavy tasks like vim/IDE |
| `fsh start --network tailscale` | Tailscale + browser | Voice input, touch interaction from a phone |
| `fsh start` (default, `--network all`) | Auth (password/token) | Fully external devices without Tailscale |

For details: `fsh help ssh`.

---

## Main terminal and auto-open

`fsh terminal` shows / sets which app opens the window (stored as `VT_TERMINAL` in `~/.vt.env`):

```bash
fsh terminal                  # current setting + installed apps
fsh terminal set ghostty      # auto | ghostty | iterm | terminal | wezterm | warp | kitty | alacritty
fsh terminal unset            # back to auto
```

- `auto` (default): the first installed app in the automatic selection order
- An app that isn't installed is rejected by `set`; if the chosen app can't be opened at run time, fsh says so and falls back to auto
- Ghostty (1.3+) opens the window inside the already-running app via AppleScript, falling back to `open -na`
- Supported apps: iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app (macOS); common emulators on Linux (auto only)
- If already inside tmux, continues in the current window without opening a new one (checks `$TMUX`, guaranteeing idempotency)

## Post-install integration (optional)

Integrate so that opening a new terminal window automatically enters
`tmux -L fsh new -A -s dev`. Choose one of the two approaches below.

### Method A — Auto-register terminal profiles (recommended)

```bash
fsh install-profiles --dry-run   # preview changes
fsh install-profiles             # apply for real
```

Auto-registers an iTerm2 Dynamic Profile. For Ghostty / WezTerm / Kitty / Alacritty /
Windows Terminal / Terminal.app, prints a config snippet to copy and paste. No
conflict with p10k instant prompt.

### Method B — Shell init (for SSH remote / environments without profiles)

```bash
echo 'eval "$(fsh shell-init zsh)"' >> ~/.zshrc      # zsh
echo 'eval "$(fsh shell-init bash)"' >> ~/.bashrc    # bash
fsh shell-init fish >> ~/.config/fish/config.fish    # fish
fsh shell-init pwsh >> $PROFILE                       # PowerShell
```

The generated snippet includes a 5-fold TTY guard (`interactive` + TTY + `$TMUX`
empty + IDE-embedded shell block + tmux presence). Zero console output even with
p10k instant prompt active in zsh.
