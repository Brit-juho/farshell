"""Share the user's global ~/.claude with managed Claude profiles through links.

The global directory is the single source for skills, agents, commands, hooks, plugins,
CLAUDE.md and conversation history, the way a per-session clauth runtime tree mirrors it. A
profile keeps what is its own: the login or token, .claude.json, settings.json, and any
extension it holds as a real entry (a skill, agent, command or hook with no global twin, or one
that differs from the global one). Nothing here deletes data: a real entry that is replaced by a
link is first moved to `<root>/.pre-share/<time>/`.

Links are absolute paths to exactly the global entry of the same name; fsguard accepts no other
link inside a profile root.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import agent_profiles as profiles
import config_io
import fsguard
from i18n import tr

_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._ -]{0,120}")
_MAX_COMPARE_FILES = 5000


def _global():
    base = fsguard.global_claude_dir()
    if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid():
        raise profiles.Refused(tr("전역 ~/.claude 폴더를 확인하지 못했습니다"))
    return base


def _same(first, second):
    """Identical content (files by digest, directories recursively), bounded."""
    seen = [0]

    def digest(path):
        h = hashlib.sha256()
        with open(path, "rb") as stream:
            while chunk := stream.read(1 << 20):
                h.update(chunk)
        return h.hexdigest()

    def walk(a, b):
        seen[0] += 1
        if seen[0] > _MAX_COMPARE_FILES or a.is_symlink() or b.is_symlink():
            return False
        if a.is_dir() and b.is_dir():
            names = sorted(e.name for e in os.scandir(a))
            if names != sorted(e.name for e in os.scandir(b)):
                return False
            return all(walk(a / n, b / n) for n in names)
        return a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size and digest(a) == digest(b)

    return walk(Path(first), Path(second))


class _Run:
    def __init__(self, root, replace):
        self.root, self.replace = root, replace
        self.report = {"linked": [], "already": [], "kept_own": [], "moved_aside": [], "removed_stale": [], "conflicts": []}
        self._backup = None

    def aside(self, path):
        if self._backup is None:
            self._backup = self.root / ".pre-share" / time.strftime("%Y%m%d-%H%M%S")
        self._backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._backup.parent, 0o700)
        target = self._backup / path.relative_to(self.root).as_posix().replace("/", "__")
        os.rename(path, target)
        self.report["moved_aside"].append(path.relative_to(self.root).as_posix())

    def link(self, path, expected, *, whole=False):
        """Make `path` the sanctioned link to `expected`, never overwriting what is not identical.

        A `whole` entry (history, plugins) is derived data on the profile side, so a real directory
        there is always moved aside rather than compared.
        """
        label = path.relative_to(self.root).as_posix()
        if path.is_symlink():
            if os.readlink(path) == str(expected):
                self.report["already"].append(label)
            else:
                self.report["conflicts"].append(label)  # a link the profile made elsewhere stays
            return
        if path.exists():
            if not self.replace:
                self.report["kept_own"].append(label)
                return
            # A global entry may itself be a link to a repository of the user's own: compare the content it serves.
            if not whole and not _same(path, os.path.realpath(expected)):
                self.report["kept_own"].append(label)  # the profile's own version wins
                return
            self.aside(path)
        os.symlink(expected, path)
        self.report["linked"].append(label)


def sync(profile_id, *, replace=True):
    """Link a profile to the global entries. `replace=False` only adds missing links.

    Adding is safe while sessions run (launch uses it); replacing real entries needs an idle profile.
    """
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    if profile["tool"] != "claude":
        raise profiles.Refused(tr("공유 연결은 Claude 프로필에서만 쓸 수 있습니다"))
    glob = _global()
    root = profiles.root(profile["id"])
    if replace:
        from agent_profile_runtime import require_inactive
        profiles.require_no_pending_jobs(profile["id"])
        require_inactive(profile["id"])
    run = _Run(root, replace)
    with config_io.locked(root / ".profile-operation"):
        for name in fsguard.SHARED_WHOLE + fsguard.SHARED_FILES:
            if (glob / name).exists():
                run.link(root / name, glob / name, whole=name in fsguard.SHARED_WHOLE)
        for name in fsguard.SHARED_ITEMS:
            source = glob / name
            if not source.is_dir():
                continue
            folder = root / name
            if folder.is_symlink():
                run.report["conflicts"].append(name)
                continue
            fsguard.private_profile_directory(folder)
            parked = set()
            if name == "skills" and (root / ".farshell-disabled-skills").is_dir():
                parked = {e.name for e in os.scandir(root / ".farshell-disabled-skills")}
            for entry in sorted(os.scandir(source), key=lambda e: e.name):
                if entry.name.startswith(".") or not _NAME.fullmatch(entry.name) or entry.name in parked:
                    continue
                run.link(folder / entry.name, source / entry.name)
            for entry in os.scandir(folder):
                path = Path(entry.path)
                if path.is_symlink() and not os.path.exists(path) and os.readlink(path).startswith(str(source) + os.sep):
                    path.unlink()
                    run.report["removed_stale"].append(f"{name}/{entry.name}")
    return {key: sorted(value) for key, value in run.report.items()}


def skills_covered(profile_id):
    """Every global skill is reachable from the profile (linked or the profile's own version)."""
    glob = fsguard.global_claude_dir() / "skills"
    folder = profiles.root(profile_id) / "skills"
    if not glob.is_dir():
        return True
    return all((folder / e.name).exists() for e in os.scandir(glob)
               if not e.name.startswith(".") and (Path(e.path) / "SKILL.md").is_file())


def plugins_shared(profile_id):
    try:
        link = profiles.root(profile_id) / "plugins"
        return link.is_symlink() and os.readlink(link) == str(fsguard.global_claude_dir() / "plugins")
    except (OSError, profiles.Refused):
        return False


def is_history_shared(profile_id):
    """Whether the profile's conversations live in the global ~/.claude/projects."""
    try:
        link = profiles.root(profile_id) / "projects"
        return link.is_symlink() and os.readlink(link) == str(fsguard.global_claude_dir() / "projects")
    except (OSError, profiles.Refused):
        return False
