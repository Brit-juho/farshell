"""무기한 대기가 없는 서브프로세스 실행.

`subprocess.run(timeout=)`은 기한이 지나면 자식을 죽이고 **기한 없이** 기다린다(공식 문서: "killed and
waited for"). NFS 위에서 멈춘 `git`처럼 시그널을 받아도 시스템 콜에서 못 돌아오는 자식이면 부른
스레드까지 같이 갇힌다. `run()`은 그 자리에서
1. 자식을 새 세션(프로세스 그룹)으로 띄우고,
2. 기한이 지나면 그룹 전체에 SIGKILL 을 보낸 뒤 짧게(`REAP_TIMEOUT`)만 기다리고,
3. 그래도 안 끝나면 그 자식을 "버린 프로세스" 목록에 올리고 `TimeoutExpired`를 던진다.
버린 프로세스는 다음 호출 때 회수를 시도한다(`reap()`). 반환 모양은 `subprocess.CompletedProcess`다.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

REAP_TIMEOUT = 2.0
_abandoned: list[tuple[subprocess.Popen, float, str]] = []
_abandoned_lock = threading.Lock()


def run(argv: Sequence[str], *, timeout: float, input: Optional[bytes] = None,
        cwd=None, env=None, capture_output: bool = True, check: bool = False,
        text: bool = False, stdin=None) -> subprocess.CompletedProcess:
    if timeout is None or timeout <= 0:
        raise ValueError("proc.run 은 기한이 필요하다")
    reap()
    if input is not None and text and isinstance(input, str):
        input = input.encode()
    p = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE if input is not None else stdin,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        cwd=cwd, env=env, start_new_session=True,
    )
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        try:
            out, err = p.communicate(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            _abandon(p, argv)
        raise subprocess.TimeoutExpired(list(argv), timeout) from None
    except BaseException:
        _kill_group(p)
        try:
            p.wait(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            _abandon(p, argv)
        raise
    if text:
        out = out.decode("utf-8", errors="replace") if out is not None else None
        err = err.decode("utf-8", errors="replace") if err is not None else None
    cp = subprocess.CompletedProcess(list(argv), p.returncode, out, err)
    if check:
        cp.check_returncode()
    return cp


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.kill()
        except OSError:
            pass


def _abandon(p: subprocess.Popen, argv) -> None:
    # 파이프는 닫아 둔다 — 자식이 언젠가 돌아오면 쓰기에서 바로 실패하고 끝난다.
    for f in (p.stdin, p.stdout, p.stderr):
        try:
            if f:
                f.close()
        except OSError:
            pass
    what = " ".join(str(a) for a in list(argv)[:4])
    with _abandoned_lock:
        _abandoned.append((p, time.monotonic(), what))
    logger.warning(f"[proc] SIGKILL 뒤에도 끝나지 않는 자식을 버림 pid={p.pid} ({what}) — 커널 대기(D/U) 상태일 수 있다")


def reap() -> None:
    with _abandoned_lock:
        if not _abandoned:
            return
        keep = []
        for p, since, what in _abandoned:
            if p.poll() is None:
                keep.append((p, since, what))
            else:
                logger.info(f"[proc] 버렸던 자식 회수 pid={p.pid} ({what}, {time.monotonic() - since:.0f}s 뒤)")
        _abandoned[:] = keep


def abandoned() -> list[dict]:
    reap()
    now = time.monotonic()
    with _abandoned_lock:
        return [{"pid": p.pid, "cmd": what, "age": round(now - since, 1)} for p, since, what in _abandoned]
