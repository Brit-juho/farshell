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
.venv/bin/python -m pip install -r requirements-core.txt
# Optional voice packages:
.venv/bin/python -m pip install -r requirements-voice.txt

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
detached checkout, `post_checkout` (pip when `requirements-core.txt` changed, `npm ci`/`npm run build`
when frontend inputs or `package-lock.json` changed or `frontend/dist` is missing), `dev` branch
advance, `sync`. Any failure before the branch advance returns to the saved commit/branch
(`~/.vt/update-prev.json`). `sync` reuses `claude_hooks.py`/`codex_hooks.py install` and links
`~/.claude/skills/fsh` to the repo copy; it never overwrites a non-link skill. The trust file is
outside the repo on purpose; do not add a key file to the repository. Tests: `server/tests/test_updater.py`.

## Validation

The shared required checks are listed in [AGENTS.md](../../AGENTS.md).
Browser smoke tests use Playwright from `requirements-dev.txt`:

```bash
.venv/bin/python -m pytest tests/e2e -v
```

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
committed; the guard existed for months in this repository before anyone noticed it was off.

The guard checks four things in what is being added (new file names and added lines):
forbidden paths, secret-shaped strings, this machine's user and home path, and **your own
words**. Local-only record documents are forbidden by name: everything under `docs/` except
`help/`, `ref/` and `guide/` (the local-only folder is `docs/local/`), `research_notes/`,
`reports/`, `docs/ref/*-plan.md`, `docs/ref/references.md`, and root `*_DESIGN.md`. Words
that must never appear in the repository (an organization, a profile, a device model) go in `~/.vt-guard-words`, one per line, `#` for comments. The file stays
outside the repository, matching is case-sensitive and whole-word, and the guard never
prints the word it matched. `VT_GUARD_WORDS` changes the file's location.

For audits, use `scripts/guard_personal.py range A..B` or
`scripts/guard_personal.py history`. Do not bypass the guard.
