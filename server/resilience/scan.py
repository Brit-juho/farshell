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


class ScanUnavailable(RuntimeError):
    """자식 프로세스를 띄우지 못했다. `stream`은 프로세스 안에서 대신 돌리지 않는다(기한을 잃는다) — 호출자가 503으로 답한다."""


class Cancel:
    """`stream` 취소 표. 다른 스레드(요청 쪽)가 `cancel()`하면 그 자식 프로세스 그룹을 바로 죽인다.
    프로세스가 뜨기 전에 취소돼도 뜨자마자 죽인다(`attach`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def attach(self, p: subprocess.Popen) -> None:
        with self._lock:
            self._proc = p
            dead = self.cancelled
        if dead:
            _signal_group(p)

    def cancel(self) -> None:
        with self._lock:
            if self.cancelled:
                return
            self.cancelled = True
            p = self._proc
        if p is not None:
            _signal_group(p)


def _signal_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def stream(req: dict, timeout: float, on_message, cancel: Optional[Cancel] = None) -> tuple[Optional[dict], str]:
    """자식 프로세스의 줄 단위 메시지를 하나씩 `on_message(msg)`로 넘긴다(동기 — 스레드 안에서 부른다).

    돌려주는 것: `(result, 끝난 이유)` — 이유는 `"done"`(정상), `"timeout"`(기한 — 그때까지 받은 메시지는 이미
    넘겼다), `"cancelled"`. 기한·취소면 프로세스 그룹을 죽이고 `result`는 None이다.
    `run`과 달리 기한을 넘겨도 격리 목록·차단기에 올리지 않는다 — 내용 검색의 기한은 일의 양 때문이지 멈춘
    마운트 때문이 아니다(위험한 마운트는 `skip_prefixes`로 처음부터 안 들어간다). 자식을 못 띄우면 `ScanUnavailable`.
    """
    mode = req["mode"]
    started = time.monotonic()
    try:
        p = subprocess.Popen(
            [sys.executable, "-I", WORKER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        logger.warning(f"[scan] {mode}: 자식 프로세스를 못 띄움({e})")
        raise ScanUnavailable(str(e)) from e
    if cancel is not None:
        cancel.attach(p)

    result = None
    reason = "timeout"
    buf = b""
    sel = selectors.DefaultSelector()
    try:
        try:
            p.stdin.write((json.dumps(req) + "\n").encode())
            p.stdin.close()
        except OSError:
            pass   # 취소로 이미 죽었다 — 아래에서 EOF를 읽고 끝난다
        sel.register(p.stdout, selectors.EVENT_READ)
        deadline = started + timeout
        eof = False
        while not eof and result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not sel.select(remaining):
                continue
            chunk = os.read(p.stdout.fileno(), 65536)
            if not chunk:
                eof = True
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "result" in msg:
                    result = msg["result"]
                    break
                if "error" in msg:
                    raise RuntimeError(f"scan worker: {msg['error']}")
                on_message(msg)
        if result is not None:
            reason = "done"
        elif cancel is not None and cancel.cancelled:
            reason = "cancelled"
        elif eof:
            raise RuntimeError(f"scan worker {mode}: ended without a result")
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
    _record(mode, duration=round(time.monotonic() - started, 3), ok=reason == "done", reason=reason)
    return result, reason


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
