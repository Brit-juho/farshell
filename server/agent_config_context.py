"""Request-local CLI settings roots. Never mutate the server's os.environ.

Only process-owned configuration roots are inspected; environment values and argv
are never returned to a browser. An unreadable process is not the default profile.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import hook_identity
import fsguard
import mcp_adapters
import mcp_scan
import tmux_runner
from i18n import tr

_roots: ContextVar[dict | None] = ContextVar("agent_config_roots", default=None)


def current() -> dict:
    return _roots.get() or {}


@contextmanager
def use(context: dict):
    token = _roots.set(context)
    try:
        yield
    finally:
        _roots.reset(token)


def process_args(pid: int) -> tuple[list[str], dict[str, str]]:
    """Read the same-user process without invoking a shell or printing secrets."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(1024 * 1024)
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0):
            raise OSError("process environment unavailable")
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        if not 0 < argc < 4096:
            raise OSError("process arguments unavailable")
        offset = raw.index(b"\0", 4) + 1
        while offset < len(raw) and raw[offset] == 0:
            offset += 1
        parts = raw[offset:].split(b"\0")
        argv = [p.decode("utf-8", "strict") for p in parts[:argc]]
        env_parts = parts[argc:]
    else:
        root = Path("/proc") / str(pid)
        if root.stat().st_uid != os.getuid():
            raise OSError("foreign process")
        argv = [p.decode() for p in (root / "cmdline").read_bytes().split(b"\0") if p]
        env_parts = (root / "environ").read_bytes().split(b"\0")
    env = {}
    for part in env_parts:
        if b"=" in part:
            key, value = part.split(b"=", 1)
            env[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
    return argv, env


def _safe_root(value: str, home: Path) -> Path:
    try:
        return fsguard.resolve_cli_profile(value, home)
    except fsguard.FsDenied as exc:
        raise ValueError(str(exc)) from exc


def identify(session: str) -> dict:
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", session):
        raise ValueError(tr("세션을 찾지 못했습니다"))
    name = mcp_scan._tmux_name_for(session)
    text = tmux_runner.run_text(["display-message", "-p", "-t", f"={name}:",
                                 "#{pane_id}\t#{pane_pid}\t#{pane_current_path}"])
    if not text or len(text.strip().split("\t")) != 3:
        raise ValueError(tr("세션을 찾지 못했습니다"))
    pane, pid_text, cwd = text.strip().split("\t")
    table = hook_identity._ps_table() or {}
    pane_pid = int(pid_text)
    # Follow only the pane's foreground process lineage. Background helpers and
    # nested agent tools cannot nominate a profile for this pane.
    candidates = []
    for pid, (parent, pgid, tpgid, comm) in table.items():
        if pgid != tpgid or hook_identity.is_shell(comm):
            continue
        chain, seen = pid, set()
        while chain in table and chain not in seen and len(seen) < 32:
            if chain == pane_pid:
                break
            seen.add(chain)
            chain = table[chain][0]
        if chain != pane_pid:
            continue
        base = Path(comm).name
        tool = "claude" if base.startswith("claude") else "codex" if base == "codex" else None
        if tool:
            candidates.append((len(seen), pid, tool))
    if not candidates:
        raise ValueError(tr("실행 중인 Claude 또는 Codex 프로필을 확인하지 못했습니다"))
    candidates.sort()
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ValueError(tr("세션의 CLI 프로세스가 모호합니다"))
    _, pid, tool = candidates[0]
    born = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=2)
    if born.returncode or not born.stdout.strip():
        raise ValueError(tr("프로세스 정보를 읽지 못했습니다"))
    argv, env = process_args(pid)
    home = mcp_adapters._home().resolve()
    if Path(env.get("HOME", "")).resolve() != home:
        raise ValueError(tr("세션의 사용자 홈을 확인하지 못했습니다"))
    root = _safe_root(env.get("CLAUDE_CONFIG_DIR") or str(home / ".claude"), home) if tool == "claude" else _safe_root(env.get("CODEX_HOME") or str(home / ".codex"), home)
    wt = mcp_scan.find_worktree("session:" + session)
    if not wt:
        raise ValueError(tr("세션의 프로젝트 경로를 확인하지 못했습니다"))
    return {"tool": tool, "root": str(root), "home": str(home), "session": name,
            "pane": pane, "pid": pid, "cwd": cwd, "worktree": wt["id"],
            "project": wt["path"], "argv": argv, "env": env,
            "identity": hashlib.sha256(repr((pane, pid, born.stdout.strip(), root, cwd, argv)).encode()).hexdigest()}


def public(context: dict) -> dict:
    return {k: context[k] for k in ("tool", "root", "session", "pane", "cwd", "project", "identity") if k in context}
