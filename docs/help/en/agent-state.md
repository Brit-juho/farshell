# Agent state (`fsh hooks` · `fsh pane report`)

The server works out what state each tmux session is in and shows it in the web/mobile UI.
There are four states.

| State | Meaning | How it arrives |
|---|---|---|
| `idle` | Doing nothing | Initial value · completion acknowledged (ack) · TTL expiry |
| `working` | Handling a prompt or running a tool | Claude Code/Codex start hook |
| `waiting` | Waiting for approval or input | Claude Code/Codex approval hook, or output pattern detection |
| `done` | Response or session finished | Claude Code/Codex completion hook |

## Claude Code and Codex — automatic via hooks

```bash
fsh hooks status      # check registration (also shown in fsh doctor)
fsh hooks install     # register/update (idempotent; keeps other hooks you added)
fsh hooks uninstall   # remove only the FarShell entries
fsh hooks dedupe      # remove FarShell hooks duplicated into ~/.codex/hooks.json (preview with --dry-run)
```

`./install.sh` runs this for you at install time. **Without registration, state badges and automatic
prompt-queue dispatch don't work. Claude Code completion alerts (TTS) need the hooks too.** And it
doesn't fail visibly; nothing just happens. If something doesn't show up, check here first.

In Claude Code's `~/.claude/settings.json`, six kinds are registered: tool start/end, completion,
session start, notification and approval request.
In Codex's `~/.codex/config.toml`, six kinds are registered: prompt/permission/tool/stop/session.
Both manage only FarShell entries and keep your existing hooks.

**If you clicked "Import Claude settings" in the Codex app**, Claude's FarShell hooks
(`agent_hook.sh … claude`) get copied into `~/.codex/hooks.json`. Codex reads both `config.toml` and
`hooks.json`, so each event arrives twice (one of them tagged `claude`) and the agent label goes
wrong. `fsh hooks status`/`fsh doctor` warn about this, and `fsh hooks dedupe` removes **only** the
`agent_hook.sh` entries from `hooks.json` (other hooks are kept, backup at `hooks.json.vtbak`,
idempotent). FarShell never writes to `hooks.json`.
Don't attach `tts_hook.sh` **directly** to `Stop`. `agent_hook.sh stop` already calls it internally,
so TTS would play twice.

## Other agents — report directly

Agents with no managed hooks, such as aider and agy, report from their own pane. Codex can use the
same command as a temporary fallback in environments where the hooks aren't installed.

```bash
fsh pane report --state working --agent codex
fsh pane report --state done
```

Run inside tmux, it also sends `$TMUX_PANE`, so it matches **exactly that pane**.
Outside tmux, or when the pane id isn't usable, it matches by the current directory (cwd). If more
than one session shares that directory, **nothing is shown**. That's safer than putting a badge on
the wrong session. The command's result message tells you which way it matched.

## How matching works (three steps)

1. **Pane self-report** — exact match of the `$TMUX_PANE` sent by the Claude hook/CLI against the server's pane list
2. **cwd match** — when step 1 has nothing. Only if there's exactly one candidate
3. **Give up** — highlight nothing

FarShell uses the isolated `-L fsh` socket. If you started an agent in your personal tmux (the
`default` socket), its pane id can collide by chance with another pane on our socket. So the pane id
is trusted only when the socket name in `$TMUX` is ours; otherwise it's discarded and matching falls
to step 2.

Codex hooks can receive a stale pane value from the shared app-server, so they use cwd instead of
the pane. They link only when the cwd matches exactly one session, and later events with the same
Codex `session_id` keep that link. Details in `docs/ref/agents.md`.

## When a state gets stuck

If a hook is lost (a network failure, for example), a state can stay stuck, so there's a TTL:
`working` 15 min → `idle`, `waiting` 2 min → `working`, `done` 30 min → `idle`.
Change the values with `VT_AGENT_TTL_WORKING` / `_WAITING` / `_DONE` (seconds).

## Delegation — an agent hands work to a child session (MCP)

An agent creates a child session with `spawn_session`, hands it work, and gets the result back as
`[Delegation result …]` input. The child session starts in the default permission mode, so approval
requests show up on your phone, and you can open that session from the rail and step in at any time.
A child session can't create further child sessions, and each parent can have at most 3 open child
sessions. To turn it off, set `VT_AGENT_SPAWN=0`.
List and close: `fsh delegate ls`, `fsh delegate close <id>` (the session is kept).

## Sharing a session with an agent (MCP)

An agent can't create or close sessions you made. (It makes its own child sessions through
"Delegation" above.) You create a plain session (an empty terminal), give it a nickname and
**share** it, and then the agent can use only that session through MCP
(`claude mcp add farshell -- fsh mcp`).

| Tool | What it does |
|---|---|
| `list_shared_sessions` | Shared sessions and my access (read/write) status |
| `read_session` | Reads the last lines of the screen (default 200, max 2000 lines · 64KB) |
| `send_to_session` | Types one line. Presses Enter only when `enter: true`. Only for sessions shared as read and write |

- The first use needs **your approval**. The agent gets "waiting for approval", and once you approve
  in FarShell it makes the same call again. Read and write are approved separately. Once approved,
  it stays valid as long as that agent is in the same tmux session (including across `/new`, `/clear`
  and restarts). It's gone when either session ends.
- Approving, sharing and revoking are done **only by a person signed in to the browser**. The machine token can't do it.
- A write request on a read-only share is denied without asking. Lowering the share mode takes effect immediately.
- Input is one line, at most 4000 characters; control characters and line breaks are rejected. Nothing
  is sent to a session whose agent CLI is waiting for approval (`waiting`) or working (`working`).
- Every access is logged to `~/.vt/agent_access.log` (no message text, only the character count and the first 8 characters of a hash).
- The server checks, through the pane and its parent process, that the calling agent is really
  running in that tmux pane. An agent run outside tmux can't be identified, so it's rejected.
