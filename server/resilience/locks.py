"""기한 있고 관찰 가능한 잠금.

`threading.Lock`은 누가 쥐고 있는지, 얼마나 쥐었는지, 몇 명이 기다리는지 알려 주지 않는다. 사고 때
`_discover_lock`을 쥔 스레드가 NFS 에서 멈췄는데, 그걸 알아낸 건 프로세스 샘플링뿐이었다.
`TimedLock`은
- 쥔 쪽(스레드·op·시각)과 대기자 수·최대 보유 시간을 기록하고(`health`·`watch`가 읽는다),
- `hold(timeout=...)`로 기다리는 쪽이 반드시 기한을 두게 한다. 기한을 넘기면 `LockTimeout`.
재진입은 지원하지 않는다(원래 쓰던 `threading.Lock`과 같다).
"""
from __future__ import annotations

import threading
import time
import weakref
from contextlib import contextmanager
from typing import Iterator, Optional

_all: "weakref.WeakSet[TimedLock]" = weakref.WeakSet()


class LockTimeout(TimeoutError):
    def __init__(self, name: str, waited: float, holder: dict):
        who = holder.get("op") or holder.get("thread") or "?"
        super().__init__(f"lock {name} busy for {waited:g}s (held by {who}, {holder.get('held_for')}s)")
        self.name = name
        self.waited = waited
        self.holder = holder


class TimedLock:
    def __init__(self, name: str, default_timeout: Optional[float] = None):
        self.name = name
        self.default_timeout = default_timeout
        self._lock = threading.Lock()
        self._meta = threading.Lock()
        self._holder_thread: Optional[int] = None
        self._holder_name = ""
        self._holder_op = ""
        self._since: Optional[float] = None
        self._waiters = 0
        self.max_hold = 0.0
        self.timeouts = 0
        self.reported = False   # watch 가 이번 보유에 대해 이미 스택을 기록했다
        _all.add(self)

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None, op: str = "") -> bool:
        if timeout is None:
            timeout = self.default_timeout
        with self._meta:
            self._waiters += 1
        try:
            if not blocking:
                ok = self._lock.acquire(False)
            elif timeout is None or timeout < 0:
                ok = self._lock.acquire()
            else:
                ok = self._lock.acquire(True, timeout)
        finally:
            with self._meta:
                self._waiters -= 1
        if ok:
            t = threading.current_thread()
            with self._meta:
                self._holder_thread = t.ident
                self._holder_name = t.name
                self._holder_op = op
                self._since = time.monotonic()
                self.reported = False
        else:
            with self._meta:
                self.timeouts += 1
        return ok

    def release(self) -> None:
        with self._meta:
            if self._since is not None:
                self.max_hold = max(self.max_hold, time.monotonic() - self._since)
            self._holder_thread = None
            self._holder_name = ""
            self._holder_op = ""
            self._since = None
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    # `with lock:` — 기본 기한(default_timeout)을 쓴다. 기한이 없으면 예전처럼 무기한.
    def __enter__(self):
        if not self.acquire():
            raise LockTimeout(self.name, self.default_timeout or 0.0, self.holder())
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    @contextmanager
    def hold(self, timeout: Optional[float], op: str = "") -> Iterator["TimedLock"]:
        start = time.monotonic()
        if not self.acquire(True, timeout, op=op):
            raise LockTimeout(self.name, round(time.monotonic() - start, 1), self.holder())
        try:
            yield self
        finally:
            self.release()

    def holder(self) -> dict:
        with self._meta:
            since = self._since
            return {
                "thread": self._holder_name,
                "thread_id": self._holder_thread,
                "op": self._holder_op,
                "held_for": round(time.monotonic() - since, 1) if since is not None else None,
            }

    def held_for(self) -> float:
        since = self._since
        return 0.0 if since is None else time.monotonic() - since

    def snapshot(self) -> dict:
        h = self.holder()
        with self._meta:
            waiters, timeouts = self._waiters, self.timeouts
        return {
            "name": self.name,
            "held": h["held_for"] is not None,
            "holder": h if h["held_for"] is not None else None,
            "waiters": waiters,
            "timeouts": timeouts,
            "max_hold": round(self.max_hold, 2),
        }


def all_locks() -> list[TimedLock]:
    return list(_all)


def snapshot() -> list[dict]:
    return sorted((lk.snapshot() for lk in all_locks()), key=lambda d: d["name"])
