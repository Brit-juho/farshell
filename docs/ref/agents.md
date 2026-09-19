# Agent state, queue, MCP

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Agent state (2.0) | The server decides one of `idle`/`working`/`waiting`/`done` per session and everything else just displays it — worktree tabs, pane headers, the rail list (sorted so what needs you is on top), the favicon, and the app icon badge (`waiting` count). Claude Code and Codex lifecycle hooks are installed together by `fsh hooks install`; `fsh doctor` and Settings → About report each tool separately |
| Approval detection (`waiting`) | Detected off the PTY stream using patterns in `server/detect/*.toml`. Cleared by an exit pattern, by you typing in that pane, by the next hook event, or by a 2-minute TTL. **The prompt queue will not feed a pane that is `waiting`** — `send-keys` there would be consumed as the approval answer |
| Detection coverage (2.1.1) | Settings → Agents → "Detection coverage" (`GET /api/agents/coverage`). Approval detection silently does nothing for a CLI with no patterns, and there was no way to see that — this table makes it visible per CLI: `path` (`hook`/`report`/`pty`/`none`), the toml's line count, the states it can express, and `trust` (`high`/`mid`/`low`). Claude Code reaches `hook`/`high` with its three managed events; Codex does the same with six lifecycle events. Codex also retains real-terminal-verified PTY patterns as a fallback, including `y`/`p`/`esc` approval keys. aider/gemini rely on PTY patterns or explicit `fsh pane report`. `enter`/`exit` are **literal byte strings** matched against a bounded 16KiB terminal-output window; only `options` is a regex. Background and how to fix a pattern: `docs/help/agent-detect.md` |
| Usage gauge (2.0 · Codex 2.1.5) | Two sources, merged into one list. **clauth** reads `~/.clauth/status.json` (file, not CLI). Accepts feed schema **1 and 2** (supporting only one means the gauge silently dies on the next clauth upgrade); an unknown schema still disables it silently, but Settings → About now says so (2.1.3). Shown in the right rail on wide screens and in a panel elsewhere. **Disappears entirely when there's no source** — `VT_USAGE_PROVIDER` (`auto`/`clauth`/`none`) controls it. Tokens are excluded by a field whitelist **Codex (2.1.5)** is a second limit-type provider — it calls the same OAuth usage API the Codex CLI itself uses, reading the token from `~/.codex/auth.json` on every call and never storing it. Being a remote API it uses a 60s TTL cache and, on 429/network errors, keeps showing the previous value marked `stale`. The response is whitelisted on the way in (`credits`/`model_usage` stop existing at the normaliser). `panels/usage.js` was not touched — the profile card was already provider-agnostic |
| MCP servers (97, 2.1.5) | Settings → **MCP**. Shows every MCP server defined for Claude Code / Codex / agy, for the **global scope plus the worktree you're looking at**, and toggles them. All three CLIs support scopes and on/off officially, but each only knows "this project" — there was no view across projects. **Nothing is stored**: the real config files are read every time the section opens and written on toggle, so edits made in a terminal show up immediately and there is no "our record vs. the file" drift. **Values never leave the server** — `env`/`headers` come back as the key name plus "literal or reference" only, because reading `~/.claude.json` pulls in every other server's key and this UI is exposed through the tunnel. The screen refuses to lie: config is read once per session by all three CLIs, so "off" does not mean "not callable right now" — the apply timing is always shown, **anything unverified is said to be unverified** (agy's timing is), and a write whose result could not be confirmed never renders as success. Two diagnostics come free: a literal secret sitting in the committed `.mcp.json`, and a `${VAR}` in Codex (which does **not** expand it — the literal string is passed through). Toggling requires an elevated session; it changes which tools an agent can call. `GET /api/mcp`, `POST /api/mcp/toggle` |
| Prompt queue (P4) | ⋯ menu → "Prompt Queue", or `fsh queue`. Queues up instructions while an agent is busy and feeds them in sequentially. Automatic feeding is triggered by a server-side `done` transition from either Claude Code or Codex lifecycle hooks (`POST /api/agent/event`). aider/gemini still need `fsh queue run` / "run now" unless they self-report. Feeding based on guessed output idleness is not used. Four gates before feeding: grace period (`VT_QUEUE_GRACE_SEC`, default 3 sec) · safe_mode · confirming the target pane is alive · one item at a time. Blocked or failed items are **not discarded** — they stay in the queue as `blocked`. Target resolution uses the same rules as voice (`server/tmux_target.py`). Stored at `~/.vt/queue.json` (0600), with concurrent writes serialized via flock. A remote item uses `POST /api/peer/input` and needs `control` level on the other side. |
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
- Config: Claude `hooks.PreToolUse` / `hooks.PostToolUse` / `hooks.Stop` in
  `~/.claude/settings.json`; Codex's six managed events in `~/.codex/config.toml`
- Behavior: extracts the last assistant response (up to 200 chars) from the transcript → server TTS → plays via `afplay`
- Fallback: uses macOS `say -v Yuna` if the server isn't running

```bash
# Test the hook (with the server running)
echo '{"transcript_path":"/tmp/test_transcript.jsonl"}' | ./server/tts_hook.sh
```
