# fsh help ssh — Remote access with Tailscale + SSH

On networks where screen sharing (Chrome Remote Desktop, TeamViewer, RDP/VNC) is blocked, such as a
corporate network, **Tailscale** usually still gets through (UDP hole punching, or a fallback to
the 443 DERP relay, which looks like ordinary outbound traffic). If you only need the **terminal**,
not the whole screen, connecting straight to this MacBook's tmux sessions over Tailscale + SSH is
lighter and faster.

## Why this belongs in fsh

FarShell's core design is "the tmux session is the single source of truth": desktop iTerm, the
mobile PWA and the Voice Daemon all attach to the same `tmux -L fsh` session (see
`fsh help concepts`). **SSH is just a fifth client.** Connect over SSH from the office and you pick up
exactly what you saw at home: the same screen, the same scrollback, the same running Claude session.

## Usage

```bash
fsh ssh                 # print the command to connect to session 'dev'
fsh ssh mysession       # name a different session
fsh ssh --user alice    # set the remote login account (default: current account)
```

It reads the Tailscale state **on this machine** and prints two commands to copy and run as-is
from another device (such as another laptop):

1. Plain SSH: `ssh -t user@100.x.x.x 'tmux -L fsh attach -t dev || tmux -L fsh new -A -s dev'`
   - The connecting device's public key must be in this machine's `~/.ssh/authorized_keys`.
2. Tailscale SSH: `tailscale ssh user@100.x.x.x -- '...'`
   - If your tailnet ACL allows SSH, you connect with just your Tailscale account auth, **no key registration**.

## If your public key isn't registered

On the connecting device (such as another laptop):
```bash
cat ~/.ssh/id_ed25519.pub    # if missing, create one with ssh-keygen -t ed25519
```
Copy the output, then **on this machine**:
```bash
fsh ssh --add-key "ssh-ed25519 AAAA... user@laptop"
```
This appends it to `~/.ssh/authorized_keys` without duplicates. Only the value you paste is used; nothing runs automatically.

## How this relates to fsh mobile

If you start with `fsh start --network tailscale`, the web UI (xterm.js + voice) is also exposed
only on the tailnet IP, with no Cloudflare Tunnel. So:

| Connection | Needs | Use for |
|---|---|---|
| `fsh ssh` | Tailscale + (optional) SSH key | Pure terminal; key-heavy work such as an IDE or vim |
| `fsh start --network tailscale` | Tailscale + a browser | Voice input and touch control from a phone |
| `fsh start` (default, `--network all`) | Password or machine token | Fully outside networks, devices without Tailscale |

All three share the same tmux session, so mix and match freely.

## Diagnose

```bash
fsh doctor      # includes Tailscale install/running checks
fsh status      # shows the current Tailscale IP
```
