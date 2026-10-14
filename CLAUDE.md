# FarShell — project overview

Project overview for all AI coding agents. Shared development rules and contracts
live in [`AGENTS.md`](./AGENTS.md).

FarShell puts your Mac terminal on your phone: a tmux-backed web terminal reachable from
anywhere, with voice input, file transfer, a code viewer and agent-state awareness.
macOS / Linux (X11) / WSL2. Native Windows is not supported.

---

## Quick start

```bash
./install.sh            # terminal only (~50MB)
./install.sh voice      # terminal + voice mode (~1.5GB)

fsh start               # server + public entrance
fsh mobile              # access URL + QR
fsh status              # what is running
fsh doctor              # diagnose install/environment
```

`install.sh` creates the venv, symlinks `fsh`, writes `~/.vt.env` and registers the
Claude Code hooks. Manual/alternative installs: [`docs/ref/dev.md`](./docs/ref/dev.md).

| Where | URL |
|------|-----|
| Desktop | `http://localhost:7777` |
| Public | whatever `fsh status` reports for your tunnel provider |

---

## Document map

Use the documents below for detailed guidance.

| What | Document |
|------|----------|
| Every `fsh` command and option | [`CLI.md`](./CLI.md) |
| REST/WebSocket endpoints | [`API.md`](./API.md) |
| Server + frontend structure | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| UI rules, tokens, skins | [`DESIGN.md`](./DESIGN.md) |
| Cross-tool rules + **hard contracts** | [`AGENTS.md`](./AGENTS.md) |
| Release history | [`CHANGELOG.md`](./CHANGELOG.md) |

---

## Area index

Read the relevant area document before making changes.

| Area | What lives there | Document |
|------|------------------|----------|
| Auth, devices, boundary values | Password login, device registration + OTP, one-time tickets, cross-site blocking, the machine token, and why config-file values beat stale environment variables | [`docs/ref/auth.md`](./docs/ref/auth.md) |
| Terminal, panes, shell surface | tmux sessions, split panes, rail + palette, scrollback, paste, kitty keys, themes, clipboard | [`docs/ref/terminal.md`](./docs/ref/terminal.md) |
| Agent state, queue, MCP | Claude Code hooks, `idle`/`working`/`waiting`/`done`, approval detection and its coverage, prompt queue, snippets, usage gauge, MCP servers | [`docs/ref/agents.md`](./docs/ref/agents.md) |
| Files, sharing, code viewer | Upload store, share links, the read-only viewer and `fsguard`'s three layers | [`docs/ref/files.md`](./docs/ref/files.md) |
| Tunnels, ports, remote access | `VT_TUNNEL_PROVIDER` (cloudflare/ngrok/none), extra ports, URL-change hook, zombie-reconnect watchdog, Tailscale, port dashboard | [`docs/ref/tunnel.md`](./docs/ref/tunnel.md) |
| Worktrees and git | Worktree creation/discovery, dock source control, the git account store (built but unused) | [`docs/ref/worktree.md`](./docs/ref/worktree.md) |
| Multi-host peering | Server-to-server pairing, remote panes, offline host layout | [`docs/ref/multihost.md`](./docs/ref/multihost.md) |
| Voice, clipboard, push | Voice daemon, hands-free and voice-only modes, clipboard daemon, Web Push | [`docs/ref/voice.md`](./docs/ref/voice.md) |
| Measurements: session/view rules, `waiting` release, phone | Per-CLI `waiting` kind results, release-path table, terminal-reply root cause, real-device (touch) findings, bundle-size trend, reproduction | [`docs/ref/session-view-measurements.md`](./docs/ref/session-view-measurements.md) |
| Display language | `VT_LANG`, how the server picks ko/en, `t('한국어 원문')` keys, the English dictionary, glossary | [`docs/ref/i18n.md`](./docs/ref/i18n.md) |
| Running, testing, install | Running the server, mobile testing over adb, the E2E procedure, manual install | [`docs/ref/dev.md`](./docs/ref/dev.md) |

---

## Skills

| Command | Description |
|--------|------|
| `/fsh` | Global skill. Invokable from anywhere with phrases like "voice mode", "mobile access" |
| `/fsh-start` | Start server + tmux + the public entrance |
| `/fsh-mobile` | Mobile testing (adb port forwarding, Chrome, screenshots) |
| `/fsh-voice` | Install/run the Voice Daemon |

Project-provided skill sources live in `.claude/skills/`. The global copy of `fsh` at
`~/.claude/skills/fsh/SKILL.md` is a manual `cp` — `scripts/check_docs.py` warns when those
two drift.
