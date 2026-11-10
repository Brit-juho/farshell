# Updating FarShell

```bash
fsh update                      # update on the saved channel
fsh update --channel stable     # latest signed release (vX.Y.Z)
fsh update --channel dev        # latest origin/dev
fsh update --channel devmode    # no automatic updates (pull by hand)
fsh update --to v2.5.0          # a specific release (signed tags only)
fsh update --check              # compare only, change nothing
fsh update --restart            # also run fsh restart when done
fsh update --rollback           # back to the previous commit
fsh update trust                # pin the release-signing key (once)
fsh sync [--dry-run]            # re-sync hooks + skill only
fsh update status               # result of the last server-side check (no network request)
fsh update auto on|off|status   # automatic updates (off by default)
```

## Channels

| Channel | What it takes | Verified by |
|---|---|---|
| stable | the latest `vX.Y.Z` tag (detached HEAD) | tag signature + `origin/master` history |
| dev | latest `origin/dev`, fast-forward only | nothing (relies on GitHub account security) |
| devmode | nothing | — |

`--channel` saves the choice in `~/.vt/update-channel`. With no saved channel and a
branch other than `master`, `fsh update` does not move and asks you to choose one.
Use `devmode` (pull by hand) or `dev` on development machines.

## Signature verification (stable)

stable only accepts a tag whose SSH signature matches a key **you chose to trust**. The
trusted key lives outside the repository in `~/.vt/update-signers`, so a compromised
repository cannot change it.

1. Run `fsh update trust` once. It shows the key fingerprint of the latest signed tag and
   asks you to confirm — compare it with your own release-signing key
   (`ssh-keygen -lf <key>.pub`). You can also pass the key with `--key <pubkey file>`;
   use `--replace` to swap an already-trusted key.
2. Unsigned tags, tags signed by another key, an old signed tag attached to a new name, and
   tags outside `origin/master` history are refused.

## Failure and rollback

- It will not start with local changes to tracked files (`--check` is exempt).
- If dependency install or build fails after the code moved, it returns to the original
  commit and branch. pip/npm packages already installed are not rolled back.
- On success the previous position is saved in `~/.vt/update-prev.json` for
  `fsh update --rollback`.
- The server is never restarted automatically (open sessions would drop). Use
  `fsh restart` or `--restart`.

## Hook and skill sync

After an update, the Claude Code and Codex hooks (the same registrar as `fsh hooks install`)
and the global `fsh` skill link are brought in line with the current code. After a manual
pull, run `fsh sync`. A skill that is a copy rather than a link is reported, not overwritten.

## Update check (automatic, read-only)

A few minutes after the server starts, and every 24 hours after that, the server checks `origin`
only (one `git fetch` — no tokens or user information are sent). The result shows in the "Update"
line of `fsh status` / `fsh doctor`, in the "Update available (vX.Y.Z)" line of the FarShell menu
and in Settings > Update. It only looks; it changes nothing.

- Turn it off: Settings > Update > "Check for new versions" OFF (asks for your password again), or the
  environment variable `VT_UPDATE_CHECK=0`. When off, no network request is made at all.
- On stable, only the newest tag that passes signature verification counts as "available". A tag whose
  signature does not match is shown with the reason, never announced as an update.
- The dev channel compares `origin/dev` with the current commit. devmode and installs without a channel
  only notify; they are never applied.

## Automatic updates (off by default)

```bash
fsh update auto on              # explains the risk and asks first (--yes when not interactive)
fsh update auto on --hour 3     # update hour (local time 0-23, default 04)
fsh update auto off
fsh update auto status
```

When on, FarShell **replaces itself with code fetched over the network and restarts the server**, so it only
does that when everything below holds.

| Condition | Otherwise |
|---|---|
| The channel is stored as `stable` | Notification only (dev, devmode and unset are never applied automatically) |
| The tag signature matches the trusted key and the tag is in `origin/master` history | Refused, the reason is recorded |
| A person pinned the trusted key with `fsh update trust` | Refused (the key is never trusted automatically) |
| The major version does not go up (2.x → 3.0.0 is yours to do) | Refused and notified |
| No local changes to tracked files | Refused and notified |
| It is inside the chosen hour | Postponed to the next check |
| No terminal input in the last 15 minutes and no working or waiting agent | Postponed (stops and notifies after 7 days) |

- After 3 failed attempts for the same version it stops and notifies.
- A separate process, not the server, does the work through the same path as `fsh update` — dependencies per
  the `--require-hashes` locks, and a failed build returns to the original commit. Only one update runs at a
  time (manual `fsh update` included), and the previous position is kept in `~/.vt/update-prev.json` for
  `fsh update --rollback`.
- On success the server tells the open screens first, then runs `fsh restart`. Screens disconnect briefly and
  reconnect; **tmux sessions stay because tmux, not the server, owns them**. The result is announced once in the
  menu/toast.
- Changing it needs your web password again (elevation) and is written to the audit log. Agents, peers and the
  machine token cannot change it.
- You are trusting the release account and the signing key. If that bothers you, leave it off and run
  `fsh update` yourself.
