# fsh CLI Reference

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./CLI.ko.md)

`fsh` is a single entry point you can run from any terminal. This document is the
detailed reference for all commands/options — see [README.md](./README.md) for an
overview and `fsh help concepts` for conceptual background.

---

## Command list

> 2026-09-18: `CLAUDE.md`가 따로 들고 있던 목록을 여기로 합쳤다. 그쪽이 더 완전했고
> (`worktree`·`host`·`hooks`·`files`·`usage` 등 8개가 이 표에 없었다), 같은 목록이 두
> 곳에 살면 반드시 어긋난다 — `API.md`가 같은 이유로 단일 진실이 됐다(AGENTS.md 계약 9).

Control FarShell from any terminal with the `fsh` command:

```bash
fsh start [--voice]    # start everything (server+tunnel, --voice also starts the voice daemon)
fsh stop [--purge]     # stop (--purge: also fully kill tmux sessions)
fsh status             # check current status
fsh mobile [--e2e]     # mobile access URL + QR (--e2e: encrypt payload)
fsh manage             # TUI management tool (sessions/target/hotkeys/status) — Wave 4
fsh attach [name]      # attach any tmux session in a new window
fsh voice              # voice mode (background, usable while working in Notion)
fsh voice-target [name|--auto]  # lock/unlock the voice daemon target
fsh clip               # clipboard sync daemon (Mac clipboard change → web, OSC52 fallback)
fsh queue [list|add "content" [session]|run|rm <id>|unblock <id>|clear]  # prompt queue (P4)
fsh files [ls|add <path> [--share ttl] [--pin]|rm <id>|share <id> [--ttl] [--pin] [--once]|unshare <id>|insert <id>]  # file store + share links (N19/N23)
fsh host [list|pair|add <url> --ticket <t>|ping <id>|rm <id>|rename <id|self> <name>|allow-control <id>|log|revoke-all]  # pair another Mac's FarShell (N7/N39)
fsh worktree [list|add <name> [--base b] [--ports] [--copy-modules] [--agent claude]|rm <name> [--force]|open <name>]  # git worktrees (N8/N44)
fsh git-account [list|add --provider github|gitlab [--host H] --token-stdin|rm <id>|bind <repo> <id>]  # git account store (N30 — built but unused, ADR-27)
fsh hotkey [list|set|reset|disable]  # view/change hotkeys
fsh hooks [status|install|uninstall]  # register Claude Code hooks (prerequisite for status badges/queue/TTS)
fsh pane report [--state ...] [--agent ...]  # report this pane's state (for agents without hooks)
fsh clauth [status|which]  # read-only usage view (hidden when clauth isn't installed)
fsh usage [list|add --model <name> --tokens <N> --seconds <N>]  # cumulative usage log (local LLMs etc., no quota)
fsh password [clear]   # set web login password (stores a hash) / clear=unset
fsh otp [status|setup|disable]   # require OTP when registering a new device (fully disabled until setup)
fsh device [list|rename <id> <name>|revoke <id>]  # list/rename registered devices, revoke (also invalidates sessions if a phone is lost)
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


## `fsh mobile` options

```bash
fsh mobile --e2e                       # X25519 handshake + NaCl SecretBox E2E encryption
                                       #   (signed with the server's long-term identity key — TOFU pinning, trust on first connect)
fsh mobile --safe                      # Pre-block dangerous commands (rm -rf /, sudo, etc.)
fsh mobile --network <mode>            # localhost | lan | tailscale | all (default)
fsh mobile --force                     # Force a public tunnel even without auth (password/token) set — not recommended
```

To open a public tunnel with `--network all` (the default), you must first set up
authentication via `fsh password` or `VT_AUTH_TOKEN` — if unset, the run is rejected
(to prevent unauthenticated remote code execution). To proceed anyway at your own
risk, pass `--force` explicitly.

`tailscale` mode opens the server only on your own tailnet IP, without a Cloudflare
Tunnel, and also restricts the network policy to the tailnet CIDR
(`100.64.0.0/10`) + localhost only.

---

## Prompt Queue (`fsh queue`)

Queues up instructions while an agent is working and feeds them in one at a time
afterward. This pairs with voice mode — right now, speaking while work is in
progress gets swallowed, but with the queue you can walk around, drop several
instructions, and have them run in order.

```bash
fsh queue list                  # list queue
fsh queue add "next instruction" [session]  # add to queue (max 50)
fsh queue run                   # manual drain — feed one item
fsh queue rm <id>                # delete an item (id=all clears everything)
fsh queue unblock <id>          # resume an item blocked by safe_mode
fsh queue clear                 # clear everything
```

Automatic feeding is triggered **only by Claude Code's Stop hook**. codex/aider/gemini
have no such hook, so you must feed items manually via `fsh queue run` or "Run now"
in the web UI. Four gates apply before feeding: grace period (the user may have
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
| 4 | voice packages | faster-whisper, edge-tts, sounddevice |
| 5 | tmux | Installed and version |
| 6 | cloudflared | Remote access tool |
| 7 | ffmpeg | Mobile voice decoding |
| 8 | port | VT_PORT usage status |
| 9 | fsh CLI | `~/.local/bin/fsh` symlink (`vt` also works as a backward-compat symlink) |
| 10 | PATH | Whether `~/.local/bin` is included |
| 11 | `.vt.env` | Whether the config file exists |
| 12 | Auth | Whether password/token is configured |
| 13 | Terminal app | Detected app list + current `TERM_PROGRAM` |
| 14 | Tailscale | Install/connection status (D9) |

---

## Tailscale + SSH remote access

Some corporate networks block screen sharing (Chrome Remote Desktop, TeamViewer,
RDP/VNC). Tailscale (a WireGuard-based VPN mesh) works via UDP hole punching or
443 DERP relay fallback, getting through most such firewalls. If you only need the
**terminal** and not the full screen, connecting directly to your home Mac's tmux
session via Tailscale + SSH is lighter and faster than full screen sharing.

Since the tmux session is the single source of truth, SSH is just another client
alongside the desktop iTerm, mobile PWA, and Voice Daemon — connecting via SSH from
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
| `fsh mobile --network tailscale` | Tailscale + browser | Voice input, touch interaction from a phone |
| `fsh mobile` (default, `--network all`) | Auth (password/token) | Fully external devices without Tailscale |

For details: `fsh help ssh`.

---

## Auto-open behavior

When running `fsh voice` / `mobile` / `start`:
- Auto-detects your current terminal app → opens a new window → runs `tmux new -A -s dev 'claude --resume'`
- Supported apps: iTerm2, Ghostty, WezTerm, Kitty, Alacritty, Warp, Terminal.app
- If already inside tmux, continues in the current window without opening a new one (checks `$TMUX`, guaranteeing idempotency)

## Post-install integration (optional)

Integrate so that opening a new terminal window automatically enters
`tmux -L vt new -A -s dev`. Choose one of the two approaches below.

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

### Single tmux server principle

The `fsh` CLI, server, Voice Daemon, and hooks all use the isolated `-L vt` socket,
so every client (desktop, mobile, Voice Daemon) shares the same session. This is
automatically separate from the user's existing `tmux ls` sessions.
