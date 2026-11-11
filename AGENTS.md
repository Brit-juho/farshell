# AGENTS.md

Shared instructions for every AI coding agent working on FarShell (Claude Code, Codex, aider, …).
This is the only instruction file in the repository — there is no `CLAUDE.md`. Read the project overview
below and the relevant `docs/ref/` document before changing an area.

FarShell puts your Mac terminal on your phone: a tmux-backed web terminal reachable from
anywhere, with voice input, file transfer, a code viewer and agent-state awareness.
macOS / Linux (X11) / WSL2. Native Windows is not supported.

---

## Quick start

```bash
./install.sh            # terminal only (~50MB)
./install.sh voice      # terminal + voice mode (~1.5GB)

fsh start               # server + public entrance
fsh mobile              # access URL + QR
fsh status              # what is running
fsh doctor              # diagnose install/environment
```

`install.sh` creates the venv, symlinks `fsh`, writes `~/.vt.env` and registers the
Claude Code hooks. Manual/alternative installs: [`docs/ref/dev.md`](./docs/ref/dev.md).

| Where | URL |
|------|-----|
| Desktop | `http://localhost:7777` |
| Public | whatever `fsh status` reports for your tunnel provider |

---

## Document map

Use the documents below for detailed guidance.

| What | Document |
|------|----------|
| Every `fsh` command and option | [`CLI.md`](./CLI.md) |
| REST/WebSocket endpoints | [`API.md`](./API.md) |
| Server + frontend structure | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| UI rules, tokens, skins | [`DESIGN.md`](./DESIGN.md) |
| Cross-tool rules + **hard contracts** | this file (below) |
| Release history | [`CHANGELOG.md`](./CHANGELOG.md) |

---

## Area index

Read the relevant area document before making changes.

| Area | What lives there | Document |
|------|------------------|----------|
| Auth, devices, boundary values | Password login, device registration + OTP, one-time tickets, cross-site blocking, the machine token, and why config-file values beat stale environment variables | [`docs/ref/auth.md`](./docs/ref/auth.md) |
| Terminal, panes, shell surface | tmux sessions, split panes, rail + palette, scrollback, paste, kitty keys, themes, clipboard | [`docs/ref/terminal.md`](./docs/ref/terminal.md) |
| Agent state, queue, MCP | Claude Code hooks, `idle`/`working`/`waiting`/`done`, approval detection and its coverage, prompt queue, snippets, usage gauge, MCP servers | [`docs/ref/agents.md`](./docs/ref/agents.md) |
| Files, sharing, code viewer | Upload store, share links, the read-only viewer and `fsguard`'s three layers | [`docs/ref/files.md`](./docs/ref/files.md) |
| Tunnels, ports, remote access | `VT_TUNNEL_PROVIDER` (cloudflare/ngrok/none), extra ports, URL-change hook, zombie-reconnect watchdog, Tailscale, port dashboard | [`docs/ref/tunnel.md`](./docs/ref/tunnel.md) |
| Worktrees and git | Worktree creation/discovery, dock source control, the git account store (built but unused) | [`docs/ref/worktree.md`](./docs/ref/worktree.md) |
| Multi-host peering | Server-to-server pairing, remote panes, offline host layout | [`docs/ref/multihost.md`](./docs/ref/multihost.md) |
| Voice, clipboard, push | Voice daemon, hands-free and voice-only modes, clipboard daemon, Web Push | [`docs/ref/voice.md`](./docs/ref/voice.md) |
| Measurements: session/view rules, `waiting` release, phone | Per-CLI `waiting` kind results, release-path table, terminal-reply root cause, real-device (touch) findings, bundle-size trend, reproduction | [`docs/ref/session-view-measurements.md`](./docs/ref/session-view-measurements.md) |
| Reliability: hangs, health, watchdog | Named thread pools with deadlines, `TimedLock`, `proc.run`, remote-mount detection, scan quarantine, `/api/health/*`, the server watchdog and incident bundles, frontend request deadlines and the WS silence watchdog | [`docs/ref/reliability.md`](./docs/ref/reliability.md) |
| Keyboard input | The processing order of a keystroke, delivery target and settings, scopes and virtual input, paste, Android measurements | [`docs/ref/keyboard-input.md`](./docs/ref/keyboard-input.md) |
| Session list and work context | How the rail groups sessions and edits titles, and keeping work context consistent across CLIs | [`docs/ref/session-ui.md`](./docs/ref/session-ui.md) |
| Display language | `VT_LANG`, how the server picks ko/en, `t('한국어 원문')` keys, the English dictionary, glossary | [`docs/ref/i18n.md`](./docs/ref/i18n.md) |
| Running, testing, install | Running the server, mobile testing over adb, the E2E procedure, manual install | [`docs/ref/dev.md`](./docs/ref/dev.md) |

---

## Skills

| Command | Description |
|--------|------|
| `/fsh` | Global skill. Invokable from anywhere with phrases like "voice mode", "mobile access" |
| `/fsh-start` | Start server + tmux + the public entrance |
| `/fsh-mobile` | Mobile testing (adb port forwarding, Chrome, screenshots) |
| `/fsh-voice` | Install/run the Voice Daemon |

Project-provided skill sources live in `.claude/skills/`. The global copy of `fsh` at
`~/.claude/skills/fsh/SKILL.md` is a manual `cp` — `scripts/check_docs.py` warns when those
two drift.

---

## Validation

Run these checks before committing:

```bash
npm test
.venv/bin/python -m pytest
python3 scripts/check_docs.py --strict
python3 scripts/check_css_vars.py --strict   # needs `npm run build` first (reads frontend/dist)
python3 scripts/check_css_order.py
python3 scripts/check_i18n.py --strict
python3 scripts/check_python_lock.py
.venv/bin/python -m pytest tests/e2e         # browser tests; see "Working rules" for where to run it
```

`npm test` includes TypeScript checking; Vite builds do not. Put server tests in
`server/tests/`; root `tests/` is outside pytest's default test paths.
Run `npm run build` for frontend build changes and browser checks when UI behavior changes.
Verify affected behavior with actual responses, operations and logs.

## Project contracts

1. Preserve the fixed, unhashed build outputs — `app.js`, `core.js`, `app.css`, `voice.js` and the lazy
   chunks (`chunkFileNames: '[name].js'`) — and the two Vite build passes (`npm run build` = main + `VOICE_BUILD=1`).
   `index.html` and the service worker request them by name. The **initial payload (`app.js` + `core.js`) must stay
   within 300 KiB** (`frontend/tests/build-output.test.js`): put new code in a lazy chunk, not in `core.js`.
2. Keep path access checks in `server/fsguard.py`. Use resolved paths and
   `is_relative_to`, never string-prefix containment checks.
3. Preserve `OriginGuardMiddleware` protection for cross-site HTTP and WebSocket
   requests. Configure `VT_ALLOWED_ORIGINS` instead of adding exemptions.
4. Use the shared `#file-input` in `frontend/index.html` for uploads.
5. Update `~/.vt.env` through `lib/vt_env.sh` (`vt_env_load/get/set/unset`), never
   through `echo` or `sed`. Configuration must be parsed, not executed, and use mode 0600.
6. Read security boundary values from `~/.vt.env` before environment values.
   Keep `BOUNDARY_KEYS` aligned in Python and shell, and keep
   `apply_boundary_overrides()` above `import auth` in `server/main.py`.
   `VT_CONFIG` is not a boundary key.
7. Keep personal settings, credentials, real hostnames, domains, usernames, tailnet
   names and public IPs out of the repository. Machine configuration belongs in
   `~/.vt.env`; personal agent instructions belong in the user's global configuration.
   Respect the existing ignore rules and `scripts/guard_personal.py`; never bypass
   the guard with `FSH_GUARD_SKIP=1`.
8. Preserve compatibility with older `server/agent_hook.sh` payloads for
   `POST /api/agent/event`.
9. Define colors in `styles/theme/tokens.css` or `skins.css`; do not add hex literals
   in `frontend/js` or `styles/layers`.
10. Keep endpoint tables only in `API.md`. Other documents should link to it.
11. Every user-visible change ships in **two parts**: the Korean source and the English.
    - **Strings:** wrap them in `t('한국어 원문')` (frontend, `frontend/js/core/i18n.js`), `tr()`
      (server) or `_t` (shell) and add the English entry to `frontend/js/locales/en.js`. The Korean
      source text is the key. `lib/locales/en.sh` is generated by `python3 scripts/gen_cli_i18n.py`;
      never edit it by hand.
    - **Docs:** user-facing docs exist as a pair and change together in the same commit —
      `README.md`/`README.ko.md`, `CLI.md`/`CLI.ko.md`, `docs/guide/*.md`/`*.ko.md`, and
      `docs/help/*.md` (Korean) with `docs/help/en/*.md` (English). `docs/ref/` is agent-facing and
      is not paired.
    - **Done means** `python3 scripts/check_i18n.py --strict` passes and the change was seen in
      English (`VT_LANG=en`) as well as in Korean. A string added without its English entry is
      unfinished work. See [`docs/ref/i18n.md`](./docs/ref/i18n.md).
12. Python dependencies install only from the hash-locked files in `locks/` with
    `pip install --require-hashes` (`install.sh`, CI, `fsh update`). `requirements-*.txt` are
    the lock's inputs, not an install path. After changing them run `scripts/lock_python.sh` and
    commit `locks/`; never relax or drop `--require-hashes` to get an install through. See
    [`docs/ref/dev.md`](./docs/ref/dev.md) "Python dependency lock".
13. Only `docs/help/`, `docs/ref/` and `docs/guide/` are tracked under `docs/`.
    New `fsh help` topics need both a `bin/fsh` entry and a `docs/help/` file.
14. `docs/ref/` describes **current behavior** only. Research notes, notes on other products,
    plans, reports and design drafts stay local (`docs/local/`, or anywhere
    `.gitignore` already excludes) — never under `docs/ref/` or the repository root. The folder
    is allowed as a whole, so `.gitignore` alone does not stop a file placed there;
    `scripts/guard_personal.py` blocks such new files by name. Do not add examples, fixtures or
    comments with real account, organization, device or path names; use neutral values
    (`alice`, `acme`, `sample-repo`).
15. The app document (`GET /`) is served with `server/security_headers.py` `APP_CSP`, which includes
    `script-src 'self'`. Do not add inline `<script>`, `on*=` handlers, `javascript:` URLs, `eval`/`new Function`
    or Blob/data script modules (AudioWorklet included); put code in a same-origin file under `/static/`, use
    `data-action` + `registerAction`, and keep the policy string in that one place. Verify any change that
    could add one in a real browser against an isolated server (console must show no CSP violation).
    `style-src` still allows inline styles. Details: [`docs/ref/auth.md`](./docs/ref/auth.md) «Security headers».
16. The server never writes agent configuration files automatically — it only checks them. `server/hook_health.py` reads
    `~/.claude/settings.json` and `~/.codex/config.toml` (read-only plan functions) and reports; repair is always the user's
    `fsh hooks install`/`fsh sync`. Do not add an auto-repair: it races with the user, gstack and clauth, gives a remotely
    reachable process write access to agent settings, and resurrects hooks the user removed on purpose.
    **The one exception is FarShell's own managed profile folders** (`~/.vt/agent-profiles/<id>/`), never the global
    `~/.claude`/`~/.codex` (those are only read). Two person-initiated paths write there: the explicit
    `fsh profile sync-settings` / the managed launch (`fsh claude <profile>`), and creating a new session with an account
    a person chose (`session_profile.sync_settings`), which places **only the usage statusLine wrapper** in that
    profile's `settings.json` and, for FarShell's own hooks already in that profile, refreshes their script path to the
    current checkout (`agent_profile_settings.sync(..., statusline_only=True)` -> `claude_hooks.refresh_installed`: it
    never adds a hook, never touches other people's hooks, MCP definitions or `.claude.json`). A failure is logged without values or paths and never blocks the session.
    Do not widen the automatic call: a global hook or MCP definition (with its keys) must never spread to every
    profile without a person running the sync. Details: `docs/ref/agents.md` «Account choice for new sessions».
17. **Automatic update runs network code — keep it bounded.** `update.auto` is off by default and only ever applies
    a signature-verified `stable` release tag (`updater.resolve_target`, trust file outside the repo, `origin/master`
    history), never on the dev channel, devmode, an unset channel, a tree with local changes, a major-version jump or
    without a pinned key (trust is a human act: `fsh update trust`). Changing `update.check`/`update.auto`/the hour
    needs a browser session **plus elevation**, is audited, and is refused for agents, peers, the machine token and
    relayed calls (keep `/api/update/*` out of `relay_policy.py` and the socket allowlist). The server applies it
    through a detached child under `update.lock`, never in-process. See [`docs/ref/dev.md`](./docs/ref/dev.md)
    «Update check and automatic update».

## Branches and releases

- `master` holds **released code only**. Its tip is always the latest release commit, which the
  `vX.Y.Z` tag points at. Do not commit feature work to it.
- `dev` is where development accumulates until the next version is decided. Do the work on a
  short-lived `feat/<topic>` (or `fix/<topic>`) branch cut from `dev`, and merge it back with
  **`git merge --no-ff`** so every feature leaves one merge commit (`merge: feat/<topic> — summary`)
  that groups its commits. Do not commit feature work straight to `dev`, and do not squash or
  fast-forward a feature in. `CHANGELOG.md` keeps the `[Unreleased]` section and `VERSION` stays at
  the last released version. Delete the feature branch after it is merged.
- `release/X.Y` preserves the X.Y line at its release tag (`release/2.3` is exactly `v2.3.0`).
  It exists for urgent fixes to that version: commit the fix there, tag `vX.Y.(Z+1)`, then
  merge it into `dev` (also with `--no-ff`) so it is not lost.
- To release: run **`python3 scripts/release.py X.Y.Z`** (`--dry-run` first if unsure). It releases
  the tip of `origin/dev` **only if that commit's CI (`ci.yml`) already succeeded** — it never waits
  for a new CI run, and it stops at once if CI is running or red (fix CI first; that is not release
  time). It builds the release commit (`VERSION`, the two README badges, `[Unreleased]` →
  `[X.Y.Z] — date`, `chore(release): vX.Y.Z`) in a temporary worktree and checks it changed only those
  four files, signs the annotated tag `FarShell vX.Y.Z` with SSH and verifies it against
  `~/.vt/update-signers` (`fsh update --channel stable` refuses unsigned tags, `docs/ref/dev.md`),
  pushes `dev`, `master` (fast-forward), `release/X.Y` and the tag in **one atomic push**, then waits for
  the `Release` workflow and checks the GitHub Release assets. Target: under 5 minutes end to end; it
  prints per-step times. If the tip of `origin/dev` is already the `chore(release): vX.Y.Z` commit (an
  earlier run stopped before tagging), it resumes without a new commit. `ci.yml` skips pushes whose
  head commit is a release commit, and `release.yml` refuses a tag unless the tagged commit — or, for a
  metadata-only release commit, its parent — has a successful `ci.yml` run. Pushing a tag publishes a
  public release — confirm with the user first.
- Never force-push `master`, `dev` or a `release/*` branch; a push must only move the tip forward.

## Working rules

- Preserve unrelated changes. Do not use `git stash`; stage only the intended changes.
- Confirm design direction and policy changes with the user.
- Instruction files: this `AGENTS.md` is the only shared one. A developer's own working rules go in the untracked
  `.claude/AGENTS.md` (git-ignored; Claude Code reads it together with this file). Never create `CLAUDE.md`,
  `.claude/CLAUDE.md` or `CLAUDE.local.md`: while any of them exists Claude Code stops reading `AGENTS.md`.
  Check what a session loaded with `/context all`.
- Isolated test servers must set `VT_CONFIG`, `VT_WORKSPACE_PATH`, `VT_STATE_DIR` and `VT_TMUX_SOCKET` (all under a
  temporary directory, with `HOME` there too), set `VT_UPDATE_CHECK=0`, and unset `VT_TOKEN`, to avoid accessing the
  user's runtime state. Without `VT_STATE_DIR` and `VT_UPDATE_CHECK=0` the background update check does a
  `git fetch` and writes its status into the real state directory.
- Run browser (e2e) tests from a checkout whose path has no `.claude` component: `fsguard` denies `.claude`
  directories, so the file-viewer tests fail under `.claude/worktrees/…`. Copy the tree elsewhere (with its own
  `git init`) if you work in such a worktree.
- Build: `npm run build` writes the git-ignored `frontend/dist`; Python dependencies install only from `locks/`
  (contract 12). Plans, reports and superseded documents live in the untracked `docs/local/` (archived under
  `docs/local/archive/`) and are never the current standard — `docs/ref/` is.
- Apply changes to a running instance with `fsh restart`. Do not use
  `fsh stop && fsh start`: stopping the tunnel changes its public URL.
- Use Korean commit messages with `type: summary` and a body explaining why.
