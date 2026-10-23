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
import re
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


# session_state()의 결과. "없음"과 "모르겠음"은 호출자가 다르게 다뤄야 한다 —
# 모르는 걸 없다고 답하면 복원 중인 화면이 멀쩡한 세션 칸을 비워서 저장한다.
SESSION_EXISTS = "exists"
SESSION_MISSING = "missing"
SESSION_UNKNOWN = "unknown"

_MISSING_STDERR = (b"can't find session", b"no server running", b"error connecting to")


def session_state(name: str) -> str:
    """세션 존재 여부를 세 갈래로 답한다. `has_session`은 타임아웃(124)·tmux 없음(127)·
    기동 중 실패까지 전부 False라 「없음」으로 오판한다.

    `missing`은 tmux가 직접 「그런 세션/서버가 없다」고 말했을 때(rc 1 + 알려진 stderr)만이다.
    그 밖의 실패는 한 번 더 물어 보고, 그래도 확정 못 하면 `unknown`(원인은 로그에 남긴다).
    """
    rc, err = 0, b""
    for attempt in range(2):
        rc, _, err = run(["has-session", "-t", name], timeout=3.0)
        if rc == 0:
            return SESSION_EXISTS
        if rc == 1 and any(m in err for m in _MISSING_STDERR):
            return SESSION_MISSING
    logger.warning("tmux has-session unknown: name=%s rc=%s stderr=%r", name, rc, err[:200])
    return SESSION_UNKNOWN


@dataclass
class PaneInfo:
    session: str
    command: str
    pid: int
    path: str = ""
    # A2: tmux pane id("%12"). 훅이 자기보고한 $TMUX_PANE과 정확 매칭하는 키다
    # — cwd 문자열 일치는 같은 디렉토리에 세션이 둘이면 답을 못 낸다.
    pane_id: str = ""
    # 2.1 D2 — 세션이 어느 워크트리 소속인지 tmux 자신에게 물어본 값
    # (`@fsh_wt` 커스텀 옵션, worktree._sessions_for_path의 1차 판정 기준).
    # 아직 안 심겨 있으면 빈 문자열 — cwd 추론(폴백)으로 넘어간다.
    wt_tag: str = ""
    # 세션을 만든 곳(`@fsh_origin`). 기록이 없으면 "external"(normalize_origin).
    origin: str = "external"


# 세션을 만든 곳. 기록 값은 이 화이트리스트뿐이고 "external"은 값이 아니라 "기록 없음"이다
# (fsh CLI·직접 띄운 tmux는 옵션을 안 심는다). 옛 `agent` 값(2026-10-06 폐기: 에이전트는
# 세션을 만들지 않는다)은 모르는 값이라 external로 읽힌다.
ORIGIN_OPTION = "@fsh_origin"
RECORDABLE_ORIGINS = frozenset({"local", "peer"})
ORIGIN_EXTERNAL = "external"


def normalize_origin(raw: str | None) -> str:
    """tmux가 돌려준 옵션 값을 API 값으로. 모르는 값·빈 값은 모두 external."""
    v = (raw or "").strip()
    return v if v in RECORDABLE_ORIGINS else ORIGIN_EXTERNAL


# 에이전트 세션 공유 표시(2026-10-06). 사용자가 평범한 세션을 닉네임과 함께 "공유"하면 두 옵션이
# 세션에 적힌다 — tmux 가 살아 있는 동안 서버 재시작을 견딘다. 읽기는 기존 list-sessions
# 호출의 포맷에 합쳐져 추가 tmux 호출이 없다. 값은 사람이 tmux 로 직접 바꿀 수도 있으니 읽을 때마다
# 다시 검증한다(`parse_share`). 쓰기·권한 판단은 agent_share.py 가 한다.
SHARE_NICK_OPTION = "@fsh_share_nick"
SHARE_MODE_OPTION = "@fsh_share_mode"
SHARE_MODES = ("read", "readwrite")
SHARE_NICK_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def parse_share(nick: str | None, mode: str | None) -> dict | None:
    """tmux 옵션 두 값 → `{nick, mode}` 또는 None. 닉네임이 규칙에 안 맞으면 공유 아님,
    모드가 모르는 값이면 가장 낮은 `read`로 읽는다(권한이 실수로 올라가지 않게)."""
    n = (nick or "").strip()
    if not SHARE_NICK_RE.fullmatch(n):
        return None
    m = (mode or "").strip()
    return {"nick": n, "mode": m if m in SHARE_MODES else "read"}


def get_all_panes() -> list[PaneInfo]:
    """모든 세션의 모든 pane 정보를 단일 호출로 수집 (G3 핵심).

    purplemux getAllPanesInfo 패턴: list-panes -a 한 번으로 N개 세션 처리.
    """
    fmt = "#{session_name}\t#{pane_current_command}\t#{pane_pid}\t#{pane_current_path}\t#{pane_id}\t#{@fsh_wt}\t#{@fsh_origin}"
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
                wt_tag=parts[5] if len(parts) > 5 else "",
                origin=normalize_origin(parts[6] if len(parts) > 6 else ""),
            )
        )
    return panes


def list_sessions() -> list[dict]:
    """세션 메타 정보를 단일 호출로 수집."""
    fmt = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_created}\t#{@fsh_wt}\t#{@fsh_origin}\t#{@fsh_share_nick}\t#{@fsh_share_mode}"
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
                "wt_id": parts[4] if len(parts) > 4 else "",
                "origin": normalize_origin(parts[5] if len(parts) > 5 else ""),
                "share": parse_share(parts[6] if len(parts) > 6 else "", parts[7] if len(parts) > 7 else ""),
            }
        )
    return sessions


def set_option(session: str, key: str, value: str) -> bool:
    """세션에 커스텀 옵션을 심는다(`@fsh_wt` 등).

    2.1 D2 — 세션↔워크트리 소속을 서버 메모리(재시작에 사라짐)가 아니라
    tmux 세션 자신에 적어, 서버 재시작을 그대로 견디게 한다. 다른
    tmux_runner 헬퍼처럼 실패해도 예외를 던지지 않는다(호출자는 이미 열린
    세션을 막을 이유가 없는 부가 기록이라 rc만 보고 넘어간다).
    """
    rc, _, _ = run(["set-option", "-t", session, key, value], timeout=2.0)
    return rc == 0


def set_origin(session: str, origin: str) -> bool:
    """세션을 만든 곳을 `@fsh_origin`에 적는다. 화이트리스트(local/peer) 밖의 값은
    tmux를 부르지 않고 False — 호출부가 외부 입력을 그대로 넘겨도 임의 값이 들어가지 않는다."""
    if origin not in RECORDABLE_ORIGINS:
        return False
    return set_option(session, ORIGIN_OPTION, origin)


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


async def session_state_async(*args, **kwargs) -> str:
    """`session_state`의 async 버전. 인자는 그대로 넘긴다."""
    return await asyncio.to_thread(functools.partial(session_state, *args, **kwargs))


async def has_session_async(*args, **kwargs) -> bool:
    """`has_session`의 async 버전. 인자는 그대로 넘긴다."""
    return await asyncio.to_thread(functools.partial(has_session, *args, **kwargs))


async def set_origin_async(*args, **kwargs) -> bool:
    """`set_origin`의 async 버전."""
    return await asyncio.to_thread(functools.partial(set_origin, *args, **kwargs))


async def set_option_async(*args, **kwargs) -> bool:
    """`set_option`의 async 버전. 인자는 그대로 넘긴다."""
    return await asyncio.to_thread(functools.partial(set_option, *args, **kwargs))
