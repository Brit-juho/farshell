# Agent state, queue, MCP

## Features

| Feature | Description |
|------|------|
| Agent state (2.0) | The server decides one of `idle`/`working`/`waiting`/`done` per session (the reserved `error` has no entry path and the frontend folds it to `idle`) and everything else just displays it; labels and the representative-state priority live in `frontend/js/design/state-classes.js` — worktree tabs, pane headers, the rail list (sorted so what needs you is on top), the favicon, and the app icon badge (`waiting` count). Claude Code and Codex lifecycle hooks are installed together by `fsh hooks install`; `fsh doctor` and Settings → About report each tool separately |
| Approval detection (`waiting`) | Detected off the PTY stream using patterns in `server/detect/*.toml`. Cleared by an exit pattern, by an `interrupt` screen phrase (a rejected approval or ESC — Claude Code emits **no** Stop hook for either), by you typing in that pane, by the next prompt/Stop, by a pre/post of the **same** tool call (see «Session owner and waiting release»), or by a 2-minute TTL. **The prompt queue will not feed a pane that is `waiting`** — `send-keys` there would be consumed as the approval answer |
| Detection coverage (2.1.1) | Settings → Agents → "Detection coverage" (`GET /api/agents/coverage`). Approval detection silently does nothing for a CLI with no patterns, and there was no way to see that — this table makes it visible per CLI: `path` (`hook`/`report`/`pty`/`none`), the toml's line count, the states it can express, and `trust` (`high`/`mid`/`low`), and (2026-10-03) `kinds`/`kindSources`/`badges` — which waiting kinds the CLI can tell apart and whether each comes from a `hook`, the `screen`, or the `fallback`. Claude Code reaches `hook`/`high` with its eight managed events (Pre/PostToolUse, Stop, SessionStart, Notification, PermissionRequest, UserPromptSubmit, SessionEnd — the last two since 2026-10-13, so older installs show `pty` until `fsh hooks install` is re-run); Codex does the same with six lifecycle events. Codex also retains real-terminal-verified PTY patterns as a fallback, including `y`/`p`/`esc` approval keys. aider/agy rely on PTY patterns or explicit `fsh pane report`. `enter`/`exit` are **literal byte strings** matched against a bounded 16KiB terminal-output window; `options` and `interrupt_stale_after` are regexes. Background and how to fix a pattern: `docs/help/agent-detect.md` |
| Usage gauge (2.0 · Codex 2.1.5) | Two sources, merged into one list. **clauth** reads `~/.clauth/status.json` (file, not CLI). Accepts feed schema **1 and 2** (supporting only one means the gauge silently dies on the next clauth upgrade); an unknown schema still disables it silently, but Settings → About now says so (2.1.3). Shown in the right rail on wide screens and in a panel elsewhere. **Disappears entirely when there's no source** — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls it. Tokens are excluded by a field whitelist **Codex (2.1.5)** is a second limit-type provider — it calls the same OAuth usage API the Codex CLI itself uses, reading the token from `~/.codex/auth.json` on every call and never storing it. Being a remote API it uses a 60s TTL cache and, on 429/network errors, keeps showing the previous value marked `stale`. The response is whitelisted on the way in (`credits`/`model_usage` stop existing at the normaliser). `panels/usage.js` was not touched — the profile card was already provider-agnostic |
| MCP servers | The independent MCP · Skills popup provides Library, Tool settings and Current session views. Native CLI files remain the source of installed settings; FarShell stores portable snapshots and deployment tracking separately. Changes use review plans and source/target revision checks. Configuration, independent probe results and unknown live CLI state are displayed separately. See the MCP sections below for adapters, credentials and synchronization limits. |
| Prompt queue (P4) | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. Automatic feeding is triggered by a server-side `done` transition from either Claude Code or Codex lifecycle hooks (`POST /api/agent/event`). aider/agy still need `fsh queue run` / "run now" unless they self-report. Feeding based on guessed output idleness is not used. Gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 1 sec) · no recent human input in that session (`VT_HUMAN_INPUT_IDLE_SEC`, default 3 sec; automatic drains only — see `server/pane_input.py`) · safe_mode · target not `waiting` · target pane alive · one item at a time. Blocked or failed items are **not discarded** — they stay in the queue as `blocked`.

Item states: `pending` → `sending` → removed on success, or `blocked`. A `sending` item stays in place and **cannot be deleted or cleared** (409 `item_sending`). Transient reasons (waiting, human typing, missing pane, tmux/remote failure) are retried: the sweep (`VT_QUEUE_SWEEP_SEC`, default 60 sec) returns them to `pending` after 5·10·20·40 sec (cap 300), up to 5 attempts, then sends a Web Push and stops. A retried item with an explicit local target is drained right away only if that session is known `idle`/`done`; otherwise the next `done` drains it. safe_mode blocks are never retried. A `sending` item older than 30 sec, or one left over from a server restart, becomes `blocked` without retry — it may already have been typed. Every transition is appended to `~/.vt/queue_transitions.jsonl` (rotated at 1 MB; `fsh queue log`).

Text injection: every automatic path (queue, targeted voice, `/api/peer/input`) goes through `tmux_target.send_to_tmux`, which loads a uniquely named tmux buffer and runs `paste-buffer -p -r -d -t <pane id>`, waits `VT_PASTE_ENTER_GAP_MS` (default 200), then sends Enter separately. Multi-line text arrives as one bracketed paste instead of being submitted line by line. All automatic writers, including `send_to_session` and `type_to_tmux`, hold a per-pane lock so one writer's Enter cannot land inside another's text. `send_to_session` also returns 409 `target_human_typing` while a person is typing in that session. Untargeted voice input still writes to the attached PTY directly. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock. A remote item uses `POST /api/peer/input` and needs `control` level on the other side. |
| Prompt snippets (L3) | Left rail → 📋, or `Mod+K` → "Prompt snippets". The iTerm2 Snippets idea: save a frequently used instruction or command block and fire it into the pane you're looking at right now. **Distinct from the queue** — the queue is a waiting line ("run this when the agent is free"), a snippet has no waiting concept at all, it goes in immediately. So `snippet_store.py` is pure CRUD with no status/target/drain state machine. A multi-line snippet gets a trailing `\n` on every line, so lines execute sequentially. Stored at `~/.vt/snippets.json` (0600 + flock, same rules as the queue), capped at 100 items / 8000 chars. **Web UI only — there is no `fsh snippet` subcommand** |
| Detection coverage trust (N9) | The table in Settings → "Agents". `trust: high` means a **first-party signal** exists — a Claude Code hook (`hook`) or `fsh pane report --agent <name>` self-reporting (`report`). Through 2.1.3 only `hook` was high and that value was only ever set for claude, so other CLIs stayed at mid no matter how many patterns you added. Self-reporting is recorded in `~/.vt/agent-report-seen.json` — the same kind of durable fact as "a hook is installed" (status entries expire on a TTL, which would make the grade flicker) |
| Stale-server warning (N28) | `fsh doctor` and `fsh status` compare the newest mtime under `server/*.py` against the server process start time and tell you to restart when the code is newer. Python loads modules at import time, so without a restart a fix is **not** live at all — the preventive measure for a real incident where paste truncation was chased as a code bug for hours |

The MCP popup now navigates by tool first: All, Claude, Codex, agy and OpenCode. A
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

The popup now has Library, Tool settings, and Current session views. The library
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
The Current session view uses the agent reported for the focused tmux session and
labels its merged configuration as the **next start** state, since a running agent
may not have loaded later changes. Plugin-provided skills stay linked to the
plugin that supplies them.

### Waiting kind (2026-10-03)

A `waiting` entry carries `kind` = `approval | choice | plan | question` (`GET /api/agent/status`, `/ws-agent`; removed when waiting ends, and part of the prompt-id fingerprint). The frontend only reads it.

- **Priority: hook kind > screen kind > fallback.** Hook: `PermissionRequest` `tool_name` — `AskUserQuestion` -> `choice` (options from `tool_input.questions[0].options[].label`), `ExitPlanMode` -> `plan`, anything else (or no tool) -> `approval`; Codex `PreToolUse` `request_user_input` -> `choice` + waiting. Screen: `[kinds]` in `server/detect/<cli>.toml`, matched **only against the spec of the CLI the detector identified** (entry `agent` first, then the process scan; unknown CLI -> all specs, as before). A waiting with no kind is `question`. The "{tool} 실행 승인이 필요합니다" text is for `approval` only.
- **`enter` hits are `approval`**; `[kinds]` literals are waiting triggers too. Order when several hit: plan, approval, `enter`, choice, question (the generic `Enter to select` footer must lose to a specific prompt).
- **Codex choice ignores `esc to interrupt`** (`ignore_exit_on = ["choice"]`): the question screen keeps the working status line on screen.
- **Codex (measured on 0.160.0, 2026-10-06 — evidence in `session-view-measurements.md` section 1):** declining an approval, ESC while working and ESC on the question overlay emit no hook and leave `■ Conversation interrupted - use /feedback ...`; `interrupt = ["Conversation interrupted"]` ends them as `done`. That line stays in scrollback and tmux partial redraws re-send it during the next turn, so `interrupt_stale_after` (a regex in `codex.toml`) ignores the phrase when a new `› ` input or `• ` answer line follows it. The Plan-mode `Stop` hook comes ~40-130 ms **before** the `Implement this plan?` overlay, so `on_waiting` lets a `plan` screen reopen a `done` entry for `PLAN_AFTER_DONE_SEC` (10 s); other kinds still cannot revive `done`. Approval keys are any one letter or `esc` (`(y)`, `(p)`, `(a)`); plan/question overlays have no `(key)` and use the digit shown. `_strip_ansi` turns cursor-forward (`ESC [ n C`) into blanks so overlay columns stay apart. Answering the plan overlay with `3` (stay in Plan mode) starts no turn, so the entry stays `working` until the next hook or the TTL (open).
- **agy** has no hooks; `exit` stays empty (`esc to cancel` is always on screen). `/artifact to review` is a `[badge]` (`badge: "plan"`), never waiting. agy has no end-of-turn signal, so the badge sits on a **working** entry (measured 2026-10-06) and the rail/Fleet row shows 「계획 있음」 as a secondary mark next to 작업 중 (`stateKeyword` in `rail-data.ts` shows badges on working and done; the state itself is untouched).
- **`badge: "question"`**: Stop/done whose `last_assistant_message` ends with `?` (rhetorical questions and quotes make promoting it to waiting too risky — it would block the queue). Cleared on the next prompt/tool/session start.
- **Notification hook** (`permission_prompt|idle_prompt`, registered by `fsh hooks install`): recorded as `last_notification` only — never used to decide kind or status. Re-run `fsh hooks install` to register it; until then `fsh hooks status`/`doctor` show Notification as `add` and coverage reports Claude as `pty`, not `hook`.
- **`PermissionRequest` (registered by `fsh hooks install`, 2026-10-04):** fires just before Claude Code shows a permission dialog; the script exits with no output, so it never allows or denies anything and the normal dialog appears. It gives the server the exact `tool_name` (kind) at once instead of waiting for the screen. There is no "approved/denied" event, so clearing is still the next pre/post/stop or the screen's exit pattern. Existing installs must re-run `fsh hooks install`; until then Claude's kinds come from screen patterns (`kindSources` reports that honestly).

### Session owner and waiting release (2026-10-13)

Design and evidence: local plan `docs/plan-2.4/60-agent-status-attribution.md`. Code: `server/agent_status.py` (module note «세션 소유자»).

- **One owner per tmux session.** A tmux session's state is the state of the one agent session (hook `session_id`) that owns it — not the most urgent of every entry carrying that name. Before this, `/clear`/`--resume`/restart left the previous session's entry behind for 30-60 minutes, and because `done` outranks `working` the live server showed a working session as done (three entries on one session). The owner is set by SessionStart (`startup`/`resume`/`clear`), or by the first event when there is no owner. The previous owner is **retired**: its entry is deleted and its late hooks are ignored for an hour (`resume` brings a retired conversation back).
- **Non-owner events are ignored** (`/api/agent/event` returns `state: null`, `resolved_by: "ignored"`; no queue drain, delegation event or completion voice). This covers child processes that inherit the pane's `TMUX_PANE` (`claude -p` from a Bash tool or an MCP server). A `startup` from another session while the owner is `working`/`waiting` does not take over — an interactive replacement comes as `clear`/`resume`, or as `startup` after SessionEnd released the pane.
- **SessionEnd releases.** It deletes the entry instead of marking it `done`, so `/clear` no longer produces a 30-minute 「완료」 badge plus a queue drain. Clients receive an `agent_event` whose state has `released: true` and drop that entry.
- Self-report (`fsh pane report`) and screen-detection placeholder entries (`pane:<name>`) are used only for sessions with no hook owner; a hook claiming the session drops the placeholder. `GET /api/agent/status` `all` and the `/ws-agent` snapshots leave out non-owner entries of owned sessions (`agent_status.visible_state`).
- **Waiting is sticky.** Claude `AskUserQuestion`/`ExitPlanMode` raise `waiting` at PreToolUse (bypass mode also sends PreToolUse → PermissionRequest → Notification → PostToolUse, measured on 2.1.296). The entry remembers which tool call (`tool_use_id`; PermissionRequest inherits the preceding pre's) and which agent (`agent_id`, a subagent) made it wait. Only a pre/post from the same agent and the same call releases it, or the next prompt, Stop, human input, the screen's exit pattern or the TTL. Without ids (older CLIs, screen-made waits) the main agent's next pre/post releases it as before. Subagent pre/post never change the main state, so a background subagent cannot revive a finished session; a subagent's PermissionRequest still raises `waiting`.
- **Who sent the hook (P2).** `agent_hook.sh` sends its own pid (`hook_pid`) and the time it received the event (`ts`, ns). While the request is open, `server/hook_identity.py` reads the process table once: skipping shells upward from the hook gives the agent process; if the pane's `pane_pid` is not among its ancestors the pane claim is `foreign` (dropped — a daemon or another terminal); if `~/.claude/sessions/<pid>.json` is a terminal interactive session (`entrypoint: "cli"`) with the hook's `session_id` it is `ok`; otherwise it is `ok` only when the agent leads (or sits under shells that lead) the tty's foreground process group, else `child`. Measured shapes: the interactive Claude is the pane shell's child and leads the foreground group; Bash-tool commands and hooks run in their own tty-less groups; MCP servers inherit Claude's group; `claude -p` also writes a session file with `kind: "interactive"` but `entrypoint: "sdk-cli"` (an isolated run where that file was trusted let a Bash-tool `claude -p` take the pane over — hence the entrypoint check). `ok` takes ownership at once, even from a busy owner left behind by a crash; `child` can never take an owned session; `unknown` (old hooks, `ps` failure) keeps the status-based rule. The response carries `identity`. A hook from a background Claude session (`CLAUDE_JOB_DIR`) is not sent at all, and an event older than the newest one applied to that entry (`ts`) is dropped.
- **Claude hooks without a pane are not attributed by cwd.** A Claude hook runs under the pane's shell, so no `TMUX_PANE` means a Claude outside tmux (desktop app, IDE, another terminal); before, its state landed on the FarShell session sharing that cwd. `fsh pane report` keeps the cwd fallback.
- **Tmux changes (P1).** The tmux hook (`/api/tmux/kick`, now also on `client-session-changed`) runs `deps.on_tmux_change`: entries learn the tmux session id (`$N`) and follow a rename; entries of a closed session are retired, so a new session reusing the name does not inherit them (`agent_status.reconcile_sessions`). A web PTY whose tmux client moved to another session (`detach-on-destroy off`, `switch-client`, `prefix s`) has its screen detection, interrupt phrases, human-input release and inline-approval tokens attributed to the session it now shows (`deps.tmux_for_pty`), and its detection window is cleared; the frontend's tab/PTY pairing (`session_store`) is left alone.
- **Claude interrupt guard.** `detect/claude.toml` `interrupt_stale_after = '^\s*⏺'`: an `Interrupted · What should Claude do instead?` line followed by an answer/tool line is a redraw of a past turn, not a new interrupt.
- **Codex title.** Codex prefixes the terminal title with `[ ! ] Action Required` (blinking with `[ . ]`) whenever it needs an answer (seen live on 0.160.1). Every agent-name check (5 s, only while a browser is connected) reads the pane titles: a working Codex owner with that title becomes `waiting` (kind `question`), and a wait during which the title was seen ends when it disappears — `working` if the title shows a spinner, otherwise `done` (a decline or ESC sends no hook). A wait whose title was never seen is not released by the title, so a Codex with titles turned off keeps the hook rules.
- **Immediate push.** Transitions outside a hook request — screen detection, interrupt phrases, input release, TTL, retirement — go out at once as `agent_event` with `event` = `screen`/`interrupt`/`ttl`/`ack`/`release`/`rename`/`title` and `resolved_by: "server"` (`agent_status.subscribe`). The 15-second `agent_status_sync` stays as the safety net. The frontend (`agent/state.js`) recomputes a session from all its entries on every event, the same rule as the snapshot, instead of overwriting it with the one entry the event carried.

### Measurements: release paths, reject/ESC, per-CLI results

The release-path table, the terminal-reply root cause (DA1/DA2/XTVERSION/OSC 10/11), the reject/ESC findings and the per-CLI measurement table moved to [`session-view-measurements.md`](./session-view-measurements.md) (that file is canonical; this one keeps the rules). Rules in one line each: input made only of terminal protocol replies (`server/terminal_reports.py`) never releases `waiting`; an `interrupt` screen phrase (`detect/<cli>.toml`) ends `waiting`/`working` as `done`; detection runs on ANSI-stripped text.

### Claude Code and Codex hooks

`server/agent_hook.sh` is the shared entry point (it posts over the unix socket when one exists, else with the machine token; it never fails or blocks the agent either way). Claude Code posts `{pre,post,stop}`;
Codex posts prompt, permission, tool, stop, and session lifecycle events while adding
`agent: "codex"`. Both drive status badges and prompt-queue auto-feed. Only Claude's
`stop` reports completion to the server; session voice policy controls TTS. The legacy `tts_hook.sh` only preserves explicitly configured ntfy delivery.

**Codex session attribution (2026-10-06):** its shared app-server can run hooks with
the `TMUX_PANE` it inherited from an earlier TUI. The live server showed a Codex
entry with the third session's cwd attached to the second tmux session. Codex hook
events therefore ignore `pane` and use cwd only when it identifies exactly one tmux
session. Later events with the same Codex `session_id` keep that binding even when
they omit cwd. If several sessions share cwd, the event stays unattributed rather
than appearing on the wrong session; an unattributed stop also cannot trigger
automatic queue delivery. Claude hooks and direct `fsh pane report` still
use the verified pane first. The actual hook environment was not captured in the
live daemon, so daemon inheritance is the likely cause inferred from the state and
process architecture; the stale-pane input was reproduced against the real tmux
pane list.
Since 2026-10-13 a Codex hook whose process is verified to run inside the pane (see
«Session owner and waiting release» — the TUI's own process tree, e.g. `codex --no-daemon`)
uses that pane like a Claude hook. Unverified Codex hooks still use cwd, but among the panes
in that cwd only the ones running `codex` are candidates, so one Codex session in a repository
with several shells is attributed.

Register both with `fsh hooks install` (idempotent, preserves other hooks, and backs
up the changed config files). `fsh hooks status` and `fsh doctor` report Claude's
eight events and Codex's six events separately.

**Writing other tools' config files (`server/config_io.py`, 2026-10-14).** Hook install/uninstall
and the MCP writers share one write path. It writes through symbolic links (the file at the end
of the link, so a dotfiles repo or a per-account config folder that links a shared file keeps
its link), keeps the file's mode, `fsync`s before the rename, names its lock after the real file
(a link path and the real path take the same lock), and re-checks the file's mtime/size just
before writing: if Claude Code or Codex changed it after we read it, nothing is written and
the command says to retry. The lock is advisory — Claude Code does not honor it — so the
re-check narrows the window, it does not close it. A file with other hard links is refused,
because a rename would split the names and an in-place write is not atomic. CRLF and BOM are
not preserved (native Windows is unsupported; a BOM file fails to parse and is refused, not
damaged). Design and reasoning: `docs/plan-2.4/60-magpie-candidates.md` (local).

**`~/.codex/hooks.json` duplicates (2026-10-06):** the Codex desktop app's "import Claude
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
**Register `agent_hook.sh stop`, never `tts_hook.sh` directly** — registering
both plays the TTS summary twice.

- Speech: `server/voice_sessions.py` applies session policy, output ownership and serial playback; `voice_completion.py` extracts Claude/Codex final responses.
- Hooks stay installed for status/queue regardless of whether session TTS is enabled.
- No automatic server-side speech when a browser disconnects or the server is unavailable.
- Configure current-session voice in Settings; see [voice.md](voice.md).


```bash
# Test the hook (with the server running)
echo '{"transcript_path":"/tmp/test_transcript.jsonl"}' | ./server/tts_hook.sh
```

### Hook aggregation (P3-6, current behavior — no engine, by design)

crush's `internal/hooks/` runs a `deny > allow > none` aggregation with sticky halt
because **one crush process runs every matched hook itself** and must fold their exit
codes into a single decision before continuing the agent loop. We are not that
process — Claude Code and Codex are, and hook-decision aggregation across matched
groups happens **inside their own runtimes**, which we don't own and can't observe.
So the question this section actually answers is narrower: does *our own* installed
hook (`agent_hook.sh`) ever end up invoked more than once for what a human would call
"one event," and if so, what does our server do about it?

**Yes, more than one hook group for the same event is a normal, expected outcome —
not an edge case:**
- Both installers (`claude_hooks.apply_install`, `codex_hooks.apply_install`)
  explicitly preserve any pre-existing hook the user already had for that event
  (`kept.append(g)` for a foreign Claude group; text outside the `farshell:hooks`
  markers for Codex). If the user had their own `Stop` hook before running
  `fsh hooks install`, both it and ours are registered afterward, and Claude
  Code/Codex run both when a session stops.
- `SessionStart` (2026-09-28) uses matcher `"startup|resume|clear"` — **not** `compact`, which
  fires mid-task; the server ignores `source: "compact"` too, for hooks registered without it.
- Our `PreToolUse`/`PostToolUse` matcher is always `"*"` (`apply_install`, both
  files) — every tool call matches it. A user with a tool-specific hook (matcher
  `"Bash"`, say) will see **both** groups fire on every Bash call. This isn't
  hypothetical; it's true for anyone who had hooks configured before installing
  FarShell.
- Two copies of *our own* hook for one event is the one case the installers
  actively prevent, not something that can currently happen: `claude_hooks.
  apply_install` has an explicit "우리 항목이 두 개 이상 — 첫 번째만 남기고 나머지는
  버린다" dedup branch (the branch existing at all says it was seen or anticipated
  before the current idempotent installer shipped), and `codex_hooks.apply_install`
  replaces the whole managed block as one unit, so a second copy can't accumulate
  from repeated `fsh hooks install` runs. `plan()`/`plan_text()` are what `fsh hooks
  status`/`fsh doctor` read to report this without touching the file.

**What our server does when that happens: nothing needs to merge, because nothing
converges on one process.** Each matched hook group is a separate OS subprocess
started directly by Claude Code/Codex — a foreign hook script never talks to our
server, and Claude Code's own decision-aggregation across groups (if the hooks
return allow/deny) happens before or after `agent_hook.sh` runs, entirely inside the
CLI. `POST /api/agent/event` (`server/routes/agents.py:212`) only ever hears from
invocations of `agent_hook.sh` itself, and there is exactly one such registration
per event by construction (see dedup above). `agent_status.on_event()` mutates the
in-memory entry directly with no merge step — if it were ever called twice for the
same logical moment, the second call's fields simply overwrite the first's
(last-write-wins), which is a non-issue in practice because that double-call
scenario is the one thing the installers rule out.

**Conclusion: no aggregation engine to build.** The situation crush's `hooks.go
aggregate()` exists for — one process reconciling conflicting decisions from
multiple hooks it itself ran — doesn't arise on our side. If we ever become the
process that runs multiple hooks ourselves (not just receives one call from one),
revisit this and bring over crush's `deny > allow > none` + sticky-halt rule rather
than inventing a new one — it's already proven. The one crush detail worth carrying
over regardless of that: `runner.go:172`'s rule to **never read a timed-out/killed
child's stdout or stderr after abandoning it** (data race on the buffer). It doesn't
apply to hook running today, but it applies directly to the health-check timeout
handling in `40-mcp.md` §3 (P3-2) — note it there when that item is picked up.

## Agent-to-agent messages (2026-09-28, stage 1)

One agent sends a message to another agent's tmux session, on this Mac or a paired one. Stage 1 = MCP server + peer delivery + store + CLI; a UI panel is stage 2.

- **Delivery is a queue item** (`server/agent_msg.py` → `queue_store.add(..., source={"agentMsg": id})`). Typing into a pane at an arbitrary moment either lands mid-task or gets **consumed as an approval answer**; the queue already refuses both (stop hook only, never into `waiting`, safe_mode, one at a time). One addition: if the target is **known** idle/done (`agent_status.known_status_for_session` — unlike `status_for_session` it returns None for "no entry"), it is drained after the grace period, because no stop hook will come. Unknown state is never guessed; it stays queued for `fsh queue run`. `queue_runner.drain_once` reports back so the message becomes `delivered`/`blocked`.
- **Sender identity is resolved, not claimed.** `/api/agent/message` runs the hook's 3-step `pane_resolve` on the caller's `$TMUX_PANE`; the remote side takes the sender host from the signed grant. The reply address in the header comes from there.
- **With `VT_AUTHZ_ENFORCE=msg` the sender is the authenticated caller** (socket peer pid / verified pane), body claims are ignored and an unverifiable caller gets 403 `identity_unverified`. Messages between a delegation parent and child, or between siblings (open delegations), go through as before; any other pair needs a person's approval first — the tool answers "waiting for user approval, repeat the call once approved" (HTTP 202) and the request appears in the browser's approval inbox. `fsh msg send` and the MCP `send_message` behave the same. Details: `docs/ref/auth.md` «Agent messages under the engine».
- **Receiving from another Mac needs `control` plus `fsh host allow-agent <id>`** (`grant.agentMessages`). Making another Mac's agent do work is remote execution; `control` alone must not open it silently. The body is covered by the signature hash.
- **Ping-pong guard**: hop limit 10 per conversation, counted from the receiver's own records as well as the sender's claim (the larger wins); 20/min per sending host.
- **The header** tells the receiving agent the text came from another agent, with `reply_to`/`to` for the answer. The body is flattened to one line — the queue submits with Enter, and a multi-line body would be split into several prompts.
- **`fsh mcp`** (`server/mcp_farshell.py`) is a dependency-free stdio JSON-RPC server with six tools: `list_agents`, `send_message`, `read_messages` (messages) and `list_shared_sessions`, `read_session`, `send_to_session` (session sharing, below). It only calls the local FarShell API — over the unix socket first (`~/.vt/run/fsh.sock`, the server identifies the caller by peer pid; see `docs/ref/auth.md` «Unix socket»), falling back to the machine token (`~/.vt.env`, else env) when there is no socket — and, for the sharing tools, also sends `TMUX_PANE`/`TMUX`/its pid as `X-Fsh-*` headers so the server can verify which tmux pane it runs in. `fsh msg` exposes the message tools for shells and agents without MCP (the sharing tools are MCP-only).
- Automatic delivery works for agents with lifecycle hooks (Claude Code, Codex). Others (aider, agy, …) receive the message but it is fed manually.
- **Three fixes from the first real M1↔M5 run (2026-09-28):**
  - `tmux_target.send_to_tmux` typed text and `Enter` in **one** `send-keys` call; Claude Code read the burst as a paste and took the Enter as a newline — the message sat unsubmitted in the input box (0/5 in a repeat test). Now the text goes with `-l` and Enter is a **separate** call (5/5). This also affected the plain prompt queue, voice input and `/api/peer/input` with `enter`. `-l` additionally stops a text that equals a key name (`Enter`, `Up`) from being pressed as that key, in `type_to_tmux` too.
  - A reply that arrived was read as a new request and answered again (hop 2). Replies now carry a different header ("답장 … 다시 답할 필요가 없습니다"); only requests tell the agent how to answer. The hop limit stays as the backstop.
  - A freshly started agent had no status entry, so messages waited until a human typed the first prompt. `SessionStart` (startup/resume/clear) now marks the session idle and drains **only items that name that session** (`targeted_only`) — untargeted queue items still belong to whichever agent stops first. Re-run `fsh hooks install` (or `install.sh`) so existing machines register the new hook.

## Agent delegation (MCP, 2026-10-13)

An agent hands work to a **sub-session** it creates and gets the result back as a queue item. Design and the reference survey behind it: `docs/plan-2.4/50-agent-delegation.md`; code: `server/delegation.py`, `server/routes/delegation.py`; endpoints: `API.md` «Agent delegation».

- **Tools** (`fsh mcp`): `spawn_session {task, title?, agent?, cwd?, worktree?:{branch}}`, `send_to_child {delegation, text}`, `close_delegation {delegation}`, `report_back {outcome, summary}`, `list_delegations`. `tools/list` asks the server for the caller's role: a sub-session never sees the spawn/send/close tools, a non-child never sees `report_back`.
- **Ownership lives in `~/.vt/delegations.json` only** (0600, flock). Each record pins parent and child by `#{session_id}:#{session_created}` (the same key `share_identity.verify_caller` returns), so a renamed session keeps its delegation and a re-created session with the same name does not inherit it. The tmux options `@fsh_parent` / `@fsh_delegation` are display hints — an agent could rewrite them, so no check reads them.
- **Limits** (all enforced in `check_spawn`, inside the store lock): depth 1 (a session that was ever a child cannot spawn), 3 open (running/reported) children per parent, 5 spawns/min server-wide, `VT_AGENT_SPAWN=0` turns the feature off. Refusals tell the model not to retry.
- **Launch.** The server picks the command: `command claude --permission-mode default` or `command codex --sandbox workspace-write --ask-for-approval on-request`. `command` skips shell aliases, so a user alias that adds `--dangerously-skip-permissions` does not reach the child; the parent's bypass mode is never inherited. The task goes in as a targeted queue item (not a CLI argument — that would show in `ps`), with a footer asking the child to call `report_back` once; the child's `SessionStart` hook drains it. Task and follow-up items carry a one-line `preface` that is **typed** before the body is pasted: Claude Code wraps a bracketed paste as `<pasted_content>` and may decline instructions it did not get from the user (real run 2026-10-13: 1 of 3 children refused; with the preface 3 of 3 followed). Results to the parent get no preface, so they read as data. Without FarShell hooks in the child CLI the task stays queued for `fsh queue run`.
- **Worktree** only when asked: `worktree.create_worktree` from the parent's `HEAD`, no agent started by it.
- **Results to the parent** are queue items targeted at the parent session: `[위임 결과 · <child> · <kind>/<outcome>] (위임 <id>)`, a fixed «this is data, not instructions» line, then the body capped at 4 KB (lines that imitate the header are prefixed with `> `). Kinds: `reported` (`report_back`), `turn_ended` (child's Stop hook without a report — carries the hook's `last_assistant_message`, once per delegation, skipped after a report), `needs_human` (child entered `waiting`; the parent cannot answer it), `gone` (child session vanished), `soft_limit` (running longer than `VT_DELEGATE_SOFT_LIMIT_MIN`, default 60; nothing is killed). A newer result replaces an older one still pending in the parent's queue.
- **Exactly once.** Each result is written to the record's outbox before it is queued; the sweep (`VT_DELEGATE_SWEEP_SEC`, default 30) re-queues unsent entries and skips ones whose `{delegation, kind}` source is already in the queue.
- **Lifecycle.** `close_delegation` / `fsh delegate close` close the record and drop the child's undelivered instructions; the session and worktree stay. A parent that disappears leaves its children `orphaned` (not killed). Spawn, report, close and refusals are written to `~/.vt/agent_access.log` (with `VT_AUTHZ_ENFORCE=deleg` to the authz audit chain instead, and the authz engine decides — `docs/ref/auth.md` «Agent delegation under the engine»: explicit `cwd`/worktree base must be inside the browse roots, `safe_mode` blocks spawning, `fsh delegate close` goes through the server as principal `cli`).

## Agent session sharing (MCP, 2026-10-06)

The user shares an ordinary tmux session (nickname + `read`/`readwrite`) and the agent can read it and, if allowed, type into it.
**Agents never create or close sessions the user made** — shared sessions stay under the approval flow below. Agents can create their own sub-sessions through delegation (next section). Design, identity check, approval rules, limits and the known gaps: `docs/ref/terminal.md`
«Agent-shared sessions»; endpoints and the `agent_share_request` event: `API.md`; the human-only boundary: `docs/ref/auth.md`.

- Tool descriptions say the same thing to the model: only shared sessions work, the first use needs the user's approval (repeat the
  same call once approved — the tool returns `사용자 승인 대기 중` for a `202`), shared sessions cannot be created or closed by the agent (sub-sessions are delegation's job).
- `read_session {nickname, lines?}` → last lines of the pane (default 200, max 2000, 64 KB). `send_to_session {nickname, text, enter?}` →
  one line, literally; `enter:true` presses Enter. No newlines/control characters (several commands = several calls); refused while an
  agent CLI in the target is `waiting` or `working`.
- `list_shared_sessions` shows each session's nick, mode, state and **my** read/write status (`granted`/`pending`/`denied`/`none`/
  `unavailable`); it never shows session names or folders.
- The `agent_status` states are only known for sessions with hooks. A plain shell has no state, so sends to it are allowed.


### MCP 갱신·출처 추적 (2026-10-06)

- 각 설정 항목에 `source_id`, 파일 revision, 원문 hash, 소유자, 쓰기 가능 여부,
  설정 상태와 실효 우선순위를 제공한다. OpenCode의 같은 이름 정의는 필드 병합 참여
  출처로 표시한다. JSONC와 추가 디렉터리 정의는 읽기 전용이다.
- 보관함 가져오기·원본 갱신·배포·동기화는 서버에 보관한 300초 단회 계획을 검토한
  뒤 적용한다. 원문 명령·인자·URL·자격증명 값은 미리보기 응답에 포함하지 않는다.
- 새 배포는 기본 꺼짐이다. Claude는 비공유 프로젝트 설정에만 배포하고, agy는 전역만
  지원한다. 의미를 보존할 수 없는 자격증명 참조·추가 필드는 복제를 거절한다.
- 원본 추적은 FarShell이 배포한 대상만 선택적으로 켤 수 있다. 조회 때 원본 변경을
  발견하지만 쓰기는 사용자의 미리보기 확인 이후에만 한다. 대상 직접 편집은
  `diverged`로 중단한다. 적용 직전 대상 락 안에서 원본과 대상 revision을 재검사한다.
  외부 CLI가 FarShell의 락에 협조하지 않으므로 모든 외부 변경의 원자적 차단을
  보장하는 것은 아니다.
- 설정 켬/끔, 독립 검사, 실제 CLI 실행 상태는 별개다. 독립 HTTP 검사에서 401/403은
  인증 필요이며, initialize 응답 형식을 확인한다. 리다이렉트·과대 응답을 거절하고
  SSE 검사는 미지원으로 표시한다. 실제 실행 상태는 확인 채널이 없어 `unknown`이다.
- 플러그인 번들의 MCP 이름은 플러그인 아래 읽기 전용으로 표시한다. 인증·플러그인
  설치·스킬 설치의 양방향 동기화는 수행하지 않는다.
