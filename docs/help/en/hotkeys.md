# fsh help hotkeys — All hotkeys and how to change them

## Default hotkeys

### Desktop (Voice Daemon, while `fsh voice` is running)

| Key | Action |
|---|---|
| `Ctrl+Shift+M` | Toggle recording (start/stop) |
| Earphone Play/Pause | Toggle recording (same as `Ctrl+Shift+M`) |

### Web UI (mobile/browser)

| Action | Result |
|---|---|
| Click the mic button | Toggle recording |
| Click the voice-only button | Switch UI mode (hides the terminal) |
| Click the earphone button | Turn the media-key trigger on/off |
| Earphone Play/Pause | Toggle recording (when the earphone trigger is on) |
| `Cmd+F` / `Ctrl+Shift+F` | Scrollback search |
| `Alt+Shift+F` | In-terminal search (also on Mac — formerly `Cmd+Shift+F`) |
| `Cmd+Shift+F` / `Ctrl+Shift+G` | File content search — the sidebar 「Files › Search contents」. In the palette, the `?` prefix |
| `Cmd+S` / `Ctrl+S` (while editing in the viewer) | Save the file — the first save asks for your web login password once (kept 15 minutes). There is no autosave |
| `Cmd+.` / `Alt+Shift+.` | Focus mode on/off — hides every bar so the panes reach the window edges (rebind it in Settings → Keymap) |
| `⌥C` · `⌥W` · `⌥R` in the palette `?` mode | Toggle match case · whole word · regex (`⌥Enter` moves the search to the sidebar) |
| `⋯` on a rail row, or right-click | Session menu (open/close, rename, open on Mac, connected screens, kill) |

## Change a hotkey

    fsh hotkey list                   # show current settings
    fsh hotkey set voice ctrl+shift+x # change it
    fsh hotkey reset voice            # restore the default
    fsh hotkey disable voice          # disable it

These are stored in `~/.vt.env` as:

    VT_HOTKEY_VOICE="ctrl+shift+x"
    VT_HOTKEY_VOICE_DISABLED="false"

Restart the daemon after changing:

    fsh restart --voice

## Key token format

Join modifiers and a key with `+`:

| Token | Meaning |
|---|---|
| `ctrl`, `control` | Control |
| `shift` | Shift |
| `alt`, `option`, `opt` | Alt/Option |
| `cmd`, `command`, `meta`, `super` | Cmd/Win/Super |
| `v`, `a`, `1`, `2`, ... | Letter/number keys |
| `f1` ~ `f12` | Function keys |

Examples:
- `ctrl+shift+m` (default)
- `cmd+shift+m`
- `alt+f1`

## Platform notes

### macOS
- Allow your terminal app under System Settings → Privacy → Accessibility.
- If the key conflicts with a macOS system shortcut, it fails silently.

### Linux
- Works normally on X11.
- **On Wayland, global hotkey capture is restricted by the security policy.** If `XDG_SESSION_TYPE=wayland`, the daemon prints a warning. Use the mobile 🎤 button or an X11 session instead.

### WSL2
- Needs WSLg (Windows 11) or X11. Without them, use the mobile 🎤 instead.

## Media key (earphone) toggle

To stop the earphone Play/Pause from triggering voice input:

### Mobile (browser)
Tap the 🎵 earphone button on the voice bar. When it's off, the OS keeps its default media controls (music play/pause works normally).

### Desktop (`~/.vt.env`)
    VT_VOICE_MEDIA_KEYS="off"

Then restart the daemon with `fsh restart --voice`.
