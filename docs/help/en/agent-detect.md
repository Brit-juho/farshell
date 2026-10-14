# Detection patterns (`server/detect/*.toml`)

These files are one of two ways FarShell learns that an agent CLI is "waiting for approval or input"
(`waiting`). The other is the Claude Code / Codex hooks (see `fsh help agent-state`). They match
**literal strings** in the PTY output stream, so you can attach any CLI, but nothing is detected
until you fill in the actual prompt text.

The "Detection coverage" table on the Settings → Agent tab (`GET /api/agents/coverage`) shows what is
currently filled in for each CLI.

## Why TOML, not Python

When a CLI updates, its prompt text changes. Instead of editing server code and restarting every
time, you change one line in these files. `load_patterns()` in `server/agent_prompt_detect.py` reads
them at every server start (or on a `force=True` reload).
(Measured 2026-10-06: the coverage table (`/api/agents/coverage`) re-reads files on every request,
but the PTY detector caches. After editing a toml, restart the server for the change to affect
real waiting/interrupt detection.)
A file that fails to parse is skipped, **and only that file** (one bad regex must not kill
approval-wait detection for the whole server).

## Format

```toml
# Example CLI — fill it in to turn on waiting detection (the PTY detector caches patterns, so restart the server).
name = "codex"

enter = [
  "text that appears only in the real prompt 1",
  "text that appears only in the real prompt 2",
]

exit = [
  "signal that the prompt is gone",
]

# optional — capture the choices (for inline approval on mobile)
options = '^\s*[❯>]?\s*(\d+)\.\s+(.+?)\s*$'
```

| Field | Meaning |
|---|---|
| `name` | Falls back to the file name without the extension. `claude.toml` → `claude` |
| `enter` | String list. If any one appears in the recent output window (16KiB), the session counts as `waiting` |
| `exit` | String list. If any appears, `waiting` is cleared immediately: the signal that the prompt is gone |
| `[kinds]` | Table. Key = waiting kind (`approval`/`choice`/`plan`/`question`), value = list of literal strings (same literal-byte rule as `enter`). Text listed here is also a signal to enter waiting. A match in `enter` counts as `approval`. If several match in the same window, the winner order is plan → approval → enter → choice → question |
| `ignore_exit_on` | List of kinds (e.g. `["choice"]`). While that kind is showing, `exit` is ignored, because the Codex question screen also draws the status line `esc to interrupt`. Write this key **above** the `[kinds]` table (TOML rule) |
| `prefer_latest_kind` | `true`/`false` (default `false`). When text from different dialogs stays in the output history, pick the kind that **appeared last in the window** (Codex: plan text left above approval text). Option numbers are also taken only from after the current title line. Write this key **above** the `[kinds]` table (TOML rule) |
| `interrupt` | String list. When the user **denies** an approval or cuts work off with ESC, the CLI doesn't fire a Stop hook and leaves only this text on screen. The moment it's newly drawn, that session's `waiting`/`working` entry is finished as `done`. It is not a signal to enter waiting. If `enter`/`exit` was redrawn after the text, the screen is already stale and it is ignored. Write it **above** the `[kinds]` table |
| `interrupt_stale_after` | Regex (multiline mode). If it matches **after** that `interrupt` text, it's a redraw of a screen that has already passed, not a new interrupt, and it's ignored. Codex leaves the interrupt line in scrollback, and it rides along on partial tmux redraws in the next turn (measured on 0.160.0), so a following new input line (`› `) or response line (`• `) tells them apart. Without it, only the text is checked. Write it **above** the `[kinds]` table |
| `[badge]` | Table. Key = auxiliary marker value, value = list of literals. A match doesn't raise the session to waiting; it only adds a `badge` to the entry (agy `/artifact to review` -> `plan`). It's removed when user input arrives |
| `options` | Regex (a string or a list of strings; only the first one that matches, from the front, is used). Capture group 1 = number, 2 = label. If named groups `label`/`key` exist, those are used (Codex: the key sent is `(y)`/`(a)`/`esc`, not the number). On a match, number buttons appear on the mobile fleet home. If it fails, `enter`/`exit` still work |

If neither `enter` nor `[kinds]` has an effective pattern, screen-based waiting detection doesn't work.
Check hook installation and pattern status together in the coverage table in Settings.

## How the kind is decided — per-CLI spec isolation

`kinds`/`enter` match **only against the toml of the CLI the detector identified** (the `agent` on
an entry written by a hook or self-report takes priority; otherwise a process scan). agy and Claude
Code both draw `Do you want to proceed?`, so without isolation one's text would raise the other to
waiting. A CLI with no toml is never raised to waiting. If the CLI can't be identified (a shell,
ssh, etc.), it falls back to the old behavior and checks every toml. Priority is hook kind > screen
kind > fallback (`question`).
`kinds`/`kindSources` in `GET /api/agents/coverage` show what each CLI can distinguish.

## Writing patterns

- ⚠ **Beware of false positives.** Don't use short strings that can show up by chance in build logs
  or diffs (`"Yes"`, `"y/n"`). Pick longer text that appears only in the real prompt.
- `enter`/`exit`/`[kinds]`/`interrupt`/`[badge]` match as **literal strings** (not regex).
  Only `options` and `interrupt_stale_after` are regexes.
- Real terminal output is mixed with ANSI codes for cursor movement, color and line erase. The
  detection window is text with **ANSI stripped, NBSP turned into a space, and cursor moves to the
  start of a line (`\x1b[19;1H`) turned into `\n`** (`_append_clean`), so you never need to put
  escapes in a literal or regex.
  The reason became clear in a 2026-10-05 measurement: Claude Code draws `Interrupted · What should ...`
  split word by word with color codes, and agy draws the selection-screen hint line
  (`↑/↓ Navigate · enter Select · esc Skip`) the same way. When matching on raw bytes, neither one
  hit on a real screen (only synthetic-string tests passed).
  Text that wraps on a narrow PTY (80 columns) (`Would you like to` / `proceed?`) doesn't appear on
  one line, so also add a pattern that uses only the start of the line.

## Evidence and verification

Use only text confirmed from real CLI output or official source.
Check the source and version at the top of each TOML. Even text read from source can differ in a
real terminal because of ANSI handling and line wrapping. Measured results and limits are in
`docs/ref/session-view-measurements.md`.

After editing a TOML, refresh the PTY detection cache with `fsh restart`, open that CLI's real
approval, choice and cancel screens, and check that the state and options are right. The coverage
table reads files on every request, but a changed table doesn't mean the PTY detection cache was
refreshed. `patternLines` is the number of file lines, comments included. A higher value doesn't
mean more effective patterns were added.

To check the status-report path separately from patterns, run this inside an **isolated test session**:

```bash
fsh pane report --state working --agent codex
fsh pane report --state done
```

These commands only verify state reporting; they don't verify CLI screen detection.
Avoid experiments that revert an existing TOML with `git checkout`, since that can wipe other
people's changes. Restore only the part you edited.

## Related

- `fsh help agent-state` — the four states (`idle`/`working`/`waiting`/`done`) and the full
  Claude Code hook path
- `server/agent_prompt_detect.py` — the actual matching logic (window, flap guard, ANSI stripping)
- Settings → Agent tab — per-CLI detection coverage table
