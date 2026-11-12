# Terminal, panes, shell surface

## Features

| Feature | Description |
|------|------|
| Clipboard sync | OSC52 + `fsh clip` polling daemon (copies outside the terminal) → pushed to web clipboard. **OSC52 rules** (`term/osc52.js`): a tmux session (remote panes included) only ever delivers what tmux itself copies (copy-mode, `set-buffer -w`) — with `set-clipboard external` tmux drops requests from programs inside the pane; a plain terminal (no tmux) is **blocked by default** (one warning toast per session) and allowed with Settings › Terminal › `terminal.osc52Untrusted` (per device); anything over 1 MiB is ignored; the copy toast shows the first 40 characters and the length. Details and the measured tmux table in `docs/ref/voice.md` |
| Paste | **The server decides, not the browser.** The browser cannot know whether the app inside a pane turned bracketed paste on, so guessing would print `200~` literally in the wrong place, or auto-indent stair-stepping in the right one. The server reads the pane's real input mode — `ICANON` via termios, DECSET 2004 seen on the output stream — and decides. Path: WS `paste` message, or `POST /api/sessions/{id}/paste`. **tmux sessions are delegated to tmux itself** (`paste-buffer -p` looks at the pane's actual mode; `-S` on 3.7+ also turns off tmux's own sanitising since we already did it) — we do not guess a second time. Control characters are whitelisted by iTerm2's rule (tab · LF · FF · CR · `^V`); ESC is neutralised to `␛` only when bracketing, and simply dropped otherwise. In canonical mode a line over `PC_MAX_CANON` (1024B on macOS) does **not** get truncated — the whole line vanishes (verified on a real PTY) — so it is refused up front with a reason instead of disappearing. `fsh doctor paste` shows what this terminal actually sends |
| tmux session management | Create/attach/detach/kill tmux sessions from the web |
| tmux config | **The repo's `config/vt-tmux.conf` is always the base**; `VT_TMUX_CONF` replaces it entirely (isolated tests, `scripts/profile_sandbox.py`). Resolution lives in `tmux_runner._resolve_conf_path` and `bin/fsh`'s `_resolve_tmux_conf` and must stay in the same order. Personal settings go in `~/.config/vt/tmux.local.conf`, read by `source-file -q` on the last line of the base (missing = skipped, present = overrides). The base also sets `@fsh_conf_layered 1`, which `fsh doctor` reads from the live server because `#{config_files}` only lists start-up files and does not change on `source-file`. `fsh tmux reload` (`server/tmux_conf.py`, also run by `install.sh` and `fsh sync`) moves an old `~/.config/vt/tmux.conf` aside — the copy older installs made once and that then shadowed every later repo change: a byte-identical shipped version becomes `tmux.conf.bak-YYYYMMDD`, an edited one `tmux.conf.legacy` (never overwritten, never deleted; skipped when `VT_TMUX_CONF` is set) — then runs `source-file` only if the fsh tmux server is already up; it never starts or kills one. Lines removed from the config persist in the running server until it restarts |
| Scrollback buffer | Restores previous output on WS reconnect (the last 256 KB per session; storage keeps up to 512 KB, and 5000 chunks is only a safety cap) |
| Scrollback persistence | **Does not exist.** PTY output is never written to disk, so there is no `~/.vt/scrollback/` log. The `~` search palette greps only the live in-memory ring buffer, so it cannot reach finished sessions or output from before a server restart. Persisting arbitrary terminal output (including `cat .env`) would leave secrets remotely searchable, and redacting it would need a real parser, not a key=value heuristic. If the need arises, an explicit per-session "export this log" is the lighter alternative to silent accumulation. |
| Terminal search | Ctrl+F / Cmd+F → xterm.js search addon |
| Mobile text selection (`term/touch-select.js`) | **Does not create a browser Range at all.** Do not build it on native range selection over xterm's accessibility text layer: that layer redraws its rows on every PTY render and each redraw replaces the text nodes the selection is anchored to, collapsing the range mid-drag, so any output while selecting (a tmux status-line clock, a spinner) would kill the selection before Copy could run. Instead, long-pressing the terminal directly selects the word under the finger through **xterm's own selection model** (`term.select`/`getSelection`/`clearSelection` — the same API a desktop mouse-drag uses), painted by a separate renderer the accessibility layer's row-replacement never touches. Two self-drawn handle elements let you drag either end (recomputing a flat `row*cols+col` pair each move); `keybar.js`'s `setTerminalSelectionMode` still owns entry/exit (blur hidden textarea, hide keyboard, show the Copy (복사)·Select all (모두 선택)·Done (완료) bar) — `touch-select.js` owns what triggers it and what happens inside. Selection text stays byte-identical across 5s of continuous new output. Real-finger handle geometry on a physical device is not verified — see DESIGN.md §7. |
| Mobile auto-copy / auto-paste (`term/selection.js`) | **Disabled on touch devices**, regardless of the `mouse.autocopyOnSelect` setting (desktop drag-select is unaffected and still respects it). Copying on any selection ending in a `mouseup`, or running a PuTTY-style copy-or-paste on Android's long-press `contextmenu`, would show a clipboard permission prompt before the user had chosen to select anything and make the touch experience act on its own. |
| Session name editing | A rail session row's menu → rename — opened by the **⋯** button or by right-click (same menu, `rowMenuItems`) — or the mobile session sheet → rename (PATCH API; the tmux session name is also changed via `rename-session` — names may contain only letters, digits, dash and underscore (anything else is 400; a name already in use is 409), and the tmux session name is the single source of truth). There is no session tab strip, so there is no "double-click the tab" path — the single source of a session's name is its record (`sessionDisplayName` in `core/store.js`) |
| Split panes | The terminal area is a binary tree whose leaves can hold a terminal, code viewer, or dock panel (source control, queue, files, ports, usage). An empty pane's picker opens any of them; dock and pane instances of the same panel can coexist. Split from the pane header or by dropping a **rail session row** on a pane edge (5 drop zones); the divider is draggable. A **tool activity** in the activity bar is a drag source too — drop it on a pane and that leaf becomes a panel pane (edge = split that way, centre = replace what was there; the session itself stays alive in the rail). The sidebar collapses if the tool you dragged out was the one it had open, so the same panel is never shown in two places at once. Mouse only: compact has no activity bar and caps panes at 1, so there is nowhere to drag to on touch. Pane cap per width tier (compact 1 / regular 4 / wide 6, unlimited at xwide (1600px and up)) — over the cap the split buttons are disabled with the reason in their tooltip. Below 720px the same tree renders one pane at a time; coarse-pointer devices can swipe left/right between them. The layout is saved to `/api/workspace` and restored on load, including panel-only layouts with no live sessions; a leaf whose session died is demoted to an empty pane instead of a ghost |
| Workbench + command palette | There is no top-level ⋯ menu. The desktop's left edge is the **workbench** (`shell/Workbench.tsx`): a 48px activity bar (44/48/52 with Menu size (메뉴 크기) `ui.side.scale`) and **one sidebar whose width every activity shares** (`ui.side.width`, default 360 — 320/392 for the small/large menu size —, 240~480, ≤ 40vw, regular ≤ 260; old `ui.dock.width` → `ui.rail.width` are read as fallbacks, never rewritten). The Sessions (세션) activity shows the rail (sessions only: Needs attention (개입 필요) / Running (실행 중) / ended sessions, plus Conversation history (대화 기록), Rail settings (세션 목록 설정) and New session (새 세션)); the tool activities (Changes (변경), Files (파일), Queue (큐), Attachments (첨부), Snippets (스니펫), Ports (포트), Usage (사용량), System (시스템)) show the existing panels in the dock body (`shell/Dock.tsx`, `window.vtDockHost`). Both stay mounted and only toggle `hidden`, and switching activities never resizes the panes. Clicking the current activity or `railToggle` (⌘B / Alt+Shift+B) collapses the sidebar — sessions to the rail's 48px strip, tools to 0. The bottom group holds Manage MCP · skills (MCP · 스킬 관리), the mic and Settings (설정); Settings also opens from the title bar's FarShell menu or `Mod+,`. The same menu (and the palette, same labels) has Clear cache (캐시 지우기) (`app.clear-cache`: confirm, unregister service workers, delete all Cache Storage, re-request the page's static assets bypassing the HTTP cache, re-subscribe push if it was on, reload; `localStorage`/`ui.*` untouched; on a phone, where there is no title bar, the same action is the Clear cache (캐시 지우기) button in Settings › About (정보 및 진단) › App files (앱 파일), `panels/settings/sections/about.js` — it only calls `getAction('app.clear-cache')`, so the confirm dialog and the flow are the same, and its code lives in the settings chunk) and Sign out (로그아웃) (`session.logout`: `POST /api/auth/logout` — web session cookie only, the registered device stays; a no-op notice when no web password is set). Both actions are registered by `core/app-session-lazy.js`, bodies live in `shell/app-session.ts`. Dragging the sidebar's edge defers terminal fits until release (`window.vtSurfaceDrag`, the refit gate in `layout/surface.js`), so each pane sends at most one WS `resize`. `Mod+K` is the keyboard path. The palette shows each command's current key binding, read from the keymap registry. **Focus mode** (`layout/focus.js`, `focusMode` ⌘. / Alt+Shift+.) zeroes only the bars' reservations (`--vt-activity-w`, `--vt-side-w`, `--titlebar-h`, `--statusbar-h`), so entering or leaving refits each visible pane once (one WS `resize`), and revealing a bar from a screen edge floats it over the panes without any refit; crossing 720px ends it |
| Settings + keymap | `Mod+,`. General settings remain shared; keyboard overrides use device-profile `keymap.bindingsV2` after the first edit (legacy global overrides are read until then). Key bindings are rebindable, and **`passthrough` executes the app action and also forwards the key to the original terminal; terminal-only delivery skips the app action** — that's how you get `Mod+F` back as the shell's `forward-char`. Mouse section can turn off "forward mouse events to the app", making drag-select always work even under vim/tmux mouse mode. **Defaults differ per platform** — see the row below |
| Settings persistence | Personal settings live outside the checkout: shared settings (including skins) in `~/.config/vt/workspace.json`, screen settings in `~/.vt/device-settings/profile{mobile,mac,windows,linux}.json` (0600; paths can be overridden). Browser registration and cookie expiry do not delete the profiles. Existing cookie settings migrate without overwriting profile values. Writes send only changed keys; server-loaded skins apply to the page and open terminals without writing them back. Restart/build/update preserves these files. |
| Platform key defaults | `Mod` is Cmd on macOS but **Ctrl everywhere else**, so the mac-safe defaults would take keys the shell and tmux actually use. `keymap.js` carries a `defNonMac` per action: `search` Ctrl+Shift+F, `palette` Ctrl+Shift+K, `splitRight` Ctrl+Shift+D, `contentSearch` Ctrl+Shift+G (`Ctrl+Shift+F` is taken by `search`), `splitDown` Alt+Shift+D, `paneClose` Alt+Shift+W, `railToggle` Alt+Shift+B, `worktreeNew` Alt+Shift+N. `searchInPane` is Alt+Shift+F on every platform (macOS gave its old ⌘⇧F to `contentSearch`, file content search). `keymap.match` tries user-set combos before defaults, so a moved default never overrides a binding the user chose. **`paneClose` and `worktreeNew` use Ctrl+Shift+W / Ctrl+Shift+N on macOS too**: Cmd+Shift+W / Cmd+Shift+N are the browser's close-window and incognito, so they never reach the page in a normal tab. Rule: prefer `Ctrl+Shift+…` (a terminal cannot encode it), drop to `Alt+Shift+…` when that slot is taken or the browser owns it (Ctrl+Shift+W closes the window, Ctrl+Shift+N opens incognito, Ctrl+Shift+B toggles the bookmarks bar); the reserved list also carries the Shift variants and is compared **normalized**, so a combo recorded in the settings screen ('mod+shift+w') is caught too. `Ctrl+Alt` is avoided for most defaults because it is AltGr on European layouts. `Mod+…` elsewhere in these docs means the macOS default. Rebindings use the current device profile; explicit Meta remains distinct from Ctrl outside macOS |
| Connected screens | Open from the status bar chip or a session row's menu. Each browser tab has its own tmux attach PTY, keyed by a tab-local screen ID that survives reload; sharing one PTY per tmux name across browsers would make two web screens appear as one and impossible to detach separately. The panel lists clients with a You (나) badge; Keep only this screen (이 화면만 남기기) detaches the rest (including a Mac iTerm2 window). The selected web session id stays fixed while the panel is open; the server derives its tty and refuses to detach it. Five seconds after the last WebSocket leaves, that tab's tmux client is released while the tmux session remains; reconnecting revives it. A failed list request stays visible with a retry button. A remote host that refuses control shows the list as read-only. |
| Session cards (pane-picker, palette) | The "pick a session" sheet you get from clicking an empty pane, and the command palette's session rows, both reuse the same card builder (`agent/preview.js`). **Cards show no live preview**: the meta line is a static `cwd` string filled from the same `/api/tmux/sessions` response the card already needs for its title/command, so no socket is opened per card. The `/ws-preview` server endpoint exists, but the frontend does not open it |
| Rail | **Rail rows are tmux sessions** in two zones — Needs attention (waiting; collapsible, switchable in rail settings) and Running (group headers with pane-order members, remaining singles in name order) — three lines each: state icon (+unseen ring) · agent · name / workspace kind (Main (메인)/WT) · workspace-relative directory · branch (main too) · diff / state keyword · elapsed · origin icon, plus "Other screens N" (「다른 화면 N」) when they apply (`shell/RailRow.tsx`, `rail-data.ts`; details under "Session and view rules → Rail"). **A group is derived from the screen, not stored** — a view whose pane tree holds 2+ sessions is a grouped view; you make one by dragging a rail row onto the terminal area (`layout/dnd.js`) and name it from the group header’s Edit (편집) button (`window.vtRenameTab`; `tabDisplayLabel()` computes member names when unset). Row verbs are Open (열기) / Close (닫기) / Kill (종료) (`term/session-actions.js`); the ⋯ button and right-click open the same `Portal`-rendered menu (`rowMenuItems`). There is no sleep/wake (재우기/깨우기) and no "grouped / ungrouped / sleeping" zone (「묶음 / 묶지 않음 / 잠자는 중」), and no `@fsh_grp` tag, `group_store.py` or `/api/groups*`: groups are never stored. |
| Views (mechanics for the row above) | There is no visible tab strip: the rail switches, names and closes views. **Each view owns its own pane tree** — switch away and back and that layout is exactly as you left it. A view has no identity of its own beyond its pane-tree members (a group *is* its pane-tree members): `openLayoutTab` dedups on `findTabBySessionId(sessionId)`. `worktreeId` is "which worktree this view is currently looking at" (`setTabWorktree`), shown as a small **branch chip next to the pane header** (`layout/panes.js`, `layout/branch-chips.js`, `term/tab-worktree.js`'s `branchChipMap`) using the same current-cwd context as the session lists. Two worktrees of the same repo cannot be two separate views; open them as split panes instead. The layout snapshot is `v6` (`{tabs:[{id,worktreeId,hostId,label,tree}], activeTab, screen, closed}`), saved per browser tab; `v1`–`v5` are still read, and `repoId`/`groupId` on old snapshots are dropped on load. Closing a view (`closeView`) closes every session it held and remembers a grouped view's composition — tmux is never touched. The last view can be closed too: the store then keeps one unlabeled empty-pane view that reads as "no views" (onboarding). Selecting a view focuses its top-left pane. Each view carries a segmented state bar (one segment per session; see "View tab" below). Dragging a session out of another view leaves an empty pane there, and closes that view if it ends up wholly empty (`closeViewIfEmptied`). The same session is never placed in two views: the surface layer moves one DOM node around |
| tmux detach detection | Shows `[process exited]` on PTY EOF |
| kitty keyboard protocol | Sends combinations that legacy encodings have no room for — `Ctrl+Shift+<letter>`, `Shift+Enter`, `Ctrl+Enter`, `Ctrl+Tab` — as CSI u (`frontend/js/term/kitty-keys.js`). Encoding happens **only while an app has turned the mode on** (`CSI > flags u`); otherwise a shell that never enabled it would literally print `\x1b[97;6u`. The mode is a push/pop stack, so a vim that opens and closes inside a shell restores the previous value. Routing is decided once before encoding; app-only shortcuts never reach the PTY. Only implemented flag 1 is advertised. ⚠ **Layer 3 (tmux `extended-keys`) is off by default** — it changes the user's tmux server options, so it is a trial switch, not part of the base config: `fsh tmux extkeys on|off|status` (see «Extended-keys trial» below). With tmux 3.3+ and that option off, tmux blocks CSI u before it reaches the inner app (`set -s extended-keys on`) |
| Skins | Settings → "Appearance (모양)" (Mod+, or the FarShell menu's Settings item) is the **only** place to switch skins — the 9 skin chips in Settings → Appearance (farshell/macos/catppuccin/windows/vscode/notepad/warp/linear/vercel, `theme.js`'s `vtSkins()`) render there. The command palette (Mod+K → "테마") also lists all skins, reading `vtSkins()` directly rather than scanning the DOM — so it works even when the Settings panel isn't open. There is no skin that imports a Ghostty config or Warp theme YAML. |

## Extended-keys trial

The base config keeps tmux `extended-keys off`. Until the trial below has results, nothing in the repo turns it on.
`fsh tmux extkeys on|off|status` (`server/tmux_conf.py`) is the switch:

- `on` writes a marked block to `~/.config/vt/tmux.local.conf` and runs the same reload as `fsh tmux reload`:
  `# >>> fsh extkeys` · `set -sq extended-keys on` · `set -sq 'terminal-features[90]' 'xterm*:extkeys'` · `# <<< fsh extkeys`.
  The numbered `terminal-features[90]` assignment is used instead of `set -as` because an append stacks one more
  identical entry on every reload, while the numbered form stays a single entry and `set -u` removes exactly it
  (checked on tmux 3.7c with an isolated socket). `-q` keeps tmux older than 3.2 from erroring; `on` itself refuses below 3.2.
- `off` removes only the marked block (other lines are kept), reloads, then sets `extended-keys off` and unsets
  `terminal-features[90]` on the running server, because a reload does not undo values that left the config.
- `status` prints the live `show -s extended-keys`, whether `extkeys` is in the live `terminal-features`, whether the block
  is in the file, the tmux version, and a warning when file and server disagree or `VT_TMUX_CONF` bypasses the local file.
- Both directions are idempotent. A block whose begin/end markers do not pair is never edited (the command stops with an error).
- Clients attached before the change may take it up late; reopen the tab or pane.

Checklist (run `fsh tmux extkeys on`, then each item; record results in the TODOS entry):

1. Mac physical keyboard, Safari and Chrome: `Shift+Enter` inserts a newline in Claude Code's prompt; `Ctrl+Enter`, `Ctrl+Shift+<letter>` reach the app.
2. Android on-screen keyboard bar: `Shift+Enter` inserts a newline.
3. Plain shell (no app running): pressing those combinations does **not** print a literal `\x1b[...u` / `[13;2u`.
4. vim: modifier combinations still work, and after quitting vim the shell prompt is unaffected.
5. Codex and Claude Code: their own shortcuts (newline, interrupt, history) still work.
6. iTerm2 attached to the same session at the same time (`tmux -L fsh attach`): both clients behave, neither shows stray escape text.
7. Revert with `fsh tmux extkeys off`, then `fsh tmux extkeys status` shows `off`, no block, and `extkeys` absent.

If any item fails, run `off` first and write down which item, browser/keyboard and what appeared; the base config stays unchanged.

## Session and view rules

> This section describes what the code does, and the Features rows above agree with it. If you change one, change
> both.

### Terms

| Term (UI) | Meaning |
|---|---|
| Session (Sessions (세션)) | A durable logical task in the catalog. A tmux runtime is one execution of it; closing a view never stops execution. Legacy live APIs still address tmux sessions. |
| View (뷰) | One header tab. Holds one or more panes. 1 pane = single view, 2+ = grouped view. |
| Pane (칸) | One region of a view. Holds one session, or is empty. |
| Open (열림) | The session is placed in a pane of this browser tab. A placement, not a state. |
| Browser tab | One FarShell browser tab. Each has its own view layout. |

There is no "sleep/wake" (재우기/깨우기) concept. Verbs: **open, close, move, kill**.

### Invariants

1. Within one browser tab a session sits in at most one pane.
2. The "current session" is not stored separately — it is the focused pane of the active view.
3. Reload never reopens a session or view the user closed. Only the saved layout is restored.
4. The layout is saved per browser tab. A new browser tab starts from a copy of the most recently saved layout.
5. Authentication expiry is never session termination. Boot waits for login, and a 401/403 or invalid attach response leaves the saved layout intact. Only a confirmed missing local session becomes an empty pane — and "confirmed" means tmux itself said so: `tmux_runner.session_state` returns `missing` only for `can't find session` / `no server running` / `error connecting to`; a timeout or any other failure is `unknown`, which `/api/tmux/attach` answers with 503 (retried by boot), never 404. Even a confirmed miss keeps the pane's tmux name (`lost`, `layout/persist.js`) through later saves, and `recoverLostPanes` re-attaches it when the name reappears in a `tmux_sessions` push (never into a session already open elsewhere). Re-login restores the server copy before a potentially damaged local cache; view titles, splits and remembered closed groups stay in that snapshot. A terminal WebSocket authentication failure opens the login gate while retaining its view. Pure PTY processes can only be restored while the server still owns them; tmux-backed sessions reconnect by name.

### Actions

| Action | Result |
|---|---|
| Click a rail row, session not open | Opens it in a new single view |
| Click a rail row whose closed grouped view is remembered | Reopens that whole grouped view |
| Click a rail row, session already open | Goes to that view and focuses its pane |
| Drag a rail row or a pane onto a pane edge | Splits that pane in the drop direction. **The only way to group** (touch devices at >= 720px also have the row-menu items below). The source pane becomes empty — and if that empties its whole view (another view than the one you dropped into), that view is closed automatically (`closeViewIfEmptied`). Pane × on a sole session is different: the view stays with an empty pane. With only one view nothing is closed |
| Row menu Open to the right (오른쪽에 나란히 열기)Open below (아래에 열기) (**touch only**) | Splits the **active pane** right / down and places that session in the new pane — the same edge handling as the drag above (`applyPaneDrop` in `layout/dnd.js`): a session from another view is moved, and if that empties its view the view is closed (`closeViewIfEmptied`); a session that is not open is attached first. Offered **only at width >= 720px with `(pointer: coarse)`** (`touchSplitAvailable` in `term/session-actions.js`; also hidden when the pane cap is reached or the session already sits in the active pane). Mobile devices (including landscape and desktop-mode iPad) and windows below 720px cannot create splits; existing grouped layouts stay intact and show one active pane at a time. The items are not offered there. **This is a touch-only relaxation of 「grouping is drag-only」**: touch drag is deliberately not built (long-press already opens the row menu), so a tablet would otherwise have no way to group at all; a mouse device keeps drag as the only way |
| Pane × | The session leaves the group and becomes "not open" in the rail; the pane stays as an empty pane (a session that actually *ends* is different — next row). Same function as the rail row menu's Close (닫기), the mobile session sheet and Fleet (`term/session-actions.js` `closeSession` / `closePaneAction`) |
| Empty pane | Click to pick a session; its own × removes the pane |
| View × | Closes the view only (no confirmation; shown even when it is the last view — closing the last one shows the onboarding screen). A grouped view's composition (tree + label, members by tmux name) is remembered; a session belongs to at most one remembered group. Memory policy: at most 10 entries (oldest dropped first); on every `tmux_sessions` push members whose tmux session no longer exists are removed (an entry with fewer than 2 members is deleted; an empty list is never trusted; remote-host members are left alone); reopening keeps the entry until at least one member attached — all failed = entry stays plus a toast, some failed = those panes stay empty plus a one-line toast |
| A session ends | **Confirmed end only** — and the server makes sure that signal exists: tmux runs with `detach-on-destroy off`, so when a session dies its web client is moved to another session and the socket would never close (the pane then showed some unrelated session). `deps.apply_tmux_state` remembers the tmux session id (`$3`, unchanged by renames) each web PTY was attached to and, when that client is found on a different session while its id is gone from the live list, destroys that PTY so the socket ends with 4004. A rename, a deliberate `switch-client` (original session alive) or a PTY whose id was never recorded does not trigger it. Then: the terminal socket closes with 4004 and the server's tmux list (a successful response) no longer has the name, or the user confirms Kill (종료). If that was the **last session of its view** — a single view, or a group whose other members already ended and left only plain empty panes — the **view is closed** (`closeEndedSession` in `term/session-actions.js`; the last view becomes the onboarding screen) and a toast says the session can be resumed from the rail. A group that still holds another session, a viewer or panel pane, or a pane waiting to be restored keeps its view and only that pane becomes empty, exactly like Pane ×. A restore after a restart or reboot is **not** a confirmed end: those panes stay (`lost` / Execution needs verification (실행 확인 필요)) so a reboot never wipes a layout. Remote-host sessions do not get this signal (a host that is briefly down looks the same as a dead session). A resumed session reuses a Execution needs verification pane of the same catalog session when one exists (`stalePaneByCatalog`), otherwise it opens as a new single view |
| View tab click · right-click menu | There is no tab strip, so these do not exist. Switching = click the rail row (goes to that view, focuses its pane); renaming = the rail group header's Edit (편집) (`renameView`); closing = the rail row's Close / pane × (`closeView`). `layout/branch-chips.js` feeds the pane-header branch chip |
| Kill | Always confirmed; warns when working/waiting. Its pane becomes empty in every browser tab |
| New session | Opens in a new single view |

There is no "drag a pane onto the tab bar" and no "ungroup" action — leaving a group is pane ×. ⌘/Ctrl-click on a rail
row does not split the current view.

Implementation notes: the active session is derived in `layout/store.js` (`_deriveActiveSession`) from the
active view's focused pane — `null` on an empty pane. The store never holds zero tabs; one unlabeled tab with one empty
pane reads as "no views" (`isLayoutEmpty`: tab bar hidden, onboarding shown). Reopening a remembered group attaches its
members and rebuilds the tree; a member already open elsewhere in this browser tab is not moved — its slot stays empty.
Layout snapshot `v6` retains `screen` and `closed` and stores catalog session/runtime IDs when supplied by attach; it is written to `localStorage` under a per-tab key plus the "latest" key,
and to `/api/workspace` as `ui.layouts[screen]` (merged per tab id by the server, capped at 20) plus `ui.layout` (latest).
Boot attaches only the sessions in the saved layout's open panes; `restoreWorkspace`, `reconcileMissingTmuxSessions`, the
"attach every tmux session" fallback and the empty-pane fill-in on restore do not exist. Session order (mobile swipe)
follows the layout and is not persisted on its own.

### Moving between sessions

There is **one** move order: view tab order, then pane order inside each view (top-left first), empty panes, viewer/panel
panes and unreachable remote panes skipped. `layout/compact.js` `openSessionOrder()` builds it from the saved layout and
`stepSession(delta)` moves along it, wrapping at the ends and crossing view boundaries at a view's first/last pane. Swipe
(`stepView`), the mobile header label (`n/m`, shown in the second line of `#voice-session-picker`), and the palette session
rows all read it, so reload does not change it (the phone's order sheet reorders views). There is no
separate order array (`core/store.js` has no `order`, `setSessionOrder` or `moveSessionBy*`; the
ordinal for "터미널 N" names is registration order and is only a label). Moving **never assigns**: it goes through
`revealSession` (switch view, focus that pane) and does not touch the tree. Do not use `switchTo` for moving: it assigns the target session to the active pane, pulling a session out of another view
and displacing the current one. Rail and Fleet keep grouped-view members together; remaining single sessions stay in name order. There are no next/previous-tab or move-tab key bindings (`tabPrev/Next`, `tabMovePrev/Next`) because there is no tab strip.

**Mobile session management.** The header session name (`#voice-session-picker`) does not open a sheet: it **toggles the bottom Sessions (세션) tab** (Fleet, `shell/Fleet.tsx`) and the terminal (`layout/mobile-nav.js`, event `vt:sessions-toggle`; the name turns accent while the tab is up). There is no session sheet (`layout/session-sheet.js`) or desktop `.wt-chevron`; Fleet's row menu is the same `rowMenuItems` the rail uses (open/close, rename, open on Mac, connected screens, work log, move up/down, sharing and permissions, end); a group is renamed from its header's edit button. Sessions not open in this browser tab appear as rows with Open (열기).

**Editing views on mobile.** The Running (실행 중) group header in Fleet has a Edit views (뷰 편집) toggle (square-pen icon, `.vt-fleet-edit-btn`, `aria-pressed`). On, the group lists **views** instead of session rows — a grouped view is one block (label + members), a single view one row — each with 44px up/down buttons (`aria-disabled` at the ends, no wrap) and a 44px Close (닫기) button (SVG x, `aria-label` "<name> 뷰 닫기"). Up/down call `moveLayoutTab` + `saveLayoutNow` (the same pair as the desktop view drag), so reload keeps it and the active view/focus never change. Close calls **the same `closeView` as the desktop view close** (`term/session-actions.js`): sessions are closed (tmux lives on) and a grouped view's composition is remembered. UI lives in `layout/session-order.js` (lazy `screens` chunk), which `Fleet.tsx` loads with a dynamic `import()` and re-renders on every layout change. Row-click navigation is not rendered in edit mode.

**Blank views on mobile.** Phones have no view tab bar, so a view emptied by a pane × could never be removed. Opening the Sessions (세션) tab below 720px therefore runs (`layout/mobile-nav.js` `setTab('fleet')`) `pruneBlankViews()` (`layout/store.js`): every **single, unnamed view whose only pane is an empty terminal pane** is closed (when the active view is one of them and another view exists, the active view moves to the neighbour). Grouped views, named views, views with a viewer/panel pane and a lone remaining view (the onboarding state) are never touched. On desktop, Pane × leaves an empty pane and nothing prunes.

### View tab

There is no tab strip; the rail and Fleet represent views. `viewSegments` in `design/state-classes.js` defines the per-view state-segment model, which no current UI draws:

- One equal segment per session in pane order (top-left first); single views get a one-segment bar.
- `viewSegments` returns `{name, status, unseen, thick, label, branch}` per session; `thick` is true when the state change is not yet seen (`unseen`). Entries without a name (empty panes) are skipped.
- `segmentsTooltip` lists "name · state[ · branch][ · 확인 안 함]" per session and `segmentsAria` gives the same information for screen readers. Only `frontend/tests/state-classes.test.js` calls `viewSegments`; no CSS draws the bar.
- Mobile's Edit views list shows each view's pane count (including empty, file and panel panes).

### States (rail, mobile)

- Four states — idle, working, waiting, done — each with its own **SVG shape** as well as colour (`stateIcon` in
  `ui/icons.js`; the rail rows and the phone Fleet draw it). There is no `error` state in the UI (unknown values fold to idle). Representative state
  `waiting > done(unseen) > working > done(seen) = idle` is one function, `representativeState`.
- **Unseen** = any state change the user has not looked at yet (not only done). Shown as a ring around the icon
  (`agent/state.js`; "looking" = focused pane of the active view with the window visible, injected by
  `agent/paint.js`; the first snapshot after load flags only `done`).
- `waiting` shows a fixed keyword instead of the raw question text: Approval request (승인 요청) / Choice requested (선택 요청) / Plan review (계획 확인) / Question (질문)
  (fallback). Measured earlier on Claude Code 2.1.287, codex 0.158.0 and agy 1.1.23 with an isolated tmux socket (not re-run on the locally installed versions):

  | Kind | Claude Code | Codex | agy |
  |---|---|---|---|
  | approval | hook (`PermissionRequest` tool_name) | hook | screen only |
  | choice | hook (`tool_name: AskUserQuestion`) | Plan mode only; hook is `PreToolUse request_user_input`, screen `Question 1/1` | screen only |
  | plan | hook (`tool_name: ExitPlanMode`) | screen only (`Implement this plan?`) | no blocking dialog → **plan badge** (shown beside `working`, agy has no end-of-turn signal), not waiting |
  | question | `Stop` message ending in `?` → helper badge on done, **never** promoted to waiting | same | same (screen) |

  Rules: hook kind wins, then screen pattern (scoped to the detected CLI's own spec), then fallback. A waiting
  entry with no kind is "Question (질문)"; a `PermissionRequest` for an unknown tool is "Approval request (승인 요청)". The `kind` field keeps
  AskUserQuestion/ExitPlanMode from being labelled "tool 실행 승인이 필요합니다". **Installs that predate the `PermissionRequest` hook must re-run `fsh hooks install`**; without that hook, Claude Code approvals fall back to screen detection.

### Rail

- Needs attention (개입 필요) (waiting sessions): collapsible, and switchable on/off in rail settings or with the eye button in its head (like VS Code's Open Editors). A waiting row that is open in this tab carries **inline answer buttons** under its text — one per detected option (「1 · 예」…) plus a quiet Go to terminal (터미널로) — the same `AnswerButtons` + `answerSessionPrompt` (`shell/rail-fetch.ts`, `POST /api/sessions/{id}/keys` with `prompt_id`) the phone Fleet uses. The Sessions (세션) activity shows the waiting count as an amber badge (counted by the rail, `onWaitingCount`, even when the zone is hidden).
- Running (실행 중): grouped views have one editable title and pane-order members; remaining single sessions use name order. Closed remembered groups stay together too. A waiting session shown in Needs attention is left out here (`splitRunning`), so no session appears twice. The zone collapses from its head, and each grouped view from the caret before its icon (the title still opens the group); both are kept per device in `rail.view` (`runningCollapsed`, `groupsCollapsed` by view id). See [session UI design](./session-ui.md).
- **Ended sessions.** No tabs: below Running the same list continues with Recently ended (최근 종료) (collapsible, newest N, default 5, `rail.view` device setting; sessions not live and not archived, `pickRecentEnded`) and one "Library N ›" line (「보관함 N ›」) that swaps the list in place for the archived sessions (back chevron, Empty (비우기)). Ended rows (`StoppedRow`) use the running rows' columns (dashed state circle, dimmed name, elapsed on line 1) with an action line below (Check again (다시 확인) · Unarchive (보관 해제) · Resume (이어서 시작) — on hover/focus with a fine pointer, always on touch); the only accent-filled button is Resume. Every row uses the shared `Menu`: running rows gain Task history (작업 기록) (and Rename (이름 변경) even when not open) via `rowMenuItems({record})`; ended rows add Resume · Archive (보관)/Unarchive (보관 해제) · Delete (삭제) (archived only). Rename sets the catalog title and, for an open session, the web session name too. Rail settings add Group by repository (저장소별로 묶기) (repository → workspace view, which has no sorting or delegation tree), Recently ended shown (최근 종료 표시 개수) and Auto-archive (자동 보관).
- **Auto-archive / delete.** The server worker archives (never deletes) sessions whose last runtime ended `auto_archive_days` ago (default 30, 0 = off, `POST /api/session-catalog/settings`); running and unconfirmed sessions are skipped. `DELETE /api/session-catalog/sessions/{id}` soft-deletes an archived session record only — conversations and raw copies stay — and refuses sessions with delegation relations. Confirmation is a dialog, not a password.
- **Delegation lineage (「계보선」).** Single sessions are built into a parent → child tree (`buildSessionTree`) from the session list's `lineage` field, which the server fills from the delegation ledger (`routes/tmux.py _lineage_by_name`) — not from `@fsh_parent`, which an agent could rewrite. A child always sits under its parent in creation order, whatever the sort. In the desktop rail children are indented 24 and joined by a neutral 1px branch line on x = 19 (the parent icon's axis), and each child's line 3 adds Separate terminal (별도 터미널); the phone Fleet keeps the coloured lane (parent's state colour, a reported child's branch in the done colour, failed in the error colour). Closed children fold into 「닫힌 하위 N」 (expandable, dimmed). A child whose parent is gone or orphaned is drawn at top level. Child rows drop line 2 and show the agent keyword while running, the delegation result after (`보고 · 성공/실패`, `닫힘`). The parent's 「하위 보고 r/n」 chip collapses its children. Child menu: View report (보고 보기) (opens the parent's Task history (작업 기록) with this child selected on the right — [agents.md](./agents.md#native-subagents)) · Go to parent session (부모 세션으로 이동) · Close delegation (위임 닫기) (this Mac only; `POST /api/delegations/{did}/close-by-user`, human session). Lines run in the icon column so they never cross the parent's text; geometry lives in `styles/screens/70-worktree-rail.css`. The same tree is drawn in the phone Fleet.
- **Sort: By name (이름순) | Manual (수동)** (switch in the Running header). Manual order moves a top-level entry (a parent with its children) by drag inside the rail (the rail row menu has no move items; the phone, which cannot drag, has Move up/Move down (위로/아래로 이동) in its row sheet). Switching to manual starts from the current name order; switching back keeps the manual order for next time. Stored as one global setting `rail.order` = `{"sort","order"}`; child collapse / closed-children expansion is one device setting `rail.lineage`. Grouped views keep their view order above the tree.
- Collapsed rail (48px strip, v2.10.1 ring design) has no zone headings, so it draws **one list in the same tree order as the expanded rail**
  (`compactRailOrder` flattens the expanded sources: Needs attention first, then split-view members, then `buildSessionTree` — configured
  sort / manual order, children right under their parent; a parent's collapsed children and un-expanded closed children stay hidden, as
  in the expanded rail). Each session appears once; a thin separator sits between Needs attention and the rest. A waiting child is lifted into
  Needs attention and drawn flat (no relation shown). Cell (40, `--vt-cell`): agent mark (18; shell mark when the agent is unknown) in the **state
  colour**, wrapped by a 2px state ring whose **shape** carries the state — idle dotted, waiting dashed (breathing), working rotating arc,
  done solid (`shell/compact-mark.ts`; colour alone never says it). Reduced-motion stops the spin and the breathing; animations use
  transform/opacity only. 「Not seen yet」 (`unseen`) is a 6px dot at the mark's lower right with a 2px cut-out in the cell colour (the ring
  is taken by the state); the expanded row keeps its icon ring. A 12px badge at the cell's upper right counts internal sub-agents that are
  **working** (hidden at 0, `9+`, hollow 「+」 at the display limit); no click, tooltip only («Internal agents N working · …»), so
  「main waiting, sub-agents working」 reads at a glance. Delegation children (S3-b) are 0.8× cells directly under the parent, same column
  centre, with a 2px branch on the left (ㄴ for the last; merged = done colour, failed = error colour, closed = faint). Vertical alignment: the
  strip's body top padding is `28 + 6 + line1/2 − cell/2` (24 at normal size), so the first icon centre equals the expanded first row's, and the
  gap between sections is 32 (header 28 + 4). `aria-label` on the ring is the state (+ not seen yet (아직 확인 안 함)); the cell's own `aria-label` is the session name.
  The expanded rail also shows each session once: a waiting session appears only under Needs attention (`splitRunning`).
- **Hover card on the collapsed strip** (`shell/RailHoverCard.tsx` + `shell/rail-hover-card.ts`, `styles/screens/71-rail-hover-card.css`). A cell has **no** `data-tip`
  (the shared tooltip stays for everything else); hovering it for 250ms (immediately when a card is already open or just closed) or keyboard-focusing it
  (`:focus-visible`, no delay) opens one 300px-wide card to the right of the strip (`position:fixed`, portalled to `<body>`, max height 380, nudged to stay
  inside the viewport; size is independent of `ui.side.scale`). Rows, all from values the client already holds — no request on hover — and **a row with no value is not drawn**:
  head (agent mark in the state colour · name · elapsed) → state chip (shape + word; waiting adds the kind Approval request (승인 요청)/Choice requested (선택 요청)/Plan review (계획 확인)/Question (질문), working/done the
  badge Has question (질문 있음)/Has plan (계획 있음)) → tool (working only) → location (directory with `~` and a middle ellipsis keeping the last 3→2→1 segments, branch with Worktree (워크트리) tag and +N −N
  or 「파일 N」, the remote host with its latency/offline reason when viewing another Mac) → internal agents (「내부 에이전트 N개 · 작업 중 M」, up to 5 rows ordered working → idle → failed → done,
  same names numbered `#N` by start time over the whole list, 「외 K개」) → delegated children (「하위 세션 N · 보고 M/N」, up to 3 rows, waiting first) and, for a child, 「부모: 이름」 + its task →
  meta (Other screens/Terminals N (다른 화면/터미널 N), 공유 · nick · mode or Not shared (공유 안 함) for shareable names on this Mac, 「{맥}에서 만듦」, not seen yet). Over the line budget (14 body rows) it is cut meta → children → internal agents; location is never cut.
  The tool name is a **snapshot taken when the card opens**, refreshed only when the session's state changes — a live tool name would flicker with every call (same reason `NativeChildren` draws none).
  Rules (WCAG 1.4.13): hoverable (the pointer may cross to the card; 160ms grace + a transparent bridge), dismissable (Esc closes it and it does not reopen until the pointer leaves the cell or focus moves),
  persistent (no timeout; pointer/focus leaving, a press, scrolling or resizing closes it). Touch pointers never open it (a phone has no strip); the row menu summary is the touch path. While open the cell gets
  `aria-describedby="vt-rail-card"`. Layout: every row is the same 3-column grid `[14px icon | value | right value]`, row height 20, 8 between sections.
- **Menu size** (`ui.side.scale`, device scope, Settings › Appearance (모양) › Menu size (메뉴 크기): small / normal / large; desktop only). One `<html data-vt-side-scale>`
  drives `--vt-act-w` (activity bar and collapsed strip 44 / 48 / 52), `--vt-cell` (collapsed cell 36 / 40 / 44, ring and mark scale with it),
  the rail's `--vt-line1`/`--vt-line` and `--fs-xs/sm` one step (rows 18·16 / 20·18 / 22·20) and the **default** sidebar width 320 / 360 / 392 (`SIDE_SCALES` in
  `shell/side-prefs.js`; a stored `ui.side.width` or an old-key fallback is never overridden). Changing it moves the bar widths at once, so
  `Workbench.tsx` wraps the change in the refit gate (one fit/`resize` per pane, same as a width drag).
- Row (desktop, redesign phase 4 — anatomy in DESIGN.md §3): a 16px state-icon column, then line 1 agent · name · delegation
  toggle · elapsed · ⋯; line 2 WT · current directory · branch (main is shown too) · diff; line 3 state keyword · tool · badge ·
  internal-subagent chip · share · origin icon; line 4 connected-screen counts when they apply. The phone Fleet keeps the
  elapsed time on line 3 (`KeywordLine` without `noSince`). A grouped view's title appears once in its editable header,
  never repeated below its members. There is no main/worktree SVG.
- Row menu: the ⋯ button and right-click open the same menu (open/close, rename, open on Mac, connected screens,
  the agent-sharing items below, kill).
  The menu opens with a **summary line** — a disabled header row (`.vt-menu-summary`, `role="note"` with an `aria-label`,
  never selectable, wraps instead of ellipsizing): state · (when waiting) the kind keyword · 「웹 외 N」 · where it was
  created (이 화면에서 만듦 / 다른 맥에서 만듦 — 「<맥 이름>에서 만듦」 when `originHost` is recorded / 직접 만듦) · branch (`rowSummary` in `shell/rail-data.ts`,
  drawn by `Menu`'s `summary` prop in the rail and by the phone Fleet sheet). Why: `ui/tooltip.js` is off for touch pointers
  (no hover), so on a phone this line is the only way to read a row's hidden facts — the waiting kind, non-web attaches, origin, branch.
- **Shared mark.** A session shared with agents shows `ShareMark` on line 3 (`shell/RailRow.tsx`, shared with Fleet): an
  icon and the nickname — eye = read-only, pen = read/write (`share-read`/`share-write` in `ui/icons.js`); read/write also
  gets the word Write (쓰기) and the waiting colour, so the risky mode differs by shape, text and colour. It is a security state,
  so there is **no rail-settings switch** for it. The 48px rail shows it in the hover card, the menu summary line in
  「공유 · 닉네임 · 읽기 전용/읽기·쓰기」 (`shareSentence`), which is what a touch screen reads.
- The sliders button in the rail header is **rail settings** (`RailSettings.tsx` on top of `RepoVisibility.tsx`, one
  sheet): Needs attention on/off (`rail.attention.show`; collapse state `rail.attention.collapsed`), per-origin visibility
  (`rail.origin.{peer,external}.show`; local cannot be hidden), and the repository display list (저장소 표시). All device-scoped.
  Hiding an origin never hides a session that is open or waiting; turning Needs attention off leaves waiting sessions in Running, identified by their keyword.
- Code: `shell/rail-data.ts` (pure: lists, keyword, menu items, prefs), `shell/rail-fetch.ts` `collectSessionRows`
  (one row builder shared by the rail and the phone Fleet), `shell/RailRow.tsx` (row, keyword line, origin icon),
  `shell/rail-prefs.ts`. 「다른 화면 N」 = web screens on the session (server `webScreens`: PTYs the server holds, from the
  session store) minus this browser tab's own PTY (1 when the session is open here) — other browser tabs and phones only. Non-web
  attaches (Mac terminal etc., server `otherClients` = `attached − webScreens`, floor 0) get a separate small terminal icon + count
  with a tooltip (`ScreensMark` in `RailRow.tsx`, shared with Fleet); no extra tmux call. A server without `webScreens`
  falls back to an `attached − own PTY` count. Branch/diff come from
  `GET /api/repos`, matched through `@fsh_wt` or, failing that, the server's cwd-based `worktrees[].sessions`.
- Remote hosts use the same rows: switching host reloads the screen against that Mac's relayed API (`core/host-route.js`),
  so its sessions are rendered like local ones, with whatever `origin` that server reports (absent = no icon, treated as local).

### Origin

Recorded when a session is created and shown as a rail icon: this browser / another Mac (peer create) /
external (no record — `fsh` CLI or plain tmux). Only sessions created in this browser tab open automatically.

**Rail icon.** The rail and Fleet show a small icon per origin
(`laptop` = peer, `square-terminal` = external; local has none) with a Korean tooltip, and rail settings can
hide each of the two. The frontend opens a session by itself only when boot restores the *saved
layout* (sessions this browser tab had placed itself, whatever their origin) and the explicit `#tmux=<name>` handoff link;
no path attaches a session because of its origin.

**Server record.** The server writes the tmux user option `@fsh_origin`
right after `new-session` (same mechanism as `@fsh_wt`, so it survives a server restart). Reads ride the existing
`list-sessions` / `list-panes` format strings — no extra tmux call. The API field is `origin` on
`GET /api/tmux/sessions`, `/ws-workspace` `tmux_sessions` and `GET /api/sessions`.

| Value | Written by | Meaning |
|---|---|---|
| `local` | `POST /api/tmux/create`, worktree open (`worktree._open_agent`, `open_worktree`) | created from this UI |
| `peer` | `POST /api/peer/sessions/create` | another Mac created it; the server decides this itself, the request body is never read for it |
| `external` | nothing (absence of the option) | `fsh` CLI, plain `tmux new`, or any unknown option value (an `agent` value included) |

**Creator name.** For a `peer` session the server also writes `@fsh_origin_host` (the caller's label from the signed peer grant, sanitised, ≤60 chars; `tmux_runner.set_origin(session, "peer", host)`); it is returned as `originHost` and the rail/Fleet/menu summary read 「<name>에서 만듦」 (`originShort`/`originLabel` in `shell/rail-data.ts`). No record (sessions made before the field, older servers) keeps Created on another Mac (다른 맥에서 만듦). Only `peer` uses it.

There is no `agent` origin (robot icon, 「에이전트가 만듦」, `rail.origin.agent.show`). Delegation creates sub-sessions (`docs/ref/agents.md` «Agent delegation»); they are recorded as `local` origin and marked with `@fsh_parent`/`@fsh_delegation`. A stale `rail.origin.agent.show` in saved settings is ignored (`core/settings.js` drops keys outside the schema).

**Agent-shared sessions.** An agent works only in a tmux session the user made and shared.
Agents cannot create, rename, kill or share sessions and cannot reach an unshared one by guessing a name (unshared and
nonexistent nicks both answer 404). The user creates an ordinary session (an empty terminal) and shares it with a nickname
and a mode; the MCP tools (`list_shared_sessions`, `read_session`, `send_to_session`) are the only door. Code:
`server/agent_share.py` (rules, store, audit), `server/share_identity.py` (caller identity), `server/routes/agent_share.py`
(HTTP), `server/mcp_farshell.py` (tools). Endpoint contract: `API.md` «Agent session sharing».

- **Mark.** tmux user options `@fsh_share_nick` (`[A-Za-z0-9_-]{1,32}`, unique case-insensitively, checked under a lock) and
  `@fsh_share_mode` (`read`|`readwrite`). Read in the same `list-sessions` call as `@fsh_origin`, so no extra tmux call;
  `GET /api/tmux/sessions` and the `tmux_sessions` push carry `share: {nick, mode} | null`. The mark lives as long as tmux does
  (server restarts keep it). A value someone typed in by hand is re-validated on every read: a bad nick means not shared, an
  unknown mode reads as `read`. Unshare uses `/api/agent/share/sessions/{name}`, separate from the tmux kill route, including for a session named `kill`.
- **Grants.** `~/.vt/agent_grants.json` (0600, flock). A grant is (agent session A, shared session S) → read and/or write.
  Both ends are keyed by `#{session_id}` + `#{session_created}` — not the name — so a rename follows, and a session killed and
  recreated under the same name starts with nothing. It is deliberately **not** bound to the Claude/Codex session id: `/new`,
  `/clear` and a CLI restart are the same tmux session and keep the grant. When A or S ends, the grant and its requests are
  swept on the next read of the store. Malformed grants or requests are treated as empty; before a later write replaces the
  damaged file, its exact bytes are saved to a private `agent_grants.json.corrupt-*` backup. If backup fails, the write is
  refused. **Effective permission = min(granted, the share's current mode)**: lowering the mode to `read` stops writing on the
  very next call. Raising it again revives an existing write grant **without another approval**; revoke the write grant or
  unshare if the reduction should be permanent.
- **Engine mode.** With `VT_AUTHZ_ENFORCE=share` the grants/requests/share policy live in `~/.vt/authz/` and the audit goes to `~/.vt/audit/authz.jsonl`; `agent_grants.json` and `agent_access.log` are still written in parallel (rollback = unset the flag). See `docs/ref/auth.md` «Agent session sharing under the engine».
- **Approval is human-only.** No grant → the call creates a **pending request** (one per agent/session/kind) and returns `202`;
  the agent is told to repeat the same call once approved. Read and write are approved separately (allowing a write request may
  give `read` only; a read request can never give write). A write request on a read-only share is refused at once, without a
  request. A denied request is not re-created for 5 minutes; pending ones expire after 24 h; at most 20 pending per agent.
  The user is told by the `agent_share_request` event on `/ws-workspace` (and a Web Push with no session names when no browser is
  open). All approval/share/revoke endpoints sit behind `auth.require_human_session` — browser cookie only; the machine token,
  which the agent itself holds, gets 403 (`docs/ref/auth.md`). Peers cannot use any of it (no peer route, not in `relay_policy`).
- **Caller identity** (the part that stops "B pretends to be A's pane"). The MCP sends `TMUX_PANE`, `TMUX`, its pid as
  `X-Fsh-*` headers. The server requires: `TMUX` is FarShell's socket (`pane_resolve.is_our_socket`); `TMUX_PANE` exists in
  FarShell's tmux, which gives the pane's `pane_pid` and session from tmux itself; and the **parent chain of the calling pid
  (64 levels at most) reaches that `pane_pid`**. Anything else is 403 `identity_unverified` with a reason; outside tmux there is no
  `TMUX_PANE`, so it is refused. The server requires a direct loopback connection and looks up the TCP peer's owning pid with
  `lsof`; if it cannot establish that owner, it refuses the call. Proxy headers cannot switch the check to a claimed pid.
- **Read.** `capture-pane -p -J` of the session's active pane: last 200 lines by default, 2000 at most, trailing blank rows
  dropped, 64 KB at most (the newest part is kept; `truncated` says so). 30/min per (agent session, shared session).
- **Write.** `send-keys -l -- <text>` literally; Enter only with `enter:true`. It does not use the prompt queue (the main use is a
  shell). Text: 1–4000 chars, one line, **no control characters, ESC, tab, newline, Unicode line separators or bidi
  overrides or Unicode format characters such as U+200B** — a newline would run a command without `enter`, so multi-line input is refused and N commands are N calls. If an
  agent CLI is known (hooks) to be `waiting` the send is refused (it would be consumed as the approval answer), `working` is refused
  too, an unknown state (plain shell) is allowed. 10/min per pair.
- **Audit.** `~/.vt/agent_access.log` (jsonl, 0600, rotated once at 1 MB to `.log.1`): time, agent (name + `$id`), nick, action
  (`read`/`send`/`list`/`request`/`decide`/`revoke`/`share`/`unshare`), result, lines read, chars sent and the **first 8 hex of
  the text's sha256 — never the text**. Read by the browser only (`GET /api/agent/share/audit`).
- **Known limits.** (1) This is a guard against a deceived agent or an accidental call, **not a security boundary against a
  malicious process running as the same OS user with unrestricted file access**. Such a process can read the session-signing
  key in `~/.vt.env`, forge a browser session cookie and pass a human-only approval endpoint, as an adversarial test reproduces. Run less-trusted agents with a sandbox or separate OS account that cannot read `~/.vt.env`. File mode 0600
  alone does not separate processes running as the same user. (2) Pane text can contain anything — what an agent reads is as trustworthy as the
  program that printed it (prompt injection through a shared terminal is possible). (3) `send_to_session` into a plain shell
  that is waiting at an interactive prompt (`sudo`, `read`) is not detected — the state comes from agent hooks only. (4) A
  shared session is shared at its **active pane** only. (5) A grant is not time-limited (it ends with either session or by
  revocation); there is no per-share expiry.

**Frontend.** Code: `frontend/js/share/` (the `share` chunk; `vite.config.mjs` must name it or the
catch-all sends it to `core`), `frontend/js/share-lazy.js` (the only always-loaded part: bridges and event listeners),
row menu in `shell/rail-data.ts`.

| Row (this Mac, tmux) | Items added to the row menu / phone sheet |
|---|---|
| not shared or shared | Sharing and permissions… (공유 및 권한…): sharing form, direct session grants and revocation; shared sessions also offer confirmed unsharing |
| viewing another host (relayed), non-tmux row | none (sharing is not relayed) |
| tmux name outside `[A-Za-z0-9_.-]{1,64}` (e.g. Hangul) | same entry, permissions only; sharing controls are hidden |

The grant list is scoped to the live session's exact identity; global/default permissions and pending approvals remain in Settings → Permissions.
There is no separate workspace-details row-menu action; directory and branch stay on the row and full cwd stays in the menu summary.
Working tool labels use 「도구: Bash」: the hook's tool name, not the terminal login shell.
Native subagents are grouped under 「에이전트 내부 / 기록 조회」; FarShell delegated terminal children under 「FarShell 위임 세션 / 별도 터미널」.


- **Calls go out with the cookie only** (`share/share-api.js`). `vtFetch` appends `?token=` and `apiFetch` adds `Authorization`
  for a legacy token link — either makes the server treat the request as not human (403 `human_session_required`). A page
  opened through a `?token=` link therefore cannot share or approve; the dialog says so.
- **Share dialog** (`share/share-dialog.js`, on `core/dialog.js`'s `openDialog`): the warning text, nickname (checked as you
  type, default suggested from the session name, 409 → That nickname is taken (이미 쓰는 닉네임입니다)), mode radios, and the unshare note.
  Lowering read/write to read-only warns that granted writes stop on the next call. 44px targets under a coarse pointer.
- **Approval modal** (`authz/inbox.js`, buttons in `authz/data.js`): requests arrive as `authz_request` events (the
  older `agent_share_request` events are still handled until the new ones arrive, `share-lazy.js`). Read requests offer
  Allow (허용)/Deny (거부); other requests offer Once (한 번만), For this session (이 세션 동안), Deny (`once` is unavailable for request kinds that
  accept only allow or deny, with the reason shown). The default focus is Deny. Every decision button is ignored for
  400 ms after the modal is drawn (`ARM_MS`), and the focus trap keeps keys off the terminal. The panel shows a pending count
  in 「에이전트 권한…」. A screen that views another Mac does not listen (approval is this-Mac only).
- **Permissions panel** (`authz/permissions.js`, `authz/grants.js`): the Permissions (권한) section of settings
  (`panels/settings/index-data.js`) lists the grants and the audit trail.

Only `local`/`peer` are ever written (`tmux_runner.set_origin` rejects everything else without calling tmux);
unknown values read back as `external`. The server has no auto-attach path: sessions attach only through explicit
`POST /api/tmux/attach|create`, so "only this tab's own sessions open automatically" is a frontend rule.

### tmux-centric session management

The web UI uses tmux sessions as its default:
- On startup only the sessions in this browser tab's saved layout are attached; nothing is attached just because it
  exists (there is no "attach the first tmux session" fallback).
- A saved tmux leaf keeps the tmux name alongside its short-lived web PTY id. Boot attaches that name again and
  places the new id in the same view/pane. A new browser tab saves its copied layout under its own screen id after
  restoration. During a server restart, a failed workspace read or transient attach error retries boot before
  restoring or saving the layout, so a temporary failure cannot replace those saved names with empty panes.
- 「+ 새 세션」 creates a tmux session (`POST /api/tmux/create`) and opens it in a new single view.
- Four verbs, one implementation (`frontend/js/term/session-actions.js`), called by the rail row menu, the mobile
  Fleet and the pane/view buttons:
  - **Open (열기)** — `openSessionFromRail` (rail click: focus if already open, reopen a remembered grouped view via
    `reopenClosedView`, otherwise a new single view).
  - **Close (닫기)** — `closeSession` (the session leaves its pane, tmux keeps running; `detachSession` is an alias), `closePaneAction` (pane ×), `closeView` (view ×, remembers a grouped view's composition).
  - **Move (옮기기)** — drag a rail row or pane onto a pane edge (`layout/dnd.js`); no function of its own.
  - **Kill (종료)** — `confirmAndKillSession` then `killSession` (`DELETE /api/tmux/kill/{name}`, irreversible; the
    confirmation text from `killWarning` names the agent status when it is `waiting`/`working`).
  There is no three-way wording ("remove from layout / put session down / end completely", 배치에서 빼기 / 세션 놓기 / 완전 종료) and no sleep/wake (재우기/깨우기).
- Duplicate-attach prevention: a session already open in this browser tab is never placed twice — clicking it focuses
  its pane.
- iTerm2 and the web can be attached to the same tmux session at the same time (shown as 「다른 화면 N」 on the row).

Current-cwd metadata, workspace kind/details and editable grouped-view headers: [session UI design](./session-ui.md).

Keyboard routing, modal priority, Option modes, device migration and paste timing limits: [keyboard input](./keyboard-input.md).

### Session menu summary

Desktop rail menus and mobile Fleet sheets share a non-interactive summary header.
Status and origin appear first, followed by the workspace label, project basename and
full path, then the branch. Missing fields are hidden; long paths and branch names
wrap without truncation. The accessible summary retains the full context.

### Settings numeric controls

`ui/number-spinner.js` supplies the number field and custom step buttons for range controls and both voice minimum-duration fields. Native number semantics and the input's min/max/step remain; browser spinners are hidden. Typed values commit on change/Enter, invalid or empty edits restore the current value, and bounds/step are normalized before saving. Arrow keys and buttons share that path. While saving, step buttons are disabled and the field is read-only to preserve focus; rejection restores the prior value unless the caller returns its own authoritative value (the settings store deliberately retains locally applied values after persistence failure). Desktop arrows stack; touch/narrow-screen buttons are side by side at 44px.

## Durable catalog and conversation archive

`server/session_catalog/` owns private SQLite logical sessions, workspaces, physical runtime
observations and recorded stop/resume operations. A complete batch observation uses the tmux
server socket/PID/birth epoch plus session ID/birth; partial observations mark executions unknown
and cannot terminate them. Stopped catalog rows remain. Archive requires an explicitly stopped
runtime. Independent delegated tmux sessions are not stopped together.

Visible name and activity (format version 2): `sessions.title` is the visible name and `sessions.name_source` says who chose
it. While it is `auto` it follows the tmux name (a rename inside tmux included) and, when a CLI starts in the session, takes
that CLI folder's repository name (`session_history.worker.Collector.track_sessions`, setting `auto_name`); a name a person
gives (`title` edit) makes it `human` and nothing changes it again until `auto_name:true`. The tmux name itself is never
changed by the server. `sessions.last_activity` moves forward on run start, agent activity (at most once a minute) and the
last observation before a run ends; the list is ordered by it and auto-archive uses it. A run's `branch_at_end` is the
workspace branch known when the run is seen to end. A conversation's title is refreshed whenever new transcript lines
carry one (Codex: its first prompt), and `conversation_local` keeps the last prompt a person typed and the prompt count for
this Mac only (not replicated). Tests: `server/tests/test_catalog_v2_behaviour.py`, `test_conversation_summary.py`.

What counts as a complete observation (`session_catalog/inventory.observe`): "the tmux server is absent" is decided in
one place, `tmux_runner.server_absent`, shared with the delegation ledger — `no server running on` and, after a reboot
emptied the socket directory, `error connecting to … (No such file or directory)`. Either one is a complete, empty
snapshot, so the previous runtimes are recorded as stopped at once instead of sitting in `unknown` until a new tmux
server starts. A permission error or timeout stays unknown. A pane kept after its process exited (`remain-on-exit`) is a
normal row (`dead`, `dead_status`), not an incomplete snapshot. A mismatch between `list-sessions` and `list-panes`
(a session created or closed in between) is read once more before it counts as incomplete. A runtime seen again
after it ended (identity reuse) is skipped and logged for that session only; the rest of the same reconcile — other
stops, new sessions, and the collection that follows — still commits. Tests: `server/tests/test_catalog_observe.py`.

Catalog resume creates an interactive shell, then sends the quoted Claude/Codex resume
command to the exact pane ID returned by tmux. Exiting the CLI returns to that shell;
it does not stop the tmux runtime. Creation or command-delivery uncertainty records
`needs_review`, and retrying the operation never sends the command again.

Resume restores permissions as well as the conversation. Claude uses a verified owner-hook
record keyed by native profile root and conversation ID, explicitly including `default`.
Old unscoped records cannot establish which profile supplied the mode. Scoped writes require
the verified hook agent PID to match the foreground CLI profile PID; older hooks still
report state but cannot establish a permission policy for resume. Codex re-reads the
catalog-bound native transcript immediately before launch, checks parent/native identity,
and restores the final `turn_context` approval policy, sandbox details and reviewer. YOLO is
`never` approval plus `danger-full-access`; OFF is replayed explicitly rather than inherited
from current config. Codex 0.164.0 or newer is required; an unknown/older runtime is refused before launch.
Custom/named permission profiles, unsupported policies and read-only
network access cannot be faithfully represented by these CLI arguments and are refused.
Malformed/incomplete latest records, conflicting native copies, unavailable policy and a
changed file/source refuse without sending the command. Reads are bounded to 64 MiB and
1 MiB per record; compressed sources must fit the decompression budget. Policies not yet
written by the native CLI cannot be reconstructed. Claude hook timestamps prevent delayed
older events from restoring an earlier bypass, including across server restart. A newer
owner event whose profile cannot be verified invalidates older scoped records.

`server/session_history/` stores immutable SHA-256 raw chunks before committing manifests and
normalizes supported Claude/Codex JSONL, including compressed Codex records. Claude child metadata
and selected tool-result attachments are raw archive units. `dependency_complete:false` remains
explicit: complete restoration across arbitrary CLI versions is not certified. Source disappearance
and rewrite preserve earlier manifests. The collector is independent of browser visibility; Git
discovery, FTS projection and encrypted replication have separate tasks. Shutdown drains an active
worker before releasing its state. Interactive collection commits at most 256 KiB of new raw bytes
per file pass; explicit/offline capture retains the 8 MiB budget. Compressed projection allows one
supported record (up to the 1 MiB line limit) to make progress even with the smaller raw budget.
Oversized records are skipped in bounded passes with continuation state stored in the manifest;
restarting collection cannot parse their tails as separate records. Later supported records remain
searchable. The manifest distinguishes complete raw preservation from omitted search content and
reports oversized/invalid records and decompression/index limits in the archive UI. Legacy incomplete
projections are replayed once with stable message IDs. Replication drains at most 32 attempts per peer
within a one-second scheduling window, preserving the existing request timeout, chunk and byte-rate
limits; a bounded event already begun can outlast that window so a slow host still advances.
Explicit cancellation is also checked within the event and prevents subsequent work.
The disposable trigram index can fall back to normalized SQL
search while absent or behind the catalog. It keeps no copy of the bodies (`content=''`); an `owners`
table maps each row to its origin and conversation, and an index in the older self-contained layout
is rebuilt from scratch.

Archive APIs are direct-host human only; sensitive operations additionally require elevation.
They are absent from broad relay and agent socket allowlists. Replication uses a distinct human
approved peer grant and sealed public-key packets. Raw chunks are verified before manifest ACK;
projection batches are bounded and replayable. Approved source scopes restrict transfer. Origin and
replica quotas are accounted separately, including pending and orphan bytes. Receiver key rotation
retains old private keys for in-flight packets; keys are excluded from backup exports. Explicit deletion
can request replica tombstones with delivery ACKs, while source disappearance emits no deletion.
Raw chunks remain until a separate cleanup operation; a tombstone is not a physical secure erase.

Restore points are thinned hourly (`archive.prune_manifests`): per file the newest 20 manifests, the
newest of each generation and the newest of each local day are kept forever; manifests received
from peers get the same rule per origin and file. Within a generation chunk lists are prefixes of the
newest one, so no chunk loses its last reference. A local manifest is dropped only after every send
peer's checkpoint has passed it, and a manifest event carries the conversation's messages since its
previous surviving manifest, so a peer added later still receives every message. Origin and replica
quota usage is counted in full at most every 5 minutes; chunks created in between are added to both
roles, so the cached check can only overestimate. The server keeps one idle catalog connection so the
WAL is not checkpointed and deleted on every short connection; `private_file` therefore checks an
existing store file with `lstat` and never opens and closes it (closing any descriptor drops every
POSIX lock the process holds on that file).
Receiver records never become local executions.
Protocol tests cover large projections, lost ACKs and revoked grants; these are not evidence of
an operational central host connection.

Daily consistent catalog snapshots retain seven generations. Export includes local and replicated
referenced raw chunks, excluding CLI credentials and transport private keys. The offline
`session_history.backup.restore_package` function restores only into an absent catalog directory;
sources and replication are disabled for review and historical runtimes are stopped. There is
currently no browser backup-import flow.

New catalog attach paths validate durable runtime IDs; layout v6 retains stopped placeholders and
rejects same-name replacement runtimes. Legacy name-only snapshots retain compatibility behavior;
legacy name-only layout adoption remains separate from grant migration. Execution-bound legacy grants
are denied until an elevated human reviews and binds them to observed host/epoch identities. Explicit
new grants, pair membership and file leases use the same execution fence. Remote delegation includes
runtime assertions in the signed request body and pins both host identities; missing or changed
assertions are refused. Durable peer terminal paths bind host/session/runtime IDs in the signed URL.
Repository visibility is imported once into catalog metadata; the legacy preferences remain recovery evidence.
Native resume returns `started_unverified` until separately checked; it is not an OAuth/profile
replacement certificate. Endpoint and authentication contracts are in [API.md](../../API.md).
