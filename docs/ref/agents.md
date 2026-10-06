# Agent state, queue, MCP

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Agent state (2.0) | The server decides one of `idle`/`working`/`waiting`/`done` per session (the reserved `error` has no entry path and the frontend folds it to `idle`) and everything else just displays it; labels and the representative-state priority live in `frontend/js/design/state-classes.js` — worktree tabs, pane headers, the rail list (sorted so what needs you is on top), the favicon, and the app icon badge (`waiting` count). Claude Code and Codex lifecycle hooks are installed together by `fsh hooks install`; `fsh doctor` and Settings → About report each tool separately |
| Approval detection (`waiting`) | Detected off the PTY stream using patterns in `server/detect/*.toml`. Cleared by an exit pattern, by an `interrupt` screen phrase (a rejected approval or ESC — Claude Code emits **no** Stop hook for either), by you typing in that pane, by the next hook event, or by a 2-minute TTL. **The prompt queue will not feed a pane that is `waiting`** — `send-keys` there would be consumed as the approval answer |
| Detection coverage (2.1.1) | Settings → Agents → "Detection coverage" (`GET /api/agents/coverage`). Approval detection silently does nothing for a CLI with no patterns, and there was no way to see that — this table makes it visible per CLI: `path` (`hook`/`report`/`pty`/`none`), the toml's line count, the states it can express, and `trust` (`high`/`mid`/`low`), and (2026-10-03) `kinds`/`kindSources`/`badges` — which waiting kinds the CLI can tell apart and whether each comes from a `hook`, the `screen`, or the `fallback`. Claude Code reaches `hook`/`high` with its six managed events (Pre/PostToolUse, Stop, SessionStart, Notification, PermissionRequest); Codex does the same with six lifecycle events. Codex also retains real-terminal-verified PTY patterns as a fallback, including `y`/`p`/`esc` approval keys. aider/agy rely on PTY patterns or explicit `fsh pane report`. `enter`/`exit` are **literal byte strings** matched against a bounded 16KiB terminal-output window; only `options` is a regex. Background and how to fix a pattern: `docs/help/agent-detect.md` |
| Usage gauge (2.0 · Codex 2.1.5) | Two sources, merged into one list. **clauth** reads `~/.clauth/status.json` (file, not CLI). Accepts feed schema **1 and 2** (supporting only one means the gauge silently dies on the next clauth upgrade); an unknown schema still disables it silently, but Settings → About now says so (2.1.3). Shown in the right rail on wide screens and in a panel elsewhere. **Disappears entirely when there's no source** — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls it. Tokens are excluded by a field whitelist **Codex (2.1.5)** is a second limit-type provider — it calls the same OAuth usage API the Codex CLI itself uses, reading the token from `~/.codex/auth.json` on every call and never storing it. Being a remote API it uses a 60s TTL cache and, on 429/network errors, keeps showing the previous value marked `stale`. The response is whitelisted on the way in (`credits`/`model_usage` stop existing at the normaliser). `panels/usage.js` was not touched — the profile card was already provider-agnostic |
| MCP servers (97, 2.1.5) | Dock MCP button (mobile: Quick Open → MCP) opens an independent popup — one server per collapsed `<details>` row: name, per-tool on/off, warnings that stay visible when collapsed, last verify result; toggles, [검증], credential slots, 「복제」 (valid targets only) and tags live in the expanded body; plugins load separately so the list never waits on the slow Codex query. Shows every MCP server defined for Claude Code / Codex / agy, for the **global scope plus the worktree you're looking at**, and toggles them. All three CLIs support scopes and on/off officially, but each only knows "this project" — there was no view across projects. **Nothing is stored**: the real config files are read every time the section opens and written on toggle, so edits made in a terminal show up immediately and there is no "our record vs. the file" drift. **Values never leave the server** — `env`/`headers` come back as the key name plus "literal or reference" only, because reading `~/.claude.json` pulls in every other server's key and this UI is exposed through the tunnel. The screen refuses to lie: config is read once per session by all three CLIs, so "off" does not mean "not callable right now" — the apply timing is always shown, **anything unverified is said to be unverified** (agy's timing is), and a write whose result could not be confirmed never renders as success. Two diagnostics come free: a literal secret sitting in the committed `.mcp.json`, and a `${VAR}` in Codex or agy (neither expands it — the literal string is passed through; agy confirmed on 1.2.7 in an isolated HOME, 2026-09-23, ADR-46). Because agy has **no way to reference a variable at all** (no expansion, no Codex-style `env_vars` list — it just passes its own environment through), copying a server that has `env`/`headers` to agy is refused rather than writing a secret in plain text or a dead `${…}` string. Toggling requires an elevated session; it changes which tools an agent can call. `GET /api/mcp`, `POST /api/mcp/toggle` |
| Prompt queue (P4) | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. Automatic feeding is triggered by a server-side `done` transition from either Claude Code or Codex lifecycle hooks (`POST /api/agent/event`). aider/agy still need `fsh queue run` / "run now" unless they self-report. Feeding based on guessed output idleness is not used. Four gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 3 sec) · safe_mode · confirming the target pane is alive · one item at a time. Blocked or failed items are **not discarded** — they stay in the queue as `blocked`. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock. A remote item uses `POST /api/peer/input` and needs `control` level on the other side. |
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

### Measurements: release paths, reject/ESC, per-CLI results

The release-path table, the terminal-reply root cause (DA1/DA2/XTVERSION/OSC 10/11), the reject/ESC findings and the per-CLI measurement table moved to [`session-view-measurements.md`](./session-view-measurements.md) (that file is canonical; this one keeps the rules). Rules in one line each: input made only of terminal protocol replies (`server/terminal_reports.py`) never releases `waiting`; an `interrupt` screen phrase (`detect/<cli>.toml`) ends `waiting`/`working` as `done`; detection runs on ANSI-stripped text.

### Claude Code and Codex hooks

`server/agent_hook.sh` is the shared entry point. Claude Code posts `{pre,post,stop}`;
Codex posts prompt, permission, tool, stop, and session lifecycle events while adding
`agent: "codex"`. Both drive status badges and prompt-queue auto-feed. Only Claude's
`stop` delegates stdin to `tts_hook.sh` for the TTS summary.

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

Register both with `fsh hooks install` (idempotent, preserves other hooks, and backs
up the changed config files). `fsh hooks status` and `fsh doctor` report Claude's
five events and Codex's six events separately.

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

- Script: `server/tts_hook.sh` (the TTS half, invoked by `agent_hook.sh stop`
  **only while the voice daemon is running** — same check as `fsh`'s `_is_running voice`.
  The agent-event POST is *not* gated on voice; status badges and the queue need it always.)
- Config: Claude `hooks.PreToolUse` / `hooks.PostToolUse` / `hooks.Stop` / `hooks.SessionStart` in
  `~/.claude/settings.json`; Codex's six managed events in `~/.codex/config.toml`
- Behavior: extracts the last assistant response (up to 200 chars) from the transcript → server TTS → plays via `afplay`
- Fallback: uses macOS `say -v Yuna` if the server isn't running

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
- **Receiving from another Mac needs `control` plus `fsh host allow-agent <id>`** (`grant.agentMessages`). Making another Mac's agent do work is remote execution; `control` alone must not open it silently. The body is covered by the signature hash.
- **Ping-pong guard**: hop limit 10 per conversation, counted from the receiver's own records as well as the sender's claim (the larger wins); 20/min per sending host.
- **The header** tells the receiving agent the text came from another agent, with `reply_to`/`to` for the answer. The body is flattened to one line — the queue submits with Enter, and a multi-line body would be split into several prompts.
- **`fsh mcp`** (`server/mcp_farshell.py`) is a dependency-free stdio JSON-RPC server with six tools: `list_agents`, `send_message`, `read_messages` (messages) and `list_shared_sessions`, `read_session`, `send_to_session` (session sharing, below). It only calls the local FarShell API with the machine token (env, else `~/.vt.env`) and, for the sharing tools, also sends `TMUX_PANE`/`TMUX`/its pid as `X-Fsh-*` headers so the server can verify which tmux pane it runs in. `fsh msg` exposes the message tools for shells and agents without MCP (the sharing tools are MCP-only).
- Automatic delivery works for agents with lifecycle hooks (Claude Code, Codex). Others (aider, agy, …) receive the message but it is fed manually.
- **Three fixes from the first real M1↔M5 run (2026-09-28):**
  - `tmux_target.send_to_tmux` typed text and `Enter` in **one** `send-keys` call; Claude Code read the burst as a paste and took the Enter as a newline — the message sat unsubmitted in the input box (0/5 in a repeat test). Now the text goes with `-l` and Enter is a **separate** call (5/5). This also affected the plain prompt queue, voice input and `/api/peer/input` with `enter`. `-l` additionally stops a text that equals a key name (`Enter`, `Up`) from being pressed as that key, in `type_to_tmux` too.
  - A reply that arrived was read as a new request and answered again (hop 2). Replies now carry a different header ("답장 … 다시 답할 필요가 없습니다"); only requests tell the agent how to answer. The hop limit stays as the backstop.
  - A freshly started agent had no status entry, so messages waited until a human typed the first prompt. `SessionStart` (startup/resume/clear) now marks the session idle and drains **only items that name that session** (`targeted_only`) — untargeted queue items still belong to whichever agent stops first. Re-run `fsh hooks install` (or `install.sh`) so existing machines register the new hook.

## Agent session sharing (MCP, 2026-10-06)

The user shares an ordinary tmux session (nickname + `read`/`readwrite`) and the agent can read it and, if allowed, type into it.
**Agents never create or close sessions.** Design, identity check, approval rules, limits and the known gaps: `docs/ref/terminal.md`
«Agent-shared sessions»; endpoints and the `agent_share_request` event: `API.md`; the human-only boundary: `docs/ref/auth.md`.

- Tool descriptions say the same thing to the model: only shared sessions work, the first use needs the user's approval (repeat the
  same call once approved — the tool returns `사용자 승인 대기 중` for a `202`), sessions cannot be created or closed.
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
