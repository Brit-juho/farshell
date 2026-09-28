# Agent state, queue, MCP

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Agent state (2.0) | The server decides one of `idle`/`working`/`waiting`/`done` per session and everything else just displays it — worktree tabs, pane headers, the rail list (sorted so what needs you is on top), the favicon, and the app icon badge (`waiting` count). Claude Code and Codex lifecycle hooks are installed together by `fsh hooks install`; `fsh doctor` and Settings → About report each tool separately |
| Approval detection (`waiting`) | Detected off the PTY stream using patterns in `server/detect/*.toml`. Cleared by an exit pattern, by you typing in that pane, by the next hook event, or by a 2-minute TTL. **The prompt queue will not feed a pane that is `waiting`** — `send-keys` there would be consumed as the approval answer |
| Detection coverage (2.1.1) | Settings → Agents → "Detection coverage" (`GET /api/agents/coverage`). Approval detection silently does nothing for a CLI with no patterns, and there was no way to see that — this table makes it visible per CLI: `path` (`hook`/`report`/`pty`/`none`), the toml's line count, the states it can express, and `trust` (`high`/`mid`/`low`). Claude Code reaches `hook`/`high` with its three managed events; Codex does the same with six lifecycle events. Codex also retains real-terminal-verified PTY patterns as a fallback, including `y`/`p`/`esc` approval keys. aider/agy rely on PTY patterns or explicit `fsh pane report`. `enter`/`exit` are **literal byte strings** matched against a bounded 16KiB terminal-output window; only `options` is a regex. Background and how to fix a pattern: `docs/help/agent-detect.md` |
| Usage gauge (2.0 · Codex 2.1.5) | Two sources, merged into one list. **clauth** reads `~/.clauth/status.json` (file, not CLI). Accepts feed schema **1 and 2** (supporting only one means the gauge silently dies on the next clauth upgrade); an unknown schema still disables it silently, but Settings → About now says so (2.1.3). Shown in the right rail on wide screens and in a panel elsewhere. **Disappears entirely when there's no source** — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls it. Tokens are excluded by a field whitelist **Codex (2.1.5)** is a second limit-type provider — it calls the same OAuth usage API the Codex CLI itself uses, reading the token from `~/.codex/auth.json` on every call and never storing it. Being a remote API it uses a 60s TTL cache and, on 429/network errors, keeps showing the previous value marked `stale`. The response is whitelisted on the way in (`credits`/`model_usage` stop existing at the normaliser). `panels/usage.js` was not touched — the profile card was already provider-agnostic |
| MCP servers (97, 2.1.5) | Settings → **MCP** (its own section since ADR-48 — one server per collapsed `<details>` row: name, per-tool on/off, warnings that stay visible when collapsed, last verify result; toggles, [검증], credential slots, 「복제」 (valid targets only) and tags live in the expanded body; plugins load separately so the list never waits on the slow Codex query). Shows every MCP server defined for Claude Code / Codex / agy, for the **global scope plus the worktree you're looking at**, and toggles them. All three CLIs support scopes and on/off officially, but each only knows "this project" — there was no view across projects. **Nothing is stored**: the real config files are read every time the section opens and written on toggle, so edits made in a terminal show up immediately and there is no "our record vs. the file" drift. **Values never leave the server** — `env`/`headers` come back as the key name plus "literal or reference" only, because reading `~/.claude.json` pulls in every other server's key and this UI is exposed through the tunnel. The screen refuses to lie: config is read once per session by all three CLIs, so "off" does not mean "not callable right now" — the apply timing is always shown, **anything unverified is said to be unverified** (agy's timing is), and a write whose result could not be confirmed never renders as success. Two diagnostics come free: a literal secret sitting in the committed `.mcp.json`, and a `${VAR}` in Codex or agy (neither expands it — the literal string is passed through; agy confirmed on 1.2.7 in an isolated HOME, 2026-09-23, ADR-46). Because agy has **no way to reference a variable at all** (no expansion, no Codex-style `env_vars` list — it just passes its own environment through), copying a server that has `env`/`headers` to agy is refused rather than writing a secret in plain text or a dead `${…}` string. Toggling requires an elevated session; it changes which tools an agent can call. `GET /api/mcp`, `POST /api/mcp/toggle` |
| Prompt queue (P4) | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. Automatic feeding is triggered by a server-side `done` transition from either Claude Code or Codex lifecycle hooks (`POST /api/agent/event`). aider/agy still need `fsh queue run` / "run now" unless they self-report. Feeding based on guessed output idleness is not used. Four gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 3 sec) · safe_mode · confirming the target pane is alive · one item at a time. Blocked or failed items are **not discarded** — they stay in the queue as `blocked`. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock. A remote item uses `POST /api/peer/input` and needs `control` level on the other side. |
| Prompt snippets (L3) | Left rail → 📋, or `Mod+K` → "Prompt snippets". The iTerm2 Snippets idea: save a frequently used instruction or command block and fire it into the pane you're looking at right now. **Distinct from the queue** — the queue is a waiting line ("run this when the agent is free"), a snippet has no waiting concept at all, it goes in immediately. So `snippet_store.py` is pure CRUD with no status/target/drain state machine. A multi-line snippet gets a trailing `\n` on every line, so lines execute sequentially. Stored at `~/.vt/snippets.json` (0600 + flock, same rules as the queue), capped at 100 items / 8000 chars. **Web UI only — there is no `fsh snippet` subcommand** |
| Detection coverage trust (N9) | The table in Settings → "Agents". `trust: high` means a **first-party signal** exists — a Claude Code hook (`hook`) or `fsh pane report --agent <name>` self-reporting (`report`). Through 2.1.3 only `hook` was high and that value was only ever set for claude, so other CLIs stayed at mid no matter how many patterns you added. Self-reporting is recorded in `~/.vt/agent-report-seen.json` — the same kind of durable fact as "a hook is installed" (status entries expire on a TTL, which would make the grade flicker) |
| Stale-server warning (N28) | `fsh doctor` and `fsh status` compare the newest mtime under `server/*.py` against the server process start time and tell you to restart when the code is newer. Python loads modules at import time, so without a restart a fix is **not** live at all — the preventive measure for a real incident where paste truncation was chased as a code bug for hours |

### Claude Code and Codex hooks

`server/agent_hook.sh` is the shared entry point. Claude Code posts `{pre,post,stop}`;
Codex posts prompt, permission, tool, stop, and session lifecycle events while adding
`agent: "codex"`. Both drive status badges and prompt-queue auto-feed. Only Claude's
`stop` delegates stdin to `tts_hook.sh` for the TTS summary.

Register both with `fsh hooks install` (idempotent, preserves other hooks, and backs
up the changed config files). `fsh hooks status` and `fsh doctor` report Claude's
three events and Codex's six events separately.
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
- **`fsh mcp`** (`server/mcp_farshell.py`) is a dependency-free stdio JSON-RPC server with three tools: `list_agents`, `send_message`, `read_messages`. It only calls the local FarShell API with the machine token (env, else `~/.vt.env`). `fsh msg` exposes the same for shells and agents without MCP.
- Automatic delivery works for agents with lifecycle hooks (Claude Code, Codex). Others (aider, agy, …) receive the message but it is fed manually.
- **Three fixes from the first real M1↔M5 run (2026-09-28):**
  - `tmux_target.send_to_tmux` typed text and `Enter` in **one** `send-keys` call; Claude Code read the burst as a paste and took the Enter as a newline — the message sat unsubmitted in the input box (0/5 in a repeat test). Now the text goes with `-l` and Enter is a **separate** call (5/5). This also affected the plain prompt queue, voice input and `/api/peer/input` with `enter`. `-l` additionally stops a text that equals a key name (`Enter`, `Up`) from being pressed as that key, in `type_to_tmux` too.
  - A reply that arrived was read as a new request and answered again (hop 2). Replies now carry a different header ("답장 … 다시 답할 필요가 없습니다"); only requests tell the agent how to answer. The hop limit stays as the backstop.
  - A freshly started agent had no status entry, so messages waited until a human typed the first prompt. `SessionStart` (startup/resume/clear) now marks the session idle and drains **only items that name that session** (`targeted_only`) — untargeted queue items still belong to whichever agent stops first. Re-run `fsh hooks install` (or `install.sh`) so existing machines register the new hook.
