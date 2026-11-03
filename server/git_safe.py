"""Hardened argv/env for every git call the server makes on a user path.

A pane cwd, a project folder or a repo picked in the UI can hold an attacker's repository. git reads
that repository's config, and some keys run programs: `core.fsmonitor` runs on `status`/`diff`, and a
bare repository committed *inside* a normal one is discovered as the repository when you `cd` into it,
so its config (fsmonitor included) is attacker-written even after an ordinary `git clone`. The rail
badge calls `git status` for the pane's cwd on its own, so without these flags merely `cd`-ing into
such a folder ran a command as the FarShell server.

- `safe.bareRepository=explicit` — never adopt a bare repository found by discovery (git >= 2.38;
  older git ignores the key). Only honoured from protected config, which `-c` is.
- `core.fsmonitor=false` — no fsmonitor hook process.
- `core.hooksPath=/dev/null` (read-only calls only) — hooks never run for calls the user did not ask for.
  Calls that carry out a user action (commit, worktree add/remove) keep the repository's hooks, as the
  git CLI would.
- `GIT_OPTIONAL_LOCKS=0` — background `status` does not take index.lock and race the user's own git.
- `GIT_TERMINAL_PROMPT=0` — never wait on a credential prompt.

Diff-producing commands also need `DIFF_FLAGS` (no external diff / textconv programs).

Not covered: a repository whose own `.git/config` the attacker can write (an unpacked archive with a
`.git`, a sandboxed agent allowed to write `.git/config`) can still define `filter.<x>.clean`, which
`status` runs for files whose stat data changed. git has no switch that disables filters generically.
"""
from __future__ import annotations

import os

_COMMON = ("-c", "safe.bareRepository=explicit", "-c", "core.fsmonitor=false")
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")

# For diff/show/log -p: never run diff.external or a textconv driver the repository names.
DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")


def argv(*args: str, cwd: str | os.PathLike | None = None, hooks: bool = False,
         readable_paths: bool = False) -> list[str]:
    """`git [-C cwd] <hardening> <args>`. `hooks=True` only for a git action the user asked for.

    `readable_paths=True` (diff/show text shown to people) keeps non-ASCII file names unquoted in headers
    (`core.quotePath=false`); `"`, backslash and control characters are still C-quoted, which
    `routes/git.py` `_redact_diff` parses.
    """
    head = ["git"]
    if cwd is not None:
        head += ["-C", os.fspath(cwd)]
    head += list(_COMMON)
    if not hooks:
        head += list(_NO_HOOKS)
    if readable_paths:
        head += ["-c", "core.quotePath=false"]
    return head + list(args)


def env(extra: dict[str, str] | None = None) -> dict[str, str]:
    out = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    if extra:
        out.update(extra)
    return out
