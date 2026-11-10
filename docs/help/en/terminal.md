# fsh help terminal — Main terminal and opening windows

When fsh opens a new terminal window, **you choose which app it uses.** It isn't fixed to one app.
The value lives in `VT_TERMINAL` in `~/.vt.env`, and the CLI and the web app share one implementation (`server/terminal_open.py`).

```bash
fsh terminal                  # current setting + installed apps
fsh terminal set ghostty      # auto | ghostty | iterm | terminal | wezterm | warp | kitty | alacritty
fsh terminal unset            # back to automatic selection
```

- `auto` (default): the first installed app from iTerm2 → Ghostty → WezTerm → Kitty → Alacritty → Terminal.app
- `set` refuses an app that isn't installed. If the chosen app can't be opened at run time, fsh tells you and falls back to automatic selection.
- Ghostty (1.3+) only adds a window to an app that's already running (AppleScript). The first time,
  allow the macOS "Automation" permission prompt. If you deny it, fsh falls back to `open -na`, which is slower.
- Warp can't run a command automatically, so fsh opens the app and copies the command to the clipboard (you paste it).
- On Linux, fsh picks an installed terminal emulator automatically (`VT_TERMINAL` isn't used).

## Where windows open

| From | What happens |
|---|---|
| `fsh attach [session]` | Attaches that tmux session in a new window |
| `fsh handoff desktop` | Brings the `dev` session you were viewing on your phone to the Mac terminal |
| Web: "Open on Mac" | Opens a new window on the Mac running the server (same implementation) |
| Web settings: "Also open new sessions in a Mac terminal" | Opens a window every time you create a session (off by default) |

- **The window title is the session name** (`server`, `dev` …). fsh enables `set-titles` on its dedicated tmux server to do this.
- **One window per session**: fsh doesn't open another window for a session that already has one attached.
- The command runs with `exec`, so when the tmux session ends the terminal app closes the window. The exception is **Ghostty: a window opened through AppleScript stays open after the process exits** (close it yourself). Also, with tmux's `detach-on-destroy off`, killing a session just moves the client to another session and the window stays open.
- `fsh start`/`restart`/`mobile`/`voice` don't open a window.
- If no window opens: run the manual command fsh prints (`tmux -L fsh new -A -s dev`) in any terminal,
  then check the "Terminal apps" item in `fsh doctor` and the macOS Automation permission.

## Saved sessions and conversation archives

The sidebar's Sessions view browses repositories → workspaces → sessions, with All sessions and Session archive views.
Closing a view keeps its execution running. Task history shows the logical task name and execution history.
Stop an execution explicitly before archiving it; independently running child sessions are not stopped together.

In Conversation archive settings, review a Claude or Codex configuration home and enable collection.
FarShell stores raw copies and searchable messages separately in private storage, so records already saved
remain readable after CLI source files disappear. Raw files may include sensitive conversations and tool output.
The default limit is 20 GiB. Collection pauses at the limit; existing archives are never deleted automatically.

Central replication requires pairing and separate approval of direction, archive identity and public key on both servers.
Disconnecting replication stops future transfers and preserves copies already received. Viewing a replica does not launch a remote CLI.
Restoring or exporting raw files requires password verification; existing source files are never overwritten.
Starting a CLI through Continue conversation and verifying actual resumption of the same conversation are separate outcomes.
Archive metadata currently reports `dependency_complete:false`; restoration of every companion file across all CLI versions is not guaranteed.

Use Review existing execution permissions to compare legacy permissions with current executions before reconnecting them.
A replacement session with the same name does not automatically receive the previous execution's permissions.
Replication can be restricted to registered source scopes; local archive and incoming replica limits are separate.
Rotating a replication key retains earlier keys so in-flight records can still be decrypted.

Records exceeding 1 MiB are omitted from searchable content, while their raw bytes are preserved.
Archive status separately reports complete raw preservation and reasons for omitted search content.
Supported messages following oversized records remain indexed, with progress preserved across collector restarts.

History deletion can target this host alone or include approved replicas.
It hides records and prevents reimport; it does not erase CLI source files or archived raw bytes on disk.
Deletion acknowledgments from offline replica hosts complete after reconnection.
**Task history** is one window. The left side is the session (status, resume, stop, rename, archive, linked
conversations, delegation, internal subagents, run history); the right side is the record of the internal subagent or
delegated session you pick (prompts, answers, tool calls, tool results). A child row's **View transcript** and
**View report** and the internal chip open the same window with the right side preselected. Picking the
parent in the delegation list moves the window to that session's record.

## tmux config (`fsh tmux`)

FarShell's tmux always uses the repository's `config/vt-tmux.conf` as its base.
Put your own preferences (for example `set -g mouse off`) in `~/.config/vt/tmux.local.conf`
instead of editing that file. It is read at the very end of the base config, so it overrides the
same options; a missing file is skipped silently.

```
fsh tmux reload    # re-read the config into the running tmux (sessions stay)
fsh tmux status    # what is applied now
fsh tmux extkeys on|off|status   # trial switch for extended keys (Shift+Enter etc.)
```

- An old `~/.config/vt/tmux.conf` copy left by earlier installs is moved aside by `fsh tmux reload`
  (and `fsh sync`). An untouched copy becomes `tmux.conf.bak-<date>`, an edited one
  `tmux.conf.legacy`; nothing is deleted. Move the lines you changed into `tmux.local.conf`.
- Reloading does not undo values you **removed** from the config; they stay in the running tmux
  until the tmux server restarts, which closes every session.
- The "tmux config" line in `fsh doctor` shows the same state.
- `fsh tmux extkeys on` writes a `# >>> fsh extkeys` … `# <<< fsh extkeys` block to
  `~/.config/vt/tmux.local.conf` (`extended-keys on` plus `extkeys` in `terminal-features`) and reloads at once.
  `off` removes only that block and turns it off in the running tmux too. Your own lines are never touched
  and repeating the command gives the same result. The default stays off. Needs tmux 3.2 or newer.
  Screens that are already attached may pick it up late — reopen the tab or pane if it does not take effect.
