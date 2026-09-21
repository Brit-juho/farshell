"""tmux 소켓 파일의 위치와 뒷정리 — 두 테스트 스위트가 같이 쓴다.

**`tmux kill-server`는 서버 프로세스만 죽이고 소켓 파일은 남긴다.** 전용 소켓을
매번 새 이름(uuid)으로 만드는 테스트 관용구와 합쳐지면 죽은 소켓 파일이 끝없이
쌓인다 — 2026-09-21에 `/tmp/tmux-501/` 에 296개가 있었고 그중 살아있는 건 실사용
소켓(`fsh`) 하나뿐이었다. 테스트들은 `kill-server`를 이미 제대로 부르고 있었다.
빠진 건 그 다음 한 줄이다. 그래서 kill과 unlink를 한 쌍으로 묶어 여기 둔다.

`server/tests`와 `tests/e2e`는 conftest를 공유하지 않는다(pytest.ini의 testpaths는
`server/tests` 뿐이고, e2e는 경로를 직접 줘서 돈다). 같은 함수를 양쪽에 베껴 두면
한쪽만 고쳐지는 게 이 저장소에서 반복된 실패 방식이라, 공용 모듈로 뺀다.
둘 다 pytest.ini의 `pythonpath = server` 로 import 한다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def socket_dir() -> Path:
    """tmux가 소켓을 두는 디렉토리 — `$TMUX_TMPDIR`(없으면 `/tmp`)/`tmux-<uid>`.

    tmux 자신의 규칙을 그대로 따른다. `TMUX_TMPDIR`을 바꿔 격리하는 환경에서
    `/tmp`로 고정해 두면 지울 대상을 못 찾고 조용히 누수가 이어진다.
    """
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return Path(base) / f"tmux-{os.getuid()}"


def socket_path(name: str) -> Path:
    """`tmux -L <name>` 이 쓰는 소켓 파일 경로."""
    return socket_dir() / name


def kill_server(name: str, timeout: float = 5.0) -> None:
    """전용 소켓의 tmux 서버를 죽이고 **소켓 파일까지 지운다.**

    `kill-server`는 요청만 보내고 바로 돌아오지만, unlink는 서버가 실제로
    내려갔는지와 무관하게 디렉토리 엔트리를 지우는 일이라 기다릴 필요가 없다.
    서버가 이미 스스로 끝나 파일이 없는 경우도 정상이므로 `missing_ok`로 삼킨다.

    정리 실패가 테스트 결과를 바꿔서는 안 된다 — 소켓 이름은 매번 새로 만들어져
    다음 실행이 대신 지워주지 못하므로 누수는 남지만, 그건 테스트를 빨갛게 만드는
    것보다 나은 실패다.
    """
    subprocess.run(["tmux", "-L", name, "kill-server"],
                   capture_output=True, timeout=timeout)
    try:
        socket_path(name).unlink(missing_ok=True)
    except OSError:
        pass
