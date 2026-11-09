# Agent state, queue, MCP

## Customization manager

The dock popup opens the unified manager (`panels/agent-config.js`,
`routes/agent_config.py`). All FarShell collects native settings without moving
them; Selected session binds to the foreground CLI's actual settings root and
project. Request-local roots (`agent_config_context.py`) do not mutate the server
environment. Codex plugin-list caching is keyed by settings root.

`agent_config.py` stores only organization/presets/environment overlays and
verified profile roots in private host-local state. Native settings remain the
installation truth. Mixed plans preserve nonmembers and check both the scanned
source and actual write destination; per-file reviewed hashes are checked inside
the shared config lock. Native writes are backed up. Project-shared plugin writes
are available only through this reviewed manager, not the legacy toggle API.

Restart jobs require an identifiable native CLI, known original arguments, an
empty recognized composer and an exact conversation ID. They wait up to 15 minutes,
request normal exit, and resume the same profile/directory/conversation without a
kill fallback. New process/hook evidence is required; MCP runtime stays unknown.
Private one-use launch files keep environment values out of command arguments.

Remote management has a dedicated signed capability. A peer can inspect and
request an exact peer-bound plan, but cannot execute a write. The target's direct
human/elevated approval and current peer permission are required. Generic MCP
write relays remain blocked. No login credentials or raw skill contents are sent
to peers. Existing peer-sync is observed/triggered through its installed scheduler;
its file scope and merge policy are not changed.

User workflow and limits: [customization manager](../guide/agent-config.md).
Endpoint contracts: [API.md](../../API.md).

## Features

| Feature | Description |
|------|------|
| Agent state | The server decides one of `idle`/`working`/`waiting`/`done` per session (the reserved `error` has no entry path and the frontend folds it to `idle`) and everything else just displays it; labels and the representative-state priority live in `frontend/js/design/state-classes.js` — worktree tabs, pane headers, the rail list (sorted so what needs you is on top), the favicon, and the app icon badge (`waiting` count). Claude Code and Codex lifecycle hooks are installed together by `fsh hooks install`; `fsh doctor` and Settings → About report each tool separately |
| Approval detection (`waiting`) | Detected off the PTY stream using patterns in `server/detect/*.toml`. Cleared by an exit pattern, by an `interrupt` screen phrase (a rejected approval or ESC — Claude Code emits **no** Stop hook for either), by you typing in that pane, by the next prompt/Stop, by a pre/post of the **same** tool call (see «Session owner and waiting release»), or by a 2-minute TTL. **The prompt queue will not feed a pane that is `waiting`** — `send-keys` there would be consumed as the approval answer |
| Detection coverage | Settings → Agents → "Detection coverage" (`GET /api/agents/coverage`). Approval detection silently does nothing for a CLI with no patterns, so this table makes that visible per CLI: `path` (`hook`/`report`/`pty`/`none`), the toml's line count, the states it can express, and `trust` (`high`/`mid`/`low`), plus `kinds`/`kindSources`/`badges` — which waiting kinds the CLI can tell apart and whether each comes from a `hook`, the `screen`, or the `fallback`. Claude Code reaches `hook`/`high` with its twelve managed events (tool, permission, prompt, stop, subagent, session lifecycle; the list is `EVENTS` in `server/claude_hooks.py`), and Codex with its eight (`server/codex_hooks.py`). Either reaches `hook`/`high` only when every managed event is installed; an install missing any of them shows `pty` until `fsh hooks install` is run again. Codex also retains real-terminal-verified PTY patterns as a fallback, including `y`/`p`/`esc` approval keys. aider/agy rely on PTY patterns or explicit `fsh pane report`. `enter`/`exit` are **literal byte strings** matched against a bounded 16KiB terminal-output window; `options` and `interrupt_stale_after` are regexes. Background and how to fix a pattern: `docs/help/agent-detect.md` |
| Usage gauge | Two sources, merged into one list. **clauth** reads `~/.clauth/status.json` (file, not CLI). Accepts feed schema **1 and 2** (supporting only one would silently kill the gauge on a clauth upgrade); an unknown schema disables it, and Settings → About says so. Shown in the right rail on wide screens and in a panel elsewhere. **Disappears entirely when there's no source** — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`native`/`none`) controls it. Tokens are excluded by a field whitelist. **Codex** is a second limit-type provider — it calls the same OAuth usage API the Codex CLI itself uses, reading the token from `~/.codex/auth.json` on every call and never storing it. Being a remote API it uses a 60s TTL cache and, on 429/network errors, keeps showing the previous value marked `stale`. The response is whitelisted on the way in (`credits`/`model_usage` do not exist past the normaliser). `panels/usage.js` is provider-agnostic |
| MCP servers | The independent MCP · Skills popup provides Library, Tool settings and Current session views. Native CLI files remain the source of installed settings; FarShell stores portable snapshots and deployment tracking separately. Changes use review plans and source/target revision checks. Configuration, independent probe results and unknown live CLI state are displayed separately. |
| Prompt queue | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. Automatic feeding is triggered by a server-side `done` transition from either Claude Code or Codex lifecycle hooks (`POST /api/agent/event`). aider/agy still need `fsh queue run` / "run now" unless they self-report. Feeding based on guessed output idleness is not used. Gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 1 sec) · no recent human input in that session (`VT_HUMAN_INPUT_IDLE_SEC`, default 3 sec; automatic drains only — see `server/pane_input.py`) · safe_mode · target not `waiting` · target pane alive · one item at a time · (with `VT_AUTHZ_ENFORCE` `deleg`/`msg`, items that came from a delegation or an agent message are re-checked by the engine inside the pane lock right before the first key; a revoked grant or closed delegation leaves them `blocked` with reason `grant_revoked`/`delegation_closed`/`relationship_gone` — `docs/ref/auth.md` «Re-check right before typing»). Blocked or failed items are **not discarded** — they stay in the queue as `blocked`.

Item states: `pending` → `sending` → removed on success, or `blocked`. A `sending` item stays in place and **cannot be deleted or cleared** (409 `item_sending`). Transient reasons (waiting, human typing, missing pane, tmux/remote failure) are retried: the sweep (`VT_QUEUE_SWEEP_SEC`, default 60 sec) returns them to `pending` after 5·10·20·40 sec (cap 300), up to 5 attempts, then sends a Web Push and stops. A retried item with an explicit local target is drained right away only if that session is known `idle`/`done`; otherwise the next `done` drains it. safe_mode blocks are never retried. A `sending` item older than 30 sec, or one left over from a server restart, becomes `blocked` without retry — it may already have been typed. Every transition is appended to `~/.vt/queue_transitions.jsonl` (rotated at 1 MB; `fsh queue log`).

Text injection: every automatic path (queue, targeted voice, `/api/peer/input`) goes through `tmux_target.send_to_tmux`, which loads a uniquely named tmux buffer and runs `paste-buffer -p -r -d -t <pane id>`, waits `VT_PASTE_ENTER_GAP_MS` (default 200), then sends Enter separately. Multi-line text arrives as one bracketed paste instead of being submitted line by line. All automatic writers, including `send_to_session` and `type_to_tmux`, hold a per-pane lock so one writer's Enter cannot land inside another's text. `send_to_session` also returns 409 `target_human_typing` while a person is typing in that session. Untargeted voice input still writes to the attached PTY directly. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock. A remote item uses `POST /api/peer/input` and needs `control` level on the other side. |
| Prompt snippets | The dock's **Snippets** tab (also the left rail button and `Mod+K` → "Prompt snippets"; on compact it opens as a sheet). The iTerm2 Snippets idea: save a frequently used instruction or command block and fire it into a pane. **Distinct from the queue** — the queue is a waiting line ("run this when the agent is free"), a snippet has no waiting concept at all, it goes in immediately. So `snippet_store.py` is pure CRUD with no status/target/drain state machine. **The run method is chosen when you run it, not when you save it**: each row has "Type into this session" and "New session". **The snippet's own text decides the last Enter** — a line break is an Enter keypress, and a single trailing `\n` means "press Enter after the last line" (`snippet_store.normalize_text` / `frontend/js/snippets-util.js`; every line break, and the final one, is drawn as a `↵` marker in the live preview under the editor). The editor's 「전송 방식」 choice (끝에 Enter / 입력만) sets or removes that trailing newline; typing a trailing line break selects 끝에 Enter. "Type into this session" sends the body as one paste (`sendPaste`, server-side marker handling) and then one Enter if the text ends with a newline; "New session" splits the active pane to the right, creates a tmux session in the same cwd and types the text line by line. The new session name is typed in an inline field (blank = the snippet's name, then the cwd folder name); a taken name becomes `name-1`, `name-2` (`POST /api/tmux/create` `unique_name`, numbered under a lock). Names are reduced to `[A-Za-z0-9_-]` because other endpoints reject anything else; the UI toasts when the name changed. Stored at `~/.vt/snippets.json` (0600 + flock, same rules as the queue), capped at 100 items / 8000 chars. Search, edit (name/text/scope/send mode, a sub-screen of the panel) and delete (a confirm dialog) are in the panel. **Web UI only — there is no `fsh snippet` subcommand** |
| Detection coverage trust | The table in Settings → "Agents". `trust: high` means a **first-party signal** exists — a Claude Code or Codex hook (`hook`) or `fsh pane report --agent <name>` self-reporting (`report`). A CLI without a hook stays at mid however many patterns it has. Self-reporting is recorded in `~/.vt/agent-report-seen.json` — the same kind of durable fact as "a hook is installed" (status entries expire on a TTL, which would make the grade flicker) |
| Stale-server warning | `fsh doctor` and `fsh status` compare the newest mtime under `server/` (`.py` files) against the server process start time and tell you to restart when the code is newer. Python loads modules at import time, so without a restart a fix is **not** live at all — a stale server is easily mistaken for a code bug |

The MCP popup navigates by tool first: All, Claude, Codex, agy and OpenCode. A
server name shared across tools appears as a separate row in each tool. The last
selected tool is saved per browser and restored when the popup opens. The server
and plugin tabs keep the same tool selection. Tags remain name-based in the
existing catalog, but a group on/off request carries the selected tool so it
cannot change another tool's configuration. Copy targets are checked against
the complete scan, including other tools. The All view is for comparison;
group writes are available within a selected tool.

OpenCode MCP definitions use its native `type: local` with an array `command` and
`environment`, or `type: remote` with `url`. FarShell scans and prepares probes from
those fields, and reports malformed entries as scan errors. The portable library
accepts local definitions only when no OpenCode-only fields would be lost; its
OpenCode deployment renderer writes the native local form. Remote OpenCode library
imports remain unavailable because the library cannot retain the distinction
between omitted OAuth and `oauth: false`. Direct remote deployment therefore
requires explicit `oauth: false`. OpenCode's own OAuth credentials stay in OpenCode.

The popup has Library, Tool settings, and Current session views. The library
shows only portable, secret-free MCP definitions saved in the FarShell catalog,
grouped as common or tool-specific by their configured targets. Installed direct
skills for Claude and Codex appear in Tool settings; skills supplied by a plugin
are nested under that plugin instead of appearing as independent installations.
An empty FarShell library offers a reviewable import of existing CLI MCP definitions;
only portable definitions are selected, and definitions with literal values stay in
their original tool settings for manual review. Import does not change those settings.
Import uses a server-generated
preview bound to the destination file; a changed file or existing name stops the
write. Codex's `[[skills.config]]` can disable a direct skill without deleting it.
Claude direct skills (including linked ones) toggle through `skillOverrides` by folder name:
the global context writes the profile's `settings.json` (enable removes the entry, disable
writes `"off"`), a project context writes `<project>/.claude/settings.local.json` (`"on"` or
`"off"`). Effective state is local > project `settings.json` > user, per key (verified
with `claude -p`). Rows are project-scoped like Claude MCP rows. A skill whose
frontmatter `name` differs from its folder, or an unreadable settings file, is not writable.
Plugin skills are not affected by `skillOverrides`.
The Current session view uses the agent reported for the focused tmux session and
labels its merged configuration as the **next start** state, since a running agent
may not have loaded later changes. Plugin-provided skills stay linked to the
plugin that supplies them.

### Waiting kind

A `waiting` entry carries `kind` = `approval | choice | plan | question` (`GET /api/agent/status`, `/ws-agent`; removed when waiting ends, and part of the prompt-id fingerprint). The frontend only reads it.

- **Priority: hook kind > screen kind > fallback.** Hook: `PermissionRequest` `tool_name` — `AskUserQuestion` -> `choice` (options from `tool_input.questions[0].options[].label`), `ExitPlanMode` -> `plan`, anything else (or no tool) -> `approval`; Codex `PreToolUse` `request_user_input` -> `choice` + waiting. Screen: `[kinds]` in `server/detect/<cli>.toml`, matched **only against the spec of the CLI the detector identified** (entry `agent` first, then the process scan; unknown CLI -> all specs). A waiting with no kind is `question`. The "{tool} 실행 승인이 필요합니다" text is for `approval` only.
- **`enter` hits are `approval`**; `[kinds]` literals are waiting triggers too. Order when several hit: plan, approval, `enter`, choice, question (the generic `Enter to select` footer must lose to a specific prompt).
- **Codex choice ignores `esc to interrupt`** (`ignore_exit_on = ["choice"]`): the question screen keeps the working status line on screen.
- **Codex (measured on 0.160.0 — evidence in `session-view-measurements.md` section 1):** declining an approval, ESC while working and ESC on the question overlay emit no hook and leave `■ Conversation interrupted - use /feedback ...`; `interrupt = ["Conversation interrupted"]` ends them as `done`. That line stays in scrollback and tmux partial redraws re-send it during the next turn, so `interrupt_stale_after` (a regex in `codex.toml`) ignores the phrase when a new `› ` input or `• ` answer line follows it. The Plan-mode `Stop` hook comes ~40-130 ms **before** the `Implement this plan?` overlay, so `on_waiting` lets a `plan` screen reopen a `done` entry for `PLAN_AFTER_DONE_SEC` (10 s); other kinds still cannot revive `done`. Approval keys are any one letter or `esc` (`(y)`, `(p)`, `(a)`); plan/question overlays have no `(key)` and use the digit shown. `_strip_ansi` turns cursor-forward (`ESC [ n C`) into blanks so overlay columns stay apart. Pressing `3` on a Codex approval menu chooses "No": the request is cancelled, the file is not created, and the screen shows `■ Conversation interrupted`.
- **agy** has no hooks; `exit` stays empty (`esc to cancel` is always on screen). `/artifact to review` is a `[badge]` (`badge: "plan"`), never waiting. agy has no end-of-turn signal, so the badge sits on a **working** entry (measured) and the rail/Fleet row shows 「계획 있음」 as a secondary mark next to 작업 중 (`stateKeyword` in `rail-data.ts` shows badges on working and done; the state itself is untouched).
- **`badge: "question"`**: Stop/done whose `last_assistant_message` ends with `?` (rhetorical questions and quotes make promoting it to waiting too risky — it would block the queue). Cleared on the next prompt/tool/session start.
- **Notification hook** (`permission_prompt|idle_prompt`, registered by `fsh hooks install`): recorded as `last_notification` only — never used to decide kind or status. Re-run `fsh hooks install` to register it; until then `fsh hooks status`/`doctor` show Notification as `add` and coverage reports Claude as `pty`, not `hook`.
- **`PermissionRequest` (registered by `fsh hooks install`):** fires just before Claude Code shows a permission dialog; the script exits with no output, so it never allows or denies anything and the normal dialog appears. It gives the server the exact `tool_name` (kind) at once instead of waiting for the screen. There is no "approved/denied" event, so clearing is still the next pre/post/stop or the screen's exit pattern. Existing installs must re-run `fsh hooks install`; until then Claude's kinds come from screen patterns (`kindSources` reports that honestly).

### Session owner and waiting release

Code: `server/agent_status.py` (module note «세션 소유자»).

- **One owner per tmux session.** A tmux session's state is the state of the one agent session (hook `session_id`) that owns it — not the most urgent of every entry carrying that name. Without a single owner, `/clear`/`--resume`/restart would leave the previous session's entry behind for 30-60 minutes, and because `done` outranks `working` a working session would show as done. The owner is set by SessionStart (`startup`/`resume`/`clear`), or by the first event when there is no owner. The previous owner is **retired**: its entry is deleted and its late hooks are ignored for an hour (`resume` brings a retired conversation back).
- **Non-owner events are ignored** (`/api/agent/event` returns `state: null`, `resolved_by: "ignored"`; no queue drain, delegation event or completion voice). This covers child processes that inherit the pane's `TMUX_PANE` (`claude -p` from a Bash tool or an MCP server). A `startup` from another session while the owner is `working`/`waiting` does not take over when the owner's process identity is unknown (`verified` is none); a hook whose process is confirmed takes over at once — an interactive replacement comes as `clear`/`resume`, or as `startup` after SessionEnd released the pane.
- **SessionEnd releases.** It deletes the entry instead of marking it `done`, so `/clear` does not produce a 30-minute 「완료」 badge plus a queue drain. Clients receive an `agent_event` whose state has `released: true` and drop that entry.
- **Expiry checks the screen first** (`agent_status.sweep`, `set_screen_probe` ← `deps._screen_probe`, `agent_prompt_detect.screen_state`). The TTLs assume a hook was lost (`waiting` 2 min → `working`, `working` 15 min → `idle`, `idle` +30 min → deleted), which would turn a question a person simply has not answered yet into `idle`. Right before expiring, the server reads that session's visible tmux screen (last 14 lines only, so a quoted prompt phrase higher up cannot fool it; `tmux capture-pane`, 1 s timeout): a `waiting` whose screen still shows the prompt stays `waiting`; a `working` whose screen still shows `esc to interrupt` stays `working`; a `working` whose screen shows a prompt becomes `waiting` (self-heal for a lost PreToolUse). If the screen cannot be read the plain TTL behavior applies, and a `waiting` is capped at 24 h. The agents loop also runs `recover_from_screens` (hook CLIs only, per session at most every 30 s): a session the server has no entry for (the state is memory-only, so a restart empties it) whose screen shows a prompt is revived as a screen-made `waiting` placeholder (`pane:<name>`) that the next real hook takes over. The Claude `choice` literals in `detect/claude.toml` include `Enter to select · ↑/↓ to navigate` (the footer of the live `AskUserQuestion` screen on Claude Code 2.1.318) and `Enter to select · Tab/Arrow keys to navigate`.
- **Acknowledging a `done`** (clicking the session) sets it to `idle` locally and calls `POST /api/agent/ack` (`agent_status.ack_session`, browser session only). The server keeps `done` for 30 minutes otherwise, and the next snapshot would turn the local `idle` back into `done` + an unseen mark.
- Self-report (`fsh pane report`) and screen-detection placeholder entries (`pane:<name>`) are used only for sessions with no hook owner; a hook claiming the session drops the placeholder. `GET /api/agent/status` `all` and the `/ws-agent` snapshots leave out non-owner entries of owned sessions (`agent_status.visible_state`).
- **Waiting is sticky.** Claude `AskUserQuestion`/`ExitPlanMode` raise `waiting` at PreToolUse (measured on Claude Code 2.1.318: a default-mode Bash approval sends PreToolUse → PermissionRequest → Notification while it waits, then PostToolUse after the answer and Stop; bypass mode sends no PermissionRequest and no Notification). The entry remembers which tool call (`tool_use_id`; PermissionRequest inherits the preceding pre's) and which agent (`agent_id`, a subagent) made it wait. Only a pre/post from the same agent and the same call releases it, or the next prompt, Stop, human input, the screen's exit pattern or the TTL. Without ids (CLIs that do not send them, screen-made waits) the main agent's next pre/post releases it. Subagent pre/post never change the main state, so a background subagent cannot revive a finished session; a subagent's PermissionRequest still raises `waiting`.
- **Resume keeps recorded permissions, including OFF.** Claude owner hooks store profile-root/native-ID-scoped modes with event timestamps (`session_catalog/permission_modes.py`, `scoped_permission_modes`). Explicit `default`, `acceptEdits`, `plan` and `bypassPermissions` override later global defaults; unscoped records alone are insufficient. Codex reads the last native `turn_context` from the original source, restoring approval policy, sandbox details and reviewer (`session_catalog/execution_policy.py`). Missing, ambiguous, corrupt or unsupported policy refuses launch; the CLI's unrecorded changes cannot be inferred. See [terminal](terminal.md#durable-catalog-and-conversation-archive) for limits. Separate from delegation, where a child never inherits its parent's bypass.
- **Who sent the hook.** `agent_hook.sh` sends its own pid (`hook_pid`) and the time it received the event (`ts`, ns). While the request is open, `server/hook_identity.py` reads the process table once: skipping shells upward from the hook gives the agent process; if the pane's `pane_pid` is not among its ancestors the pane claim is `foreign` (dropped — a daemon or another terminal); if `~/.claude/sessions/<pid>.json` is a terminal interactive session (`entrypoint: "cli"`) with the hook's `session_id` it is `ok`; otherwise it is `ok` only when the agent leads (or sits under shells that lead) the tty's foreground process group, else `child`. Measured shapes: the interactive Claude is the pane shell's child and leads the foreground group; Bash-tool commands and hooks run in their own tty-less groups; MCP servers inherit Claude's group; `claude -p` runs are recorded in transcripts with `entrypoint: "sdk-cli"` (seen on Claude Code 2.1.312 on this Mac; the session files written by interactive runs carry `kind: "interactive"` and `entrypoint: "cli"`). A session file alone would let a Bash-tool `claude -p` take the pane over — hence the entrypoint check). `ok` takes ownership at once, even from a busy owner left behind by a crash; `child` can never take an owned session; `unknown` (old hooks, `ps` failure) keeps the status-based rule. The response carries `identity`. A hook from a background Claude session (`CLAUDE_JOB_DIR`) is not sent at all, and an event older than the newest one applied to that entry (`ts`) is dropped.
- **Claude hooks without a pane are not attributed by cwd.** A Claude hook runs under the pane's shell, so no `TMUX_PANE` means a Claude outside tmux (desktop app, IDE, another terminal); attributing it by cwd would land its state on an unrelated FarShell session sharing that cwd. `fsh pane report` keeps the cwd fallback.
- **Tmux changes.** The tmux hook (`/api/tmux/kick`, also fired on `client-session-changed`) runs `deps.on_tmux_change`: entries learn the tmux session id (`$N`) and follow a rename; entries of a closed session are retired, so a new session reusing the name does not inherit them (`agent_status.reconcile_sessions`). A web PTY whose tmux client moved to another session (`detach-on-destroy off`, `switch-client`, `prefix s`) has its screen detection, interrupt phrases, human-input release and inline-approval tokens attributed to the session it now shows (`deps.tmux_for_pty`), and its detection window is cleared; the frontend's tab/PTY pairing (`session_store`) is left alone.
- **Claude interrupt guard.** `detect/claude.toml` `interrupt_stale_after = '^\s*⏺'`: an `Interrupted · What should Claude do instead?` line followed by an answer/tool line is a redraw of a past turn, not a new interrupt.
- **Codex title.** Codex prefixes the terminal title with `[ ! ] Action Required` (blinking with `[ . ]`) whenever it needs an answer (observed on Codex 0.164.0: the pane title alternates `[ . ]` and `[ ! ] Action Required` while an approval waits). Every agent-name check (5 s, only while a browser is connected) reads the pane titles: a working Codex owner with that title becomes `waiting` (kind `question`), and a wait during which the title was seen ends when it disappears — `working` if the title shows a spinner, otherwise `done` (a decline or ESC sends no hook). A wait whose title was never seen is not released by the title, so a Codex with titles turned off keeps the hook rules.
- **Immediate push.** Transitions outside a hook request — screen detection, interrupt phrases, input release, TTL, retirement — go out at once as `agent_event` with `event` = `screen`/`interrupt`/`ttl`/`ack`/`release`/`rename`/`title` and `resolved_by: "server"` (`agent_status.subscribe`). The 15-second `agent_status_sync` stays as the safety net. The frontend (`agent/state.js`) recomputes a session from all its entries on every event, the same rule as the snapshot, instead of overwriting it with the one entry the event carried.

### Measurements: release paths, reject/ESC, per-CLI results

The release-path table, the terminal-reply root cause (DA1/DA2/XTVERSION/OSC 10/11), the reject/ESC findings and the per-CLI measurement table moved to [`session-view-measurements.md`](./session-view-measurements.md) (that file is canonical; this one keeps the rules). Rules in one line each: input made only of terminal protocol replies (`server/terminal_reports.py`) never releases `waiting`; an `interrupt` screen phrase (`detect/<cli>.toml`) ends `waiting`/`working` as `done`; detection runs on ANSI-stripped text.

### Claude Code and Codex hooks

`server/agent_hook.sh` is the shared entry point (it posts over the unix socket when one exists, else with the machine token; it never fails or blocks the agent either way). Claude Code posts its tool, permission, prompt, stop, subagent and session events;
Codex posts prompt, permission, tool, stop, and session lifecycle events while adding
`agent: "codex"`. Both drive status badges and prompt-queue auto-feed. The `stop` event of either CLI reports completion to the server; the session's voice policy decides whether it is spoken. `tts_hook.sh` only forwards to ntfy, and only when `VT_NOTIFY_URL` is set.

**Codex session attribution:** its shared app-server can run hooks with
the `TMUX_PANE` it inherited from an earlier TUI, which would attach a Codex
entry to the wrong tmux session. Codex hook
events therefore ignore `pane` and use cwd only when it identifies exactly one tmux
session. Later events with the same Codex `session_id` keep that binding even when
they omit cwd. If several sessions share cwd, the event stays unattributed rather
than appearing on the wrong session; an unattributed stop also cannot trigger
automatic queue delivery. Claude hooks and direct `fsh pane report` still
use the verified pane first.
A Codex hook whose process is verified to run inside the pane (see
«Session owner and waiting release» — the TUI's own process tree, e.g. `codex --no-daemon`)
uses that pane like a Claude hook. Unverified Codex hooks still use cwd, but among the panes
in that cwd only the ones running `codex` are candidates, so one Codex session in a repository
with several shells is attributed.

Register both with `fsh hooks install` (idempotent, preserves other hooks, and backs
up the changed config files). `fsh hooks status` and `fsh doctor` report the Claude Code and Codex event sets separately.

**Writing other tools' config files (`server/config_io.py`).** Hook install/uninstall
and the MCP writers share one write path. It writes through symbolic links (the file at the end
of the link, so a dotfiles repo or a per-account config folder that links a shared file keeps
its link), keeps the file's mode, `fsync`s before the rename, names its lock after the real file
(a link path and the real path take the same lock), and re-checks the file's mtime/size just
before writing: if Claude Code or Codex changed it after we read it, nothing is written and
the command says to retry. The lock is advisory — Claude Code does not honor it — so the
re-check narrows the window, it does not close it. A file with other hard links is refused,
because a rename would split the names and an in-place write is not atomic. CRLF and BOM are
not preserved (native Windows is unsupported; a BOM file fails to parse and is refused, not
damaged).

**`~/.codex/hooks.json` duplicates:** the Codex desktop app's "import Claude
settings" copies Claude's FarShell hooks into `hooks.json` (`agent_hook.sh pre|post|
permission|stop|session_start claude`, plus untagged `session_end`/`prompt`). Codex reads
both `config.toml` and `hooks.json`, so every Codex event reaches the server twice, one of
them tagged `claude` — which mislabels the agent and bypasses the cwd attribution above
(it only applies to `agent == "codex"`). FarShell never writes `hooks.json`, so anything
`codex_hooks.find_foreign_farshell_hooks()` finds there is a foreign copy. `fsh hooks status`,
`fsh hooks install` (one line) and `fsh doctor` warn about it (a warning, not a failure);
`GET /api/hooks/status` carries `tools.codex.duplicates` (`count`, `events`). The fix is
`fsh hooks dedupe [--dry-run]`: removes only `agent_hook.sh` entries, keeps other hooks,
prunes emptied groups/events, writes `hooks.json.vtbak` once and the file atomically with
its indentation preserved; idempotent. A broken `hooks.json` is left untouched.
**Register `agent_hook.sh stop` only, never `tts_hook.sh` directly** — `agent_hook.sh` already calls it for `stop`, so registering both sends each ntfy notification twice.

- Speech: `server/voice_sessions.py` applies session policy, output ownership and serial playback; `voice_completion.py` extracts Claude/Codex final responses.
- Hooks stay installed for status/queue regardless of whether session TTS is enabled.
- No automatic server-side speech when a browser disconnects or the server is unavailable.
- Configure current-session voice in Settings; see [voice.md](voice.md).


```bash
# Check ntfy delivery (the script does nothing unless VT_NOTIFY_URL is set)
echo '{"transcript_path":"/tmp/test_transcript.jsonl"}' | VT_NOTIFY_URL=<url> ./server/tts_hook.sh claude
```

### Hook aggregation (current behavior, no merge step)

Each matched hook group is a separate subprocess started by Claude Code/Codex; a hook the user
registered for the same event runs alongside ours and its decision handling stays inside the CLI.
`POST /api/agent/event` only hears from `agent_hook.sh`. The installers (`claude_hooks.apply_install`,
`codex_hooks.apply_install`) keep user hooks and leave exactly one FarShell registration per event,
so repeated `fsh hooks install` never stacks copies. There is no merge step: `agent_status.on_event()`
writes the in-memory entry directly and a repeated call overwrites the previous fields (last-write-wins).
`SessionStart` matches `"startup|resume|clear"` (not `compact`, which the server also ignores);
`PreToolUse`/`PostToolUse` use matcher `"*"`, so a user's tool-specific hook fires alongside ours.

## Agent-to-agent messages

One agent sends a message to another agent's tmux session, on this Mac or a paired one.  The pieces are an MCP server, peer delivery, a store and a CLI; nothing in `frontend/js` calls the message endpoints.

- **Delivery is a queue item** (`server/agent_msg.py` → `queue_store.add(..., source={"agentMsg": id})`). Typing into a pane at an arbitrary moment either lands mid-task or gets **consumed as an approval answer**; the queue already refuses both (stop hook only, never into `waiting`, safe_mode, one at a time). One addition: if the target is **known** idle/done (`agent_status.known_status_for_session` — unlike `status_for_session` it returns None for "no entry"), it is drained after the grace period, because no stop hook will come. Unknown state is never guessed; it stays queued for `fsh queue run`. `queue_runner.drain_once` reports back so the message becomes `delivered`/`blocked`.
- **Sender identity is resolved, not claimed.** `/api/agent/message` runs the hook's 3-step `pane_resolve` on the caller's `$TMUX_PANE`; the remote side takes the sender host from the signed grant. The reply address in the header comes from there.
- **With `VT_AUTHZ_ENFORCE=msg` the sender is the authenticated caller** (socket peer pid / verified pane), body claims are ignored and an unverifiable caller gets 403 `identity_unverified`. Messages between a delegation parent and child, or between siblings (open delegations), go through; any other pair needs a person's approval first — the tool answers "waiting for user approval, repeat the call once approved" (HTTP 202) and the request appears in the browser's approval inbox. `fsh msg send` and the MCP `send_message` behave the same. Details: `docs/ref/auth.md` «Agent messages under the engine». The permission behind it is the scope `msg.send` (the vocabulary is 17 scopes plus conditions, see `docs/ref/auth.md` «Permission vocabulary»); reading one's own sent/received history needs no scope.
- **Receiving from another Mac needs `control` plus `fsh host allow-agent <id>`** (`grant.agentMessages`). Making another Mac's agent do work is remote execution; `control` alone must not open it silently. The body is covered by the signature hash.
- **Ping-pong guard**: hop limit 10 per conversation, counted from the receiver's own records as well as the sender's claim (the larger wins); 20/min per sending host.
- **The header** tells the receiving agent the text came from another agent, with `reply_to`/`to` for the answer. The body is flattened to one line — the queue submits with Enter, and a multi-line body would be split into several prompts.
- **`fsh mcp`** (`server/mcp_farshell.py`) is a dependency-free stdio JSON-RPC server. Message tools: `list_agents`, `send_message`, `read_messages`. Session-sharing tools: `list_shared_sessions`, `read_session`, `send_to_session`. Delegation tools are described below; `whoami`, `request_access`, `list_my_grants`, `lease_*`, `pair_*` and `browser_*` are offered only when the matching feature is enabled. It only calls the local FarShell API — over the unix socket first (`~/.vt/run/fsh.sock` on the default port, `fsh-<port>.sock` otherwise; the server identifies the caller by peer pid; see `docs/ref/auth.md` «Unix socket»), falling back to the machine token (`~/.vt.env`, else env) when there is no socket — and, for the sharing tools, also sends `TMUX_PANE`/`TMUX`/its pid as `X-Fsh-*` headers so the server can verify which tmux pane it runs in. `fsh msg` exposes the message tools for shells and agents without MCP (the sharing tools are MCP-only).
- Automatic delivery works for agents with lifecycle hooks (Claude Code, Codex). Others (aider, agy, …) receive the message but it is fed manually.
- **Text and Enter are separate calls.** `tmux_target.send_to_tmux` pastes the text (`load-buffer` then `paste-buffer -p`, bracketed paste) and presses Enter with its own `send-keys` call. Sent in one burst, Claude Code reads the input as a paste and takes the Enter as a newline, leaving the message unsubmitted in the input box. This applies to the plain prompt queue, voice input and `/api/peer/input` with `enter` as well. `type_to_tmux` types with `send-keys -l`, so a text that equals a key name (`Enter`, `Up`) is not pressed as that key.
- **Replies are marked as replies.** A reply carries a different header ("답장 … 다시 답할 필요가 없습니다"); only requests tell the agent how to answer, so a reply is not answered again. The hop limit is the backstop.
- **A fresh agent receives queued messages.** `SessionStart` (startup/resume/clear) marks the session idle and drains **only items that name that session** (`targeted_only`) — untargeted queue items belong to whichever agent stops first. This needs the `SessionStart` hook: run `fsh hooks install` (or `install.sh`) to register it.

## Agent delegation (MCP)

An agent hands work to a **sub-session** it creates and gets the result back as a queue item. Code: `server/delegation.py`, `server/routes/delegation.py`; endpoints: `API.md` «Agent delegation».

- **Tools** (`fsh mcp`): `spawn_session {task, title?, agent?, cwd?, worktree?:{branch}}`, `send_to_child {delegation, text}`, `close_delegation {delegation}`, `report_back {outcome, summary}`, `list_delegations`. `tools/list` asks the server for the caller's role: a sub-session never sees the spawn/send/close tools, a non-child never sees `report_back`.
- **Ownership lives in `~/.vt/delegations.json` only** (0600, flock). Each record pins parent and child by `#{session_id}:#{session_created}` (the same key `share_identity.verify_caller` returns), so a renamed session keeps its delegation and a re-created session with the same name does not inherit it. The tmux options `@fsh_parent` / `@fsh_delegation` are display hints — an agent could rewrite them, so no check reads them.
- **Permissions under the engine** are two scopes with conditions: `deleg.spawn` (the spawn quota below) and `deleg.control` with `role=parent` (send, close) or `role=child` (report). The ledger decides who is parent/child; the engine's grants are derived from it. The CLI/service callers are the `local` principal and may only close.
- **Limits** (all enforced in `check_spawn`, inside the store lock): depth 1 (a session that was ever a child cannot spawn), 3 open (running/reported) children per parent, 5 spawns/min server-wide, `VT_AGENT_SPAWN=0` turns the feature off. Refusals tell the model not to retry.
- **Limits on a child.** Its grants are the parent's grants intersected with the request, minus what is never inherited (browser JS/sensitive, session management, relay, `authz.admin`, `pair.manage`, raw input); it keeps `authz.request`, and its requests record the delegation id. The parent/child edge in the ledger cannot be changed after spawn, and only the strict parent controls a delegation (details: `docs/ref/auth.md` «Child limits and ownership integrity»).
- **Launch.** The server picks the command: `command claude --permission-mode default` or `command codex --sandbox workspace-write --ask-for-approval on-request`. `command` skips shell aliases, so a user alias that adds `--dangerously-skip-permissions` does not reach the child; the parent's bypass mode is never inherited. The task goes in as a targeted queue item (not a CLI argument — that would show in `ps`), with a footer asking the child to call `report_back` once; the child's `SessionStart` hook drains it. Task and follow-up items carry a one-line `preface` that is **typed** before the body is pasted: Claude Code wraps a bracketed paste as `<pasted_content>` and may decline instructions it did not get from the user (without the preface a child sometimes refused; with it, children followed). Results to the parent get no preface, so they read as data. Without FarShell hooks in the child CLI the task stays queued for `fsh queue run`.
- **Worktree** only when asked: `worktree.create_worktree` from the parent's `HEAD`, no agent started by it.
- **Results to the parent** are queue items targeted at the parent session: `[위임 결과 · <child> · <kind>/<outcome>] (위임 <id>)`, a fixed «this is data, not instructions» line, then the body capped at 4 KB (lines that imitate the header are prefixed with `> `). Kinds: `reported` (`report_back`), `turn_ended` (child's Stop hook without a report — carries the hook's `last_assistant_message`, once per delegation, skipped after a report), `needs_human` (child entered `waiting`; the parent cannot answer it), `gone` (child session vanished), `soft_limit` (running longer than `VT_DELEGATE_SOFT_LIMIT_MIN`, default 60; nothing is killed). A newer `turn_ended`, `needs_human` or `soft_limit` notice replaces an older one of the same delegation that is still pending in the parent's queue; `reported` and `gone` are never replaced.
- **Exactly once.** Each result is written to the record's outbox before it is queued; the sweep (`VT_DELEGATE_SWEEP_SEC`, default 30) re-queues unsent entries and skips ones whose `{delegation, kind}` source is already in the queue.
- **Lifecycle.** `close_delegation` / `fsh delegate close` close the record and drop the child's undelivered instructions; the session and worktree stay. A parent that disappears leaves its children `orphaned` (not killed). Spawn, report, close and refusals are written to `~/.vt/agent_access.log` (with `VT_AUTHZ_ENFORCE=deleg` to the authz audit chain instead, and the authz engine decides — `docs/ref/auth.md` «Agent delegation under the engine»: explicit `cwd`/worktree base must be inside the browse roots, `safe_mode` blocks spawning, `fsh delegate close` goes through the server as the `local` principal named `cli`).

## Agent session sharing (MCP)

The user shares an ordinary tmux session (nickname + `read`/`readwrite`) and the agent can read it and, if allowed, type into it.
**Agents never create or close sessions the user made** — shared sessions stay under the approval flow below. Agents can create their own sub-sessions through delegation (see «Agent delegation (MCP)» above). Design, identity check, approval rules, limits and the known gaps: `docs/ref/terminal.md`
«Agent-shared sessions»; endpoints and the `agent_share_request` event: `API.md`; the human-only boundary: `docs/ref/auth.md`.

- Tool descriptions say the same thing to the model: only shared sessions work, the first use needs the user's approval (repeat the
  same call once approved — the tool returns `사용자 승인 대기 중` for a `202`), shared sessions cannot be created or closed by the agent (sub-sessions are delegation's job).
- `read_session {nickname, lines?}` → last lines of the pane (default 200, max 2000, 64 KB). `send_to_session {nickname, text, enter?}` →
  one line, literally; `enter:true` presses Enter. No newlines/control characters (several commands = several calls); refused while an
  agent CLI in the target is `waiting` or `working`.
- `list_shared_sessions` shows each session's nick, mode, state and **my** read/write status (`granted`/`pending`/`denied`/`none`/
  `unavailable`); it never shows session names or folders.
- The `agent_status` states are only known for sessions with hooks. A plain shell has no state, so sends to it are allowed.
- With `VT_AUTHZ_ENFORCE=peer` the five tools `read_session`, `send_to_session`, `list_shared_sessions`, `spawn_session` (needs `deleg` too) and `list_delegations` get an optional `host` argument (a host id): the call goes to that Mac's `/api/peer/v2/*` with this agent vouched for by a signed obo, and a person on that Mac approves (202 → «call again»). `tools/list` only shows `host` when `whoami` reports `features.remote`; with the flag off the tool definitions are unchanged. See `auth.md` «Other Macs, grant based».


- With `VT_AUTHZ_ENFORCE=browser` the MCP server also offers isolated-browser tools — `browser_list_adapters`, `browser_open`, `browser_navigate`, `browser_snapshot`, `browser_screenshot`, `browser_click`, `browser_type`, `browser_close`, `browser_tabs`, `browser_handoff`, and `browser_eval` only for an agent that holds `browser.js`. They appear only when `whoami` announces `features.browser` and the agent holds `browser`; elements are addressed by the `e1`… numbers from `browser_snapshot`. Rules and limits: `docs/ref/auth.md` «Browser broker» User-profile attach and downloads are not offered.

### MCP 갱신·출처 추적

- 각 설정 항목에 `source_id`, 파일 revision, 원문 hash, 소유자, 쓰기 가능 여부,
  설정 상태와 실효 우선순위를 제공한다. OpenCode의 같은 이름 정의는 필드 병합 참여
  출처로 표시한다. JSONC와 추가 디렉터리 정의는 읽기 전용이다.
- 보관함 가져오기·원본 갱신·배포·동기화는 서버에 보관한 300초 단회 계획을 검토한
  뒤 적용한다. 원문 명령·인자·URL·자격증명 값은 미리보기 응답에 포함하지 않는다.
- 새 배포는 기본 꺼짐이다. Claude는 전역 또는 비공유 프로젝트 설정(`~/.claude.json`)에 배포하고 공유 `.mcp.json`에는 쓰지 않는다. agy는 전역만 지원한다.
  의미를 보존할 수 없는 자격증명 참조·추가 필드는 복제를 거절한다.
- 원본 추적은 FarShell이 배포한 대상만 선택적으로 켤 수 있다. 조회 때 원본 변경을
  발견하지만 쓰기는 사용자의 미리보기 확인 이후에만 한다. 대상 직접 편집은
  `diverged`로 중단한다. 적용 직전 대상 락 안에서 원본과 대상 revision을 재검사한다.
  flock은 협조적 락이라 락을 지키지 않는 외부 작성자(Claude Code 자신 포함)는
  막지 못한다. 막는 대신 교체 직전에 mtime·크기를 다시 확인하고 교체 뒤 다시
  읽어 의도한 상태인지 검사하며, 확인에 실패하면 `unknown`으로 보고한다.
- 설정 켬/끔, 독립 검사, 실제 CLI 실행 상태는 별개다. 독립 HTTP 검사에서 401/403은
  인증 필요이며, initialize 응답 형식을 확인한다. 리다이렉트·과대 응답을 거절하고
  SSE 검사는 미지원으로 표시한다. 응답의 실제 실행 상태(`runtime_state`)는 항상 `unknown`이다.
- 플러그인 번들의 MCP 이름은 플러그인 아래 읽기 전용으로 표시한다. 인증·플러그인
  설치·스킬 설치의 양방향 동기화는 수행하지 않는다.


## Native subagents

Native CLI children appear below their parent in the expanded desktop rail; Fleet shows
a count chip. These are read-only records, separate from delegated tmux sessions. A row shows name, state and elapsed time only — the child's current tool is not drawn (it appears and vanishes with every call, so it flickered); the record still lists tool calls.

Records open in the **「작업 기록」 window** (`history/session-record.js`, redesign phase 6), which merges the session record and the
subagent record that used to be two windows. Left (460): the session — status, 이어서 시작 · 실행 종료 · 이름 변경 · 세션 보관 ·
삭제, linked conversations, delegation (parent / delegated children with their report state), internal subagents, run history.
Right: the selected internal subagent (polled transcript, `panels/subagent-viewer.js` `mountSubagentTranscript` +
`subagent-poll.js`) or delegated session (its latest linked conversation from the conversation archive, plus the report text when
opened from 「보고 보기」), filterable by kind (전체 / 지시 · 답변 / 도구만), with 새로고침. Every entry point opens the same window
and only preselects the right side: row menu 「작업 기록」 → the most recent subagent, else the most recent delegated child;
child row 「보고 보기」 → the parent's record with that child selected; 「기록 보기」 on a subagent row → that subagent; the internal
chip → the first working subagent. The separate 「서브에이전트 기록」 / 「에이전트 내부」 windows (`#vt-subagent`) are gone.
Catalog routes are direct-host only, so while viewing another host the left side shows the name and internal subagents only.
All record text (instructions, responses, tool calls/results, titles) is inserted as literal text.
Thinking/reasoning is excluded by the server. API details live in [API.md](../../API.md#native-subagent-transcripts).

`fsh hooks install` registers Claude SubagentStart/SubagentStop/StopFailure/TeammateIdle
and Codex SubagentStart/SubagentStop in addition to the existing hooks. Running CLI
sessions may need restarting and Codex may require hook trust review. Configuration
registration does not prove that an installed CLI version emits every event.

The server keeps at most 32 children per parent in memory. Done/failed rows expire one
hour after ending; idle rows expire one hour after becoming idle. Working rows with no
event for 15 minutes become stale idle, which does not establish completion. Codex Stop
means idle between turns. Completed rows are evicted before idle rows; working rows are
never evicted for a new child. If all 32 are working, the UI reports the display limit.
A server restart clears the roster; a later Start or child tool hook can rebuild it.

Parent ownership and child event ordering are checked independently. Child permission
hooks still set the parent's waiting state, and only matching child/tool hooks clear it.
Pure child lifecycle events cannot drain the parent's queue or trigger completion speech.
Clear/resume/startup and owner retirement discard children; compaction does not.

Transcript access uses a dedicated guard without widening file-browser roots. Claude
paths must match the current parent's child location; Codex session metadata must match
both child and parent IDs. Regular files are opened without following swapped symlink
components. The remote host performs its own checks through the `agents` relay group.
The viewer uses bounded incremental reads, file-generation cursors, cancellation and
final reads after completion. Unsupported transcript formats fail explicitly.

## Managed launch profiles

`agent_profiles.py` owns a private SQLite registry and persistent per-profile native
roots. `agent_profile_runtime.py` provides sanitized launch environments, interactive
native login, tmux/session attribution, test-only inference requests and observed
native assistant-turn receipts. Lifecycle operations preserve native roots; archived
profiles cannot launch. JSON/TOML hook disabling uses a private restoration vault.
Fixed GitHub plugin snapshots support same-marketplace dependencies and native
update/recovery. Official API quota adapters support DeepSeek, OpenRouter, Z.ai
and MiniMax international; Alibaba quota remains unsupported. Configuration,
conversation, hook and plugin adapters live in the `agent_profile_*` modules. Read and
apply routes are human-only; review plans bind the exact elevated cookie, source hashes
and profile revisions. Nested plans retain the profile ID for the operation lock.
Imports journal writes before rename and preserve backups. Rollback rechecks before/after
hashes; it refuses external edits. Profile selection never replaces global auth stores.

`usage/profiles.py` reads namespaced native OAuth credentials and whitelisted quota
responses without implementing OAuth refresh. API-key profiles do not fall back to
OAuth credentials. Native mode excludes the default Codex reader; `none` excludes all
usage sources. Arbitrary API endpoint quotas remain unsupported. Registry account
labels remain unverified logical grouping. The retirement report (`fsh profile retirement`) keeps
`same-account-concurrency-not-certified` as a blocker until concurrent use is observed: two live
sessions of profiles on one account with a fresh matching identity, or two or more sessions of one
token or API-key profile. See the paired profile help for current limits
and [API.md](../../API.md) for endpoints.

`scripts/profile_sandbox.py` starts a disposable macOS Seatbelt server with its own
HOME/state/run/config/workspace/tmux. Fake CLI mode has no external network; live mode
requires explicit provider-network access. Only standalone native executables are copied
into its bin directory. Production credential directories remain denied. The companion
HTTP/browser check refuses live-login sandboxes and records fixture results separately.
