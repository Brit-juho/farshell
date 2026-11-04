# Session and view measurements (2026-10)

Evidence behind the session/view rules, the `waiting` kind and release logic, and the phone (touch) work of 2026-10-05/06. Rules live in [`terminal.md`](./terminal.md) (「Session and view rules」) and [`agents.md`](./agents.md); this file keeps the numbers and the procedures. Screenshots are not tracked.

CLI versions: Claude Code 2.1.287 (first pass) and 2.1.289 (re-measured after fixes); Codex 0.158.0 measured, 0.160.0 installed afterwards; agy (Antigravity CLI) 1.1.23 first, 1.2.14 after, 1.2.16 re-checked on 2026-10-06. tmux 3.7c.

## 1. Waiting-kind measurements per CLI (2026-10-05 and 2026-10-06)

Isolated: own tmux socket, empty git repo under /tmp, isolated server with `VT_CONFIG`/`VT_STATE_DIR`/`VT_RUN_DIR`/`VT_TUNNEL_PROVIDER=none`, hooks installed into a temp `CLAUDE_CONFIG_DIR`; PTY detection requires a pane on the server's own tmux socket **and** an attached PTY (WebSocket) — a pane nobody has open is never scanned.

| CLI (version) | Scenario | Result |
|---|---|---|
| Claude Code 2.1.289 | Bash approval | `approval` (hook `PermissionRequest`, options 1-4 from the screen) - pass |
| Claude Code 2.1.289 | AskUserQuestion | `choice`, options from the hook - pass |
| Claude Code 2.1.289 | plan mode ExitPlanMode | `plan` from the hook - pass. Screen-only fallback: the title wraps at 80 columns (`Would you like to` / `proceed?`), so `Claude has written up a plan and is ready to execute` was added |
| Claude Code 2.1.289 | reject `4. No` / ESC | **mismatch, fixed** (above). Trust-folder dialog -> `approval` (screen) - pass |
| Claude Code 2.1.289 | `/api/agents/coverage` | `path: hook`, kindSources `hook` for approval/choice/plan (was `screen`; fixed) |
| agy 1.2.14 | command approval | `approval` (screen) - pass; ESC -> done (PTY), `Interrupted · What should Antigravity CLI do instead?` -> done (non-PTY, new `interrupt`) |
| agy 1.2.14 | ask-user choice | **mismatch, fixed**: footer is split by colour codes, never matched before; now `choice`. Skipping with ESC outside FarShell leaves `(skipped)` on screen and **stays `waiting` until the TTL** (no stable phrase chosen; PTY ESC works). Re-measured 2026-10-06 on agy 1.2.16 with `capture-pane -p -e` x5 plus the raw `pipe-pane` stream replayed through `PromptDetector`: the footer disappears and `? <question>` / `(skipped)` stay, then the model adds free text (`You skipped the question. How would you like to proceed?`, not fixed CLI text). In the cleaned window `(skipped)` is **not at a line head** (glued after stray `>4;2m` residue of an unstripped `CSI > 4;2 m`), so a `\n(skipped)` anchor cannot match; bare `(skipped)` would also hit tool output and end a `working` entry. No `interrupt` phrase added |
| agy 1.2.14 | plan mode artifact | badge `plan` appears (`1 artifact · /artifact to review`) but on a **`working`** entry: agy has no end-of-turn signal, so "done + plan" does not occur; the badge is not used to end the turn because the footer persists into later turns. The row shows 「계획 있음」 as a secondary mark beside 작업 중 (the frontend used to drop badges outside `done`; fixed) |
| Codex 0.160.0 | command approval (`mkdir`, read-only sandbox) | **pass.** Real screen `Would you like to run the following command?` + `1. Yes, proceed (y)` / `2. ... (p)` / `3. No, and tell Codex ... (esc)`: `waiting`/`approval`, options `y`/`p`/`esc`. Hook `PermissionRequest` gave `approval` first (`options` filled once the screen arrived) |
| Codex 0.160.0 | file-edit approval (`apply_patch`) | **mismatch, fixed.** Option 2 is `(a)` (`Yes, and don't ask again for these files (a)`); the old `(y\|p\|esc)` regex dropped it (live: only `y`, `esc`). Regex now takes any one-letter key or `esc`; live result `y`/`a`/`esc` |
| Codex 0.160.0 | decline (`esc`) at an approval, ESC while working, ESC on the question overlay | **mismatch, fixed.** All three leave `■ Conversation interrupted - use /feedback if something went wrong` (a decline adds `✗ You canceled the request to run ...` above it) and **no hook**: the entry stayed `working` (checked for 20 s+). New `interrupt = ["Conversation interrupted"]`: live `waiting`/`working` -> `done` in each case (also through the app's `/api/sessions/{id}/keys` ESC) |
| Codex 0.160.0 | stale `Conversation interrupted` line during the next turn | **would have been a false positive; guarded.** The line stays in scrollback and tmux partial redraws re-send it with no `Working ... esc to interrupt` line, so a naive phrase ended a running turn as `done` (live: `working -> done -> working` flicker, replay of the recorded frames: 100+ hits). After it the redraw also carries the new `› <user input>` / `• <answer>` lines, so `interrupt_stale_after` in `codex.toml` ignores the phrase when such a line follows. Replayed against the recorded frames of three later turns: no false hit. Residual risk: a redraw that carries only the stale line and nothing after it cannot be told apart from a real interrupt (not seen in the recorded frames; the 15 ms burst of full-history frames right after pressing Enter did contain it, but arrived before the `UserPromptSubmit` hook so the entry was still `done`) |
| Codex 0.160.0 | Plan mode `request_user_input` (question overlay) | **pass.** `Question 1/1 (1 unanswered)` ... `enter to submit answer`, bottom line still has `esc to interrupt`: `waiting`/`choice`, options `1`/`2` from the `PreToolUse` hook (screen alone also yields `3. None of the above`; the hook list wins). Plain-text question in Default mode: no overlay, `done` with `badge: question` |
| Codex 0.160.0 | Plan mode `Implement this plan?` | **mismatch, fixed (state) and extended (options).** The `Stop` hook arrives ~40-130 ms **before** the overlay is drawn, and `on_waiting` refused waiting on a `done` entry, so the live entry stayed `done` while the overlay was on screen. A `plan` screen now reopens a `done` entry that finished within `PLAN_AFTER_DONE_SEC` (10 s); other kinds keep the old rule. Overlay lines are `1. Yes, implement this plan   Switch to Default ...` (no `(key)`): a second `options` regex reads them and the digit selects at once (live: `3` closed it with `No, stay in Plan mode`). Option 3's label and description are joined by `\x1b[13X\x1b[13C` (erase + cursor forward), so cursor-forward is now stripped as blanks. Answering `3` through the app leaves the entry `working` (no turn starts, no hook): see the open item below |
| Codex 0.160.0 | coverage | `GET /api/agents/coverage` with the isolated `CODEX_HOME` hooks installed: `path: hook`, `kindSources` approval/choice `hook`, plan `screen`, question `fallback` |

How the Codex row was measured (2026-10-06, Codex CLI 0.160.0, tmux 3.7c): an isolated `CODEX_HOME` holding a copy of `auth.json` (deleted afterwards) and its own `config.toml` (`approval_policy = "on-request"`, `sandbox_mode = "read-only"`, trusted temp repo, FarShell hooks installed with `codex_hooks.py install`), tmux socket `-L fsh-probe5`, `git init` temp repo, isolated server (`env -i`, own `VT_CONFIG`/`VT_WORKSPACE_PATH`/`VT_STATE_DIR`/`VT_RUN_DIR`, `VT_TUNNEL_PROVIDER=none`, `VT_CODEX_HOME`). A WebSocket client on `/ws/{id}` stayed attached (answering the server's ping with pong; without it the server drops the client and detection goes blind) and logged every frame; status was polled from `/api/agent/status` every 50 ms. Each scenario was one real model turn; `tmux capture-pane -p -e` was taken three times per screen, and the recorded WS frames were replayed through `PromptDetector` with their original chunking. The frames that back the regression tests are in `server/tests/codex_0160_frames.py`. Not measured: decline through option `3`'s digit, the `p` option, and Codex versions other than 0.160.0.

The question heuristic: a Stop/done whose `last_assistant_message` ends with `?` only gets `badge: "question"` (never `waiting`: rhetorical questions and quotes would block the prompt queue). agy plan mode: the `/artifact to review` footer gives badge `plan` on a **working** entry (shown as a secondary 「계획 있음」 mark, never as done); agy has no end-of-turn signal and the footer persists into later turns, so it cannot end the turn.

## 2. Where `waiting` is released (2026-10-06)

| Path | Trigger | Reacts to attach/resize redraw? |
|---|---|---|
| Screen exit pattern | `exit` literal (`esc to interrupt`) drawn newer than the prompt (`PromptDetector.feed`) | Only if the redraw contains it; a redrawn dialog does not |
| Screen `interrupt` phrase | `Interrupted · ...` (Claude) / `Conversation interrupted` (Codex) newest in the window -> `done`; for Codex ignored when a new `› ` input or `• ` answer line follows it (`interrupt_stale_after`) | No |
| User input | any WS binary / `/keys` / paste -> `deps.on_terminal_input` (ESC -> `done`, else `working`) | **Yes, before the fix** (below) |
| Hook | next `pre`/`post`/`stop`/`prompt` event | No |
| TTL | `waiting` 2 min -> `working` (`sweep`) | No |
| Resize | WS `resize` message is not input; tmux redraw has no exit literal | No (measured) |

There is **no** "output resumed" release rule; the redraw hypothesis was wrong. The real cause: on every attach tmux queries the client terminal (`ESC[c`, `ESC[>c`, `ESC[>q`, `ESC]10;?`, `ESC]11;?`, `ESC[?996n`; captured from tmux 3.7c). xterm.js answers through `onData`, the frontend forwards `onData` as WS bytes, and the server could not tell those replies from a keypress. Fix: `terminal_reports.is_terminal_report` — input made only of protocol replies (DA, CPR, DSR, focus, DECRPM, DCS, OSC, APC) is not a human answer (Ctrl+F3 `ESC[1;5R` and any mixed chunk still count as typing). No timeout/cap.

Measured in an isolated server + real tmux (`-L fsh-wk`) with a hook-made `waiting` and a shell dialog, client replies emulated over WS: before — first attach and second attach -> `working`; without replies, attach/second attach/reload/resize all stay `waiting`; after the fix — all four stay `waiting` and a typed ESC still gives `done`. **Not verified with a real Claude Code** (a temp `CLAUDE_CONFIG_DIR` is not logged in) and not with a real browser xterm.js (reply bytes are the standard ones, not captured).

#### Rejecting an approval or pressing ESC (2026-10-05, real CLIs)

Measured against Claude Code 2.1.289 in an isolated server/tmux socket. **Rejecting (`4. No`) or pressing ESC at a permission dialog emits no hook at all** (no Stop, no PostToolUse; ESC while a tool runs is the same). The screen goes back to an empty input line (`⎿ Interrupted · What should Claude do instead?`), and the entry stayed `working`/`tool=Bash` until the 15-minute TTL. The earlier "waiting stays 12 s after ESC" observation was a test artefact: ESC sent with `tmux send-keys` bypasses FarShell's PTY, and only PTY input (`deps.on_terminal_input`) clears waiting. Typing/ESC in the web terminal already cleared it (ESC -> `done`, other keys -> `working`).

- **Root cause**: no hook exists for "user declined"; the only signal is the screen. Fix: `interrupt = [...]` in `detect/<cli>.toml` (claude, agy) — when it is drawn newest in the window, `agent_status.finish_interrupted_for_session` marks that tmux session's `waiting`/`working` entries `done`, and the detector drops its window so the old prompt text cannot revive `waiting`. No timeout or cap.
- **Detection runs on ANSI-stripped text** (`PromptDetector._append_clean`): the real TUIs colour each word separately and join lines with absolute cursor moves, so byte-literal matching missed `Interrupted ·` (Claude) and agy's choice footer, and glued option 2/3/4 labels into one.
- A `done` entry finished by reject/ESC carries `_reopen`, so the **next** screen prompt is accepted. Without it agy (no hooks, so nothing ever moves `done` back to `working`) ignored its next question. Entries finished by a Stop hook keep the old rule (late screen detection never reverts `done`).
- `PermissionRequest` can arrive **after** the dialog is drawn; it no longer wipes the options the screen already captured (inline approve buttons).
- Claude `kindSources` is now `hook` for approval/choice/plan whenever all six hooks are registered (`HOOK_KINDS`); it used to say `screen` although the hook decided.
- **Codex 0.160.0 (measured 2026-10-06, see the table in section 1)**: declining an approval, ESC while working and ESC on the question overlay all end with `■ Conversation interrupted - use /feedback if something went wrong` and no hook; `interrupt = ["Conversation interrupted"]` releases them. The line stays on screen for the following turns, so the phrase is only trusted when no new `› `/`• ` line follows it. Pressing ESC on the `Implement this plan?` overlay just closes it (no phrase), and answering it from the app (`3`, stay in Plan mode) leaves the entry `working` until the next hook or the 15-minute TTL.


## 3. Real-device measurement (2026-10-05/06)

**Device.** SM_A5260 (1080x2400, density 420, DPR 2.625), Chrome `innerWidth` 411 portrait / 832 landscape.

**Method.** Isolated FarShell server (own `VT_CONFIG`, `VT_WORKSPACE_PATH`, `VT_RUN_DIR`, own tmux socket, tunnel provider none) on a spare port; `adb reverse tcp:<port> tcp:<port>`; Chrome driven over CDP for DOM/computed-style reads and screenshots; touches done with real `adb shell input swipe/tap` (not CDP-synthesised events) so that scroll, long-press and swipe-navigation behave as on a finger. Read-only against the user's real server.

| Item | Result |
|---|---|
| View order (plan option C: sheet edit mode, per-view up/down) | checked on device: grouped view moves as a block, ends stop (no wrap), active view unchanged (rules as implemented; see `terminal.md`) |
| Swipe between sessions | checked with real `adb input swipe`; order is layout order (view order, then pane order), `n/m` follows |
| State display (rail rows, view-tab segment bar) | checked; the waiting row got an amber tint because the light-skin grey read as inactive |
| Reload restore | checked: closed sessions stay closed; layout is per browser tab |
| 「Connected screens」 sheet | checked; buttons were 24px (fixed, below) |
| Landscape (832px) | checked; the soft keyboard leaves little height (focus mode below) |
| Icons | the three icon changes were looked at on the device (SVG icons, no emoji) |

**Found and fixed.**
- Fleet inline approval buttons multiplied (1/2/3/1/2/3): a full-screen TUI redraw leaves several copies of one dialog in the detection window. Same key now appears once, latest label wins.
- Touch targets 24px (approval buttons, connected-screens close/detach/keep-only, view-tab x, pane-header x). Raised to 44px under `pointer: coarse` or the mobile range; tab x and pane x use transparent padding so the visible 18px box is unchanged (desktop stays 18/24px).
- `hidden` buttons showed: `.vt-btn{display:inline-flex}` beat the UA `[hidden]`, so the "resend recording" button was always visible at >=720px. Fixed in the base layer with `[hidden]{display:none !important}`.

**Found and implemented this round.** Touch pane splitting through the row menu (width >=720px and coarse pointer only; relaxes "grouping by drag only" for touch); landscape-phone keyboard focus mode (`visualViewport.height < 300px` hides tab bar, headers, rail, dock, HUD, bottom nav); a one-line summary at the top of the row menu (touch has no hover tooltip); close view and blank-view pruning in the phone sheet.

**Known limits / substitute tests.** Peer and agent origin (agent since removed) were exercised by setting the tmux option `@fsh_origin` directly rather than by a real peer host or agent spawn. The 719px/720px boundary was checked by emulation (resized viewport), not by a device that reports that width. A real desktop touch screen (>=720px coarse) was not available; the touch split menu was verified by emulating `pointer: coarse`.

### 3.1 Round 2 (2026-10-06, after the touch/keyboard/waiting fixes)

Same device and method (isolated server on port 17920, `env -i`, real soft keyboard, real `adb input swipe`). Screenshots are not tracked.

| Item | Result |
|---|---|
| `waiting` kept across attach, reload, second browser tab, landscape rotation (resize), split-attach | **pass.** The phone's real xterm.js sent OSC 10/11 colour replies and `ESC [ O` on every attach (captured with CDP `framesent`) and the entries stayed `waiting` |
| Real keys release `waiting` | pass (`yo` typed on the phone: waiting -> working) |
| A single tap on the terminal | **fail, fixed.** An SGR mouse report (`ESC [ < 0 ; x ; y M/m`) was not recognised as a terminal report, so raising the keyboard by tapping released `waiting`. `terminal_reports.py` now treats SGR mouse (tap, click, wheel) as pointer events; a mouse report mixed with a typed key, or a malformed one, still counts as human input |
| Amber tint on the waiting row | pass on light and dark skins (contrast from the device's token values: light 12.1:1 text / 6.4:1 secondary, dark 14.9:1 / 6.4:1) |
| View edit: per-view close (44x44), grouped view close and restore, empty single view cleaned on opening the sheet, last view -> onboarding, up/down regression | pass. Minor: the close label of a grouped view read "묶음 뷰 뷰 닫기" (duplicated word) — fixed |
| Row-menu summary line (state, kind keyword, non-web clients, origin, branch) | pass; wraps to two lines in the landscape rail menu, never clipped |
| Touch pane split (>=720px landscape) / absent in portrait | pass. **Fail, fixed:** in landscape the visible height is 331px, so the menu of a bottom rail row ran past the viewport and its items could not be reached. `Menu` now measures itself after mount, pulls itself inside the visible viewport and scrolls if still too tall |
| Landscape + real soft keyboard focus mode | pass: tab bar, pane headers and rail hide, the pane uses the remaining 111px; everything returns when the keyboard closes. Not distinguished: with/without a connected key bar (it is `display:none` in landscape both before and after) |
| Hit areas of the tab x and pane-header x (44x44, corner taps do only the intended action) | pass; the 4px gap against the neighbouring 18px split buttons was not stress-tested |
| Regression: edge swipe order, reload restore, closed-group restore, three origin icons (as measured; the `agent` robot icon was removed afterwards, 2026-10-06, so there are two now) | pass. The 「Connected screens」 sheet could not be reopened in landscape (no open view = no row menu entry) |

Side observations: after a server restart every pane came back empty although the tmux names had been in the layout. **Cause confirmed and fixed (2026-10-06, section 3.2).** The default `waiting` TTL is 2 minutes, so synthetic states turned `working` by themselves until `VT_AGENT_TTL_*` was raised.

### 3.2 Server restart emptied every pane (2026-10-06)

Reproduced in an isolated server (`env -i`, own tmux socket `-L fsh-rs`, port 17931/17932, Chrome for Testing via Playwright; 4 tmux sessions in 3 views: single / grouped 2 / single; desktop 1440px and 500px). Restarting only the server (same state dir) with the page left open:

| Step | Before the fix | After the fix |
|---|---|---|
| Open page, `ui.layouts[screen]` | `[[rs1],[rs2,rs3],[rs4]]` | same |
| Kill + restart server, **no reload** | all views empty; the page's saved layout became `[[null],[null,null],[null]]` | views intact, 4 terminals re-attached; layout names kept |
| Reload | empty (reads the nulled layout) | restored |
| New browser tab | empty (copies the nulled latest layout) | restored |
| One tmux session killed while the server was down (500px) | all empty | only that pane empty (`[rs2, null]`), others kept |

Cause: `session_store` is in memory, so after a restart the page's WS reconnects to a web id the server no longer has -> close 4004. `term/ws.js` treated 4004 as "session was killed" -> `vtCloseSession` -> `removeSession` -> `clearSessionFromLayout` + `saveLayoutNow`, and an empty pane serialises as `session: null`, so the tmux name was lost on the very next autosave (and the server's `ui.layout` "latest" copy, which new tabs and reloads read). Fix: on 4004 for a local tmux session, `recoverSession` (`term/session.js`) asks `/api/tmux/sessions`; alive -> `/api/tmux/attach` with the same name, `replaceSessionId` swaps the pane's id in every tab tree; not listed -> the old close path (killed = empty pane); undetermined (list/attach failed) -> normal reconnect backoff. Plain (non-tmux) PTY sessions die with the server and keep the old behaviour.

## 4. Bundle size (initial payload `app.js` + `core.js`, ceiling 358,400 B since 2026-11-04, 307,200 B before)

310,711 -> 295,839 -> 297,689 -> 297,901 (view-tab status bar) -> 299,215 -> 299,310 -> 300,947 -> 303,467 (server-restart recovery) -> 305,403 B (closed-view memory policy) -> 300,800 B (session sheet list rendering moved to the lazy `screens` chunk; `core.js` 303,321 -> 298,718, `app.js` 2,082 unchanged; headroom about 6.4 KB). Headroom was about 1.8 KB; the ceiling is asserted by `frontend/tests/build-output.test.js`.

2026-10-13: 307,168 B at HEAD (32 B of headroom) -> 307,551 B with the rail lineage settings (over) -> **252,513 B** after stripping whitespace and comments from every output chunk (`vite.config.js` `stripChunkWhitespace`, a post-order `renderChunk` esbuild pass; Vite's lib ES mode never minified whitespace, so `core.js` still carried its comments and indentation). `core.js` 305,107 -> 250,596, `app.js` 2,164 -> 1,917; headroom about 54 KB. Lazy chunks shrank too: `shell.js` 219,512 -> 168,373, `panels.js` 105,292 -> 95,132, `voice.js` 42,305 -> 32,333. Identifiers and syntax are left alone (the build-output test finds module-scope names); source maps are chained and still resolve to the original lines.

2026-11-04: 307,191 B (9 B of headroom) -> **275,880 B** after moving four modules out of `core.js` (`panels/agent-profiles.js` and `snippets-util.js` -> `settings`, `panels/system.js` -> `panels`, `ui/number-spinner.js` -> `shell`; none is needed at boot). The ceiling was raised 300 -> 350 KiB (358,400 B) in `ci.yml`, `release.yml` and `build-output.test.js`. The check exists to catch a vendor (xterm, about 290 KB) leaking into the eager bundle, not as a UX budget; 350 KiB still catches that. `voice.js` keeps its own 307,200 B check. Source grew about 77 KB in `core.js` between 10/13 and 11/04, so the 54 KB headroom from whitespace stripping lasted about three weeks.

2026-11-04 (later): 275,880 B -> **268,813 B** after `ui/select.js` moved to its own `select` chunk (stub `ui/select-lazy.js` stays in core). `npm run build` now prints the top 15 modules of `core.js` by rendered bytes (`reportCoreModules` in `vite.config.js`, information only); use it instead of source-map attribution, which is too coarse. Largest now: `layout/panes.js` 28 KB, `layout/store.js` 26 KB, `term/session.js` 19 KB, `ui/icons.js` 18 KB, `core/settings.js` 17 KB, `layout/persist.js` 15 KB, `panels/panel.js` 14 KB.

2026-11-04 (third): 268,813 B -> **260,750 B** after `panels/panel.js` + `core/dialog.js` moved to a `panel` chunk. Boot-path code (terminal, rail, `history/api.js`) now imports `core/dialog-lazy.js`, whose `confirmDialog`/`promptDialog`/`alertDialog` already returned Promises; chunks that need the synchronous `openDialog`/`nextId` still import `core/dialog.js` directly and pull the `panel` chunk with them. If the chunk cannot be fetched the stub falls back to `window.confirm`/`prompt`/`alert` (a silent "cancel" would hide why nothing happens, a silent "ok" would drop the safeguard). `build-output.test.js` asserts that neither `core.js` nor `shell.js` imports `panel.js` statically.

2026-11-04 (fourth): 260,750 B -> **252,058 B** after `term/upload.js` moved to an `upload` chunk and the guide body (`term/guide-view.js`) joined the `panel` chunk. `term/upload-lite.js` stays in core: `hasFiles`/`filesFromDataTransfer` must run synchronously inside the drop event (a `DataTransfer` is empty once the event ends), the window-level dragover/drop guard must exist from boot (otherwise a file dropped outside a target navigates the page away), and `window.vtUploadFiles/vtUploadJobs/vtCancelUpload` are stubs until the engine loads (no engine means no jobs: `[]` and `0` are the correct answers). Left alone on purpose: `term/e2e.js` (6 KB; the handshake listener must be attached before the first WebSocket message, and it is security-relevant), `layout/dnd.js`, `ui/toast.js`, `core/keymap.js` and the agent state modules (all synchronous boot-path code).

2026-11-04 (vite 8): the whitespace/comment stripping pass (`stripChunkWhitespace`, esbuild) was removed with the move to vite 8 (Rolldown has no esbuild). Output is now unminified (`minify: { mangle: false, compress: false }`, because the build-output tests find module-scope names), so the payload is **317,045 B** (`core.js` about 309 KB) against the 358,400 B ceiling. Gzip transfer size is close to the old figure; only raw bytes grew. If headroom becomes tight again, the first lever is `minify: { mangle: false }` (whitespace and syntax only), which brought it to about 221 KB in a trial.

## 5. View-tab mockups

Three variants of the segment status bar on the tab: **A** flush to the bottom edge (2/5px), **B** inner pill (3/6px), **C** top edge. **B chosen**: the 2x thickness difference (unseen vs seen) is still distinguishable at four segments and it reads in both light and dark skins; the active-tab underline moved to the background tone because the bar owns that edge.

## 6. Reproduction (isolated server)

Never point a test server at the real state. Start it with a clean environment so stale `VT_*` exports cannot leak (config-file values beat exports, `docs/ref/auth.md`):

```bash
env -i HOME="$HOME" PATH="$PATH" \
  VT_CONFIG=/tmp/x/vt.env VT_WORKSPACE_PATH=/tmp/x/workspace.json \
  VT_RUN_DIR=/tmp/x/run VT_STATE_DIR=/tmp/x/state VT_TMUX_SOCKET=fsh-x \
  VT_TUNNEL_PROVIDER=none CLAUDE_CONFIG_DIR=/tmp/x/cc \
  .venv/bin/python server/main.py   # set VT_PORT
adb reverse tcp:PORT tcp:PORT       # phone: http://localhost:PORT
```

Real-CLI checks need a pane on that tmux socket **and** an attached PTY (WebSocket); a pane nobody has open is never scanned. A temp `CLAUDE_CONFIG_DIR` is not logged in, so Claude Code hooks are exercised with captured payloads there.
