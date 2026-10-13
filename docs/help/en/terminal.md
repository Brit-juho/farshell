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
  then check the "terminal app" item in `fsh doctor` and the macOS Automation permission.
