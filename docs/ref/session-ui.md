# Session UI and work-context design

The contract for how the session list groups sessions and edits titles, and for keeping work context consistent across CLIs.

## Design constraints

- The rail lists all sessions by name, and a group title is edited only in its header. The title is not repeated under each member.
- The cwd in the tmux list is that of the active window/pane, not the first pane that appears.
- Branch and change counts are collected from one active location at one point in time. Results of a separate repository search or a cwd cache are never mixed in, and the design assumes that the `@fsh_wt` membership at creation time can differ from the current location.
- A failed lookup is never turned into empty information, and a late HTTP response never overwrites a newer push.

## Data contract

Session identity (the tmux name), the user-set view title, the current work location and the agent state are four different values.

1. The work location is read from the **active pane of the active window**. If the agent CLI reported a cwd through a hook, that is used (`agent_cwd_for`); if there is none or it can't be read, the pane cwd is used. No worktree tag is written when a session is created.
2. The server supplies that cwd's repository root, branch and change counts together as a `context` snapshot. REST and the workspace push use the same collection function. The response contract lives in [API.md](../../API.md).
3. Lookup/subscription of the pane work info is lazy-loaded with the shell UI chunk so it stays out of the initial boot size. The rail, Fleet, mobile session management, the selection card/palette and the pane header use this snapshot. The directory is shown as the Git workspace kind (`Main` (메인) / `WT`), the workspace root name and a relative path. A non-repository or an old-version response shows the folder name. The full path goes in the tooltip and in the workspace detail view. A real Git repository without a branch shows `detached HEAD` (분리된 HEAD).
4. `ready`, `nonrepo` and `unavailable` are distinguished. On failure only the last success **for the same cwd** is kept and `Waiting for update` (정보 갱신 대기) is shown. A previous branch or change count is never attached to a new cwd. When a boundary check rejects the cwd, the previous values are removed too.
5. Git lookups are merged per cwd and run at most 4 at a time. A success is cached for 10 seconds, a failure is retried after 2 seconds, and the cache holds at most 256 entries. I/O runs in a thread, and the path check and the Git root re-check are delegated to `fsguard`.
6. While a browser is visible, tmux is watched every 5 seconds by default, and a push is sent only when something changed. So an external `cd` / branch switch / edit shows up on a healthy connection by the next watch tick, and within about 15 seconds at most once the Git cache expiry is included. The watch pauses while the screen is hidden.
7. An older HTTP list response that completes after a newer push was received is discarded. A list from a server without `context` is shown with the rail fallback.

An agent's kind/state keeps the verified-pane or unique-cwd attribution rule. Differences in event capability between Claude, Codex and agy are not propagated into the work-location/Git display. When there is no basis for telling several Codex sessions in the same cwd apart, none is attributed arbitrarily.

## Session names

`server/session_names.py` is the one place that names a new tmux session; every create path uses it — `POST /api/tmux/create` (with or without `unique_name`), opening a worktree or an agent in one, catalog resume, a peer's create, and a delegated child (`<parent>-sub`, `-2`, `-3`). The base is the start folder's repository name (a linked worktree's own folder name inside one), else the folder name, `home` for the home folder; never the server's folder. A taken name gets `-2`, `-3` … (`base-<4 hex>` after 99), checked with tmux exact matching (`=name`); paths that create directly retry with the next number when tmux answers "duplicate session", so a name taken by `bin/fsh` or a hand-started tmux in between is not fatal. The server does not rename a session after creating it. Later references to a session use its key (`#{session_id}:#{session_created}`, `tmux_runner.live_session_keys`) rather than its name where a name could be reused or changed: prompt-queue targets, the voice target lock file (second line), conversation binding in the catalog (the entry's tmux id), and layout restore (name together with the host). A rename made inside tmux is followed by the web session records (`deps._follow_rename`). Not covered: `bin/fsh`'s `dev` (a user contract) and managed-profile sessions `profile-<hex>` (the name is their key). Tests: `server/tests/test_session_names.py`.

## Groups and titles

A group is derived from the current view's pane tree. A closed group uses what the v5 layout remembers. No new group store and no tmux group tag is created.

- The all-sessions area of the rail/Fleet: open groups are gathered under their title in view order, with members in pane order. A closed group is also gathered under one title, and the remaining single sessions are sorted by name. The needs-attention area uses the waiting list as is.
- The header is a small title plus the session count, with no heavy vertical rule or button border. On desktop the edit button shows when the pointer is over the header or it has keyboard focus; on touch it is always shown.
- The title is not repeated for each member. Both open and closed groups are edited with the header's **Edit** (편집) button. The title is 1–128 characters after trimming whitespace and is stored independently of the session name and branch.
- Pressing a group header or a closed member opens the group together. Pressing an open member moves to that pane. Open-together, close and full end are distinct; groups are stored per browser tab, and the group memory is kept even when a save fails.
- The mobile Sessions (세션) tab (Fleet) also provides title editing for open/closed groups and the work location/branch.
- The rail/Fleet have neither a window icon meaning "open on the current screen" nor main/separate-worktree SVGs. They do have the status icon, the agent identity, shared read/write and attached-screen markers, and the location is shown as current-directory and branch text.

## Regression tests

- The server `context` snapshot (subpath, detached HEAD, non-repository, keeping and recovering the last success for the same cwd, boundary rejection, duplicate lookups for the same cwd, worktree kind detection): `server/tests/test_session_context.py`
- Agent cwd first, terminal cwd as fallback: `server/tests/test_tmux_agent_context.py`
- A single collection source for REST and push, push only on change, watch stopped for a hidden screen: `server/tests/test_sessions_push.py`
- Work-location display (worktree kind, old-version response, waiting for update, agent-based tooltip): `frontend/tests/session-context.test.js`
- Group order, view editing and moving: `frontend/tests/session-order.test.js`, `frontend/tests/session-view-edit.test.js`, `frontend/tests/session-view-move.test.js`

## Worktree display follow-up

Following the worktree display design, the kind is determined from the current cwd's Git directory / common directory. It is independent of FarShell registration, creation tags and the CLI kind. Failing to determine the kind is distinguished from the whole Git status failing. The work-location view is offered from the rail/Fleet menu and from the metadata button on mobile and the selection card.

**The reference location is the agent CLI, not the terminal.** Location, branch and change counts are read from the `cwd` that the session's Claude/Codex last reported through a hook (`agent_status.agent_cwd_for`). That is because even when the CLI moves into a separate worktree internally, the pane's cwd (the shell) stays in the original repository. It falls back to the pane cwd when there is no agent, when several entries have different cwds, or when that cwd can't be read (outside the boundary, deleted). When the two locations differ, the response's `contextSource` is `agent`, and the terminal location shows only in the location tooltip of rail/Fleet rows and in `Terminal location` (터미널 위치) of the `Workspace` (작업 위치) dialog. It refreshes when a hook arrives (on every tool call), and if the agent sits idle the last location is kept. Subagent events do not change the CLI's own cwd.
