# FarShell

[![CI](https://github.com/Brit-juho/farshell/actions/workflows/ci.yml/badge.svg)](https://github.com/Brit-juho/farshell/actions/workflows/ci.yml)

[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./README.ko.md)
[![Version](https://img.shields.io/badge/version-2.3.0-blue.svg)](./CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-informational.svg)](./docs/guide/platforms.md)
[![Self-hosted](https://img.shields.io/badge/self--hosted-yes-success.svg)](#install)

> Your terminal, from anywhere. One-line install.

FarShell turns a macOS/Linux machine into a personal dev server: the same tmux
session, reachable by voice or from your phone. Run Claude Code, Codex, Aider,
Antigravity (agy) or just a shell — voice input, mobile access and tmux sharing work the
same either way. (Windows only through WSL2.)

- **Terminal on your phone** — scan a QR code, you're in tmux
- **Code by voice** — `Ctrl+Shift+M` from anywhere, even while doing other work
- **See what's running** — read-only code viewer + diff, port dashboard, agent state
- **Claude Code integration** — hooks give you live tool-use state, a TTS summary on
  completion, and a prompt queue that feeds itself
- **No required speech API subscription** — local STT, with online or system TTS

---

## Install

```bash
# Terminal only (~50MB)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash

# Terminal + voice mode (a few hundred MB; the speech model is downloaded later, from Settings)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash -s voice
```

`install.sh` creates a Python venv, installs the profile you picked, symlinks
`~/.local/bin/fsh`, writes `~/.vt.env` and registers the Claude Code hooks.

<details>
<summary>From source (Node.js required) or a release tarball (no Node.js needed)</summary>

```bash
git clone https://github.com/Brit-juho/farshell.git ~/farshell
cd ~/farshell && ./install.sh          # or: ./install.sh voice
```

Installing from source builds the frontend with Vite, which needs Node. The
release tarball at [Releases](https://github.com/Brit-juho/farshell/releases)
ships that build already done, so Node stays a developer-only requirement:

```bash
tar xzf farshell-<version>.tar.gz && cd farshell-<version>
./install.sh
```
</details>

---

## Quick start

**Access from your phone**

```bash
fsh password              # first use: set a web login password
fsh start                 # start the server and public tunnel
fsh mobile                # print the URL and QR code
```

**Voice input** (requires the voice install profile)

```bash
tmux -L fsh new -A -s dev  # create or attach a terminal session
# Start your shell or agent in this session, then:
fsh voice                 # start the server and voice daemon in the background
fsh voice-target dev      # lock input to the dev session
```

Use `Ctrl+Shift+M` to start and stop recording. Speech is sent as text plus Enter
to the target session. Select and download a model in Settings → Voice & notifications;
completion speech (TTS) is configured there and is off by default.
See [supported platforms](./docs/guide/platforms.md) for hotkey availability.

`fsh restart` restarts the server while keeping the tunnel URL. `fsh stop` stops
the server, tunnel and daemons while leaving tmux sessions alive.

`fsh doctor` diagnoses an install or environment that isn't behaving.

---

## Security

The default is **no authentication**. Set a password with `fsh password` before
exposing anything; `fsh start` refuses to open a public tunnel without it.

| Layer | Method |
|---|---|
| Login | Password (scrypt hash) or a machine token (`VT_AUTH_TOKEN`), with an HMAC-signed 24h session cookie |
| New devices | Per-device trust via a 90-day cookie. `fsh otp setup` adds a 6-digit gate for unseen devices only |
| Revocation | `fsh device revoke <id>` — kills that device's sessions immediately |
| Cross-site | HTTP and WS both return 403 when the Origin isn't self. No CORS wildcard |
| Code viewer | Read-only, confined to configured roots, with a denylist (`.env*`, `*.pem`, `.ssh/`, …) applied to file reads and `git diff` alike |
| E2E encryption | `--e2e` — X25519 key exchange + NaCl SecretBox, session key signed by a long-lived Ed25519 identity for TOFU protection against active MITM |

Details: [`docs/ref/auth.md`](./docs/ref/auth.md).

---

## Documentation

| What | Where |
|---|---|
| Every `fsh` command and option | [`CLI.md`](./CLI.md) |
| REST / WebSocket endpoints | [`API.md`](./API.md) |
| Module map, 3-plane model, data flow | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| Design system — principles, skins, tokens, layout | [`DESIGN.md`](./DESIGN.md) |
| Release history | [`CHANGELOG.md`](./CHANGELOG.md) |
| Supported platforms, WSL2, voice engines | [`docs/guide/platforms.md`](./docs/guide/platforms.md) |
| Configuration keys | [`config/vt.defaults.env`](./config/vt.defaults.env) — the committed defaults, with a comment per key |
| Troubleshooting | [`docs/help/troubleshoot.md`](./docs/help/troubleshoot.md) (same as `fsh help troubleshoot`) |

**Feature reference by area:**

| Area | Document |
|---|---|
| Auth, devices, boundary values | [`docs/ref/auth.md`](./docs/ref/auth.md) |
| Terminal, panes, paste, scrollback, themes | [`docs/ref/terminal.md`](./docs/ref/terminal.md) |
| Agent state, prompt queue, snippets, usage, MCP | [`docs/ref/agents.md`](./docs/ref/agents.md) |
| Files, share links, code viewer | [`docs/ref/files.md`](./docs/ref/files.md) |
| Tunnels, ports, Tailscale, remote access | [`docs/ref/tunnel.md`](./docs/ref/tunnel.md) |
| Worktrees and git | [`docs/ref/worktree.md`](./docs/ref/worktree.md) |
| Multi-host peering | [`docs/ref/multihost.md`](./docs/ref/multihost.md) |
| Voice, clipboard, Web Push | [`docs/ref/voice.md`](./docs/ref/voice.md) |
| Running, testing, manual install | [`docs/ref/dev.md`](./docs/ref/dev.md) |

Contributing agents (Claude Code, Codex, …) start at [`AGENTS.md`](./AGENTS.md).

---

## License

MIT
