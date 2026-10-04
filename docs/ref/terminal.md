# Terminal, panes, shell surface

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Clipboard sync | OSC52 (in-terminal copies) + `fsh clip` polling daemon (copies outside the terminal) → pushed to web clipboard |
| Paste (N24·N25·N26·N29, 2.1.5) | **The server decides, not the browser.** The browser cannot know whether the app inside a pane turned bracketed paste on, so guessing meant `200~` printed literally in the wrong place, or auto-indent stair-stepping in the right one. The server now reads the pane's real input mode — `ICANON` via termios, DECSET 2004 seen on the output stream — and decides. Path: WS `paste` message, or `POST /api/sessions/{id}/paste`. **tmux sessions are delegated to tmux itself** (`paste-buffer -p` looks at the pane's actual mode; `-S` on 3.7+ also turns off tmux's own sanitising since we already did it) — we do not guess a second time. Control characters are whitelisted by iTerm2's rule (tab · LF · FF · CR · `^V`); ESC is neutralised to `␛` only when bracketing, and simply dropped otherwise. In canonical mode a line over `PC_MAX_CANON` (1024B on macOS) does **not** get truncated — the whole line vanishes (verified on a real PTY) — so it is now refused up front with a reason instead of disappearing. `fsh doctor paste` shows what this terminal actually sends |
| tmux session management | Create/attach/detach/kill tmux sessions from the web |
| Scrollback buffer | Restores previous output on WS reconnect (up to 5000 chunks) |
| Scrollback persistence — **removed (2.3.0, ADR-43)** | N13 shipped an opt-in disk log of PTY output (`~/.vt/scrollback/<session>.log`) that also widened the `~` search palette to cover finished sessions and pre-restart output. 2026-09-22 measurement found the toggle had **never been turned on** — the one real log on disk was a synthetic 10M-character fixture, not session output, and secret-pattern scans found nothing because nothing real was ever written. A feature nobody uses is pure risk (arbitrary terminal output, including `cat .env`, sitting remotely searchable for 7 days) with zero offsetting value, and redacting it well enough to keep would need a real parser, not a key=value heuristic — so it was deleted rather than fixed. The `~` search palette still works; it only greps the live in-memory ring buffer now (finished-session/pre-restart reach is gone with it — that reach was the one thing lost, and it had never actually been exercised). Full writeup, the measurement, and a lighter alternative idea (explicit per-session "export this log" instead of silent accumulation) are in `docs/plan-2.3/87-remove-scrollback-persist.md` — read that before re-proposing this feature. |
| Terminal search | Ctrl+F / Cmd+F → xterm.js search addon |
| Mobile text selection (`term/touch-select.js`, rewritten 2026-09-23) | **Does not create a browser Range at all.** The previous design (native range selection over xterm's accessibility text layer) was found broken by construction: that layer redraws its rows on every PTY render, and each redraw replaces the text nodes the selection is anchored to — collapsing the range mid-drag, so any output while selecting (a tmux status-line clock, a spinner) killed the selection before Copy could run. The fix is long-pressing the terminal directly, which selects the word under the finger through **xterm's own selection model** (`term.select`/`getSelection`/`clearSelection` — the same API a desktop mouse-drag uses), painted by a separate renderer the accessibility layer's row-replacement never touches. Two self-drawn handle elements let you drag either end (recomputing a flat `row*cols+col` pair each move); `keybar.js`'s `setTerminalSelectionMode` still owns entry/exit (blur hidden textarea, hide keyboard, show the 복사·모두 선택·완료 bar) — only what triggers it and what happens inside changed. Verified: selection text stayed byte-identical across 5s of continuous new output. Not yet checked on a real device (real-finger handle geometry) — see DESIGN.md §7. |
| Mobile auto-copy / auto-paste (`term/selection.js`) | **Disabled on touch devices as of 2026-09-23**, regardless of the `mouse.autocopyOnSelect` setting (desktop drag-select is unaffected and still respects it). Previously, any selection ending in a `mouseup` copied automatically, and Android's long-press-sent `contextmenu` triggered a PuTTY-style copy-or-paste that could show a clipboard permission prompt before the user had chosen to select anything — both assumed the native-selection design above and made the touch experience feel like it was acting on its own. |
| Session name editing | A rail session row's **⋯** button (2026-09-21 — moved off right-click, see the row menu note below), or the mobile session sheet → rename (PATCH API; the tmux session name is also changed via `rename-session` — if the new name isn't alphanumeric/dash/underscore, tmux is left untouched and only the web label changes). 2.1.4 removed the session tab strip, so there is no "double-click the tab" path — the single source of a session's name is its record (`sessionDisplayName` in `core/store.js`) |
| Split panes (2.0, N4) | The terminal area is a binary tree whose leaves can hold a terminal, code viewer, or dock panel (source control, queue, files, ports, usage). An empty pane's picker opens any of them; dock and pane instances of the same panel can coexist. Split from the pane header or by dropping a **rail session row** on a pane edge (5 drop zones; before 2.1.4 the drag source was the session tab); the divider is draggable. A **dock tab** is a drag source too — drop it on a pane and that leaf becomes a panel pane (edge = split that way, centre = replace what was there; the session itself stays alive in the rail). The dock collapses if the tab you dragged out was the one it had open, so the same panel is never shown in two places at once. Mouse only: the dock is collapsed by default below 1600px and compact caps panes at 2, so there is nowhere to drag to on touch. Pane cap per width tier (compact 2 / regular 4 / wide 6, unlimited at xwide) — over the cap the split buttons are disabled with the reason in their tooltip. Below 720px the same tree renders one pane at a time; coarse-pointer devices can swipe left/right between them. The layout is saved to `/api/workspace` and restored on load, including panel-only layouts with no live sessions; a leaf whose session died is demoted to an empty pane instead of a ghost |
| Left rail + command palette (2.0) | The ⋯ menu is gone. The left rail (sessions / files / queue / ports / usage / settings) is the pointer path and `Mod+K` the keyboard path — **both expose the same things**, so learning either one is enough. The palette shows each command's current key binding, read from the keymap registry |
| Settings + keymap (2.0) | `Mod+,`. Settings live in `/api/workspace.settings`, so a change on your phone shows up on the Mac. Key bindings are rebindable, and **`passthrough` hands a key back to the terminal** — that's how you get `Mod+F` back as the shell's `forward-char`. Mouse section can turn off "forward mouse events to the app", making drag-select always work even under vim/tmux mouse mode. **Defaults differ per platform** — see the row below |
| Platform key defaults (2026-09-18) | `Mod` is Cmd on macOS but **Ctrl everywhere else**, so the mac-safe defaults were taking keys the shell and tmux actually use. `keymap.js` now carries a `defNonMac` per action: `search` Ctrl+Shift+F, `palette` Ctrl+Shift+K, `splitRight` Ctrl+Shift+D, `searchInPane` Alt+Shift+F, `splitDown` Alt+Shift+D, `paneClose` Alt+Shift+W, `railToggle` Alt+Shift+B, `worktreeNew` Alt+Shift+N. **`paneClose` and `worktreeNew` moved on macOS too** (Ctrl+Shift+W / Ctrl+Shift+N): their old Cmd+Shift+W / Cmd+Shift+N are the browser's close-window and incognito, so they never reached us in a normal tab. Rule: prefer `Ctrl+Shift+…` (a terminal cannot encode it), drop to `Alt+Shift+…` when that slot is taken or the browser owns it (Ctrl+Shift+W closes the window, Ctrl+Shift+N opens incognito, Ctrl+Shift+B toggles the bookmarks bar); the reserved list now also carries the Shift variants and is compared **normalized**, so a combo recorded in the settings screen ('mod+shift+w') is caught too. `Ctrl+Alt` is avoided because it is AltGr on European layouts. `Mod+…` elsewhere in these docs means the macOS default. A user's own rebinding always wins and stays platform-independent |
| Connected screens (2.0, corrected 2026-09-29) | Open from the HUD chip or a session row's menu. Each browser tab has its own tmux attach PTY, keyed by a tab-local screen ID that survives reload; previously `/api/tmux/attach` reused one PTY across every browser for the same tmux name, so two web screens appeared as one and could not be detached separately. The panel lists clients with a "me" badge; "Keep only this screen" detaches the rest (including a Mac iTerm2 window). The selected web session id stays fixed while the panel is open; the server derives its tty and refuses to detach it. Five seconds after the last WebSocket leaves, that tab's tmux client is released while the tmux session remains; reconnecting revives it. A failed list request stays visible with a retry button. A remote host that refuses control shows the list as read-only. |
| Session cards (pane-picker, palette) | The "pick a session" sheet you get from clicking an empty pane, and the command palette's session rows, both reuse the same card builder (`agent/preview.js`). Through 2.1's step 4 each card opened its own `/ws-preview` WebSocket per session to stream a live, ANSI-rendered terminal snippet — with the palette, pane-picker and the (CSS-hidden but still-loaded) legacy rail all doing this at once, one session could have three such sockets open. **2.1 D5 removed the live preview**: the card's meta line is now a static `cwd` string filled from the same `/api/tmux/sessions` response the card already needed for its title/command, no socket required. The `/ws-preview` **server endpoint itself is untouched** (ADR-7) — only the frontend stopped opening it |
| Session groups (ADR-29, 2026-09-18) | **A group is derived from the screen, not stored** — a tab whose pane tree holds 2+ distinct sessions is a group, one session is an individual. You make one by dragging a session onto the terminal area to split it (`layout/dnd.js`'s existing drop zones; rail rows are `SESSION_MIME` drag sources), and you name it by naming the tab (`window.vtRenameTab`; `tabDisplayLabel()` computes member names when unset). Rail rows are tmux sessions again — open in this browser tab or not (the 「열리지 않음」 zone, formerly 「잠자는 중」), alphabetical, with 열기 / 닫기 / 완전 종료 (`term/session-actions.js`; 재우기·깨우기 retired 2026-10-03 — see "Session and view rules" below), opened from a row's **⋯** button (`RailRow.tsx`'s `Menu`, `Rail.tsx`'s `ctxMenuItems`). Through 2026-09-21 this menu was right-click only; a user pointed out the row's left-click was already spoken for (open/switch), so right-click had no left-click equivalent and was replaced by the button — not the same **⋯** as the pre-2.0 app-wide menu in the row above, which stays gone. The menu (and the host-switcher one, C1) render through a `Portal` now, for the same reason `RepoVisibility`'s sheet does (`.vt-sidepanel`'s `position:fixed` stacking context could otherwise bury `.vt-menu` under another overlay — see the Portal comment in `Rail.tsx`). Remote hosts keep the old three-bucket view. **ADR-29 supersedes ADR-20**, and the `@fsh_grp` tag · `group_store.py` · `/api/groups*` that its first rollout built were all deleted again by the last round of user feedback. Full rollout (steps A–E), the four addenda that reshaped it, the layout-snapshot versions and the known regressions: [`adr29-session-groups.md`](./adr29-session-groups.md) |
| Header tabs (10 §4, 2.1 D4, ADR-29 D — mechanics for the row above) | The header has a **tab strip** (`#vt-wtabs`) and **each tab owns its own pane tree** — switch away and back and that layout is exactly as you left it. Through 2.1.6 a tab's identity was the worktree (`repo`+`branch` each got their own tab), 2.1 D4 moved it to the repo and ADR-29 D to the group; the group redefinition then removed tab identity altogether — `openLayoutTab` dedups on `findTabBySessionId(sessionId)` now, because a group *is* its pane-tree members. `worktreeId` remains "which worktree this tab is currently looking at" (`setTabWorktree`), shown as a small **branch chip next to the pane header** (`layout/panes.js`, `term/tab-worktree.js`'s `branchChipMap`) rather than a second tab, and only when its repo actually has more than one worktree open — one worktree means there is nothing to disambiguate. Accepted cost: two worktrees of the same repo can no longer be two separate tabs; open them as split panes instead. The layout snapshot is `v4` (`{tabs:[{id,worktreeId,hostId,label,tree}], activeTab}`); `v1`/`v2`/`v3` are still read, and `repoId`/`groupId` on old snapshots are dropped on load since they no longer mean anything. Since 2026-10-03 the snapshot is `v5` (adds `screen`, `closed`) and is saved per browser tab. Closing a tab (`closeView`) closes every session it held and remembers a grouped tab's composition — tmux is never touched. The last tab can be closed too: the store then keeps one unlabeled empty-pane tab that reads as "no views" (tab bar hidden, onboarding). Clicking a tab focuses its top-left pane. Dragging a session out of another tab leaves an empty pane there, and closes that tab if it ends up wholly empty (`closeViewIfEmptied`; 2026-10-03 rule decision). The same session is never placed in two tabs: the surface layer moves one DOM node around |
| tmux detach detection | Shows `[process exited]` on PTY EOF |
| kitty keyboard protocol (N12) | Sends combinations that legacy encodings have no room for — `Ctrl+Shift+<letter>`, `Shift+Enter`, `Ctrl+Enter`, `Ctrl+Tab` — as CSI u (`frontend/js/term/kitty-keys.js`). Encoding happens **only while an app has turned the mode on** (`CSI > flags u`); otherwise a shell that never enabled it would literally print `\x1b[97;6u`. The mode is a push/pop stack, so a vim that opens and closes inside a shell restores the previous value. Our own UI shortcuts are matched first, so `Mod+K` never leaks to the PTY even with the mode on. ⚠ **Layer 3 (tmux `extended-keys`) is deliberately absent** — that is a change to the user's tmux server options and needs its own approval. With tmux 3.3+ and that option off, tmux blocks CSI u before it reaches the inner app (`set -s extended-keys on`) |
| Skins (N14) | Settings → "모양" (Mod+, or rail ⚙ → Settings) is the **only** place to switch skins now — `#theme-row`'s 6 chips (farshell/macos/catppuccin/windows/vscode/notepad, `theme.js`'s `vtSkins()`) render there. The command palette (Mod+K → "테마") also lists all skins, reading `vtSkins()` directly rather than scanning the DOM — so it works even when the Settings panel isn't open. A 7th "import a Ghostty config or Warp theme YAML" skin (`theme-import.js`/`theme-custom.js`, a lazy chunk) shipped 2026-09-14 and was removed 2026-09-22 — 8 days in, no workspace had ever actually saved `theme.skin: "imported"` |

## Session and view rules (target, agreed 2026-10-03 — Terms, Invariants and Actions implemented 2026-10-03; View tab, States, Rail, Origin not yet)

> This section is the **target**. Until each step lands, the Features rows above (Session groups, Header tabs)
> describe what the code does today. When a step ships, update those rows and drop the matching "target" note here.
> Why it changed: the old model's 재우기/깨우기 meant nothing but "not placed in this browser tab" (the agent keeps
> running), six close paths each did something different, reload re-attached sessions the user had closed, and the
> tab and the rail used two different urgency orders.

### Terms (implemented)

| Term (UI) | Meaning |
|---|---|
| Session (세션) | One tmux session. Runs until killed; closing a view never stops it. |
| View (뷰) | One header tab. Holds one or more panes. 1 pane = single view, 2+ = grouped view. |
| Pane (칸) | One region of a view. Holds one session, or is empty. |
| Open (열림) | The session is placed in a pane of this browser tab. A placement, not a state. |
| Browser tab | One FarShell browser tab. Each has its own view layout. |

"Sleep/wake" (재우기/깨우기) is retired as a concept and a word. Verbs: **open, close, move, kill**.

### Invariants (implemented)

1. Within one browser tab a session sits in at most one pane.
2. The "current session" is not stored separately — it is the focused pane of the active view.
3. Reload never reopens a session or view the user closed. Only the saved layout is restored.
4. The layout is saved per browser tab. A new browser tab starts from a copy of the most recently saved layout.

### Actions (implemented)

| Action | Result |
|---|---|
| Click a rail row, session not open | Opens it in a new single view |
| Click a rail row whose closed grouped view is remembered | Reopens that whole grouped view |
| Click a rail row, session already open | Goes to that view and focuses its pane |
| Drag a rail row or a pane onto a pane edge | Splits that pane in the drop direction. **The only way to group.** The source pane becomes empty — and if that empties its whole view (another view than the one you dropped into), that view is closed automatically (`closeViewIfEmptied`). Pane × on a sole session is different: the view stays with an empty pane. With only one view nothing is closed |
| Pane × | The session leaves the group and becomes "not open" in the rail; the pane stays as an empty pane. Same function as the rail row menu's 닫기, the mobile session sheet and Fleet (`term/session-actions.js` `closeSession` / `closePaneAction`) |
| Empty pane | Click to pick a session; its own × removes the pane |
| View × | Closes the view only (no confirmation; shown even when it is the last view — closing the last one shows the onboarding screen). A grouped view's composition (tree + label, members by tmux name) is remembered; a session belongs to at most one remembered group |
| Click a view tab | Switches and focuses the **top-left** pane (fixes focus staying on the previous session) |
| Kill | Always confirmed; warns when working/waiting. Its pane becomes empty in every browser tab |
| New session | Opens in a new single view |

There is no "drag a pane onto the tab bar" and no "ungroup" action — leaving a group is pane ×. ⌘/Ctrl-click on a rail
row no longer splits the current view.

Implementation notes (2026-10-03): the active session is derived in `layout/store.js` (`_deriveActiveSession`) from the
active view's focused pane — `null` on an empty pane. The store never holds zero tabs; one unlabeled tab with one empty
pane reads as "no views" (`isLayoutEmpty`: tab bar hidden, onboarding shown). Reopening a remembered group attaches its
members and rebuilds the tree; a member already open elsewhere in this browser tab is not moved — its slot stays empty.
Layout snapshot `v5` adds `screen` and `closed`; it is written to `localStorage` under a per-tab key plus the "latest" key,
and to `/api/workspace` as `ui.layouts[screen]` (merged per tab id by the server, capped at 20) plus `ui.layout` (latest).
Boot attaches only the sessions in the saved layout's open panes; `restoreWorkspace`, `reconcileMissingTmuxSessions`, the
"attach every tmux session" fallback and the empty-pane fill-in on restore are gone. Session order (mobile swipe) now
follows the layout and is no longer persisted on its own.

### View tab

- Label (member names joined with `+`, unless the user named it) and ×. No agent icon, no state icon.
- A **state bar** along the bottom edge, split into one equal segment per session in pane order (top-left first).
  Single views get a one-segment bar.
- Segment colour = state colour; only `waiting` breathes.
- **Thick segment = changed and not yet seen; thin = seen.** Idle segments always show (grey).
- Hover tooltip lists "name · state" per session, since colour alone is not enough.

### States (rail, mobile) — shared parts implemented 2026-10-04; rail/mobile surfaces adopt the icons in the Rail step

- Four states — idle, working, waiting, done — each with its own **SVG shape** as well as colour (implemented:
  `stateIcon` in `ui/icons.js`; surfaces still draw dots until the Rail step). `error` is removed from the UI until
  something can actually enter it (implemented; unknown values fold to idle). Representative state
  `waiting > done(unseen) > working > done(seen) = idle` is one function, `representativeState` (implemented).
- **Unseen** = any state change the user has not looked at yet (not only done). Shown as a ring around the icon
  (implemented in `agent/state.js`; "looking" = focused pane of the active view with the window visible, injected by
  `agent/paint.js`; the first snapshot after load flags only `done`).
- `waiting` shows a fixed keyword instead of the raw question text: 승인 요청 / 선택 요청 / 계획 확인 / 질문
  (fallback). Measured 2026-10-03 (Claude Code 2.1.287, codex 0.158.0, agy 1.1.23, isolated tmux socket):

  | Kind | Claude Code | Codex | agy |
  |---|---|---|---|
  | approval | hook (`PermissionRequest` tool_name) | hook | screen only |
  | choice | hook (`tool_name: AskUserQuestion`) | Plan mode only; hook is `PreToolUse request_user_input`, screen `Question 1/1` | screen only |
  | plan | hook (`tool_name: ExitPlanMode`) | screen only (`Implement this plan?`) | no blocking dialog → **done + plan badge**, not waiting |
  | question | `Stop` message ending in `?` → helper badge on done, **never** promoted to waiting | same | same (screen) |

  Rules: hook kind wins, then screen pattern (scoped to the detected CLI's own spec), then fallback. A waiting
  entry with no kind is "질문"; a `PermissionRequest` for an unknown tool is "승인 요청". The server used to label
  AskUserQuestion/ExitPlanMode as "tool 실행 승인이 필요합니다". Fixed by the `kind` field (2026-10-03, step 7).

### Rail

- 「개입 필요」 (waiting sessions): collapsible, and switchable on/off in rail settings (like VS Code's Open Editors).
- 「세션」: every session, **name order only** — no urgency sorting, rows don't jump around.
- Row: line 1 state · agent · name; line 2 main/worktree icon · branch (main is shown too) · diff; line 3 state
  keyword · elapsed · origin icon; when applicable "open" mark and the grouped view's name. The plug (tmux) icon goes.
- Row menu: the ⋯ button and right-click open the same menu (open/close, rename, open on Mac, connected screens, kill).
- The header gear (today 「저장소 관리」) becomes **rail settings**.

### Origin

Recorded when a session is created and shown as a rail icon: this browser / another Mac (peer create) / an agent /
external (no record — `fsh` CLI or plain tmux). Only sessions created in this browser tab open automatically.

### tmux-centric session management

The web UI uses tmux sessions as its default:
- On startup, tmux sessions are auto-detected → attaches to the first one
- "+ New" → creates a tmux session (`POST /api/tmux/create`)
- Closing a tab → only detaches (the tmux session stays alive). Kill is done via `DELETE /api/tmux/kill/{name}`.
  Three UI surfaces (worktree tab bar, rail context menu, mobile session sheet, tmux panel) used to each spell
  "close" their own way with their own logic. 2.1 D3 named the three real actions — **배치에서 빼기** (remove from
  layout only), **세션 놓기** (detach the web session, tmux keeps running) and **완전 종료** (kill tmux, irreversible)
  — and moved the implementations into `frontend/js/term/session-actions.js`, which the four surfaces now call
  instead of each rolling their own. The kill confirmation text names the session's agent status when it's
  `waiting`/`working`, so a session mid-approval isn't killed by mistake
- Duplicate-attach prevention: a tmux session already open in the web switches to its existing tab
- iTerm2 and the web can be attached to the same tmux session at the same time
