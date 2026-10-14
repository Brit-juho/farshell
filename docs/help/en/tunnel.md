# fsh help tunnel — Manage tunnels

Manage the entrance for access from outside (a Cloudflare or ngrok tunnel). `fsh start` normally brings it up for you.
Use these commands to check status, handle exceptions, and publish other local apps. For access scope and auth, see `fsh help access`.

```bash
fsh tunnel list                      # every open tunnel (main + extra ports)
fsh tunnel status                    # tunnel provider, mode (anonymous/named) and run state
fsh tunnel restart                   # force a new tunnel even in a zombie-reconnect state (no response)
fsh tunnel watchdog                  # check/start the daemon that detects zombie reconnects
                                     #   (starts automatically with fsh start; auto-restarts on detection)
fsh tunnel hook                      # check the URL-change hook and run it now (fsh help tunnel-hook)
```

## Fixed domain (named tunnel)

An anonymous tunnel gets a new address whenever the tunnel process is recreated. To keep the address fixed, you need a named tunnel
(a Cloudflare account + a domain you own). An ngrok reserved domain (`VT_TUNNEL_PROVIDER=ngrok` + `VT_NGROK_DOMAIN`) works too.

```bash
fsh tunnel setup                     # named tunnel wizard
fsh tunnel switch <anon|named>       # switch anonymous <-> named
fsh tunnel teardown                  # remove the named tunnel
```

`fsh restart` leaves the tunnel alone, so restarting just the server keeps the address. To keep the address,
always use `fsh restart` instead of `fsh stop && fsh start`.

## Publish another local app

Publish **another local server** (for example port 3000), not FarShell, through a separate tunnel. This works only when the access scope is `all`.

```bash
fsh tunnel expose 3000 "App name" [--mode public|session|pin] [--ttl 1h|24h|7d|30d] [--force]
fsh tunnel unexpose 3000             # close that port's tunnel and sharing policy
fsh tunnel pin 3000                  # reissue the access code (tunnel and URL stay)
fsh tunnel mode 3000 <public|session|pin>   # change the share mode (tunnel and URL stay)
```

| Mode | Meaning |
|---|---|
| `session` (default) | Only signed-in devices |
| `pin` | Only people who know the access code |
| `public` | Anyone with the URL (no auth) |

`expose` asks for confirmation because it opens something to the public internet. Remote-control ports such as Chrome remote debugging (9222) open only with `--force`.
For a `session` share, opening the public address directly shows only an instruction screen. Start from the "first open" link in the `expose` output (or the FarShell port panel). An exposure whose time has run out is taken down, tunnel included, within a minute.

## Get notified when the address changes

For a hook that publishes the tunnel URL to Notion, Slack and the like whenever it changes, see `fsh help tunnel-hook`.
