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
- **View**: one arrangement of panes. A view with one pane is a single view; with two or more it's a group view. The desktop has no row of view tabs — click a session or group in the sidebar's Sessions view to go to its view.
- **Open**: a session is placed in a pane of this browser tab. It describes placement, not state.
- Each browser tab saves its own view layout. After a reload, **sessions you closed don't reopen.**

### Title bar and status bar (desktop, 720px and wider)

- **Title bar** (top): from the left, the **FarShell menu** (new session, quick open, focus mode, copy access URL, settings,
  status bar settings, clear cache, sign out; the number of pending approvals shows as a badge. "Clear cache" removes the
  saved app files and reloads — your settings and login stay (a phone has no title bar, so the same thing is the "Clear cache" button in Settings → About → App files). "Sign out" ends this browser's login only; the device registration stays), the **Mac** this screen shows (if you have
  paired Macs, click it to switch the whole screen to another one), and the active pane's **repository › branch ›
  session** (hidden below 1280px). Click the **Search or run a command** field in the middle, or press `Cmd+K`, to open
  the palette. The focus mode button (four-corners icon), the key bar button (touch devices) and window buttons sit on the right.
- **Status bar** (bottom): the **agents chip** (`Needs you N · Working N · Done N`; when something
  needs you, click it to jump to the session that has waited longest) is pinned at the left end, and short messages
  (copied, saved…) appear briefly just to its right. Then E2E, safe mode and connected screens, and on the right CPU,
  GPU, RAM, network, usage, notifications, version and clock. A chip with no value is hidden, and when space runs out
  the less important chips hide first. Choose what goes where in Settings → **Status bar** (saved on this device only;
  the agents chip can't be moved or turned off).
- Phones (narrower than 720px) have neither bar; the top bar and the bottom tabs do that job.
- **Focus mode** (wide screens): turn it on and off with `Cmd+.` (`Alt+Shift+.` off the Mac), the button at the right of the
  title bar, the FarShell menu or the palette ("Focus mode"). It hides the activity bar, sidebar, title bar and status bar so
  the panes reach the window edges (terminals resize only once on the way in and once on the way out). Rest the pointer on the
  left, top or bottom edge and that bar floats over the panes (the panes keep their size); move away and it hides again — the
  left one also closes at once when you pick a session or press `Esc`. When a session is waiting for approval, a pill at the
  top right shows "Needs you N · name · kind"; click it to go there, or click "End" on its right to leave focus mode. Toasts,
  the approval dialog and the palette still appear. In Settings → Appearance, "Focus mode" sets showing bars at the edges, the
  needs-you pill and the edge reveal delay (instant / 250ms / 500ms). Tablets without a mouse use an exit button at the bottom
  left instead of the edges. Reloading the page or crossing 720px in window width turns it off.
- **Phone mic**: the round mic floating at the bottom right of the Terminal tab (above the bottom tabs, or above the key bar when it is open)
  starts recording; tap again to stop. A dot on it means voice needs setting up (long-press for the voice panel).
- **Phone focus mode**: the first button in the Terminal tab's top bar (the four-corners icon) hides the top bar and bottom tabs and leaves
  the terminal and key bar. The mic moves to the first key bar key. A small pill at the top right shows a session waiting for approval as
  "Needs you N name"; tap it to open that session. To leave, tap the pill's exit button or pull down from the very top edge and let go
  (a short pull snaps back).

### Workbench — activity bar and sidebar (desktop, 720px and wider)

- **Activity bar** (the 48px vertical bar on the far left): the top group picks what the sidebar shows — Sessions · Changes · Files ·
  Queue · Attachments · Snippets · Ports · Usage · System (Ports and Usage only when available). The bottom group is MCP · skills,
  the mic and Settings (with the number of pending approvals as a badge). The current activity has a 2px blue line on its left.
  Hover an icon for its name.
- **Sidebar**: one column to the right of the activity bar. Every activity shares its width (360 by default; drag its right edge,
  240–480), so switching activities never resizes the terminals. Click the current activity again, or press `Cmd+B` (`Alt+Shift+B`
  off the Mac), to collapse or expand it. Sessions collapse to a 48px strip, tools to nothing. Drag a tool icon onto a pane to open
  that tool in the pane.
- **Pin to the right**: the 「Pin to the right (side by side)」 button in a tool panel's header pins that tool to the right 300px of
  the window and puts the left sidebar back on Sessions, so you see the session list and a tool such as Changes or Queue side by side.
  The pinned tool's activity icon gets a small blue dot in its top-right corner. The same button (「Unpin from the right」) or closing
  the panel unpins it. At 1280px and wider it takes room beside the panes; narrower, it floats over them as a drawer (pane sizes
  stay the same). Focus mode hides it. Saved on this device only.

### Sidebar Sessions view (session list)

Sessions are grouped by repository. A repository head shows its name and main branch, and a session stays under the repository it started in (even after `cd`).
A session working in another worktree of the same repository sits under a "Worktree <branch>" branch; sessions outside any repository are listed under "Outside a repository" at the bottom.
Sessions waiting for input move up to the "Needs attention" section. Repositories with a waiting session come first, then by most recent activity.
Each cell has two lines: state ring and agent · name · elapsed · ⋯ / (only when there is something to say) waiting kind · tool at work · "Finished · not seen" · your last instruction, and the change count.
Open "Ended N" at the bottom of a repository to see the sessions that ended there; the "Resume" button is always visible. Clicking an ended session shows its linked conversations and where and under which name a resume will open.

| Action | Result |
|------|------|
| Click a row (session not open) | Opens it in a new view. If it belonged to a group view you closed, the whole group reopens |
| Click a row (session already open) | Goes to that view and focuses the pane |
| Row ⋯ / right-click / long-press (menu) | The top line of the menu summarizes status · (if waiting for input) type · Non-web N · origin · branch (touch has no hover tooltip). It's an info line; tapping it does nothing |
| Drag a row to a pane edge | Splits in that direction — this is how you group sessions (touch devices can also use the menu items below) |
| Row menu "Open to the right", "Open below" | Shown only on touch devices at least 720px wide (tablets). Splits the active pane in that direction and puts the session there (a session from another view is moved over, and a view left empty is closed). Not available on phones under 720px |
| Pane `×` | The session becomes "Not open" in the sidebar (tmux keeps running). The pane stays as an empty pane |
| A session in a split-screen group | A small pane mark sits next to the row name. The row menu's "Open the whole group" (or clicking a row of a closed group) opens the whole group. Closing the group's last pane closes the group view and its layout is remembered |
| Row `⋯` or right-click | Open/close · Rename · Open on Mac · Connected screens · agent sharing items (below) · **Kill** |

- **Kill** ends the tmux session, and the claude or shell inside it, with no undo. A confirmation always appears,
  and it warns you if the session is working or waiting for input. Panes open in other browser tabs become empty panes.
- **Mobile session management**: tap the header session name to switch between the Sessions tab and the terminal. The Sessions tab shows two-column tiles grouped by repository (a tile waiting for input gets an amber border); the repository header's "+" opens a new session in that repository. Long-press a tile to open, close, rename or stop it, and tap the "Ended N" tile to see that repository's ended sessions with a Resume button. You answer prompts in the terminal, not from the list.
- **Mobile navigation order**: swipe left/right, the header's `n/m`, and the palette use the same order.
  It goes view order → pane order within a view (top left first), skips empty panes, and wraps from the end to the start. Navigating never moves a session;
  it only goes to that session's view and pane. To change the order, enable Edit views on the mobile Sessions tab
  and use the up/down buttons per view. A group view moves as one.
  While editing, each view also has a Close button (the session stays alive, a group view's layout is remembered, and closing the last view
  shows the start screen). Each view's pane count (empty, file and panel panes included) is shown there too. Opening the Sessions tab on a phone automatically cleans up empty single views ("Empty screen").
- **Agents and session sharing**: lets an agent (Claude Code, for example) read and type into **only the sessions you've shared** through the FarShell MCP tools.
  Agents can't create or close sessions.
  - **Sharing**: row menu "Sharing and permissions…" → in the "Sharing and permissions · session name" dialog choose a nickname (1-32 characters: letters, digits, `_`, `-`) and a mode (Read-only / Read & write), then press "Share".
    A shared row shows an eye (read-only) or pen (read & write) icon with the nickname, and it also appears in the summary line at the top of the menu.
    To change it, edit the nickname or mode in the same dialog and press "Save"; to end it, press "Unshare" (every permission and pending request for that session disappears immediately).
  - **Approving**: the first time an agent uses a shared session, an "Agent access request" dialog appears. Read and write are asked separately,
    and a write request also offers "Allow read only". The default choice is Deny, and for a moment after the dialog appears, presses are ignored
    (so keys you were typing don't approve it). Once allowed, that agent keeps access even if it runs `/new` or `/clear`.
    "Later" (Esc) is a deferral, not a denial. Reopen it from Settings > Permissions > "Waiting for approval" (the number badge on the Settings button is the pending count).
  - **Revoking**: in the row menu's "Sharing and permissions…", under "Permissions granted for this session", press "Revoke input only" (keeps read) or "Revoke" for each permission.
    The full list can be filtered in Settings > Permissions > "Granted access", and recent access is in "Audit log"
    (what was typed, shown or sent is never recorded — only its length and the first 8 characters of a hash). After a revoke, the agent's next call goes back to waiting for approval.
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
- The sliders icon in the Sessions view header opens the **session list settings** sheet. The "Repository" tab picks which repositories the list shows, the "View" tab shows or hides the "Needs attention" section and sessions by origin (other Macs, external) on this device, and the "Cleanup" tab holds this Mac's session record settings: auto-archive ended sessions (never deletes), name sessions after the repository when a CLI starts, and end a resumed session when its CLI exits. Changing "Cleanup" asks for your password again.

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

If you want plain drag to copy without Shift, put `set -g mouse off` in `~/.config/vt/tmux.local.conf` and run `fsh tmux reload`.
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
| **Search** | `Cmd+F` / `Ctrl+Shift+F` → scrollback search, `Alt+Shift+F` → in-terminal search (move with ▲▼). The old `Cmd+Shift+F` now searches file contents (a key you set yourself is kept) |
| **File content search** | In the palette (`Cmd+K`) type `?` and the text (`? groupByZone`) — results are grouped by file with the match highlighted. ↑↓ move, Enter opens the file at that line, `Cmd+Enter` in a new pane, `⌥Enter` moves the search to the sidebar. Options: `⌥C` match case, `⌥W` whole word, `⌥R` regex (plain text by default), include and exclude globs (`src/**, *.ts` / `**/*.test.*`). It searches the current session's repository. `Cmd+Shift+F` (Mac) / `Ctrl+Shift+G` opens the sidebar 「Files › Search contents」, which searches under the tree's current folder. Files in `.gitignore`, secrets such as `.env` and key files, files over 2 MB and binaries are not searched. It stops at 10 seconds or 500 result lines and shows what it found so far. When you have switched to another Mac, it searches that Mac's files |
| **Upload files** | `⋯ → Upload files` → saved to `~/.vt/files/` |
| **Grid view** | The grid icon at the top → live previews of every tmux session (pushed only on change) |
| **Theme** | Settings (`Cmd+,`) → Appearance → switch skin (remembered) |
| **Also open on Mac** | Turn the `⋯` toggle on → opening a new session also opens an iTerm window (macOS) |
| **Handoff** | `fsh handoff mobile` (to your phone) / `desktop` (to your Mac) |
| **File tree** | The sidebar's Files activity. Only the rows on screen are drawn, so big folders open at once; a folder shows 1000 entries and 「Load more」 continues. Keyboard: ↑↓ move, ←→ collapse / expand, Enter open, ⌥Enter insert the path into the terminal, ⇧F10 row menu, type to jump to a name, `/` name filter (`rail`, `*.tsx`). Right-click menu: open · insert path · copy path · download · share link · refresh. Files that changed on disk get a blue dot and their folders a ring until you open them. Row height: Settings → Appearance → 「Compact file tree」 (28px → 22px) |
| **Previews** | Markdown opens rendered; the 「Rendered \| Source」 switch in the top bar flips it (raw HTML shows as text, external images are blocked). CSV/TSV open as a table (up to 1 MB), SVG as a picture only (its scripts never run), video (mp4, webm) and audio (mp3, m4a, wav) in the browser's player. PDFs are not previewed — download only |
| **Download** | Get the real file from the tree's row menu, the viewer header (also on the phone), the pane viewer or the binary/PDF screen. If the file on disk differs from the version you were viewing, nothing is downloaded and you are told why. Size limit: `VT_MAX_UPLOAD_MB`. Files on this computer only — while viewing a remote host the buttons are dimmed with the reason |
| **Edit and save** | 「Edit」 in the viewer's top bar edits the file in place (no autosave). Save with ⌘S / Ctrl+S or 「Save」; the first save asks for your **web login password** once (kept for 15 minutes on this device). Works for text files up to 2 MB that are fully loaded, with a known encoding and one kind of line ending (otherwise 「Edit」 is dimmed with the reason). If the file changed elsewhere before you save, nothing is overwritten and a conflict banner appears — 「Compare」 · 「Reload」 · 「Overwrite with mine」. While editing, the gutter next to the line numbers marks changed lines (added = green bar, modified = dashed blue bar, deleted = red wedge, relative to the version loaded or last saved). Closing the viewer with unsaved edits (×, Esc, the phone's back button), opening another file in it or closing its pane asks 「Save / Discard / Cancel」 (Esc and the default focus are Cancel; Save closes only after the save succeeds). Moving the viewer to another surface does not ask: the edits are kept and continue there. On a phone the edit bar stays at the top, so 「Save」 is visible above the keyboard |
| **New file, new folder, rename, delete** | The tree's row menu (「New file in this folder…」 · 「New folder here…」 on a folder row · 「Rename…」 · 「Move to Trash…」) and the viewer's top bar. An existing name is never overwritten. Deleting only **moves the file to the OS trash**, so it can be restored (Finder 「Put Back」 on macOS, the desktop trash on Linux); where no trash can be used, nothing is deleted and you are told why. A folder goes to the trash with its contents. Symlinks, a browse or write root itself and moving a folder into itself are refused. Writes work only on this computer's files, inside the write boundary (`VT_WRITE_ROOTS`, the browse boundary by default), and never inside execution-config folders such as `.github` or `.vscode` |
| **Phone 「Files」 tab** | The 4th bottom tab. It opens the file tree; the 「Files \| Attachments」 switch at the top reaches the attachments screen (the last side is remembered). The upload badge sits on this tab. A long press on a row opens the row menu as a bottom sheet |
| **Landscape phone + soft keyboard** | (Automatic focus) When the height visible above the keyboard drops below 300px, the tab bar, pane headers and workbench hide and the terminal uses all the remaining height. Dismiss the keyboard and the usual screen returns. (On a portrait phone the bottom nav hides while the keyboard is up. This is intended.) |

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
| You closed a pane but the session is still in the sidebar | Expected. Closing only removes it from the pane; the session keeps running. To end it, use **Kill** in the row menu |

More troubleshooting: `fsh help troubleshoot`


## Permissions when resuming a conversation

Resume and Conversation archive's Continue conversation also restore the last recorded permissions.

"Archive" at the bottom of the session list lets you search archived sessions by name, conversation, branch, last instruction or folder, and the repository chips show only one repository's (root folder's) sessions.
"+ New session" asks for the repository first; the grey text on the right is the name the session will get. The "+" on a repository head opens a new session right there.
How raw conversations are kept (collection, archive limits, backup, replication with other Macs) is set in Settings › "Conversation archive". The conversation history window only searches and reads.
Claude bypass and Codex YOLO stay on or off as recorded. OFF is applied explicitly, so a later change
to global settings cannot turn it on. Codex also restores its approval policy, sandbox and approval reviewer.
A notice shows the restored mode after launch.

If records are missing, corrupt, conflicting or unsupported, FarShell refuses to start and explains why.
If only older Claude records saved without a profile distinction exist, resume directly in the CLI to record a new hook.
Legacy hooks without the PID needed for process verification still report state, but cannot supply a resume policy.
Codex permission restoration requires version 0.164.0 or newer. Unsupported Codex settings include custom permission profiles and network access in read-only mode.
Changes the CLI never recorded cannot be restored. FarShell execution grants are not inherited.
