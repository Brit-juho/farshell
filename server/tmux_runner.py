"""tmux 명령 공통 실행 헬퍼 (Phase 8 G3).

- 단일 tmux 서버 원칙: 모든 호출이 -L fsh 격리 소켓 + -f vt-tmux.conf 사용
- timeout 일관 적용 (기본 2초)
- batch 패턴: list-panes -a로 한 번에 모든 세션 정보 수집

purplemux/src/lib/tmux.ts 패턴을 Python으로 변형.
"""

from __future__ import annotations

import asyncio
import functools
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


def run(args: list[str], timeout: float = 2.0, input: Optional[bytes] = None) -> tuple[int, bytes, bytes]:
    """tmux 명령 실행. (returncode, stdout, stderr) 반환.

    실패해도 예외 안 던짐 — 호출자가 returncode로 판단.
    `input`은 `load-buffer -b <name> -`(stdin에서 버퍼 채우기)처럼 표준입력이
    필요한 명령용(N25 3/n) — 그 외엔 안 써도 된다.
    """
    cmd = base_args() + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            input=input,
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
    # A2: tmux pane id("%12"). 훅이 자기보고한 $TMUX_PANE과 정확 매칭하는 키다
    # — cwd 문자열 일치는 같은 디렉토리에 세션이 둘이면 답을 못 낸다.
    pane_id: str = ""


def get_all_panes() -> list[PaneInfo]:
    """모든 세션의 모든 pane 정보를 단일 호출로 수집 (G3 핵심).

    purplemux getAllPanesInfo 패턴: list-panes -a 한 번으로 N개 세션 처리.
    """
    fmt = "#{session_name}\t#{pane_current_command}\t#{pane_pid}\t#{pane_current_path}\t#{pane_id}"
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
                pane_id=parts[4] if len(parts) > 4 else "",
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


# --------------------------------------------------------------------------
# async 래퍼 — 이벤트 루프에서 부를 때는 **반드시** 이쪽을 쓴다
# --------------------------------------------------------------------------
#
# 위의 동기 함수들은 전부 `subprocess.run`이라 호출하는 동안 스레드가 멈춘다.
# async 핸들러에서 그대로 부르면 그 시간만큼 **서버 전체**가 멈춘다 — HTTP도
# WebSocket도, 그리고 PTY 입출력 브로드캐스트도. 웹 터미널에서는 그것이
# "타이핑이 멎었다가 한 번에 쏟아지는" 증상으로 나타난다(2026-09-16 실측:
# tmux 폴링만으로 20초 중 2.1초가 막혔고, 워크트리 탐색까지 겹쳤을 때는
# 40초 중 16.4초였다).
#
# to_thread를 호출부마다 흩뿌리지 않고 여기 한 곳에 두는 이유는, 블로킹이
# `_client_rows` 같은 **동기 헬퍼 한 겹 아래**에 숨어 있을 때 호출부만 보면
# 놓치기 때문이다(실제로 `/api/tmux/clients`가 그렇게 감사에서 빠졌다).
# `server/tests/test_no_blocking_in_async.py`가 위반을 기계적으로 막는다.


async def run_async(*args, **kwargs) -> tuple[int, bytes, bytes]:
    """`run`의 async 버전. 이벤트 루프를 막지 않는다.

    ⚠ **인자를 그대로 넘긴다(투명 래퍼).** 여기서 `timeout`/`input`에 기본값을
    채워 넘기면 호출 규약이 바뀐다 — `run`을 monkeypatch한 테스트의 가짜 함수가
    받지 않는 인자를 받게 되어 TypeError가 난다(실제로 한 번 깨뜨렸다).
    """
    return await asyncio.to_thread(functools.partial(run, *args, **kwargs))


async def run_text_async(*args, **kwargs) -> Optional[str]:
    """`run_text`의 async 버전. 인자는 그대로 넘긴다(위 주석 참고)."""
    return await asyncio.to_thread(functools.partial(run_text, *args, **kwargs))


async def get_all_panes_async(*args, **kwargs) -> list[PaneInfo]:
    """`get_all_panes`의 async 버전. 인자는 그대로 넘긴다."""
    return await asyncio.to_thread(functools.partial(get_all_panes, *args, **kwargs))


async def has_session_async(*args, **kwargs) -> bool:
    """`has_session`의 async 버전. 인자는 그대로 넘긴다."""
    return await asyncio.to_thread(functools.partial(has_session, *args, **kwargs))
