"""디렉터리 순회를 자식 프로세스에서 돌리고 기한을 건다.

- 원격·가상 마운트(`mounts.scan_skip_prefixes`), 격리 목록(`quarantine`), 차단기가 막은 경로는
  처음부터 건너뛴다.
- 기한(`timeout`)을 넘기면 자식 프로세스 그룹을 죽이고, 마지막으로 들어가던 디렉터리를 격리 목록에
  올린 뒤 `ScanTimeout`을 던진다. 부르는 쪽은 직전 결과를 계속 쓰면 된다.
- 자식을 못 띄우면(파이썬 실행 파일 문제 등) 같은 모듈을 프로세스 안에서 돌린다 — 이때도 마운트·격리
  건너뛰기는 그대로라 사고 경로는 막힌다.
이 함수는 **동기**다. 스레드 안에서(`pools.run("fs", ...)`) 부른다.
"""
from __future__ import annotations

import json
import logging
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import breaker, mounts, quarantine, scan_worker

logger = logging.getLogger(__name__)

WORKER = str(Path(__file__).with_name("scan_worker.py"))
KILL_WAIT = 2.0

_last_lock = threading.Lock()
_last: dict = {}


class ScanTimeout(TimeoutError):
    def __init__(self, mode: str, stuck: Optional[str], timeout: float):
        super().__init__(f"scan {mode} timed out after {timeout:g}s (stuck at {stuck})")
        self.mode = mode
        self.stuck = stuck
        self.timeout = timeout


def skip_prefixes(extra: Optional[list[str]] = None) -> list[str]:
    return sorted(set(mounts.scan_skip_prefixes() + quarantine.paths()
                      + breaker.blocked_prefixes() + list(extra or [])))


def _record(mode: str, **info) -> None:
    with _last_lock:
        _last[mode] = {"at": time.time(), **info}


def last() -> dict:
    with _last_lock:
        return {k: dict(v) for k, v in _last.items()}


def run(req: dict, timeout: float) -> dict:
    mode = req["mode"]
    started = time.monotonic()
    try:
        p = subprocess.Popen(
            [sys.executable, "-I", WORKER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning(f"[scan] 자식 프로세스를 못 띄움({e}) — 프로세스 안에서 순회한다")
        result = _inprocess(req)
        _record(mode, duration=round(time.monotonic() - started, 3), ok=True, inprocess=True)
        return result

    last_enter: Optional[str] = None
    result = None
    buf = b""
    sel = selectors.DefaultSelector()
    try:
        p.stdin.write((json.dumps(req) + "\n").encode())
        p.stdin.close()
        sel.register(p.stdout, selectors.EVENT_READ)
        deadline = started + timeout
        done = False
        while not done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not sel.select(remaining):
                continue
            chunk = os.read(p.stdout.fileno(), 65536)
            if not chunk:
                done = True
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "enter" in msg:
                    last_enter = msg["enter"]
                elif "result" in msg:
                    result = msg["result"]
                    done = True
                elif "error" in msg:
                    raise RuntimeError(f"scan worker: {msg['error']}")
    finally:
        sel.close()
        if result is None:
            _kill(p)
        else:
            try:
                p.wait(timeout=KILL_WAIT)
            except subprocess.TimeoutExpired:
                _kill(p)
        try:
            p.stdout.close()
        except OSError:
            pass

    duration = round(time.monotonic() - started, 3)
    if result is None:
        stuck = last_enter
        if stuck:
            quarantine.add(stuck, f"{mode} {timeout:g}s 초과")
            breaker.trip(stuck, f"scan {mode} timeout")
        _record(mode, duration=duration, ok=False, stuck=stuck)
        raise ScanTimeout(mode, stuck, timeout)
    _record(mode, duration=duration, ok=True, skipped=len(result.get("skipped", [])),
            truncated=bool(result.get("truncated")))
    return result


def _kill(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.wait(timeout=KILL_WAIT)
    except subprocess.TimeoutExpired:
        from . import proc
        proc._abandon(p, ["scan_worker", "(stuck)"])


def _inprocess(req: dict) -> dict:
    saved = scan_worker._emit
    scan_worker._emit = lambda obj: None
    try:
        return scan_worker.MODES[req["mode"]](req)
    finally:
        scan_worker._emit = saved
