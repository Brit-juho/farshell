"""스레드에서 돌고 있는 작업 장부.

`pools.run`과 계측된 기본 실행기가 작업마다 한 줄을 남긴다. 작업이 끝나면 지운다. 끝나지 않는 작업은
계속 남아 있으므로 `watch`가 "N초째 도는 작업"을 찾아 그 스레드의 스택을 기록할 수 있고, `health`가
"어느 풀의 무엇이 몇 초째 막혀 있는지"를 답할 수 있다. 사고 당시에는 이 정보가 하나도 없었다.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

_ids = itertools.count(1)
_lock = threading.Lock()
_ops: dict[int, "Op"] = {}


@dataclass
class Op:
    id: int
    pool: str
    op: str
    key: str = ""
    submitted: float = field(default_factory=time.monotonic)
    started: Optional[float] = None
    thread: Optional[int] = None
    thread_name: str = ""
    abandoned: bool = False      # 호출자가 기한을 넘겨 떠났다(스레드는 계속 돈다)
    reported: bool = False       # watch 가 이미 스택을 기록했다

    def age(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        return now - (self.started if self.started is not None else self.submitted)

    def public(self, now: Optional[float] = None) -> dict:
        now = time.monotonic() if now is None else now
        return {
            "pool": self.pool,
            "op": self.op,
            "key": self.key,
            "state": "running" if self.started is not None else "queued",
            "age": round(self.age(now), 1),
            "abandoned": self.abandoned,
            "thread": self.thread_name,
        }


def begin(pool: str, op: str, key: str = "") -> Op:
    rec = Op(id=next(_ids), pool=pool, op=op, key=_short(key))
    with _lock:
        _ops[rec.id] = rec
    return rec


def started(rec: Op) -> None:
    t = threading.current_thread()
    rec.thread = t.ident
    rec.thread_name = t.name
    rec.started = time.monotonic()


def end(rec: Op) -> None:
    with _lock:
        _ops.pop(rec.id, None)


def snapshot() -> list[Op]:
    with _lock:
        return list(_ops.values())


def oldest_running(pool: Optional[str] = None) -> Optional[Op]:
    best = None
    for rec in snapshot():
        if rec.started is None or (pool is not None and rec.pool != pool):
            continue
        if best is None or rec.started < best.started:
            best = rec
    return best


def _short(key: str, limit: int = 300) -> str:
    key = str(key or "")
    return key if len(key) <= limit else key[: limit - 1] + "…"


def _reset_for_tests() -> None:
    with _lock:
        _ops.clear()
