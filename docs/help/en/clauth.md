# Usage display (clauth integration)

FarShell can show a **usage gauge** in the rail of the web/mobile UI. It reads the data from the
status feed that [clauth](https://github.com/anthropics/clauth) writes.

**If you don't use clauth, this feature quietly disappears.** There's nothing to configure and no error.

## Check

```bash
fsh clauth status     # usage per profile (read-only)
fsh clauth which      # just the name of the active profile
fsh doctor            # also diagnoses feed freshness
```

On the web, use the 📊 "Usage" button in the rail. With no source, the button itself is hidden.

## How it reads

It reads **only** `~/.clauth/status.json`. The clauth daemon refreshes that file every 90 seconds,
and FarShell re-parses it only when the mtime changes.

It doesn't run `clauth status --json` because that would spawn a process on every poll, and values
like `pending_switch` can't be observed from a one-shot run (only the daemon knows them).

**Tokens and credentials are never included in the response.** The server filters through a field
allowlist, so if clauth adds a sensitive field later, it still won't leak to the web.
`token_ledger.json`, `profiles/` and `session_profiles.json` are never read.

## Turn off / force on

`VT_USAGE_PROVIDER` in `~/.vt.env`:

| Value | Behavior |
|---|---|
| `auto` (default) | Show if the feed exists, hide if not |
| `clauth` | Force on. If there's no feed, **it shows the reason** (use this to find out why nothing appears) |
| `none` | Turn the feature off entirely. Use it if you don't want usage going out through a public tunnel |

```bash
source lib/vt_env.sh                 # run from the repo root
vt_env_set VT_USAGE_PROVIDER none
```

## Display rules

- Bar color: amber above 80%, red above 95%
- If the feed stalls for more than twice its refresh interval, it shows **"Updates stalled"** and dims
- If `auth_status` isn't healthy, "Re-authentication required" appears **above the gauge**
- A profile with no window (`5h`/`7d`) data shows **"No usage data"** instead of an empty bar.
  Drawing 0% would falsely say "not being used"

## Profile switching and the scope of the session display

**The web doesn't offer profile switching (`clauth <profile>`).** FarShell runs many tmux sessions.
Say you have five sessions running and switch the global profile: the existing sessions stay as
they are and only new sessions change. You can no longer tell which session is on which account,
which is especially risky when you're viewing remotely from a phone. Switch in the terminal with
clauth's own command.

For the same reason, **FarShell doesn't put a profile badge on each tab or pane.** The feed only says
"some session is running on this profile". It doesn't say which tmux session. A badge without that
mapping would confidently show the wrong account. The rail button shows only the active profile and
whether it's running, as a global indicator.
