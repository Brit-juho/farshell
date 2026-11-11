# Running, testing and installation

## Installation

Use the installer from the repository root:

```bash
./install.sh            # terminal only
./install.sh voice      # terminal and voice packages
fsh doctor
```

Source installation requires Node.js to build the frontend. Release tarballs include
built frontend assets. Speech models are downloaded separately through Settings;
see [platforms](../guide/platforms.md) for supported engines and operating systems.

### Manual installation

Use this path when the installer cannot be used or when supplying another Python
environment. Check `install.sh` for platform dependencies and the supported Python version.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r locks/core.txt
# Optional voice packages (install together with core so versions stay the same):
.venv/bin/python -m pip install --require-hashes -r locks/core.txt -r locks/voice.txt

npm ci
npm run build
mkdir -p ~/.local/bin
ln -sf "$(pwd)/bin/fsh" ~/.local/bin/fsh
```

Ensure `~/.local/bin` is on PATH. Save configuration through the shared writer,
which preserves existing keys and enforces mode 0600:

```bash
source lib/vt_env.sh
vt_env_set VT_DIR "$(pwd)"
vt_env_set VT_PYTHON "$(pwd)/.venv/bin/python"
vt_env_set VT_PORT 7777
```

For conda or pyenv, set `VT_PYTHON` to that environment's absolute Python path.
Install tmux and the selected tunnel provider using the platform's package manager
or official distribution. Then register agent hooks and verify:

```bash
fsh hooks install
fsh doctor
fsh password
fsh start
fsh mobile
```

Global skill registration is optional:

```bash
mkdir -p ~/.claude/skills/fsh
cp .claude/skills/fsh/SKILL.md ~/.claude/skills/fsh/SKILL.md
```

### Python dependency lock

Python packages install **only** from `locks/core.txt`, `locks/voice.txt` and `locks/dev.txt`
with `pip install --require-hashes` (`install.sh`, CI, `fsh update`). Each line pins an exact
version and carries the sha256 of every wheel and source archive of that version, so a file
that changed on PyPI or in transit makes pip stop instead of installing it. `install.sh`
turns that into a readable message; do not turn hash checking off to get past it.

- Inputs: `requirements-core.txt`, `requirements-voice.txt`, `requirements-dev.txt` (version
  ranges, edited by hand). They are not an install path.
- The locks are universal: one file covers macOS arm64/x86_64 and Linux x86_64/aarch64 on
  Python 3.11 or newer, with environment markers choosing the right pin and pip picking the
  matching wheel. `voice` and `dev` are resolved against `core`, so one package has one version;
  install `core` and `voice` in the same pip call.
- Regenerate after changing a requirements file, or to take newer versions (needs
  [`uv`](https://docs.astral.sh/uv/) on the developer's machine only; users and CI never need it):

```bash
scripts/lock_python.sh                          # keep current pins, apply requirement changes
scripts/lock_python.sh --upgrade                # take the newest of everything
scripts/lock_python.sh --upgrade-package httpx  # one package
python3 scripts/check_python_lock.py            # offline check (also run by scripts/lock_python.sh and CI)
```

  Commit the changed `locks/` together with the requirements change. CI fails when a
  requirement is missing from its lock or no longer satisfied, or when a lock line has no hash.
  Dependabot version-update PRs for pip stay off (`.github/dependabot.yml`); security
  advisories are handled by running `scripts/lock_python.sh --upgrade-package <name>`.
- A package with no wheel for a platform is built from its source archive (hash still checked):
  `cryptography` has no Intel-Mac wheel in current releases (needs a Rust toolchain), and
  `evdev` (voice on Linux) needs a C compiler.
- The `pip` inside the venv is upgraded by `install.sh` before the locked install and is
  not itself hash-locked.

## Running the server

`./run_server.sh` runs the backend in the foreground using the configured Python.
Use `fsh start` for managed operation and `fsh restart` to apply changes while
preserving the public tunnel URL. See `fsh help access` for network scopes.

| Environment | Access |
|---|---|
| Desktop | `http://localhost:7777` |
| Same network | Start with `--network lan`, then use the server's LAN IP and port |
| Android over adb | `adb reverse tcp:7777 tcp:7777`, then `http://localhost:7777` |
| Remote | Set authentication, run `fsh start`, then obtain the URL with `fsh mobile` |

Port 7777 is the default; use your configured port if different.

## Updating an install

`fsh update` (`server/updater.py`; user guide `fsh help update`) moves a git install by
channel: `stable` = newest `vX.Y.Z` tag whose SSH signature matches `~/.vt/update-signers`
(also: tag name == signed name, commit in `origin/master` history), `dev` = fast-forward to
`origin/dev`, `devmode` = never moves. Order: clean-tree check, fetch, resolve + verify,
detached checkout, `post_checkout` (pip `--require-hashes` from `locks/core.txt` when it changed, plus `locks/voice.txt` when voice is installed; `npm run build`
when frontend inputs changed or `frontend/dist` is missing, preceded by `npm ci` when
`package-lock.json` changed or `node_modules` is missing), `dev` branch
advance, `sync`. Any failure before the branch advance returns to the saved commit/branch
(`~/.vt/update-prev.json`). `sync` reuses `claude_hooks.py`/`codex_hooks.py install` and links
`~/.claude/skills/fsh` to the repo copy; it never overwrites a non-link skill. The trust file is
outside the repo on purpose; do not add a key file to the repository. Tests: `server/tests/test_updater.py`.
Anything that changes the checkout (`fsh update`, `--rollback`, the automatic apply) first takes
`$VT_STATE_DIR/update.lock` (`updater.update_lock`, flock, no waiting: the second one is refused).

### Update check and automatic update

`server/update_check.py` (user guide `fsh help update`) runs inside the server (`main.py` lifespan task) and reuses
`updater.py` for every judgement — `resolve_target`/`verify_tag` (signature, signed name == tag name,
`origin/master` history), `cmd_update` (checkout, `post_checkout`, restore, `sync`). It adds scheduling, a status
file and a policy; it does not re-implement verification.

**Check (read-only).** One tick every 5 minutes does local work only. The first network check runs 1–10 minutes
(random) after start, then every 24 h: `updater.fetch` (a `git fetch --tags origin`) under `updater.hardened()` —
git runs through `resilience.proc.run` (own session, group SIGKILL at the deadline), the call goes through
`pools.run("net", …, timeout=150)` and a `TimedLock`, so a hung remote cannot hold the server. No GitHub API, token
or user identifier is sent. The result is `$VT_STATE_DIR/update-status.json` (atomic write under
`update-status.lock`; a corrupt file is moved to `.corrupt` and rebuilt). `update.check` (default on, in
`update-settings.json`) or `VT_UPDATE_CHECK=0` turns the whole thing off: no request, the manual check answers
`409`. Stable compares the newest verified `vX.Y.Z` with `VERSION` (semver); dev compares `origin/dev` with `HEAD`;
devmode / no channel / dirty tree show the tag but are marked not applicable (`blocked`). `current` is re-read from
`VERSION` when the server starts and on every `GET`, so the line disappears right after an update.

**Automatic apply (`update.auto`: `off` default, `stable-only`).** `auto_blockers()` returns the reason not to
apply, and `apply_main()` runs it again under the lock right before moving: stored channel is `stable` (dev, devmode,
unset never), branch is `master` or detached, no tracked changes, a trusted key is pinned (TOFU stays a human act,
`fsh update trust`), `origin/master` exists, signature + history verify, and the major version does not increase.
Timing: local hour == `update.auto_hour` (default 4), no human terminal input for `VT_UPDATE_IDLE_MIN` (15) minutes
(`pane_input.last_human_input_age`) and no `working`/`waiting` agent (`agent_status`). Otherwise wait for the next
tick; after 7 days since the update was first seen it stops (`auto.reason: stalled`); after 3 attempts for one
version it stops (`gave_up`). An idle tab left open does **not** block an update — only recent input does.
The server never overwrites its own code: it spawns `python -m update_check apply --to vX.Y.Z` in its own session
(output in `update-auto.log`), which calls `updater.cmd_update` (failure restores the original commit; the result goes
to `last_apply`). When the child reports `ok`, the server broadcasts `update_restarting` on `/ws-workspace`, waits 2 s
and starts a detached `bin/fsh restart` (the tunnel URL is kept; tmux sessions live in the tmux server and survive).

**Threat model.** The feature runs code that came over the network, so it is off by default and bounded by the
trust file outside the repository: a compromised repository cannot add a signer, a replayed signed tag is refused
by the name check, an unsigned or foreign-signed tag is never applied, a major jump or a local edit stops it, and
a build failure rolls back. It cannot protect against a compromised signing key (the trusted one). The switches
(`PUT /api/update/settings`) need a browser login session **and** elevation, are audited as `update.settings`
(keys and new values only), and are refused for agents, peers, the machine token and relayed calls (the routes
are not in `relay_policy.py`). `fsh update auto` is the local-user path (same trust as editing the file).
To turn it off: Settings > Update, `fsh update auto off`, or `VT_UPDATE_CHECK=0` for everything.
Tests: `server/tests/test_update_check.py` (fake bare remote + test SSH key; stable/dev/devmode, bad signature,
major, dirty, no trusted key, schedule with a fake clock, window/idle/7-day/3-attempt, lock, apply + rollback),
`test_routes_update.py` (authorization, elevation, audit), `frontend/tests/{titlebar-update,update-settings}.test.js`.

## Validation

The shared required checks are listed in [AGENTS.md](../../AGENTS.md).
Browser smoke tests use Playwright from `requirements-dev.txt`:

```bash
.venv/bin/python -m pytest tests/e2e -v
```

Run them from a checkout whose path has no `.claude` component. `fsguard` denies `.claude` directories, so the
file-viewer, image and source-control dock tests time out waiting for content when the repository sits under
`.claude/worktrees/…` (the viewer answers 403 `denied`). Copy the tree to a plain path with its own `git init`
(the source-control tests read the working tree's `git status`) and link `node_modules` and `.venv` into it.

For manual verification, create a disposable session, exercise terminal input,
rename and file upload, then check scrollback after refresh and reconnection.
Delete the disposable session **after** completing those operations. Test voice
only when its packages and a model are installed. Consult [API.md](../../API.md)
for request shapes and authentication rather than copying an independent API recipe.

### Isolated test servers

Never point a test server at the user's live configuration or state. Set separate
`VT_CONFIG`, `VT_WORKSPACE_PATH`, `VT_TMUX_SOCKET`, `VT_STATE_DIR`, `VT_RUN_DIR`
and `VT_PORT`, unset `VT_TOKEN`, and use `VT_TUNNEL_PROVIDER=none`. Do not inherit
production credentials. A complete reproduction setup is in
[session-view-measurements.md](./session-view-measurements.md#6-reproduction-isolated-server).

### Mobile testing (adb)

```bash
adb reverse tcp:7777 tcp:7777
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome
adb shell screencap -p /sdcard/test.png
adb pull /sdcard/test.png /tmp/test.png
```

Use the actual device dimensions for touch gestures. Verify the soft keyboard,
portrait/landscape layouts and reconnect behavior on the device.

### Restricted execution environments

File-watching integration tests use the watchfiles polling backend to check real
file changes independently of native watcher permissions. Path tests use a temporary
HOME. Tests requiring a real process table skip with a reason when process inspection
is blocked; such skips are not evidence that the feature passed.

## Storage locking

Disk stores use `server/store_lock.py`. Follow its module documentation: waits are
bounded (default five seconds), and lock files must never be deleted because replacing
the inode breaks mutual exclusion. Audit potential lock deletion with:

```bash
rg -n 'unlink|remove|rmtree' server -g '*.py'
```

Inspect each match's context; other file deletion is legitimate.

## Personal-value guard

Only shared project configuration belongs in Git. Personal paths, accounts, tokens,
keys, generated agent settings and local databases are excluded by `.gitignore`
and checked by `scripts/guard_personal.py` even when added with `git add -f`.
The guard reads the current user's identity at runtime and does not print secrets.

`install.sh` enables the hooks in a git clone (when `core.hooksPath` is unset), and
`fsh doctor` warns when they are off. To enable them by hand:

```bash
git config core.hooksPath scripts/githooks
```

Without the hooks nothing stops a personal value or a local-only document from being
committed, and nothing warns you that the guard is off.

The guard checks four things in what is being added (new file names and added lines):
forbidden paths, secret-shaped strings, this machine's user and home path, and **your own
words**. Local-only record documents are forbidden by name: everything under `docs/` except
`help/`, `ref/` and `guide/` (the local-only folder is `docs/local/`), `research_notes/`,
`reports/`, `docs/ref/*-plan.md`, `docs/ref/references.md`, `docs/ref/*reference-logic.md`, and root `*_DESIGN.md`. Words
that must never appear in the repository (an organization, a profile, a device model) go in `~/.vt-guard-words`, one per line, `#` for comments. The file stays
outside the repository, matching is case-sensitive and whole-word, and the guard never
prints the word it matched. `VT_GUARD_WORDS` changes the file's location.

For audits, use `scripts/guard_personal.py range A..B` or
`scripts/guard_personal.py history`. Do not bypass the guard.
