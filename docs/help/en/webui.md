# Web UI guide

Everything about using FarShell in the browser. (For the CLI see `fsh help concepts`; for voice see `fsh help voice`.)

---

## 1. Connecting

| Environment | How |
|------|------|
| Desktop | `http://localhost:7777` |
| Mobile on the same Wi-Fi | Run `fsh start --network lan`, then open the server's LAN IP and port |
| From anywhere (remote) | Run `fsh start`, then `fsh mobile` → the HTTPS URL / QR for your tunnel |
| Corporate network, etc. | `fsh start --network tailscale` (restricted to your tailnet) |

> When auth is set up, the `fsh mobile` URL carries a one-time device registration ticket that is valid for
> 5 minutes. Don't share this link. For regular access, sign in with your password.

---

## 2. Sessions and views

- **Session**: one tmux session. It keeps running until you kill it (the work inside, such as claude, keeps going too).
- **View**: one tab along the top. A view with one pane is a single view; with two or more it's a group view.
- **Open**: a session is placed in a pane of this browser tab. It describes placement, not state.
- Each browser tab saves its own view layout. After a reload, **sessions you closed don't reopen.**

### Left rail (session list)

Sessions are listed by name, and sessions waiting for input also appear in the "Needs attention" section at the top.
Each row has three lines: status, agent, name / branch, change count / status keyword (Approval request, Choice requested, Plan review, Question), elapsed time, origin.

| Action | Result |
|------|------|
| Click a row (session not open) | Opens it in a new view. If it belonged to a group view you closed, the whole group reopens |
| Click a row (session already open) | Goes to that view and focuses the pane |
| Row ⋯ / right-click / long-press (menu) | The top line of the menu summarizes status · (if waiting for input) type · Non-web N · origin · branch (touch has no hover tooltip). It's an info line; tapping it does nothing |
| Drag a row to a pane edge | Splits in that direction — this is how you group sessions (touch devices can also use the menu items below) |
| Row menu "Open to the right", "Open below" | Shown only on touch devices at least 720px wide (tablets). Splits the active pane in that direction and puts the session there (a session from another view is moved over, and a view left empty is closed). Not available on phones under 720px |
| Pane `×` | The session becomes "Not open" in the rail (tmux keeps running). The pane stays as an empty pane |
| View tab `×` | Closes only the view. A group view's layout is remembered, and you reopen it from the rail |
| Row `⋯` or right-click | Open/close · Rename · Open on Mac · Connected screens · agent sharing items (below) · **Kill** |

- **Kill** ends the tmux session, and the claude or shell inside it, with no undo. A confirmation always appears,
  and it warns you if the session is working or waiting for input. Panes open in other browser tabs become empty panes.
- **Mobile session sheet list**: shown in view order. A group view (two or more panes) appears under its view name (or "Group view" if unnamed)
  with its members together; a single view is one row. tmux sessions that aren't open appear below in the "Sessions not open" section, sorted by name.
- **Mobile navigation order**: swipe left/right, the next/previous tab keys, the session sheet's list and `n/m`, and the palette all use the same order.
  It goes view tab order → pane order within a view (top left first), skips empty panes, and wraps from the end to the start. Navigating never moves a session;
  it only goes to that session's view and pane. To change the order, drag the view tabs at the top on desktop, or on mobile tap the "Edit views" icon at the top of the session sheet (shown when there are two or more views)
  and use the up/down buttons per view (a group view moves as one, and closing the sheet ends editing). The rail stays sorted by name.
  While editing, each view also has a "Close" button, so you can close views on a phone (same as the tab `×`: the session stays alive, a group view's layout is remembered, and closing the last view
  shows the start screen). When you open the sheet on a phone, empty single views ("Empty screen") are cleaned up automatically.
- The **status bar** under the view tabs has one cell per session. Color is the state; a bold cell is a change you haven't checked yet.
- **Agents and session sharing**: lets an agent (Claude Code, for example) read and type into **only the sessions you've shared** through the FarShell MCP tools.
  Agents can't create or close sessions.
  - **Sharing**: row menu "Share with agent…" → choose a nickname (1-32 characters: letters, digits, `_`, `-`) and a mode (Read-only / Read & write).
    A shared row shows an eye (read-only) or pen (read & write) icon with the nickname, and it also appears in the summary line at the top of the menu.
    To change it, use "Sharing settings…"; to end it, use "Unshare" (every permission for that session disappears immediately).
  - **Approving**: the first time an agent uses a shared session, an "Agent access request" dialog appears. Read and write are asked separately,
    and a write request also offers "Allow read only". The default choice is Deny, and for a moment after the dialog appears, presses are ignored
    (so keys you were typing don't approve it). Once allowed, that agent keeps access even if it runs `/new` or `/clear`.
    "Later" (Esc) is a deferral, not a denial. Reopen it from "Waiting for approval" in the row menu's "Agent permissions…".
  - **Revoking**: in the row menu's "Agent permissions…", use "Revoke all" or "Revoke write" per agent. You'll also see the last-used time and recent access log there
    (character count and the first 8 characters of the hash only. The text that was typed isn't stored). After a revoke, the agent's next call goes back to waiting for approval.
  - **Limits**: once shared, an agent reads **everything visible on that session's screen**. Don't share if passwords or tokens are visible.
    Read & write means the agent can run commands in that terminal. Text on the screen was printed by that program,
    so you can trust it only as far as you trust the program (prompt injection is possible), and sharing applies to just one active pane.
    This approval mechanism is meant to stop a fooled agent or an accidental call. A malicious process running with the same user's permissions that can
    read files freely can read the session signing key in `~/.vt.env`, forge a human session cookie and pass the approval
    (confirmed in practice). Run an agent you don't fully trust in a sandbox or under a separate user account
    so it can't read `~/.vt.env`.
    File mode 0600 alone can't isolate processes running as the same user. If you lower a share to read-only and then raise it back to
    read & write, the earlier write approval comes back without re-approval. To block it permanently, revoke write access or unshare.
    This feature isn't available on screens that show another Mac, and a session whose name is in Korean can't be shared (rename it in English).
    Approving and sharing don't work on a screen opened with a `?token=` link (password sign-in is required).
- The gear in the rail header opens **Rail settings**: show the "Needs attention" section, show by origin (other Macs, external), and show repos.

## 3. Where new sessions start

A new session (plain shell / new tmux) starts in your **home directory (`~`)** by default.
To start somewhere else, add this to `~/.vt.env`:

```bash
VT_START_DIR=~/projects
```

> Attaching to an existing tmux session keeps that session's original location. This setting applies only to
> **newly created** sessions.

---

## 4. Copy, paste, images

| To do this | Do this |
|--------------|------|
| **Copy** | Drag to select text → copies automatically (a "Copied" indicator appears) |
| Copy (shortcut) | Select, then `Cmd+C` (mac) / `Ctrl+Shift+C` (Linux/Win) |
| Copy (right-click) | Right-click while a selection exists |
| **Paste** | `Cmd+V` / `Ctrl+V`, or right-click (with no selection); long-press on mobile |
| **Paste an image** | Copy a screenshot or similar, then `Cmd+V` → uploads automatically → **the file path is inserted into the terminal** |

### Warning: drag-to-copy doesn't work — tmux mouse mode

tmux has mouse mode on by default (`set -g mouse on`), so **a plain drag is captured by tmux.**
For browser text selection (and the automatic copy) to work:

- **Hold `Shift` (or `Option` on mac) while dragging.** → The text is selected and copied automatically.
- Right-click paste and image paste work right away, regardless of mouse mode.

If you want plain drag to copy without Shift, set `set -g mouse off` in `config/vt-tmux.conf`.
But then the tmux scroll wheel and pane clicks stop working, and it applies to iTerm sessions too.

### Image workflow (Claude Code)

A pasted image is saved to `~/.vt/files/` on the server, and its path is typed into the terminal.
Hand that path to claude as is. (The upload files button does the same.)

> The terminal is a PTY that only accepts text (bytes), so it can't "paste" an image and display it.
> Instead, you upload it and pass the path.

---

## 5. Other features

| Feature | How |
|------|------|
| **Search** | `Ctrl+F` / `Cmd+F` → search the terminal contents (move with ▲▼) |
| **Upload files** | `⋯ → Upload files` → saved to `~/.vt/files/` |
| **Grid view** | The grid icon at the top → live previews of every tmux session (pushed only on change) |
| **Theme** | Settings (`Cmd+,`) → Appearance → switch skin (remembered) |
| **Also open on Mac** | Turn the `⋯` toggle on → opening a new session also opens an iTerm window (macOS) |
| **Handoff** | `fsh handoff mobile` (to your phone) / `desktop` (to your Mac) |
| **Landscape phone + soft keyboard** | When the height visible above the keyboard drops below 300px, the tab bar, pane headers and rail hide and the terminal uses all the remaining height. Dismiss the keyboard and the usual screen returns. (On a portrait phone the bottom nav hides while the keyboard is up. This is intended.) |

---

## 6. Voice bar (bottom right)

Shown only if you installed with voice mode.

- **Voice input**: tap → record → tap again → STT → text typed into the active session
- **Voice-only mode** (`⋯`): hides the terminal and shows just a big mic, for earphone control
- **Earphone media keys** (`⋯`): use the earphones' Play/Pause as the record toggle
- The desktop hotkey is `Ctrl+Shift+M` (while the voice daemon runs). Details in `fsh help voice`

---

## 7. Connection and offline behavior

- When the server connection drops, a "Disconnected from server" overlay appears and the app **reconnects automatically**, retrying indefinitely
  (exponential backoff, up to 30 seconds apart). It recovers by itself when the network returns.
- On reconnect, earlier output (scrollback) is restored.
- You can install it as a PWA (add to home screen). Static assets are cached, so it loads fast.

---

## 8. Common problems

| Symptom | Cause / fix |
|------|-------------|
| Drag-to-copy doesn't work | tmux mouse mode — **Shift+drag** (see section 4 above) |
| Paste doesn't work | Tap the terminal once to focus it, then `Cmd/Ctrl+V`. Reading the clipboard works only over HTTPS/localhost |
| TUI (claude) lines are misaligned | Switching views or reloading resyncs the size. If it keeps happening, see `fsh help troubleshoot` |
| New sessions open in the project folder | Set `VT_START_DIR` (section 3 above) — the default is home |
| You closed a pane but the session is still in the rail | Expected. Closing only removes it from the pane; the session keeps running. To end it, use **Kill** in the row menu |

More troubleshooting: `fsh help troubleshoot`
