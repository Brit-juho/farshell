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
