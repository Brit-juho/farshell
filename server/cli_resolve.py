"""Claude Code·Codex 실행 파일 찾기 — 한 곳(세션 로직 점검 단계 3).

서버가 CLI를 이름(`claude`)으로 찾으면 서버 프로세스의 PATH에 기댄다. 그런데 서버 PATH는 마지막으로 서버를
띄운 쪽에 따라 다르다 — 부팅 때 자동 시작(`zsh -l`)은 `~/.zshrc`를 읽지 않아 `~/.local/bin`이 빠지고, 그래서
재부팅 뒤 이어서 시작이 「CLI 실행 파일을 찾을 수 없습니다」로 실패했다.

순서
1. 그 대화를 실제로 돌렸던 CLI 프로세스에서 기록해 둔 경로(`record_from_process`, 프로세스 표에서만 읽는다 —
   훅이 보낸 값은 믿지 않는다). 링크를 풀지 않은 경로라 CLI가 자기 버전을 올려도(`~/.local/bin/claude` →
   `versions/<새 버전>`) 그대로 쓸 수 있다.
2. 기록해 둔 그 CLI의 PATH에서 이름으로.
3. 서버 PATH에서 이름으로.
4. 흔한 설치 위치(`~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.bun/bin`).

쓰기 전 검사(`trusted`): 일반 파일·실행 가능, 이름이 그 CLI, 거쳐 가는 링크와 실제 파일, 그 위 폴더 전부가 나 또는
root 소유이고 다른 사람(아무나, 믿지 않는 그룹)이 쓸 수 없을 것. 기록해 둔 PATH도 같은 기준의 폴더만 쓴다(`safe_path`).
다른 사용자가 바꿔 끼울 수 있는 파일은 실행하지 않는다.
"""
from __future__ import annotations

import grp
import os
import pwd
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

TOOLS = ("claude", "codex")


def known_dirs() -> list[str]:
    home = Path.home()
    return [str(home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", str(home / ".bun" / "bin")]


def _trusted_group(gid: int) -> bool:
    """그룹 쓰기를 허용해도 되는 그룹. root(0), macOS의 admin(80 — 이미 sudo로 무엇이든 바꿀 수 있는 관리자들,
    Homebrew의 `/opt/homebrew/bin`이 이 그룹 쓰기다), 그리고 나만 든 사용자 전용 그룹(Linux 기본)."""
    if gid == 0 or (sys.platform == "darwin" and gid == 80):
        return True
    try:
        group = grp.getgrgid(gid)
        me = pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return False
    return group.gr_name == me and set(group.gr_mem) <= {me}


def _owner_ok(st) -> bool:
    """나 또는 root 소유이고, 다른 사람(아무나·믿지 않는 그룹)이 쓸 수 없다."""
    if st.st_uid not in (os.getuid(), 0) or st.st_mode & stat.S_IWOTH:
        return False
    return not (st.st_mode & stat.S_IWGRP) or _trusted_group(st.st_gid)


def _ancestors_ok(path: Path) -> bool:
    """path의 위 폴더 전부. 바로 위 폴더는 `_owner_ok`, 더 위는 /tmp처럼 아무나 쓰되 sticky인 공용 폴더도 허용한다
    (sticky면 남이 내 하위 폴더를 지우거나 바꿔 끼울 수 없다)."""
    for parent in path.parents:
        st = parent.stat()
        if _owner_ok(st):
            continue
        if st.st_uid in (os.getuid(), 0) and st.st_mode & stat.S_ISVTX and parent != path.parent:
            continue
        return False
    return True


_MAX_LINKS = 40


def trusted(path, tool: str) -> bool:
    """실행해도 되는 CLI 파일인가: 절대 경로, 이름이 그 CLI, 링크를 따라간 실제 파일이 실행 가능한 일반 파일이고,
    거쳐 가는 링크와 그 모든 위 폴더, 실제 파일과 그 모든 위 폴더가 다른 사용자가 바꿔 끼울 수 없는 곳."""
    try:
        p = Path(path)
        if not p.is_absolute() or p.name != tool:
            return False
        real = p.resolve(strict=True)
        st = real.stat()
        if not stat.S_ISREG(st.st_mode) or not os.access(real, os.X_OK) or not _owner_ok(st) or not _ancestors_ok(real):
            return False
        current = Path(os.path.abspath(p))
        for _ in range(_MAX_LINKS):
            if not current.is_symlink():
                return True
            if current.lstat().st_uid not in (os.getuid(), 0) or not _ancestors_ok(current):
                return False
            current = Path(os.path.abspath(current.parent / os.readlink(current)))
        return False
    except (OSError, RuntimeError, ValueError):
        return False


def record_from_process(pid: int, tool: str) -> Optional[tuple[str, str]]:
    """그 프로세스가 실행한 CLI 경로와 PATH. 그 프로세스가 이 CLI가 아니면 None.

    npm으로 설치한 CLI처럼 `node …/cli.js`로 도는 경우는 실행 파일 이름이 `node`라 기록하지 않는다(그때는
    기록된 PATH에서 이름으로 찾는다)."""
    if tool not in TOOLS:
        return None
    try:
        import agent_config_context
        argv, env = agent_config_context.process_args(int(pid))
    except (OSError, ValueError, UnicodeError):
        return None
    if not argv:
        return None
    env_path = env.get("PATH", "")
    path = None
    first = argv[0]
    if Path(first).name == tool:
        candidate = first if os.path.isabs(first) else shutil.which(first, path=env_path or None)
        if candidate and trusted(os.path.abspath(candidate), tool):
            path = os.path.abspath(candidate)
    if path is None and env_path:
        candidate = shutil.which(tool, path=env_path)
        if candidate and trusted(candidate, tool):
            path = os.path.abspath(candidate)
    if path is None and not env_path:
        return None
    return path, env_path


def resolve(tool: str, recorded_path: Optional[str] = None, recorded_env_path: Optional[str] = None) -> Optional[str]:
    """쓸 수 있는 실행 파일 경로. 못 찾으면 None."""
    if tool not in TOOLS:
        return None
    candidates = []
    if recorded_path:
        candidates.append(recorded_path)
    recorded = safe_path(recorded_env_path)
    if recorded:
        candidates.append(shutil.which(tool, path=recorded))
    candidates.append(shutil.which(tool))
    candidates += [os.path.join(d, tool) for d in known_dirs()]
    for candidate in candidates:
        if candidate and trusted(candidate, tool):
            return os.path.abspath(candidate)
    return None


def _dir_ok(path: str) -> bool:
    """PATH에 넣어도 되는 폴더: 절대 경로이고, 그 폴더와 위 폴더를 다른 사용자가 바꿀 수 없다."""
    if not path or not os.path.isabs(path):
        return False
    try:
        st = os.stat(path)
        return stat.S_ISDIR(st.st_mode) and _owner_ok(st) and _ancestors_ok(Path(path))
    except (OSError, RuntimeError, ValueError):
        return False


def safe_path(value: Optional[str]) -> str:
    """기록해 둔 PATH에서 쓸 수 있는 폴더만 남긴다. `.`·빈 항목·상대 경로·남이 쓸 수 있는 폴더는 뺀다 — CLI가
    `#!/usr/bin/env node`로 시작하면 PATH의 `node`가 실행되므로, CLI 파일만 검사해서는 막을 수 없다."""
    kept = []
    for part in (value or "").split(os.pathsep):
        if part not in kept and _dir_ok(part):
            kept.append(part)
    return os.pathsep.join(kept)


def _append_dirs(current: str, extras) -> str:
    parts = [p for p in current.split(os.pathsep) if p]
    for extra in extras:
        if extra and extra not in parts and _dir_ok(extra):
            parts.append(extra)
    return os.pathsep.join(parts)


def launch_path(binary: str, recorded_env_path: Optional[str] = None) -> str:
    """이어서 시작한 CLI에 줄 PATH — 그 CLI가 쓰던 PATH(기록), 없으면 서버 PATH에 실행 파일 폴더와 흔한 위치를 덧붙인다.
    CLI가 띄우는 하위 도구(git, node, rg …)도 원래와 같은 PATH에서 찾게 하려는 것이다."""
    recorded = safe_path(recorded_env_path)
    if recorded:
        return recorded
    return _append_dirs(os.environ.get("PATH", ""), [os.path.dirname(binary)] + known_dirs())


def server_path(current: Optional[str] = None) -> str:
    """서버를 띄울 때 줄 PATH(`bin/fsh _start_server`). 지금 PATH 뒤에 흔한 설치 위치 중 있는 것(나 또는 root 소유,
    아무나 쓸 수 없는 폴더)을 붙인다. 앞이 아니라 뒤에 붙여 사용자가 정한 순서를 바꾸지 않고, 셸 설정(`~/.zshrc`)은
    실행하지 않는다."""
    return _append_dirs(os.environ.get("PATH", "") if current is None else current, known_dirs())


def report() -> list[tuple[str, str]]:
    """`fsh doctor`용: CLI마다 서버가 쓸 실행 파일 경로(못 찾으면 빈 문자열)."""
    return [(tool, resolve(tool) or "") for tool in TOOLS]


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "path":
        print(server_path())
    elif command == "report":
        for tool, found in report():
            print(f"{tool}\t{found}")
    else:
        sys.exit("usage: python -m cli_resolve path|report")
