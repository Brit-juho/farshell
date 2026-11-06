# Reliability: hangs, deadlines, health and the server watchdog

Code: `server/resilience/`, `server/server_watchdog.py`, `server/routes/health.py`; frontend
`frontend/js/core/{api,poll,ws-watchdog,reachability}.js`. Endpoints: [`API.md`](../../API.md) «Misc».

## Why this exists

The failure this layer guards against: the repository scan holds `worktree._discover_lock` while
`os.scandir` hangs forever in `open()` on a network mount (for example an NFS-backed folder under the
home directory when `VT_BROWSE_ROOTS` covers it). Requests wait on that lock with no timeout inside
`asyncio.to_thread` and fill the default thread pool (`min(32, cores + 4)`). From then on every
`to_thread` call queues forever. The event loop stays healthy, so the following keep looking normal:
- unauthenticated 401s and `/`
- the open port
- `fsh status`

The login gate then spins on 「연결을 확인하는 중」 indefinitely. Any scandir blocking in `open()` has the
same root.

Each link in that chain has its own guard:

| Link | Guard |
|---|---|
| Walking into a network/virtual filesystem | `mounts` + `scan`: automatic scans never enter risky mounts; the walk runs in a child process with a deadline |
| Holding a lock across slow I/O, waiting with no deadline | `locks.TimedLock`; waiters use `hold(timeout)`; `worktree` serves the previous list (or 503) |
| One shared thread pool | `pools`: named pools with admission caps and deadlines; the default executor is instrumented |
| `subprocess.run(timeout=)` waits forever after the kill | `proc.run`: kills the process group, waits briefly, abandons |
| Blocking on the event loop | moved to pools; `server/tests/test_no_blocking_in_async.py` forbids the known blocking calls |
| Nobody noticing | `/api/health/ready`, `fsh status`/`doctor`, the server watchdog, stack traces |
| The browser waiting forever | request deadlines, the poll helper, the WebSocket silence watchdog |

## Rules for new code

1. Every wait has a deadline (locks, subprocesses, network, thread work).
2. No slow I/O while holding a lock. If unavoidable, use `TimedLock` and give waiters a deadline.
3. Work that can hang goes to a named pool:
   - `pools.run("fs"|"git"|"proc"|"net"|"slow", fn, …, timeout=)`
   - for user paths, `fsops.call(fn, …, path=)`, which adds the path breaker
   - routes map `fsops.UNAVAILABLE` to `fsops.unavailable_response` (503 `fs_unavailable`, `Retry-After`)
4. Nothing blocking runs on the event loop. When you find a new indirect blocker, add it to `BLOCKING` in
   `server/tests/test_no_blocking_in_async.py`.
5. Broadcast over WebSockets with `resilience.wsutil.broadcast`: concurrent sends, 2 s each, and a slow
   client is closed.

## Pieces

**`pools`.** Pool sizes: `fs` 4, `git` 6, `proc` 6, `net` 8, `slow` 3. `VT_POOL_<NAME>_SIZE` overrides a
size; `default` uses the stdlib formula.
- Admission is capped at size × 5. Beyond that the call raises `Busy` immediately instead of queueing
  behind a stuck pool.
- A deadline raises `OpTimeout` and frees the caller. The thread keeps running and is counted as
  `abandoned` until it returns; threads cannot be killed.
- A pool is `degraded` when half its workers are abandoned, or when all are busy and the oldest has run
  more than 60 s.
- `install_default_executor` swaps in an `InstrumentedExecutor`, so unnamed `to_thread` work shows up in the
  registry with the real function name.

**`registry`.** The ledger of running thread work: pool, op, key (usually a path), thread, age.

**`locks.TimedLock`.**
- Records the holder (thread, op, since), waiters, timeouts and the longest hold.
- `with lock:` uses `default_timeout`; with none it waits forever, as before.
- `hold(timeout)` raises `LockTimeout` with the holder's details.
- Used for `worktree.discover`, `stt.run`, `pane_input:<pane>`, `codex.plugin_list` and `usage.codex`.

**`proc.run`.**
- Starts the child in its own session and SIGKILLs the whole group at the deadline.
- Waits only `REAP_TIMEOUT` (2 s) after the kill. A child still not reaped (kernel wait, D/U state) is
  recorded and abandoned, then reaped on a later call.
- Used by `tmux_runner.run`, `worktree._git` and `routes/git._git`.

**`mounts`.** Reads the mount table without touching the filesystems it lists:
- macOS: `getfsstat(MNT_NOWAIT)` via ctypes, which per the man page is not delayed by unresponsive file
  systems
- Linux: `/proc/self/mountinfo`

Risky mounts are NFS, SMB, AFP, WebDAV, FUSE (not `fuseblk`), 9p, virtiofs, autofs and anything without
`MNT_LOCAL`, plus the macOS File Provider roots (`~/Library/CloudStorage`, `~/Library/Mobile Documents`).
Paths are classified by string prefix only.

**macOS permission identity (`FarShell.app`).** On macOS, `fsh start` launches the server — and a tmux server it creates — through a tiny launcher app at `$VT_STATE_DIR/app/FarShell.app` (`macos/farshell-launcher.c`, built and ad-hoc signed as `com.farshell.server` by `lib/macos_app.sh`). The launcher re-spawns itself with `responsibility_spawnattrs_setdisclaim`, so it becomes the TCC "responsible process" for everything below it: permission prompts name **FarShell** instead of `python3.14`, and a decision survives restarts, releases and Python upgrades. Measured 2026-11-06 with a no-prompt probe (`~/Library/Mail`): without the launcher tccd logged `responsible=python3-…`, with it `responsible=com.farshell.server-test`. The app lives outside the repository and is rebuilt only when the launcher source or `macos/Info.plist` changes (a rebuild changes the signature and macOS asks again). The launcher forwards signals (not ones that were ignored — `nohup`'s SIGHUP), passes the exit code through, and writes the real command's pid to `FSH_APP_PIDFILE`; `bin/fsh` stores that in `server.pid`, so the watchdog, `fsh stop` and SIGUSR1 dumps still address the Python server. A tmux server that was already running keeps its old responsibility until it is restarted. `VT_MACOS_APP=0` switches back to launching Python directly; without `cc`/`codesign` the launcher is skipped. `fsh doctor` reports the state (「macOS 권한 이름」). Tests: `server/tests/test_macos_app.py`.

**macOS protected locations.** On macOS the automatic scans (repository discovery, file search, session cwd lookups) also skip the locations macOS guards with a permission prompt: `~/Desktop`, `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Movies`, `~/Music`, all of `~/Library` (app data, Mail, iCloud) and `/Volumes`. Walking into them raised one prompt per category, several at once (2026-11-06). `VT_SCAN_INCLUDE_PROTECTED=1` puts them back (for a machine that granted Full Disk Access). A path a person opens in the file tab is not refused; macOS asks once, which is expected. A session cwd inside one of them reports `reason: protected`.

`VT_SCAN_INCLUDE_MOUNTS` puts risky mounts back into automatic scans: `1`/`all` for all of them, or a
colon list of paths. Paths a user opens explicitly are never refused for being remote; they get the
deadline and the breaker instead.

**`breaker`.**
- A path whose operation timed out is blocked for 5 min: the whole mount point if it is a risky mount,
  otherwise that path and everything below it. Blocked calls fail at once without taking a thread.
- After the 5 min one probe call is allowed. If it succeeds the block is cleared.

**`scan` / `scan_worker` / `quarantine`.**
- The repository scan (`git_entrypoints`) and the file-name search (`search`) walk in `python -I
  scan_worker.py`, a stdlib-only child process.
- Before each directory the worker reports `{"enter": path}`. On macOS it first switches off File Provider
  materialization (`setiopolicy_np`).
- At the deadline (`VT_SCAN_TIMEOUT`, 8 s for repositories, 10 s for search) the parent kills the worker.
  The last `enter` is quarantined for 6 h (`$VT_STATE_DIR/scan-quarantine.json`) and tripped in the breaker.
- Later scans skip risky mounts, quarantined paths and blocked paths.
- `fuzzy_score` lives in `scan_worker.py` and `routes/files.py` imports it.

**`watch`.**
- `SIGUSR1` appends every thread's Python stack to `$VT_RUN_DIR/stacks.log` (faulthandler). Use
  `kill -USR1 <server pid>`.
- A 1 s watcher thread records a stack once in `stacks.log` and logs one summary line in the server log
  when:
  - the event loop stops answering for `VT_WATCH_LOOP_STALL_SEC`: the full dump
  - thread work runs past `VT_WATCH_SLOW_OP_SEC`: that thread's stack
  - a `TimedLock` is held past `VT_WATCH_LOCK_HOLD_SEC`: the holder's stack

**`health`.**
- `/api/health/live` is open (listed in `AUTH_EXEMPT_PATHS`) and returns only `ok`/`pid`/`uptime`.
- `/api/health/ready` requires a cookie, the machine token or the unix socket (listed in `authz/sock.py`
  `ROUTES`). It probes every pool with a no-op under a 1.5 s deadline; no thread is used for the probe
  itself.
  - It is `unready` (503) when the default pool or the loop is stuck, or when two pools are unresponsive.
  - It is `degraded` (200) when anything else is wrong.
- The socket gate resolves the caller in the `proc` pool; resolving it in the default pool would hang the
  socket too when that pool is exhausted.

## Other places that follow the same rule

- **Hook intake.** `/api/agent/event` and `/api/agent/report` resolve the pane in `proc` (5 s). On
  timeout or saturation the session is left unresolved.
- **Status sweep.** `agent_status.sweep` never reads a screen on the event loop. On the loop the probe
  raises `_ProbeDeferred`, that entry keeps its state, and one thread sweep runs in `proc`.
- **PTY CLI detection.** Detection inside the PTY output callback answers with the cached value and
  refreshes in `proc`.
- **Session contexts.** `session_context` skips git for cwds on risky mounts (`reason: remote_mount`).
  Stripe locks wait 3 s; each cwd lookup runs in `git` with an 8 s deadline; at most 4 run at once
  server-wide.
- **Other routes.** These now resolve or touch paths off the loop:
  - file tab (tree, file, raw, search) and git routes
  - git binding, worktree hide and precheck
  - share import and snippets
  - queue list, tmux preview, open-on-Mac (osascript), the cwd check in tmux create
  - device settings and workspace, catalog host lookups

## Server watchdog

`server/server_watchdog.py` is started by `fsh start` (pid `server-watchdog`) and stopped by `fsh stop`
before the server. `VT_SERVER_WATCHDOG=0` turns it off. A lock file keeps it to one instance.

Every `VT_WATCHDOG_CHECK_SEC` (15 s) it reads `/api/health/ready`, over the socket first and otherwise over
TCP with the machine token:
- **Success:** 200 is healthy, including `degraded`. 401/403 means it could not authenticate, which is not a
  reason to restart.
- **Failure:** 503, a timeout, a refused connection, or a pid file whose process is gone.
- **No pid file:** someone stopped the server on purpose, so it does nothing.
- **After a restart:** checks are skipped for `VT_WATCHDOG_GRACE_SEC` (60 s).

After `VT_WATCHDOG_FAILS` (3) failures in a row:
1. It writes an incident bundle to `$VT_RUN_DIR/incidents/<time>/`, keeping the last 20:
   - `reason.txt`
   - `ready.json`
   - `stacks.txt` (it sends SIGUSR1 first)
   - `ps.txt` (`ps -M` on macOS, per-thread state with `wchan` on Linux)
   - `lsof.txt`, `mount.txt`, `nfsstat.txt`
   - `server-log-tail.txt`
2. It runs `fsh restart` in its own session; the tunnel URL is kept.
3. It allows at most `VT_WATCHDOG_RESTART_MAX` restarts per hour (3). Beyond that it only logs, because a
   restart will not fix a cause that is still there.

**Commands.**
- `fsh status`: shows the ready verdict next to the port, plus stuck work, locks and quarantine, the
  watchdog line and the latest incident.
- `fsh doctor`: adds the checks 서버 응답, 서버 워치독, 탐색 격리 and 장애 기록.
- `fsh watchdog status|check|unquarantine`

**launchd (macOS).** `fsh watchdog agent install` registers `com.farshell.server-watchdog`
(`--mode launchd`, `KeepAlive.SuccessfulExit=false`) so launchd supervises the watchdog itself.
- The launchd copy waits for the lock while an `fsh`-started copy runs, and takes over when that copy
  stops.
- `fsh stop` kills only the `--mode fsh` copy. The launchd copy idles because the pid file is gone.
- `fsh watchdog agent uninstall` removes it.

## Frontend

- **Request deadlines.** `vtFetch`/`apiFetch` time out at 15 s for GET and 30 s for writes; long calls such
  as voice, commit and worktree create get larger budgets. A timeout throws an error with `timeout: true`.
  `vtFetchConditional` (conditional GET for the file viewer, 304 is a success) shares the same deadline code.
- **Banner.** A timeout counts as a reachability failure and shows the `hung` banner, 「서버가 응답하지 않습니다」.
- **Login gate.** Probes and submits time out at 8 s and show a retry block. A timeout never hides the gate;
  a real network error still does, so the offline shell can work.
- **Polling.** `core/poll.js` drives every poller. It never overlaps a run, backs off up to 60 s on failure
  and pauses while the tab is hidden. HUD, Rail and Fleet show 「갱신 지연」 when the last success is older
  than max(2 × interval, 90 s).
- **WebSockets.** `core/ws-watchdog.js` closes a socket that received nothing for 45 s (code 4000), so the
  normal reconnect and overlay path runs. The server sends `{"type":"ping"}` every 15 s on `/ws/{id}`,
  `/ws-agent`, `/ws-workspace` and `/ws-fs-watch`.

## Tests

| File | What it covers |
|---|---|
| `test_resilience_core.py` | pools, locks, proc, mounts, breaker, broadcast |
| `test_resilience_incident.py` | the failure above: a stuck discovery lock while 30 callers return in time, the default pool stays free and the app keeps answering; scan timeout leads to quarantine; remote mounts are never entered |
| `test_resilience_watchdog.py` | verdicts, restart budget, incident bundle, health endpoints, the 503 for blocked paths |
| `test_no_blocking_in_async.py` | the static guard |
| `frontend/tests/{api-timeout,gate-timeout,poll-helper,poll-panel,ws-watchdog}.test.js` | the frontend pieces |
