# AGENTS.md

Instructions for AI coding agents working in this repository.
Follows the [agents.md](https://agents.md) convention — read by Codex, opencode, Amp,
Cursor, Jules and others. Claude Code reads `CLAUDE.md`; Gemini CLI reads `GEMINI.md`.
**All three point here or to `CLAUDE.md` — there is one source of truth per topic.**

## Read first

| Topic | File |
|---|---|
| Project overview, install, features, architecture | **[`CLAUDE.md`](./CLAUDE.md)** (Korean: `CLAUDE.ko.md`) |
| REST/WebSocket endpoints — **the single source of truth** | [`API.md`](./API.md) |
| Module map, 3-plane model, data flow | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |
| CLI subcommands | [`CLI.md`](./CLI.md) |
| Visual rules, tokens, skins | [`DESIGN.md`](./DESIGN.md) |

`CLAUDE.md` is written for any agent, not just Claude. Read it before your first edit.

## Commands

```bash
npm test                      # frontend, node:test + jsdom (283 tests)
npm run build                 # Vite, two separate passes — see vite.config.js header
.venv/bin/python -m pytest    # server (498 tests), testpaths = server/tests
pytest tests/e2e -v           # real-browser smoke, needs playwright (requirements-dev.txt)
python3 scripts/check_docs.py --strict      # CI gate: routes ↔ API.md, fsh help ↔ docs/help/
python3 scripts/check_css_vars.py --strict  # CI gate: no undefined CSS vars
./run_server.sh               # start the server locally
fsh status                    # runtime state (server, tunnel, tmux, voice)
```

Run all four checks before committing. CI runs the same ones.

## Contracts you must not break

These are load-bearing. Each one caused a real incident.

1. **`frontend/sw.js` PRECACHE hardcodes output filenames** (`app.js`, `app.css`, `voice.js`).
   That is why `vite.config.js` uses library mode with no content hashes, built in two
   separate Rollup passes. Do not add hashes, rename outputs, or merge the passes —
   offline PWA support depends on it.
2. **`server/fsguard.py` is the only place path access is checked.** Fixed roots +
   `Path.resolve()` + `is_relative_to` + a denylist. Never add a second check elsewhere,
   and never use `startswith` for path containment.
3. **`OriginGuardMiddleware` (`server/main.py`) returns 403 for cross-site HTTP and WS.**
   It is the last line of defense when a browser already holds a valid cookie. Do not add
   exemptions; widen `VT_ALLOWED_ORIGINS` instead if you truly need to.
4. **`#file-input` in `frontend/index.html` is shared by every upload trigger.**
   Never create a second one.
5. **Never write `~/.vt.env` with `echo`/`sed`.** Use `lib/vt_env.sh`
   (`vt_env_load/get/set/unset`) — it parses rather than sources, so the config file is
   never executed, and it enforces 0600.
6. **The agent hook payload format (`POST /api/agent/event`) is a compatibility surface.**
   Remote hosts may run an older `server/agent_hook.sh`.
7. **Colors go through tokens.** No hex literals in `frontend/js` or `styles/layers`.
   Define in `styles/theme/tokens.css` / `skins.css`; `check_css_vars.py` enforces this.
8. **`API.md` is the only endpoint table.** `CLAUDE.md` keeps a category list only —
   the two drifted apart in 2026-08 and `check_docs.py` now enforces the split.
9. **`docs/*` is gitignored** except `docs/help/`, which `fsh help <topic>` reads at
   runtime. Add a help topic and you must add both the `bin/fsh` entry and the file.

## Working rules

- **Verify with real behavior**, not mocks: response codes, actual CRUD, logs. Include the
  evidence in your report. Re-check what your change affects, not just what you edited.
- **This working tree is shared** with other tmux sessions. Never `git stash`. If unrelated
  changes are present, use partial staging to keep them out of your commit.
- **Ask before deciding.** Design direction and policy choices are not yours to guess.
- Isolated test servers need `VT_CONFIG`, `VT_WORKSPACE_PATH`, `VT_TMUX_SOCKET` and
  `env -u VT_TOKEN` — otherwise they write to the real user's state and every API returns 401.
- Commit messages are Korean, `type: summary` plus a body explaining *why*. See `git log`.

## Current work

`docs/plan-2.1/` holds the 2.1 design set (gitignored, local only). If it exists, read
`docs/plan-2.1/README.md` before starting feature work, and follow
`docs/plan-2.1/90-verification.md` §4.
