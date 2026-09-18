# Worktrees and git

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Dock source control (2.1.1) | dock → "Source control". The 2.1.0 tab was read-only; stage/unstage/commit are now wired to `POST /api/git/{stage,unstage,commit}` (D16), and each file row gets a 「viewer」 button that opens the file as a pane leaf. **push and PR/MR are deliberately absent — not disabled buttons, not rendered at all (ADR-27).** FarShell runs your own Mac terminal, where you already push, and anyone juggling several accounts has already solved identity with per-repo git config (SSH `Host` aliases, `git config user.email`); there is no reason for FarShell to decide "which account" for you. Committing therefore runs under **ambient git credentials**. Server-side guards are the fsguard repo resolution, a repo-relative path check on every file, a commit-message length cap, and a 400 when nothing is staged |
| Git account store (N30/N31 — built but unused) | `~/.vt/git-accounts.json` + `~/.vt/git-bindings.json` (0600 + flock) plus a 15-minute **elevated session** (`POST /api/auth/elevate` re-verifies password + OTP and reissues the cookie with an `elev` claim; session-scoped, not device-scoped, so every tab and device elevates on its own). The account routes sit behind one `APIRouter(dependencies=[Depends(require_elevated)])` rather than per-handler checks, so a write path can't be added without the guard. PATs only — no OAuth, since taking a callback on a public tunnel is the more dangerous design. Tokens are never returned, only `auth.masked`, and `fsh git-account add` takes `--token-stdin` only (never argv). **This entire layer is currently wired to nothing (ADR-27)** — it was built for push/PR, that feature was then dropped, and it was kept rather than reverted because it is harmless on its own and 2.2 may reuse it for cross-account worktree automation. `fsh git-account` is the only way to reach it; no UI does |

## Worktrees (N8/N44, 2.1.1)

Left rail, or `fsh worktree` (which talks to `server/worktree.py` directly and works with no server running). Discovery walks the fsguard roots to depth 3, stops at the first `.git`, then reads `git worktree list --porcelain` per repo, annotating each entry with ahead/behind, a changed-file count, the tmux sessions whose pane cwd is inside it, its port band and its `origin` remote — 20-second cache, every path re-validated through fsguard.

**Depth is per root, not global (2.1.6).** A container-shaped directory name (`GitHub`, `projects`, `src`, `repos`, `work`, …) grants one extra level below it, so `~/GitHub/side_project/tools/farshell` is found even when the boundary is `$HOME`; everything else stays at depth 3. Raising the depth everywhere would walk `~/Library` and `~/.cache`, so the scan also skips a **scan-only** exclusion list (`worktree.SCAN_EXCLUDE_DIRS`: `Library`, `Downloads`, `Applications`, `go`, `.cargo`, …). That list is deliberately *not* `fsguard.EXCLUDE_DIRS` — the file browser shares that set, and hiding `~/Downloads` from the scan must not make it unbrowsable. The scan stops at 200 repos and reports `truncated: true`.

The `origin` remote (`remote: {host, owner, name}`) is read straight from `.git/config`, never via `git remote get-url` — one file read instead of one subprocess per repo. Credentials embedded in the URL (`https://user:token@host/…`) are stripped by the parser and fixed by a test. The rail prints it as `github/fornerds` on the row's second line and hashes the **owner** for the row's color bar, so repos of the same org share a color.

**Hiding repos from the rail (2.1.6).** `~/.vt/rail-repos.json` (0600 + flock) holds a list of hidden repo paths; `GET /api/worktrees` filters them out, `?include_hidden=1` returns them flagged for the settings sheet, and `POST /api/worktrees/hidden` toggles one. It lives on the server, not in device settings, because "is this my project?" is a judgement that should hold on every device — unlike rail group collapse, which is per device. Hiding a repo also hides its linked worktrees, and a path that no longer exists is ignored rather than cleaned up.

Creation is fixed at `~/.worktrees/<repo>/<name>` on branch `feat/<name>` and does four optional steps: node_modules (**symlink by default**, so a new worktree is usable without a reinstall), `.env` (`inherit` — only *existing* `PORT`/`VITE_PORT`/`DEV_PORT`/`NEXT_PUBLIC_PORT` lines are rewritten to the assigned band; missing keys are never added, and `.env` content is never returned by the API), a port band (5200, +100 per worktree, recorded in `~/.vt/worktrees.json` 0600 + flock), and an agent launched in a detached tmux session named `wt-<repo>-<branch>`.

**Every step after `git worktree add` rolls back on failure** (node_modules removed, then `git worktree remove --force` + `git branch -D`) so a half-built worktree never survives.

Delete refuses the main worktree outright and returns 409 `dirty:true` on any uncommitted change unless `force:true`; tmux sessions are only killed with `killSessions:true`, and the branch is never deleted.

Because a symlinked node_modules reflects the main repo's *current* tree rather than `base`, `GET /api/worktrees/precheck` hashes package.json + lockfile on both sides and raises a `lockfile_mismatch` warning before the create dialog.

`~/.worktrees` is auto-added to the fsguard roots when it exists — without that, every worktree created here would sit outside the browse boundary and be silently dropped from its own list
