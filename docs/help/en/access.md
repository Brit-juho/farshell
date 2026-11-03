# fsh help access — Who can connect, and how it's secured

**`fsh start` decides who can connect.** **`fsh mobile` shows the URL and QR code** for your phone.
For the current state at a glance, see the "Access security" block in `fsh status`.

## Start: `fsh start`

```bash
fsh start                              # default: public tunnel + auth (all)
fsh start --network tailscale          # same tailnet only (no tunnel)
fsh start --network lan                # same network only (no tunnel)
fsh start --network localhost          # this Mac only (no tunnel)
fsh start --safe                       # block dangerous commands
fsh start --voice                      # also start the Voice Daemon
```

| Access scope | Who can connect | Tunnel |
|---|---|---|
| `all` (default) | Anyone on the internet. **Auth is required** | Cloudflare/ngrok |
| `tailscale` | Devices on the same tailnet only | none |
| `lan` | Devices on the same network only | none |
| `localhost` | This Mac only | none |

- **No scope starts a server without auth.** A server without auth is a shell for anyone who can reach it (same
  network, tailnet, public URL, or a malicious web page opened on this Mac). With no password set, `fsh start`/`voice`/`clip`
  **create a random web sign-in password, show it once in the terminal** and store only its hash. Change it with `fsh password`.
  Outside a terminal (scripts, automatic starts) nothing is created and the start stops — run `fsh password` first.
  If a password cannot be created and you accept the risk, `fsh start --force` still opens it.
- If the server is already running, `--network` and `--safe` apply **from the next restart**:
  `fsh restart --network tailscale`.
- `fsh restart` keeps the access scope you chose last time. `fsh stop` (full shutdown) clears it.
- Narrowing the scope from `all` leaves the running tunnel process alive. To shut it down, run `fsh stop`, then start again.

## Connect from your phone: `fsh mobile`

```bash
fsh mobile                    # access URL + QR (the server must be running)
fsh mobile --e2e              # open with E2E encryption (the tunnel can't see plaintext)
fsh mobile --session dev      # open the phone straight into tmux session 'dev'
fsh mobile --temp             # devices registered with this QR expire after 1 day (for briefly opening it on someone else's phone)
```

- The URL carries a **5-minute, one-time registration ticket**. Whichever device opens it gets registered, so don't send it to anyone.
- `fsh handoff mobile` is the same as `fsh mobile --session <current session>` (it starts the server first if needed).
- If you pass the old options (`--network`/`--safe`/`--force`) to `mobile`, it runs `fsh start` for you, then shows the QR.

## Check state: "Access security" in `fsh status`

| Item | Meaning |
|---|---|
| Access scope | A value from the table above. Also shows the allowed IP list if you set one directly with `VT_ACCESS_SPEC` |
| External address | Whether it's a fixed domain (named tunnel / ngrok reserved domain) or an anonymous tunnel that changes on restart |
| Auth | Password / machine token. Warns if the scope is `all` and there's no auth |
| OTP | When on, registering a new device asks for an OTP as well (`fsh otp setup`) |
| Registered devices | Number of signed-in devices (`fsh device list`; if one is lost, `fsh device revoke <id>`) |

## Common management commands

```bash
fsh password [clear]          # set or clear the web sign-in password
fsh otp [status|setup|disable]
fsh device [list|rename|revoke|prune]
fsh tunnel list               # every open tunnel
fsh doctor                    # check install, environment and auth
```

For a fixed domain, use a named tunnel (`fsh tunnel setup`) or an ngrok reserved domain.
To get notified when the address changes, see `fsh help tunnel-hook`.
