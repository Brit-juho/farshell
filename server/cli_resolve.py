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

쓰기 전 검사(`trusted`): 일반 파일·실행 가능, 이름이 그 CLI, 링크를 따라간 실제 파일과 그 위 폴더들이 나 또는
root 소유이고 아무나 쓸 수 있는(world-writable) 곳이 아닐 것. 다른 사용자가 바꿔 끼울 수 있는 파일은 실행하지 않는다.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Optional

TOOLS = ("claude", "codex")


def known_dirs() -> list[str]:
    home = Path.home()
    return [str(home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", str(home / ".bun" / "bin")]


def _owner_ok(st) -> bool:
    return st.st_uid in (os.getuid(), 0) and not (st.st_mode & stat.S_IWOTH)


def trusted(path, tool: str) -> bool:
    try:
        p = Path(path)
        if not p.is_absolute() or p.name != tool:
            return False
        real = p.resolve(strict=True)
        st = real.stat()
        if not stat.S_ISREG(st.st_mode) or not os.access(real, os.X_OK) or not _owner_ok(st):
            return False
        for parent in real.parents:
            pst = parent.stat()
            # /tmp처럼 아무나 쓰되 sticky인 폴더는 위쪽 경로에만 허용한다 — 실행 파일이 바로 그 안에 있으면 거절.
            if pst.st_mode & stat.S_IWOTH and not (pst.st_mode & stat.S_ISVTX and parent != real.parent):
                return False
            if pst.st_uid not in (os.getuid(), 0):
                return False
        # 사용자가 실행한 경로(링크) 쪽 폴더도 같은 기준.
        if p != real:
            lst = p.lstat()
            if lst.st_uid not in (os.getuid(), 0):
                return False
            pst = p.parent.stat()
            if pst.st_mode & stat.S_IWOTH or pst.st_uid not in (os.getuid(), 0):
                return False
        return True
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
    if recorded_env_path:
        candidates.append(shutil.which(tool, path=recorded_env_path))
    candidates.append(shutil.which(tool))
    candidates += [os.path.join(d, tool) for d in known_dirs()]
    for candidate in candidates:
        if candidate and trusted(candidate, tool):
            return os.path.abspath(candidate)
    return None


def _dir_ok(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and _owner_ok(st)


def _append_dirs(current: str, extras) -> str:
    parts = [p for p in current.split(os.pathsep) if p]
    for extra in extras:
        if extra and extra not in parts and _dir_ok(extra):
            parts.append(extra)
    return os.pathsep.join(parts)


def launch_path(binary: str, recorded_env_path: Optional[str] = None) -> str:
    """이어서 시작한 CLI에 줄 PATH — 그 CLI가 쓰던 PATH(기록), 없으면 서버 PATH에 실행 파일 폴더와 흔한 위치를 덧붙인다.
    CLI가 띄우는 하위 도구(git, node, rg …)도 원래와 같은 PATH에서 찾게 하려는 것이다."""
    if recorded_env_path:
        return recorded_env_path
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
    import sys
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "path":
        print(server_path())
    elif command == "report":
        for tool, found in report():
            print(f"{tool}\t{found}")
    else:
        sys.exit("usage: python -m cli_resolve path|report")
