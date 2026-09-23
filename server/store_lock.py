"""파일 락의 단일 구현.

저장소 12곳이 `fcntl.flock(fd, LOCK_EX)` 3줄을 복붙하고 있다. 고칠 곳이 12군데면
하나는 반드시 빠지므로 여기로 모은다. **교체는 아직 안 했다** — 지금 이 모듈을
쓰는 것은 `workspace.py` 하나이고, 나머지 12곳은 별도 커밋에서 기계적으로 옮긴다
(`docs/plan-2.3/20-store-durability.md` §4 4단계).

## 무기한 대기를 하지 않는다

이 함수들은 `async def` 라우트에서 **동기로** 불린다 — 예컨대
`routes/system.py`의 `workspace_put`이 그렇다. 이벤트 루프 위에서 도는 코드라
상대가 락을 쥔 채 멈추면 그 요청만이 아니라 **터미널 WebSocket까지 서버 전체가
정지한다.** 우리는 이미 같은 모양의 고장을 한 번 겪었다(E2E 스모크가 17번째에서
붕괴한 원인이 서버 로그 파이프가 막혀 이벤트 루프가 선 것이었다).

락을 같이 잡는 상대가 실제로 있다 — `bin/fsh` CLI, `mcp_write`가 경쟁하는
Claude Code, `fsh manage` TUI. 그래서 기한을 둔다.

기한 안에 못 얻으면 `LockTimeout`이다. 요청 하나가 500으로 떨어지는 쪽이
서버가 통째로 매달리는 쪽보다 낫고, 무엇보다 **진단이 된다.**

## 락 파일은 절대 지우지 않는다

flock은 경로가 아니라 inode를 키로 잡는다. 락 파일을 지우면 두 프로세스가 같은
경로의 **서로 다른 inode**를 잠그는 창이 생겨 상호배제가 조용히 깨진다.
커널이 프로세스 종료 시 fd를 닫으며 자동 해제하므로 stale 락 회수도 필요 없다.
"""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

# 정직한 경쟁에 몇 초면 충분하다. 그보다 길다면 상대가 멈춘 것이지 붐비는 게 아니다.
DEFAULT_DEADLINE = 5.0

# 경쟁 해소가 빠릿하게 느껴질 만큼 짧고, CPU를 태우지 않을 만큼 길게.
# 이벤트 루프를 잡은 채 도는 재시도라 넉넉히 잡을 이유가 없다.
RETRY_SLEEP = 0.05


class LockTimeout(TimeoutError):
    """기한 안에 락을 얻지 못했다."""


class LockContended(LockTimeout):
    """`try_exclusive`가 즉시 실패했다 — 지금 누가 쥐고 있다."""


def _acquire(lock_path: Path, deadline: float) -> int:
    """락 fd를 열고 얻을 때까지(또는 기한까지) 논블로킹으로 재시도한다."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
    end = time.monotonic() + deadline
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= end:
                os.close(fd)
                exc = LockContended if deadline <= 0 else LockTimeout
                raise exc(f"{lock_path} 락을 {deadline}초 안에 얻지 못했다")
            time.sleep(RETRY_SLEEP)
        except BaseException:
            os.close(fd)
            raise


@contextmanager
def exclusive(lock_path, *, deadline: float | None = None):
    """배타 락. 기한을 넘기면 `LockTimeout`.

    `lock_path`는 데이터 파일이 아니라 **락 전용 파일**이어야 한다. 데이터 파일을
    직접 잠그면 `os.replace`가 inode를 갈아치워 락이 허공에 걸린다.

    기본 기한을 `None`으로 받아 **호출 시점에** `DEFAULT_DEADLINE`을 읽는다.
    기본 인자 자리에 상수를 쓰면 정의 시점에 값이 박혀서, 모듈 상수를 바꿔도
    아무 일도 일어나지 않는 "튜닝 가능한 척하는 상수"가 된다.
    """
    if deadline is None:
        deadline = DEFAULT_DEADLINE
    fd = _acquire(Path(lock_path), deadline)
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def try_exclusive(lock_path):
    """기다리지 않는다. 경쟁하면 즉시 `LockContended`.

    두 자리에 쓴다 — 읽기 전용 조회 경로(경쟁하면 캐시된 값을 돌려주고 화면이
    다음 폴링에 정정한다), 그리고 재진입 회피(이미 누가 하고 있으면 건너뛴다).
    flock은 fd 단위라 같은 프로세스가 새 fd로 다시 잡으려 해도 막히므로,
    재진입이 정당한 자리에서는 `exclusive`가 자기 자신과 데드락한다.
    """
    with exclusive(lock_path, deadline=0.0):
        yield
