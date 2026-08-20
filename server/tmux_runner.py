"""tmux 명령 공통 실행 헬퍼 (Phase 8 G3).

- 단일 tmux 서버 원칙: 모든 호출이 -L fsh 격리 소켓 + -f vt-tmux.conf 사용
- timeout 일관 적용 (기본 2초)
- batch 패턴: list-panes -a로 한 번에 모든 세션 정보 수집

purplemux/src/lib/tmux.ts 패턴을 Python으로 변형.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VT_TMUX_SOCKET = os.environ.get("VT_TMUX_SOCKET", "fsh")

# config 우선순위: VT_TMUX_CONF > ~/.config/vt/tmux.conf > 레포 내 config/vt-tmux.conf > 미사용
def _resolve_conf_path() -> Optional[str]:
    if env := os.environ.get("VT_TMUX_CONF"):
        if Path(env).is_file():
            return env
    home_conf = Path.home() / ".config" / "vt" / "tmux.conf"
    if home_conf.is_file():
        return str(home_conf)
    # 개발 모드: 레포 내 config 사용
    repo_conf = Path(__file__).parent.parent / "config" / "vt-tmux.conf"
    if repo_conf.is_file():
        return str(repo_conf)
    return None


VT_TMUX_CONF = _resolve_conf_path()


def base_args() -> list[str]:
    """tmux 호출 시 항상 앞에 붙는 인자 (-L fsh -u [-f conf])."""
    args = ["tmux", "-u", "-L", VT_TMUX_SOCKET]
    if VT_TMUX_CONF:
        args.extend(["-f", VT_TMUX_CONF])
    return args


def run(args: list[str], timeout: float = 2.0) -> tuple[int, bytes, bytes]:
    """tmux 명령 실행. (returncode, stdout, stderr) 반환.

    실패해도 예외 안 던짐 — 호출자가 returncode로 판단.
    """
    cmd = base_args() + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        logger.warning("tmux 미설치")
        return 127, b"", b"tmux not found"
    except subprocess.TimeoutExpired:
        logger.warning(f"tmux timeout: {' '.join(args[:3])}")
        return 124, b"", b"timeout"


def run_text(args: list[str], timeout: float = 2.0) -> Optional[str]:
    """성공 시 stdout 디코드 반환, 실패 시 None."""
    rc, out, _ = run(args, timeout)
    if rc != 0:
        return None
    return out.decode("utf-8", errors="replace")


def has_session(name: str) -> bool:
    rc, _, _ = run(["has-session", "-t", name], timeout=1.0)
    return rc == 0


@dataclass
class PaneInfo:
    session: str
    command: str
    pid: int
    path: str = ""


def get_all_panes() -> list[PaneInfo]:
    """모든 세션의 모든 pane 정보를 단일 호출로 수집 (G3 핵심).

    purplemux getAllPanesInfo 패턴: list-panes -a 한 번으로 N개 세션 처리.
    """
    fmt = "#{session_name}\t#{pane_current_command}\t#{pane_pid}\t#{pane_current_path}"
    text = run_text(["list-panes", "-a", "-F", fmt])
    if not text:
        return []
    panes: list[PaneInfo] = []
    for line in text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[2])
        except ValueError:
            pid = 0
        panes.append(
            PaneInfo(
                session=parts[0],
                command=parts[1],
                pid=pid,
                path=parts[3] if len(parts) > 3 else "",
            )
        )
    return panes


def list_sessions() -> list[dict]:
    """세션 메타 정보를 단일 호출로 수집."""
    fmt = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_created}"
    text = run_text(["list-sessions", "-F", fmt])
    if not text:
        return []
    sessions: list[dict] = []
    for line in text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        sessions.append(
            {
                "name": parts[0],
                "windows": int(parts[1]) if parts[1].isdigit() else 0,
                "attached": parts[2] == "1",
                "created": int(parts[3]) if parts[3].isdigit() else 0,
            }
        )
    return sessions


def is_installed() -> bool:
    return shutil.which("tmux") is not None
