# fsh CLI Reference

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./CLI.ko.md)

`fsh` is a single entry point you can run from any terminal. This document is the
detailed reference for all commands/options — see [README.md](./README.md) for an
overview and `fsh help concepts` for conceptual background.

---

## Command list

| Command | Description |
|------|------|
| `fsh voice` | Voice mode — starts server + Voice Daemon in the background (usable while working in Notion, etc.) |
| `fsh clip` | Clipboard sync daemon — pushes Mac clipboard changes to the web (covers copies outside the terminal that OSC52 can't catch) |
| `fsh mobile [options]` | Prints the mobile access URL + QR code |
| `fsh start` | Starts everything — server + tunnel + voice daemon |
| `fsh stop [--purge]` | Stop — `--purge` also fully terminates the tmux session |
| `fsh status` | Check server/tunnel/Voice Daemon/tmux status |
| `fsh manage` | TUI management tool — session list/rename/kill/attach + hotkey/status lookup |
| `fsh attach [name]` | Attach an arbitrary tmux session to a new OS terminal window |
| `fsh voice-target [name\|--auto]` | Lock/unlock the Voice Daemon target session |
| `fsh queue [subcommand]` | Prompt queue — queue up instructions while work is in progress and feed them in sequentially (see [Prompt Queue](#prompt-queue-fsh-queue) below) |
| `fsh hotkey [list\|set\|reset\|disable]` | Look up/change hotkeys |
| `fsh password [clear]` | Set a web login password (only the hash is stored) / `clear` to remove it |
| `fsh otp [status\|setup\|disable]` | Require OTP when registering a new device — fully disabled until `setup` is run |
| `fsh device [list\|revoke <id>]` | List registered devices / revoke one (also invalidates its sessions, e.g. if the phone is lost) |
| `fsh help <topic>` | Topic-specific help — `concepts`/`voice`/`hotkeys`/`target`/`troubleshoot`/`webui`/`ssh`/`tunnel-hook` |
| `fsh claude` | Opens a new terminal window with `tmux dev` + `claude --resume` |
| `fsh agent <name>` | Start claude/codex/aider/gemini (generalized) |
| `fsh handoff mobile` | Hands the current tmux session off to your phone (QR + `#tmux=`) |
| `fsh handoff desktop` | Brings a phone session back to the Mac terminal |
| `fsh template [save\|apply\|list\|rm] <name>` | Manage CLAUDE.md templates |
| `fsh popup <action>` | Quick invocation via tmux 3.2+ popup |
| `fsh run "..."` | Headless `claude -p` background run + TTS notification |
| `fsh tunnel expose <port> "name"` | Expose another local port via a separate Cloudflare tunnel |
| `fsh tunnel unexpose <port>` | Close that port's tunnel |
| `fsh tunnel list` | List all open tunnels (main + extra ports) |
| `fsh tunnel hook` | Check the URL-change hook + run it immediately (details: `fsh help tunnel-hook`) |
| `fsh tunnel restart` | Force a new tunnel even in a zombie-reconnect (unresponsive) state + rerun the hook |
| `fsh tunnel watchdog` | Check/start the zombie-reconnect auto-detection daemon (normally auto-starts) |
| `fsh ssh [session]` | Direct tmux session access via Tailscale + SSH — for environments like a corporate network where screen sharing is blocked (details: [below](#tailscale--ssh-remote-access)) |
| `fsh doctor` | Installation/environment diagnostics — see [checked items](#fsh-doctor-checked-items) below |
| `fsh install-profiles [--dry-run]` | Auto-register terminal app profiles (iTerm2 Dynamic Profile + snippets for others) |
| `fsh shell-init [zsh\|bash\|fish\|pwsh]` | Print a shell-specific safe integration snippet (`eval "$(fsh shell-init zsh)" >> ~/.zshrc`) |

> Supported OS: macOS / Linux (X11) / WSL2 (runs as Linux). Native Windows is not supported.

---

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
