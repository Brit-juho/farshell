# Session and view measurements

Evidence behind the session/view rules, the `waiting` kind and release logic, and the phone (touch) behavior. Rules live in [`terminal.md`](./terminal.md) (「Session and view rules」) and [`agents.md`](./agents.md); this file keeps the measured results. Screenshots are not tracked.

Re-run on the locally installed Claude Code 2.1.318 and Codex 0.164.0 (default and bypass hook order, the Claude choice footer, Codex approval title and decline). Other rows were measured earlier with Claude Code 2.1.289; Codex 0.160.0; agy (Antigravity CLI) 1.2.14, and were not re-run, with the agy ask-user case re-checked on 1.2.16; tmux 3.7c. Results for other CLI versions are not covered.

## 1. Waiting-kind measurements per CLI

Setup: own tmux socket, empty git repo under /tmp, isolated server with `VT_CONFIG`/`VT_STATE_DIR`/`VT_RUN_DIR`/`VT_TUNNEL_PROVIDER=none`, hooks installed into a temp `CLAUDE_CONFIG_DIR`. PTY detection requires a pane on the server's own tmux socket **and** an attached PTY (WebSocket) — a pane nobody has open is never scanned.

| CLI (version) | Scenario | Result |
|---|---|---|
| Claude Code 2.1.289 | Bash approval | `approval` (hook `PermissionRequest`, options 1-4 from the screen) |
| Claude Code 2.1.289 | AskUserQuestion | `choice`, options from the hook |
| Claude Code 2.1.289 | plan mode ExitPlanMode | `plan` from the hook. Screen-only fallback: the title wraps at 80 columns (`Would you like to` / `proceed?`), so the phrase `Claude has written up a plan and is ready to execute` is matched too |
| Claude Code 2.1.289 | reject `4. No` / ESC | No hook fires; the `interrupt` phrase releases the entry (section 2). Trust-folder dialog -> `approval` (screen) |
| Claude Code 2.1.289 | `/api/agents/coverage` | `path: hook`, kindSources `hook` for approval/choice/plan |
| agy 1.2.14 | command approval | `approval` (screen); ESC -> done (PTY), `Interrupted · What should Antigravity CLI do instead?` -> done (non-PTY, via `interrupt`) |
| agy 1.2.14 | ask-user choice | `choice`. The footer is split by colour codes, so detection runs on ANSI-stripped text. Skipping with ESC outside FarShell leaves `(skipped)` on screen and the entry **stays `waiting` until the TTL** (no stable phrase exists; ESC through the PTY works). After a skip the footer disappears and `? <question>` / `(skipped)` stay, then the model adds free text (`You skipped the question. How would you like to proceed?`, not fixed CLI text). In the cleaned window `(skipped)` is **not at a line head** (glued after stray `>4;2m` residue of an unstripped `CSI > 4;2 m`), so a `\n(skipped)` anchor cannot match; bare `(skipped)` would also hit tool output and end a `working` entry. So no `interrupt` phrase is defined for it |
| agy 1.2.14 | plan mode artifact | badge `plan` appears (`1 artifact · /artifact to review`) but on a **`working`** entry: agy has no end-of-turn signal, so "done + plan" does not occur, and the badge cannot end the turn because the footer persists into later turns. The row shows Has plan (계획 있음) as a secondary mark beside Working (작업 중) (badges are shown outside `done` too) |
| Codex 0.160.0 | command approval (`mkdir`, read-only sandbox) | Screen `Would you like to run the following command?` + `1. Yes, proceed (y)` / `2. ... (p)` / `3. No, and tell Codex ... (esc)`: `waiting`/`approval`, options `y`/`p`/`esc`. Hook `PermissionRequest` gives `approval` first (`options` filled once the screen arrives) |
| Codex 0.160.0 | file-edit approval (`apply_patch`) | Option 2 is `(a)` (`Yes, and don't ask again for these files (a)`). The options regex takes any one-letter key or `esc`; result `y`/`a`/`esc` |
| Codex 0.160.0 | decline (`esc`) at an approval, ESC while working, ESC on the question overlay | All three leave `■ Conversation interrupted - use /feedback if something went wrong` (a decline adds `✗ You canceled the request to run ...` above it) and **no hook**. `interrupt = ["Conversation interrupted"]` moves `waiting`/`working` -> `done` in each case (also through the app's `/api/sessions/{id}/keys` ESC) |
| Codex 0.160.0 | stale `Conversation interrupted` line during the next turn | The line stays in scrollback and tmux partial redraws re-send it with no `Working ... esc to interrupt` line, so a naive phrase match would end a running turn as `done` (`working -> done -> working` flicker; 100+ hits when replaying recorded frames). After a real interrupt the redraw also carries the new `› <user input>` / `• <answer>` lines, so `interrupt_stale_after` in `codex.toml` ignores the phrase when such a line follows. Residual risk: a redraw that carries only the stale line and nothing after it cannot be told apart from a real interrupt. It did not occur in recorded frames; the 15 ms burst of full-history frames right after pressing Enter does contain it, but arrives before the `UserPromptSubmit` hook, while the entry is still `done` |
| Codex 0.160.0 | Plan mode `request_user_input` (question overlay) | `Question 1/1 (1 unanswered)` ... `enter to submit answer`, bottom line still has `esc to interrupt`: `waiting`/`choice`, options `1`/`2` from the `PreToolUse` hook (screen alone also yields `3. None of the above`; the hook list wins). Plain-text question in Default mode: no overlay, `done` with `badge: question` |
| Codex 0.160.0 | Plan mode `Implement this plan?` | The `Stop` hook arrives ~40-130 ms **before** the overlay is drawn, and `on_waiting` refuses waiting on a `done` entry, so a `plan` screen reopens a `done` entry that finished within `PLAN_AFTER_DONE_SEC` (10 s); other kinds keep the rule. Overlay lines are `1. Yes, implement this plan   Switch to Default ...` (no `(key)`): a second `options` regex reads them and the digit selects at once (`3` closes it with `No, stay in Plan mode`). Option 3's label and description are joined by `\x1b[13X\x1b[13C` (erase + cursor forward), so cursor-forward is stripped as blanks. Answering `3` through the app leaves the entry `working` (no turn starts, no hook): it stays so until the next hook or the 15-minute TTL |
| Codex 0.160.0 | coverage | `GET /api/agents/coverage` with the hooks installed: `path: hook`, `kindSources` approval/choice `hook`, plan `screen`, question `fallback` |

Codex measurement notes: each scenario was one real model turn against an isolated `CODEX_HOME` (own `config.toml` with `approval_policy = "on-request"`, `sandbox_mode = "read-only"`, FarShell hooks installed with `codex_hooks.py install`) and an isolated server (`env -i`, own `VT_CONFIG`/`VT_WORKSPACE_PATH`/`VT_STATE_DIR`/`VT_RUN_DIR`, `VT_TUNNEL_PROVIDER=none`, `VT_CODEX_HOME`). A WebSocket client on `/ws/{id}` must stay attached and answer the server's ping with pong; without it the server drops the client and detection goes blind. The recorded frames that back the regression tests are in `server/tests/codex_0160_frames.py`. Not measured: decline through option `3`'s digit, the `p` option, and Codex versions other than 0.160.0.

The question heuristic: a Stop/done whose `last_assistant_message` ends with `?` only gets `badge: "question"` (never `waiting`: rhetorical questions and quotes would block the prompt queue). agy plan mode: the `/artifact to review` footer gives badge `plan` on a **working** entry (shown as a secondary Has plan (계획 있음) mark, never as done); agy has no end-of-turn signal and the footer persists into later turns, so it cannot end the turn.

## 2. Where `waiting` is released

| Path | Trigger | Reacts to attach/resize redraw? |
|---|---|---|
| Screen exit pattern | `exit` literal (`esc to interrupt`) drawn newer than the prompt (`PromptDetector.feed`) | Only if the redraw contains it; a redrawn dialog does not |
| Screen `interrupt` phrase | `Interrupted · ...` (Claude) / `Conversation interrupted` (Codex) newest in the window -> `done`; for Codex ignored when a new `› ` input or `• ` answer line follows it (`interrupt_stale_after`) | No |
| User input | any WS binary / `/keys` / paste -> `deps.on_terminal_input` (ESC -> `done`, else `working`) | No: protocol replies are filtered (below) |
| Hook | next `pre`/`post`/`stop`/`prompt` event | No |
| TTL | `waiting` older than 2 min -> `working` (`sweep`), unless the screen still shows the prompt (then it stays `waiting`) | No |
| Resize | WS `resize` message is not input; tmux redraw has no exit literal | No (measured) |

There is **no** "output resumed" release rule. The thing that must not release `waiting` is the terminal's own protocol replies: on every attach tmux queries the client terminal (`ESC[c`, `ESC[>c`, `ESC[>q`, `ESC]10;?`, `ESC]11;?`, `ESC[?996n`; as sent by tmux 3.7c). xterm.js answers through `onData`, the frontend forwards `onData` as WS bytes, and the server cannot tell those replies from a keypress by bytes alone. `terminal_reports.is_terminal_report` therefore treats input made only of protocol replies (DA, CPR, DSR, focus, DECRPM, DCS, OSC, APC) as not human (Ctrl+F3 `ESC[1;5R` and any mixed chunk still count as typing). There is no timeout or cap. SGR mouse reports (`ESC [ < 0 ; x ; y M/m`: tap, click, wheel) are pointer events and do not release `waiting` either; a mouse report mixed with a typed key, or a malformed one, still counts as human input.

Verified in an isolated server + real tmux with a hook-made `waiting` and a shell dialog, client replies emulated over WS: first attach, second attach, reload and resize all stay `waiting`, and a typed ESC still gives `done`. On a real phone, xterm.js sends OSC 10/11 colour replies and `ESC [ O` on every attach, and entries stay `waiting` through attach, reload, a second browser tab, landscape rotation and split-attach. **Not verified with a real Claude Code** (a temp `CLAUDE_CONFIG_DIR` is not logged in).

#### Rejecting an approval or pressing ESC

**Rejecting (`4. No`) or pressing ESC at a permission dialog emits no hook at all** in Claude Code (no Stop, no PostToolUse; ESC while a tool runs is the same). The screen goes back to an empty input line (`⎿ Interrupted · What should Claude do instead?`); without screen handling the entry would stay `working`/`tool=Bash` until the 15-minute TTL. ESC sent with `tmux send-keys` bypasses FarShell's PTY, and only PTY input (`deps.on_terminal_input`) clears waiting (ESC -> `done`, other keys -> `working`), so test ESC through the web terminal.

- **Mechanism**: no hook exists for "user declined"; the only signal is the screen. `interrupt = [...]` in `detect/<cli>.toml` (claude, agy, codex) — when the phrase is drawn newest in the window, `agent_status.finish_interrupted_for_session` marks that tmux session's `waiting`/`working` entries `done`, and the detector drops its window so the old prompt text cannot revive `waiting`. No timeout or cap.
- **Detection runs on ANSI-stripped text** (`PromptDetector._append_clean`): the real TUIs colour each word separately and join lines with absolute cursor moves, so byte-literal matching misses `Interrupted ·` (Claude) and agy's choice footer, and glues option 2/3/4 labels into one.
- A `done` entry finished by reject/ESC carries `_reopen`, so the **next** screen prompt is accepted (agy has no hooks, so nothing else would move `done` back to `working`). Entries finished by a Stop hook keep the rule that late screen detection never reverts `done`.
- `PermissionRequest` can arrive **after** the dialog is drawn; it does not wipe the options the screen already captured (inline approve buttons).
- Claude `kindSources` is `hook` for approval/choice/plan whenever the Claude hooks are installed (`HOOK_KINDS` in `server/routes/agents.py`).
- **Codex**: declining an approval, ESC while working and ESC on the question overlay all end with `■ Conversation interrupted - use /feedback if something went wrong` and no hook; `interrupt = ["Conversation interrupted"]` releases them. The line stays on screen for the following turns, so the phrase is only trusted when no new `› `/`• ` line follows it. Pressing ESC on the `Implement this plan?` overlay just closes it (no phrase).


## 3. Real-device measurement

**Device.** SM_A5260 (1080x2400, density 420, DPR 2.625), Chrome `innerWidth` 411 portrait / 832 landscape.

**Method.** Isolated FarShell server (own `VT_CONFIG`, `VT_WORKSPACE_PATH`, `VT_RUN_DIR`, own tmux socket, tunnel provider none) on a spare port; `adb reverse tcp:<port> tcp:<port>`; Chrome driven over CDP for DOM/computed-style reads and screenshots; touches done with real `adb shell input swipe/tap` (not CDP-synthesised events) so that scroll, long-press and swipe-navigation behave as on a finger. Read-only against the user's real server.

| Item | Result |
|---|---|
| View order (sheet edit mode, per-view up/down) | grouped view moves as a block, ends stop (no wrap), active view unchanged (rules in `terminal.md`) |
| Swipe between sessions | order is layout order (view order, then pane order), `n/m` follows |
| State display (rail rows, view-tab segment bar) | the waiting row has an amber tint (the light-skin grey read as inactive); contrast from token values: light 12.1:1 text / 6.4:1 secondary, dark 14.9:1 / 6.4:1 |
| Reload restore | closed sessions stay closed; layout is per browser tab |
| 「Connected screens」 sheet | buttons have 44px touch targets; the sheet cannot be reopened in landscape with no open view (no row menu entry) |
| Landscape (832px) | the soft keyboard leaves little height; focus mode (below) hides chrome, and everything returns when the keyboard closes |
| Icons | SVG icons, no emoji; origin icons for local and peer (two) |
| `waiting` across attach/reload/rotation | stays `waiting` (section 2); real keys release it (`yo` typed on the phone: waiting -> working) |
| Single tap on the terminal | does not release `waiting` (SGR mouse reports are pointer events) |
| View edit | per-view close (44x44), grouped view close and restore, empty single view cleaned on opening the sheet, last view -> onboarding, up/down |
| Row-menu summary line (state, kind keyword, non-web clients, origin, branch) | wraps to two lines in the landscape rail menu, never clipped |
| Touch pane split (>=720px landscape), absent in portrait | in landscape the visible height is 331px; `Menu` measures itself after mount, pulls itself inside the visible viewport and scrolls if still too tall, so items of a bottom rail row stay reachable |
| Landscape + real soft keyboard focus mode | tab bar, pane headers and rail hide, the pane uses the remaining 111px |
| Hit areas of the tab x and pane-header x | 44x44, corner taps do only the intended action; the 4px gap against the neighbouring 18px split buttons was not stress-tested |

**Rules these measurements back.**
- Fleet inline approval buttons are de-duplicated: a full-screen TUI redraw leaves several copies of one dialog in the detection window, so the same key appears once and the latest label wins.
- Touch targets are 44px under `pointer: coarse` or the mobile range (approval buttons, connected-screens close/detach/keep-only, view-tab x, pane-header x); tab x and pane x use transparent padding so the visible 18px box is unchanged (desktop stays 18/24px).
- `[hidden]{display:none !important}` lives in the base layer: `.vt-btn{display:inline-flex}` otherwise beats the UA `[hidden]` (the "resend recording" button was visible at >=720px).
- Touch pane splitting is available through the row menu (width >=720px and coarse pointer only), relaxing "grouping by drag only" for touch. Landscape-phone keyboard focus mode (`visualViewport.height < 300px`) hides tab bar, headers, rail, dock, HUD and bottom nav. The row menu starts with a one-line summary (touch has no hover tooltip). The phone sheet can close a view and prunes blank views.

**Known limits / substitute tests.** Peer origin was exercised by setting the tmux option `@fsh_origin` directly rather than with a real peer host. The 719px/720px boundary was checked by emulation (resized viewport), not by a device that reports that width. No real desktop touch screen (>=720px coarse) was available; the touch split menu was verified by emulating `pointer: coarse`.

### 3.1 Server restart keeps every pane

Restarting only the server (same state dir) with the page left open keeps the layout: views stay intact, terminals re-attach, layout names are kept; reload and new tabs restore as well. If one tmux session was killed while the server was down, only that pane is empty (`[rs2, null]`) and the others are kept.

Mechanism: `session_store` is in memory, so after a restart the page's WS reconnects to a web id the server no longer has -> close 4004. `term/ws.js` must not treat 4004 as "session was killed" outright: that path (`vtCloseSession` -> `removeSession` -> `clearSessionFromLayout` + `saveLayoutNow`) serialises an empty pane as `session: null`, losing the tmux name on the next autosave (and in the server's `ui.layout` "latest" copy that new tabs and reloads read). So on 4004 for a local tmux session, `recoverSession` (`term/session.js`) asks `/api/tmux/sessions`; alive -> `/api/tmux/attach` with the same name, and `replaceSessionId` swaps the pane's id in every tab tree; not listed -> the close path (killed = empty pane); undetermined (list/attach failed) -> normal reconnect backoff. Plain (non-tmux) PTY sessions die with the server and take the close path.

Note for synthetic tests: the default `waiting` TTL is 2 minutes, so synthetic states turn `working` by themselves unless `VT_AGENT_TTL_*` is raised.

## 4. Bundle size

The initial payload (`app.js` + `core.js`) has a ceiling of 307,200 B (300 KiB), asserted by `frontend/tests/build-output.test.js` and set in `ci.yml` and `release.yml`. The check exists to catch a vendor (xterm, about 290 KB) leaking into the eager bundle, not as a UX budget. `voice.js` has its own 307,200 B check (now 52,852 B). The current payload is 287,512 B (`app.js` 6,624 B + `core.js` 280,888 B; 19,688 B of headroom; `core.js` is 83,598 B gzip). The build strips whitespace and comments but keeps names and syntax (`rolldownOptions.output.minify: { mangle: false, compress: false, codegen: { removeWhitespace: true } }`) because the build-output tests find module-scope names and declaration shapes. History: `c05a174` (2026-11-04) removed the old whitespace-stripping plugin and the leftover `build.minify` object has no effect in lib mode, so indentation and `// frontend/js/...` comments stayed (346,035 B) and the cap was raised to 350 KiB; the 2026-11-10 fix restored the stripping through `output.minify` and put the cap back to 300 KiB. String quotes may become backticks in the output, so the tests accept `["'`]`. Gzip transfer size is far smaller than the raw figure.

`npm run build` prints the top 15 modules of `core.js` by rendered bytes (`reportCoreModules` in `vite.config.js`, information only); use it instead of source-map attribution, which is too coarse. Largest: `layout/panes.js` 18.6 KB, `layout/store.js` 17.4 KB, `ui/icons.js` 15.3 KB, `core/settings.js` 10.9 KB, `term/session.js` 10.2 KB, `layout/persist.js` 9.8 KB, `core/keymap.js` 9.5 KB.

Code kept out of `core.js` as lazy chunks, which new code should follow when it is not needed at boot:
- `panels/agent-profiles.js` and `snippets-util.js` -> `settings`; `panels/system.js` -> `panels`; `ui/number-spinner.js` -> `shell`; `ui/select.js` -> its own `select` chunk (stub `ui/select-lazy.js` stays in core).
- `panels/panel.js` + `core/dialog.js` + the guide body (`term/guide-view.js`) -> `panel` chunk. Boot-path code (terminal, rail, `history/api.js`) imports `core/dialog-lazy.js`, whose `confirmDialog`/`promptDialog`/`alertDialog` return Promises; chunks that need the synchronous `openDialog`/`nextId` import `core/dialog.js` directly and pull the `panel` chunk with them. If the chunk cannot be fetched the stub falls back to `window.confirm`/`prompt`/`alert` (a silent "cancel" would hide why nothing happens, a silent "ok" would drop the safeguard). `build-output.test.js` asserts that neither `core.js` nor `shell.js` imports `panel.js` statically.
- `term/upload.js` -> `upload` chunk. `term/upload-lite.js` stays in core: `hasFiles`/`filesFromDataTransfer` must run synchronously inside the drop event (a `DataTransfer` is empty once the event ends), the window-level dragover/drop guard must exist from boot (otherwise a file dropped outside a target navigates the page away), and `window.vtUploadFiles/vtUploadJobs/vtCancelUpload` are stubs until the engine loads (no engine means no jobs: `[]` and `0` are the correct answers).
- The file viewer body (CodeMirror 6, `panels/viewer/editor.js` + `editor-text.js` + `@codemirror/*`/`@lezer/*`) -> `editor` chunk, 586,970 B raw / 146 KB gzip, imported by `viewer/file.js` when a file opens. Each language pack is its own chunk, fetched only for that language: `editor-js` 86.6 KB (33.2 KB gzip), `-md` 64.5 KB, `-java` 50.4 KB, `-py` 49.6 KB, `-legacy` (shell, Kotlin, Swift) 46.3 KB, `-html` 37.5 KB (also pulls `-js` and `-css`), `-go` 31.4 KB, `-css` 21.7 KB, `-yaml` 19.8 KB, `-sql` 15.3 KB, `-json` 3.2 KB. One chunk with every language was 1,008,642 B (289 KB gzip). Not in `sw.js` PRECACHE (dist chunks are network-first; file contents need the server anyway). The initial payload did not change (325,488 B).
- Deliberately kept in core (synchronous boot-path code): `term/e2e.js` (6 KB; the handshake listener must be attached before the first WebSocket message, and it is security-relevant), `layout/dnd.js`, `ui/toast.js`, `core/keymap.js` and the agent state modules.

## 5. View-tab status bar

The tab carries a segment status bar as an inner pill (3/6px): the 2x thickness difference (unseen vs seen) is still distinguishable at four segments and it reads in both light and dark skins. The active-tab underline uses the background tone because the bar owns the bottom edge. Alternatives (flush to the bottom edge at 2/5px, or on the top edge) were not adopted.

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

## 7. File viewer render baseline

Tool: `frontend/tests/perf/` (not in CI). `npm run build` first (the page loads `frontend/dist/app.css`), then `node frontend/tests/perf/run.mjs` (`--runs N`, `--only a,b`, `--profile desktop|mobile`, `--out file.json`, `--chrome path` or `CHROME`). It serves the **unbundled** viewer modules (`viewer/file.js` `renderFile`/`refreshFile`, `viewer/diff.js` `_renderDiffDOM`; the file tree since §7.5 from `files/mount.tsx`, bundled once per run by `run.mjs` with vite + `vite-plugin-solid` and served as `/perf-build/tree-bench.js`) from a fake server on 127.0.0.1 that mimics `/api/fs/file` (512 KiB cap, `truncated`), `/api/fs/tree` (pages of 1000 with `after=` cursors, `total`/`next`, depth-2 prefetch budget) and `/api/fs/roots`; inputs come from `gen.mjs` (seeded synthetic JS, minified line, diff, 50 x 100 folder tree), never real code or paths. The file-watch WebSocket is a page-side fake so the tree scenario can send one "changed". Headless Chrome is driven over CDP (no dependency), a new tab per run. Measured: request start → next paint, including HTTP + JSON; **not** measured: lazy-chunk download (the editor module and the JS language pack are preloaded; until §7.7 the hljs script was too), real devices.

Scenarios: `open-512k`, `open-4m` and `long-line-1m` (both cut to 512 KiB by the server today), `concurrent-3` (three viewers at once), `diff-5000`, `tree` (open, expand 100 folders, one change on the root), `live-10s` (a change every 200 ms for 10 s through `coalesce` + `refreshFile`, as auto-refresh does). Long tasks are the browser's >50 ms tasks; p95 over 10 runs is the nearest rank (the slowest run).

Baseline 2026-11-07 (Chrome 156 headless, Node 22, Apple silicon Mac, 10 runs; median / p95):

| Scenario | Desktop paint ms | Desktop long tasks ms | Phone (4x CPU) paint ms | Phone long tasks ms | DOM nodes |
|---|---|---|---|---|---|
| open-512k | 550 / 572 | 537 / 564 | 2308 / 2343 | 2261 / 2327 | 69,677 |
| open-4m (512 KiB shown) | 546 / 567 | 523 / 556 | 2288 / 2348 | 2238 / 2286 | 68,959 |
| long-line-1m (512 KiB shown) | 519 / 549 | 513 / 544 | 2040 / 2070 | 2024 / 2056 | 74,585 |
| concurrent-3 | 1795 / 1810 | 1056 / 1086 | 8156 / 8879 | 5087 / 5599 | 209,007 |
| diff-5000 | 295 / 316 | 289 / 311 | 1213 / 1229 | 1197 / 1216 | 41,676 |

- `tree`: open 28 / 38 ms (phone 91 / 112). Expanding 100 folders, summed: 164 / 197 ms (phone 1422 / 1565). **One change on the root re-requests 101 listings** (root + every expanded folder, each `depth=2`) and redraws in 163 / 185 ms, 127 ms of it long tasks (phone 656 / 771 ms, 516 ms long tasks).
- `live-10s`: every refresh redraws the whole file even when only one line changed. Desktop: 19 redraws in 10 s, frame interval p95 517 ms, long tasks 9.8 s of the 10 s. Phone: 5 redraws, frame interval p95 2183 ms, long tasks 11.2 s — the page is effectively frozen while a file under change is open.

### 7.1 CodeMirror 6 file body (2026-11-07)

The file body now renders with CodeMirror 6, read-only (`viewer/editor.js`, [files.md](./files.md)). Same tool, same machine, same session: the old renderer was measured again from a copy of the tree just before the change ("before"), then the new one ("after"). The page preloads the editor module and the JS language pack the way it preloads hljs, so chunk download is not in these numbers (sizes: §4). `open-*` runs fail if the body was not drawn by CodeMirror (the plain-text fallback would look fast too). `diff-5000` and `tree` are unchanged code and were not re-run.

| Scenario | Desktop paint ms before → after | Desktop long tasks ms | Phone (4x CPU) paint ms | Phone long tasks ms | DOM nodes (phone) |
|---|---|---|---|---|---|
| open-512k | 556 → 47 | 548 → 0 | 2398 → 193 | 2346 → 169 (p95 181) | 69,677 → 1,038 |
| open-4m (512 KiB shown) | 543 → 52 | 531 → 0 | 2350 → 196 | 2311 → 143 | 68,959 → 1,049 |
| long-line-1m (512 KiB shown) | 530 → 42 | 525 → 0 | 2059 → 167 | 2036 → 158 | 74,585 → 906 |
| concurrent-3 | 1775 → 72 | 1047 → 0 | 8168 → 312 | 5122 → 143 | 209,007 → 3,086 |

Medians of 10 runs. `live-10s` (a change every 200 ms for 10 s): the refresh now replaces only the changed range in the open editor. Desktop: 19 → 49 refreshes, frame interval p95 517 → 17 ms, long tasks 9.9 s → 50 ms. Phone: 5 → 49 refreshes, frame interval p95 2183 → 17 ms (max 83 ms), long tasks 11.3 s → 184 ms. The phone `open-512k` long-task sum (169 ms median) is just above the 150 ms that was set as a provisional target; the largest single task is about 100 ms (initial parse and layout of the visible range).


### 7.2 Range reads, large files, continuous change (2026-11-07)

The viewer now reads `/api/fs/file` in 1 MB ranges (up to 4 MB at once, Show more (더 보기) for more, conditional refresh — [files.md](./files.md)). The fake server in `run.mjs` follows the range contract (line-end cuts, `line_truncated`, `tail`, rev/ETag/304, `expected_rev` → 409). New scenarios: `open-12m` (first 4 MB + Show more note), `live-same-10s` (change notices every 200 ms, file unchanged), `live-4m-10s` (4 MB file whose rev changes every 200 ms, so a refresh that reads several ranges can hit 409). `open-4m` and `long-line-1m` now load the whole file (they were cut to 512 KiB before). New columns: refresh calls vs. refreshes that changed content, KB in the editor, 304 and 409 counts. Same machine and tool as §7.1, 10 runs, median / p95.

| Scenario | Desktop paint ms | Desktop long tasks ms | Phone (4x CPU) paint ms | Phone long tasks ms | Loaded |
|---|---|---|---|---|---|
| open-512k | 45 / 53 | 0 / 0 | 193 / 202 | 145 / 172 | 512 KB, 1 request |
| open-4m | 98 / 119 | 0 / 0 | 476 / 485 | 254 / 278 | 4 MB, 5 requests |
| open-12m | 107 / 123 | 0 / 0 | 515 / 531 | 246 / 298 | first 4 MB (5 requests) + note |
| long-line-1m (one 1 MB+ line) | 75 / 84 | 0 / 0 | 229 / 311 | 206 / 215 | whole line, 2 requests (first `line_truncated`) |
| concurrent-3 | 73 / 80 | 0 / 0 | 312 / 319 | 176 / 190 | 3 × 512 KB |

Continuous change (10 s, a notice every 200 ms, coalesced like auto-refresh):

| Scenario | Frame interval p95 / max ms (desktop) | (phone) | Refreshes / content changed (phone) | 304 / 409 (phone) | Long tasks ms (phone) |
|---|---|---|---|---|---|
| live-10s (512 KB, changes every request) | 17 / 33 | 17 / 83 | 49 / 49 | 0 / 0 | 397 (p95 539) |
| live-same-10s (unchanged) | 17 / 33 | 17 / 83 | 49 / 0 | 50 / 0 | 168 |
| live-4m-10s (4 MB, rev every 200 ms) | 17 / 33 | 33 (p95 run 50) / 83 | 49 / 25 | 24 / 26 | 1701 (p95 3100) |

Against the targets in the plan: phone `open-512k` long tasks 145 ms (target ≤ 150 ms; 169 ms in §7.1 — the range reply is smaller than the old full reply). An unchanged file draws nothing (`live-same`: every refresh is a 304, zero content changes). Frame interval p95 under continuous change stays ≤ 50 ms in every run, including the 4 MB file on the phone profile (median 33 ms, worst run 50 ms). The cost there is the re-read: each change re-reads the loaded 4 MB (5 ranges); on the phone profile about half the refreshes hit a 409 mid-read and restart, long tasks add up to 1.7 s over 10 s and the JS heap peaks at about 200 MB in the worst run (71 MB median) before GC. Desktop never hit a 409. Not measured: real devices, a real server (the fake answers in well under a millisecond, so network and server read time are not in these numbers), and the Show more (더 보기) click itself (covered by the browser smoke test).

Initial payload (app.js + core.js): 325,488 → 327,335 B (cap 358,400; `vtFetchConditional`, the link `:line:col` split and the host-aware `openFile`).

### 7.3 Terminal under the file overlay (300 ms repaint tick)

`overlay.js` keeps two workarounds for a report that the terminal below the overlay's horizontal range stopped updating (left black or with missing glyphs): on open/move/resize/close it clears every session's glyph atlas and redraws all rows, and while the overlay is open it redraws the active session's rows every 300 ms. The plan was to find the cause (compositing with the WebGL canvas) and remove both. Probe (2026-11-07, isolated server, Playwright Chromium headless, xterm WebGL renderer): both workarounds disabled, a float overlay and a sheet over the terminal, 40 bursts of coloured lines and box-drawing frames written under it, then the region below the overlay captured before and after a forced full redraw and after an atlas clear. With SwiftShader and with ANGLE on Metal (Apple M1 Pro), 3 trials per surface: the captures were identical every time — **not reproduced**, so the cause is not known and both workarounds stay. The tick's own cost is small: one full `renderRows` of a 144 × 48 terminal took 0.2–0.3 ms. A reproduction probably needs the headed browser and the GPU compositor of the machine that showed it.

### 7.4 Refreshing a big, changing file: grown-end reads and the 1 s gap (2026-11-07)

Change ([files.md](./files.md) 「A big view refreshes only what grew」): with more than 256 KB on screen a refresh asks for the 8 KB before the loaded end first and, when the file only grew, reads just the bytes after it; auto-refresh of a view over 1 MB runs at most once a second (`coalesce` `minGap`). Same tool as §7.2 with two additions: the scenario `live-grow-4m-10s` (a 4 MB build log that gains one line every 200 ms, `rev` in the server's `dev:ino:size:mtime_ns` shape) and the column 「연속 변경 중 내려받은 본문」 (bytes of `content` the fake server sent during the 10 s of changes, the open excluded). The bench calls `coalesce` with `refreshGap` as auto-refresh does. "Before" is the `dev` viewer (`file.js`, `file-load.js`, `editor.js`, `fs-watch.js`) measured with the same bench right before the change. 5 runs, median (p95 where it differs).

| Scenario | Profile | Downloaded in 10 s before → after | Refreshes | Frame interval p95 / max ms | Long tasks ms | 409 | JS heap MB |
|---|---|---|---|---|---|---|---|
| live-grow-4m-10s | desktop | 200.7 MB → 81 KB | 50 → 10 | 17 / 17–33 → 17 / 17 | 0 → 0 | 0 → 0 | 528 (p95 712) → 25 |
| live-grow-4m-10s | phone (4x CPU) | 150.3 MB (p95 195.3) → 81 KB | 50 → 10 | 17 / 67 → 17 / 17 | 1366 → 0 | 26 → 1 | 333 → 17 |
| live-4m-10s (last line rewritten) | desktop | 200.7 MB → 40.2 MB | 50 → 10 | 17 / 33 → 17 / 17–33 | 0 → 0 | 0 → 0 | 18 (p95 108) → 35 (p95 51) |
| live-4m-10s | phone | 169.3 MB (p95 200.7) → 40.2 MB (p95 80.2) | 50 → 10 | 17 (p95 run 50) / 83–100 → 17 / 83 | 1610 (p95 3069) → 728 | 27 → 3 (p95 13) | 49 (p95 141) → 81 |
| live-10s (512 KB, last line rewritten) | both | 25.1 → 25.5 MB | 50 → 50 | unchanged (17 / 33 desktop, 17 / 83 phone) | unchanged | 0 | — |
| live-same-10s (unchanged) | both | 0 → 8 KB | 50 → 50 | unchanged | unchanged | 0 | — |

- The growing log is the case the change targets: about 8 KB of window plus the new lines per refresh instead of the whole 4 MB, and one refresh per second instead of five. Frame interval p95 stays at 17 ms on both profiles (target ≤ 50 ms), and the phone's long tasks drop from 1.4 s to 0.
- `live-4m-10s` rewrites the last line rather than appending, so every window differs and each refresh is a full re-read; only the 1 s gap reduces it (5x fewer reads, fewer 409s).
- Cost on small or rewritten files: 512 KB is above the 256 KB threshold, so `live-10s` pays one 8 KB window per refresh before the full re-read (+1.6%), and `live-same` one unconditional window after opening (then 304s on the same window).
- `open-4m` / `open-12m` were re-run with the change (open path now runs inside the viewer's queue): desktop paint 97 / 121 ms, phone 451 / 480 ms, phone long tasks 233 ms — within §7.2's range.
- Not measured: real devices, a real server and network (the fake answers in well under a millisecond), the tail view (same path; covered by `viewer-file.test.js`).

Initial payload (app.js + core.js): 327,810 B (cap 358,400; `layout/panes.js` disposes viewer editors).

### 7.5 File tree: virtual rows, paging and the `tree` watch (2026-11-09)

Change ([files.md](./files.md) 「File tree」): the Files (파일) tree is `frontend/js/files/` (flattened rows, virtual scroll, per-folder row cache keyed by `(host, path)`), folders page by 1000 (`/api/fs/tree` `after=`), and one `tree` subscription on the root re-reads only the folders a notice lists. Same tool; two changes to it: the tree scenarios mount the bundled tree (expanding calls `store.toggle` — off-screen rows are not in the DOM to click), and a new scenario `tree-5k` opens a single folder of 5,000 files (first page, then 「더 불러오기」 to the end, then one change notice on that folder). The change notice is `{kind:"tree", dirs:[""]}` (the root folder) instead of a `dir` notice. "Before" is the old `viewer/tree.js` measured with the same bench right before the change (5 runs); "after" 10 runs. Median / p95.

| Scenario | Profile | Paint ms | Expand 100 folders ms | One change: requests | One change: redraw ms (long tasks) | DOM rows | DOM nodes |
|---|---|---|---|---|---|---|---|
| tree (50 × 100 folders) | desktop | 35 → 16 | 182 → 327 | 101 → 1 | 153 (117) → 6 (0) | 5,251 → 34 | 28,099 → 791 |
| tree | phone (4x CPU) | 86 → 38 | 1,111 → 295 | 101 → 1 | 638 (508) → 4 (0) | 5,251 → 28 | 28,099 → 292 |
| tree-5k (5,000 files) | desktop | 79 (56 long) → 15 (0) | — | 1 → 5 | 69 (67) → 9 (0) | 1,001 → 34 | 14,036 → 831 |
| tree-5k | phone (4x CPU) | 325 (313 long) → 34 (0) | — | 1 → 5 | 250 (246) → 16 (0) | 1,001 → 28 | 14,036 → 292 |

- The target of the plan (a folder change: one request, only changed rows redrawn) holds: the root change is 1 request instead of 101, and since the root's listing is unchanged no row is redrawn (the redraw time is the request). On the phone profile that is 638 ms → 4 ms.
- `tree-5k`: the old tree showed the first 1,000 entries in disk order and could not reach the rest; now all 5,000 are reachable (「더 불러오기」 × 4: 21 ms desktop, 84 ms phone in total) while the DOM keeps about 30 rows. The change notice re-reads the 5 loaded pages (5 requests, 9 / 16 ms).
- Expanding 100 folders on the desktop profile is slower (182 → 327 ms summed, about 1.5 ms per folder). Part is the measurement: the new number runs until the last request ends (a re-read whose rows did not change leaves the DOM alone), the old one until the last DOM change. Part is the flatten of a 5,000-row list per expand; the per-folder cache (`FlattenCache`, `expandVer`) cut it from about 2.3 ms to about 0.6 ms per call. The phone profile, where the old tree paid for creating DOM rows, is 3.8x faster.
- Not measured: real devices, a real server and the native file watcher (the fake answers in well under a millisecond; the server side is covered by `server/tests/test_fs_watch_tree.py` with the polling backend), the name filter on a large tree.

Initial payload (app.js + core.js): 333,040 B on top of redesign phase 2 (329,732 B before; cap 358,400; +3,308 B from the phone Files | Attachments (파일 | 첨부) segment and the two device settings). The tree is the lazy `files` chunk (60,398 B, about 16 KB gzip); `panels.js` 158,584 → 149,037 B, `shell.js` 330,715 → 331,857 B.

### 7.6 File content search (`/api/fs/grep`, 2026-11-09)

Change ([files.md](./files.md) 「Content search」): one engine in the `scan_worker` child process (git candidate list → deny rules → read and match), the palette `?` mode and the sidebar Search contents (내용 검색). Measured on the repository itself (a detached checkout of the branch under `/tmp`, 1,201 candidate files from `git ls-files`, 103 binaries, 4 deny-listed) through an isolated server on this Mac (M1 Pro, APFS), `curl` total time over loopback, 6 runs each. "Cold" is the first request after the server started (first worker spawn); the files had just been checked out, so the page cache was warm in every run — a truly cold disk was not measured (`purge` needs root).

| Query | Results / files | Files read | Cold ms | Warm ms (median of 5) |
|---|---|---|---|---|
| rare literal `groupByFile` | 9 / 4 | 1,201 | 179 | 189 |
| common literal `function` (stops at the 500-line cap) | 500 / 69 | 137 | 126 | 117 |
| regex `def \w+_grep` | 2 / 2 | 1,201 | 169 | 179 |
| literal `fsguard`, `glob=server/**,!**/tests/**` | 452 / 63 | 308 (893 excluded) | 124 | 115 |
| no match | 0 / 0 | 1,201 | 158 | 179 |

- A whole-repository search costs about 150–190 ms end to end, of which the server reports 150–195 ms (`elapsed_ms`): a Python child process start, `git ls-files` and reading every candidate (not broken down further). Cold and warm are the same within noise because nothing is cached between searches by design (each search is a fresh process so the deadline can kill it).
- Hitting the 500-line cap stops reading early (137 of 1,201 files), so common words answer faster than rare ones. Include/exclude globs cut the read set before any file is opened.
- In the browser (headless Chromium, same server) typing `? groupByFile` showed 9 results in 4 groups with the preview at the first match; ↓ then Enter opened `GrepView.tsx` at its second match (line 44) in the viewer; ⌘⇧F opened the sidebar search with the tree root as scope. Korean and English (`VT_LANG=en`) both checked.
- Not measured: a cold disk, very large trees (the 10 s deadline, 200 MB read cap and 200,000-file list cap are covered by `server/tests/test_fs_grep.py` with lowered limits), a remote host through the relay (covered end to end in `test_peer_relay.py`), phones.

Initial payload (app.js + core.js): 334,755 B (333,040 B before; cap 358,400; +1,715 B for the `contentSearch` key, the palette entry's sidebar hand-off and the one-time move toast). The search screen is in the lazy `shell` chunk (331,965 → 370,704 B, +38,739) — the palette and the sidebar share it; `files.js` 60,398 → 63,618 B for the 「트리 | 내용 검색」 segment.

### 7.7 Diff: virtual list, lezer highlighting, hljs removed (2026-11-09)

Change ([files.md](./files.md) 「Diff」): `viewer/diff.js` draws only the visible rows of each file (fixed row heights, absolute placement, ± 600px), context lines are highlighted by the file view's CodeMirror language packs (`editor.js` `highlighterFor`) instead of highlight.js, and `vendor/highlight.min.js` is gone. `diff-5000` now waits for the highlighted rows before taking the paint (the old hljs path was synchronous, the new one goes through the preloaded language chunk) and fails if no context line was highlighted. Same tool and machine; "before" is the `redesign` branch measured right before the change in the same session (10 runs each), so it differs from the 2026-11-07 baseline above (295 / 1213 ms, an older checkout and Chrome session). Median / p95.

| Scenario | Profile | Paint ms before → after | Long tasks ms | Longest task ms | DOM nodes | DOM rows |
|---|---|---|---|---|---|---|
| diff-5000 (one file, 50-line hunks) | desktop | 184 / 205 → 32 / 42 | 180 / 201 → 0 / 0 | 99 → 0 | 30,023 → 728 | every line → 63 |
| diff-5000 | phone (4x CPU) | 852 / 876 → 119 / 123 | 845 / 867 → 91 / 95 | 505 → 91 | 30,023 → 776 | every line → 68 |

- The 2026-11-07 baseline (desktop 295 ms, phone 1213 ms) is now 32 / 119 ms. What is left on the phone profile is mostly the JSON parse and diff parse of the 5,000-line text and the first visible rows; hunks below the screen are not parsed for highlighting until they are drawn.
- A diff of at most 400 rows is still drawn whole (browser find and copy cover it); the bench diff is far above that.
- Not measured: scrolling through the list (rows are created per frame for the newly visible range), real devices, a real server.

Bundles (raw bytes, `npm run build`): initial payload (app.js + core.js) unchanged at 345,311 B (cap 358,400). `panels.js` 223,374 → 230,716 B (+7,342, the virtual list; 63 KB gzip); `editor.js` 650,390 → 651,601 B (+1,211, `highlighterFor`; 160 KB gzip). `frontend/vendor/highlight.min.js` (127,496 B, 43 KB gzip) is deleted and out of `sw.js` PRECACHE (`vt-static-v11`). Opening a diff now fetches the `editor` chunk plus the file's language chunk (for example `editor-js` 86.6 KB) instead of hljs, usually already cached by a file view.
